import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

// 백엔드 /api/generate 의 geometry (로컬 미터, z=높이/up).
export interface SiteGeometry {
  buildings: {
    footprint: [number, number][];
    holes: [number, number][][];
    base_z: number;
    height: number;
    flagged: boolean;
  }[];
  terrain: { vertices: [number, number, number][]; triangles: [number, number, number][] } | null;
  ortho_extent_m: [number, number, number, number] | null;
}

interface Props {
  geometry: SiteGeometry;
  orthoUrl?: string; // 정사영상 PNG (지형에 드레이프)
}

type ColorMode = "height" | "flat";

const C_BUILDING = 0x4682b4; // steel blue (단색 모드)
const C_FLAGGED = 0xd2783c; // orange — 층수 미확인
const C_TERRAIN = 0x6a9a55; // olive green
const C_EDGE = 0x27303a; // 건물 외곽선 (짙은 슬레이트)
// 높이 그라디언트: 낮음(연한 스틸) → 높음(짙은 네이비). 스틸블루 정체성 유지.
const RAMP_LO = new THREE.Color(0xa9cfe8);
const RAMP_HI = new THREE.Color(0x1f3a5f);

// three는 Y-up, 데이터는 Z-up(z=높이) → 루트 그룹을 X축 -90° 회전해 맞춘다.
export default function Viewer3D({ geometry, orthoUrl }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRefs = useRef<{
    buildings?: THREE.Group | null;
    terrain?: THREE.Object3D | null;
    buildingMeshes: THREE.Mesh[];
    edges: THREE.LineSegments[];
    sun?: THREE.DirectionalLight;
  }>({ buildingMeshes: [], edges: [] });
  const [showBuildings, setShowBuildings] = useState(true);
  const [showTerrain, setShowTerrain] = useState(true);
  const [colorMode, setColorMode] = useState<ColorMode>("height");
  const [showEdges, setShowEdges] = useState(true);
  const [showShadows, setShowShadows] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 높이 범위(범례·그라디언트 정규화용) — geometry 바뀔 때만 재계산.
  const heightRange = useMemo(() => {
    const hs = geometry.buildings.filter((b) => b.height > 0).map((b) => b.height);
    if (!hs.length) return null;
    return { min: Math.min(...hs), max: Math.max(...hs) };
  }, [geometry]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setError(null);

    let width = mount.clientWidth || 800;
    let height = mount.clientHeight || 520;

    const scene = new THREE.Scene();
    scene.background = skyTexture();

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1_000_000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(width, height);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xdfe9f5, 0x4a5568, 1.05));
    const sun = new THREE.DirectionalLight(0xfff4e2, 2.1);
    sun.castShadow = true;
    scene.add(sun);
    scene.add(sun.target);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    // Z-up → Y-up
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;
    scene.add(root);

    try {
      const { group: buildings, meshes, edges } = buildBuildings(geometry.buildings, heightRange);
      const terrain = buildTerrain(geometry.terrain, orthoUrl, geometry.ortho_extent_m);
      if (buildings) root.add(buildings);
      if (terrain) root.add(terrain);

      root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(root);

      // 지형이 없으면 그림자를 받을 바닥면을 깔아 건물 그림자가 보이게 한다.
      if (!terrain && !box.isEmpty()) root.add(shadowGround(box));

      sceneRefs.current = { buildings, terrain, buildingMeshes: meshes, edges, sun };
      if (!box.isEmpty()) {
        fitCamera(camera, controls, box);
        frameSunShadow(sun, box);
      }
    } catch (e) {
      console.error(e);
      setError("3D 미리보기를 그리지 못했습니다.");
    }

    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const ro = new ResizeObserver(() => {
      width = mount.clientWidth || width;
      height = mount.clientHeight || height;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    });
    ro.observe(mount);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      if (scene.background instanceof THREE.Texture) scene.background.dispose();
      scene.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const mat = mesh.material;
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
        else if (mat) (mat as THREE.Material & { map?: THREE.Texture }).map?.dispose(), (mat as THREE.Material).dispose();
      });
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [geometry, orthoUrl, heightRange]);

  // 표시/숨김 토글 (재빌드 없이 즉시 반영)
  useEffect(() => {
    if (sceneRefs.current.buildings) sceneRefs.current.buildings.visible = showBuildings;
    if (sceneRefs.current.terrain) sceneRefs.current.terrain.visible = showTerrain;
  }, [showBuildings, showTerrain]);

  // 색상 모드: 높이별 그라디언트 ↔ 단색 (미확인 건물은 항상 주황)
  useEffect(() => {
    for (const m of sceneRefs.current.buildingMeshes) {
      const mat = m.material as THREE.MeshStandardMaterial;
      const ud = m.userData as { flagged?: boolean; rampColor?: THREE.Color };
      if (ud.flagged) continue;
      mat.color.copy(colorMode === "height" && ud.rampColor ? ud.rampColor : new THREE.Color(C_BUILDING));
    }
  }, [colorMode]);

  // 외곽선 on/off
  useEffect(() => {
    for (const e of sceneRefs.current.edges) e.visible = showEdges;
  }, [showEdges]);

  // 그림자 on/off (태양 캐스팅만 토글 — 맵은 항상 유지)
  useEffect(() => {
    if (sceneRefs.current.sun) sceneRefs.current.sun.castShadow = showShadows;
  }, [showShadows]);

  const nB = geometry.buildings.length;
  const nT = geometry.terrain?.triangles.length ?? 0;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-700">
        <span className="font-medium text-slate-900">3D 미리보기</span>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showBuildings} onChange={(e) => setShowBuildings(e.target.checked)} className="h-4 w-4" />
          건물 <span className="text-xs text-slate-400">({nB})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showTerrain} onChange={(e) => setShowTerrain(e.target.checked)} className="h-4 w-4" disabled={!geometry.terrain} />
          지형 <span className="text-xs text-slate-400">({nT} 삼각형)</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showEdges} onChange={(e) => setShowEdges(e.target.checked)} className="h-4 w-4" />
          외곽선
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showShadows} onChange={(e) => setShowShadows(e.target.checked)} className="h-4 w-4" />
          그림자
        </label>
        <label className="flex items-center gap-1.5">
          색상
          <select
            value={colorMode}
            onChange={(e) => setColorMode(e.target.value as ColorMode)}
            className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-xs text-slate-700"
          >
            <option value="height">높이별</option>
            <option value="flat">단색</option>
          </select>
        </label>
        <span className="ml-auto text-xs text-slate-400">드래그=회전 · 휠=확대 · 우클릭드래그=이동</span>
      </div>
      <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
        <div ref={mountRef} style={{ width: "100%", height: 520 }} />
        {colorMode === "height" && heightRange && heightRange.max > heightRange.min && (
          <HeightLegend min={heightRange.min} max={heightRange.max} />
        )}
        {error && <div className="absolute inset-0 flex items-center justify-center text-sm text-red-600">{error}</div>}
      </div>
    </div>
  );
}

