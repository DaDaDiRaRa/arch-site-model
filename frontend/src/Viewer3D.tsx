import { useEffect, useRef, useState } from "react";
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

const C_BUILDING = 0x4682b4; // steel blue
const C_FLAGGED = 0xd2783c;  // orange — 층수 미확인
const C_TERRAIN = 0x6a9a55;  // olive green

// three는 Y-up, 데이터는 Z-up(z=높이) → 루트 그룹을 X축 -90° 회전해 맞춘다.
export default function Viewer3D({ geometry, orthoUrl }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const groupsRef = useRef<{ buildings?: THREE.Object3D | null; terrain?: THREE.Object3D | null }>({});
  const [showBuildings, setShowBuildings] = useState(true);
  const [showTerrain, setShowTerrain] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    setError(null);

    let width = mount.clientWidth || 800;
    let height = mount.clientHeight || 520;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xeef2f7);
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1_000_000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(width, height);
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x556070, 1.2));
    const sun = new THREE.DirectionalLight(0xffffff, 2.0);
    sun.position.set(-1, 2, 1.5);
    scene.add(sun);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    // Z-up → Y-up
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;
    scene.add(root);

    try {
      const buildings = buildBuildings(geometry.buildings);
      const terrain = buildTerrain(geometry.terrain, orthoUrl, geometry.ortho_extent_m);
      if (buildings) root.add(buildings);
      if (terrain) root.add(terrain);
      groupsRef.current = { buildings, terrain };
      root.updateMatrixWorld(true);
      fitCamera(camera, controls, root);
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
  }, [geometry, orthoUrl]);

  useEffect(() => {
    if (groupsRef.current.buildings) groupsRef.current.buildings.visible = showBuildings;
    if (groupsRef.current.terrain) groupsRef.current.terrain.visible = showTerrain;
  }, [showBuildings, showTerrain]);

  const nB = geometry.buildings.length;
  const nT = geometry.terrain?.triangles.length ?? 0;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-4 text-sm text-slate-700">
        <span className="font-medium text-slate-900">3D 미리보기</span>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showBuildings} onChange={(e) => setShowBuildings(e.target.checked)} className="h-4 w-4" />
          건물 <span className="text-xs text-slate-400">({nB})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showTerrain} onChange={(e) => setShowTerrain(e.target.checked)} className="h-4 w-4" disabled={!geometry.terrain} />
          지형 <span className="text-xs text-slate-400">({nT} 삼각형)</span>
        </label>
        <span className="ml-auto text-xs text-slate-400">드래그=회전 · 휠=확대 · 우클릭드래그=이동</span>
      </div>
      <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
        <div ref={mountRef} style={{ width: "100%", height: 520 }} />
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-red-600">{error}</div>
        )}
      </div>
    </div>
  );
}

// --- 지오메트리 빌드 --------------------------------------------------------

function buildBuildings(buildings: Props["geometry"]["buildings"]): THREE.Group | null {
  if (!buildings.length) return null;
  const g = new THREE.Group();
  const mNormal = new THREE.MeshStandardMaterial({ color: C_BUILDING, roughness: 0.85, metalness: 0 });
  const mFlagged = new THREE.MeshStandardMaterial({ color: C_FLAGGED, roughness: 0.85, metalness: 0 });

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
    g.add(new THREE.Mesh(geo, b.flagged ? mFlagged : mNormal));
  }
  return g;
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
    const dx = x1 - x0 || 1, dy = y1 - y0 || 1;
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
  return new THREE.Mesh(geo, material);
}

function fitCamera(camera: THREE.PerspectiveCamera, controls: OrbitControls, object: THREE.Object3D) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;
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