// --- 높이 범례 --------------------------------------------------------------

function HeightLegend({ min, max }: { min: number; max: number }) {
  const lo = `#${RAMP_LO.getHexString()}`;
  const hi = `#${RAMP_HI.getHexString()}`;
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 rounded-md bg-white/85 px-2.5 py-2 text-[11px] text-slate-600 shadow-sm ring-1 ring-slate-200">
      <div className="mb-1 font-medium text-slate-700">건물 높이</div>
      <div className="flex items-center gap-2">
        <span>{Math.round(min)}m</span>
        <span className="h-2.5 w-24 rounded" style={{ background: `linear-gradient(to right, ${lo}, ${hi})` }} />
        <span>{Math.round(max)}m</span>
      </div>
    </div>
  );
}

// --- 지오메트리 빌드 --------------------------------------------------------

function buildBuildings(
  buildings: Props["geometry"]["buildings"],
  range: { min: number; max: number } | null
): { group: THREE.Group | null; meshes: THREE.Mesh[]; edges: THREE.LineSegments[] } {
  if (!buildings.length) return { group: null, meshes: [], edges: [] };
  const g = new THREE.Group();
  const meshes: THREE.Mesh[] = [];
  const edges: THREE.LineSegments[] = [];
  const span = range ? range.max - range.min : 0;
  const edgeMat = new THREE.LineBasicMaterial({ color: C_EDGE, transparent: true, opacity: 0.35 });

  for (const b of buildings) {
    if (b.footprint.length < 3 || b.height <= 0) continue;
    const shape = new THREE.Shape(b.footprint.map(([x, y]) => new THREE.Vector2(x, y)));
    for (const hole of b.holes || []) {
      if (hole.length >= 3) shape.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y))));
    }
    const geo = new THREE.ExtrudeGeometry(shape, { depth: b.height, bevelEnabled: false });
    // ExtrudeGeometry는 XY 평면에서 +Z로 돌출 → base_z 만큼 올림.
    geo.translate(0, 0, b.base_z);
    geo.computeVertexNormals();

    const rampColor = range && span > 0 ? RAMP_LO.clone().lerp(RAMP_HI, (b.height - range.min) / span) : RAMP_HI.clone();
    const mat = new THREE.MeshStandardMaterial({
      color: b.flagged ? C_FLAGGED : rampColor,
      roughness: 0.82,
      metalness: 0,
      // 면 위에 외곽선이 깔끔히 얹히도록 폴리곤 오프셋.
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.userData = { flagged: b.flagged, rampColor };
    meshes.push(mesh);

    // 외곽선: 실제 모서리만(평면 삼각화 대각선 제외 — thresholdAngle 20°).
    const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 20), edgeMat);
    edges.push(edge);
    mesh.add(edge);

    g.add(mesh);
  }
  return { group: g, meshes, edges };
}

function buildTerrain(
  terrain: Props["geometry"]["terrain"],
  orthoUrl: string | undefined,
  extent: [number, number, number, number] | null
): THREE.Mesh | null {
  if (!terrain || !terrain.vertices.length || !terrain.triangles.length) return null;

  const positions = new Float32Array(terrain.vertices.length * 3);
  for (let i = 0; i < terrain.vertices.length; i++) {
    positions[3 * i] = terrain.vertices[i][0];
    positions[3 * i + 1] = terrain.vertices[i][1];
    positions[3 * i + 2] = terrain.vertices[i][2];
  }
  const index = new Uint32Array(terrain.triangles.length * 3);
  for (let i = 0; i < terrain.triangles.length; i++) {
    index[3 * i] = terrain.triangles[i][0];
    index[3 * i + 1] = terrain.triangles[i][1];
    index[3 * i + 2] = terrain.triangles[i][2];
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setIndex(new THREE.BufferAttribute(index, 1));
  geo.computeVertexNormals();

  let material: THREE.Material;
  if (orthoUrl) {
    // 정사영상 평면(top-down) UV: 정사영상 범위(없으면 지형 bbox)로 정규화.
    geo.computeBoundingBox();
    const bb = geo.boundingBox!;
    const [x0, y0, x1, y1] = extent ?? [bb.min.x, bb.min.y, bb.max.x, bb.max.y];
    const dx = x1 - x0 || 1,
      dy = y1 - y0 || 1;
    const uv = new Float32Array(terrain.vertices.length * 2);
    for (let i = 0; i < terrain.vertices.length; i++) {
      uv[2 * i] = (positions[3 * i] - x0) / dx;
      uv[2 * i + 1] = (y1 - positions[3 * i + 1]) / dy; // 북(y1)→v=0(이미지 상단)
    }
    geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    const tex = new THREE.TextureLoader().load(orthoUrl);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.flipY = false;
    material = new THREE.MeshStandardMaterial({ map: tex, roughness: 1, metalness: 0, side: THREE.DoubleSide });
  } else {
    material = new THREE.MeshStandardMaterial({ color: C_TERRAIN, roughness: 1, metalness: 0, side: THREE.DoubleSide, flatShading: false });
  }
  const mesh = new THREE.Mesh(geo, material);
  mesh.receiveShadow = true;
  return mesh;
}

// 지형이 없을 때 건물 그림자를 받는 투명 바닥면(그림자만 렌더).
function shadowGround(worldBox: THREE.Box3): THREE.Mesh {
  const size = worldBox.getSize(new THREE.Vector3());
  const center = worldBox.getCenter(new THREE.Vector3());
  const s = Math.max(size.x, size.z) * 2 || 500;
  const geo = new THREE.PlaneGeometry(s, s); // XY 평면 (데이터 Z-up 공간)
  const mesh = new THREE.Mesh(geo, new THREE.ShadowMaterial({ opacity: 0.22 }));
  // worldBox는 회전 후(월드) 좌표 → 데이터 Z(=월드 Y) 최소값에 바닥을 둔다.
  mesh.position.set(center.x, center.z, worldBox.min.y);
  mesh.receiveShadow = true;
  return mesh;
}

// 부드러운 하늘 그라디언트 배경.
function skyTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 2;
  c.height = 256;
  const ctx = c.getContext("2d")!;
  const grad = ctx.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, "#e9f0f8");
  grad.addColorStop(1, "#c8d5e4");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 2, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function fitCamera(camera: THREE.PerspectiveCamera, controls: OrbitControls, box: THREE.Box3) {
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 100;
  camera.near = maxDim / 1000;
  camera.far = maxDim * 100;
  const dist = maxDim * 1.5;
  camera.position.set(center.x + dist * 0.8, center.y + dist * 0.7, center.z + dist * 0.8);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

// 태양 그림자 카메라를 씬 경계에 맞춘다(월드 좌표).
function frameSunShadow(sun: THREE.DirectionalLight, box: THREE.Box3) {
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const r = (Math.max(size.x, size.y, size.z) || 100) * 0.75;
  const dir = new THREE.Vector3(-1, 2, 1.4).normalize();
  sun.position.copy(center).addScaledVector(dir, r * 2.2);
  sun.target.position.copy(center);
  const cam = sun.shadow.camera;
  cam.left = -r;
  cam.right = r;
  cam.top = r;
  cam.bottom = -r;
  cam.near = r * 0.1;
  cam.far = r * 5;
  cam.updateProjectionMatrix();
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.6;
}
