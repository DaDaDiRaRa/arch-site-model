import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { GTAOPass } from "three/examples/jsm/postprocessing/GTAOPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";

// 백엔드 /api/generate 의 geometry (로컬 미터, z=높이/up).
export interface SiteGeometry {
  buildings: {
    footprint: [number, number][];
    holes: [number, number][][];
    base_z: number;
    height: number;
    flagged: boolean;
    verified: boolean;
  }[];
  terrain: { vertices: [number, number, number][]; triangles: [number, number, number][] } | null;
  cadastral?: { pnu: string; ring: [number, number, number][] }[] | null;
  roads?: {
    vertices: [number, number, number][];
    triangles: [number, number, number][];
    outlines: [number, number, number][][];
  } | null;
  sidewalks?: {
    vertices: [number, number, number][];
    triangles: [number, number, number][];
    outlines: [number, number, number][][];
  } | null;
  lanes?: [number, number, number][][] | null;
  water?: {
    vertices: [number, number, number][];
    triangles: [number, number, number][];
    outlines: [number, number, number][][];
  } | null;
  ortho_extent_m: [number, number, number, number] | null;
}

export interface QaFinding {
  severity: string;
  kind: string;
  message: string;
  at: [number, number] | null;
  name: string | null;
}
export interface QaResult {
  findings: QaFinding[];
  summary: { total: number; warnings: number; by_kind: Record<string, number> };
}

export interface ShadowData {
  date: string;
  hours: number[];
  entries: { time: string; sun_alt: number; sun_az: number; polygons: [number, number][][] }[];
}

interface Props {
  geometry: SiteGeometry;
  orthoUrl?: string; // 정사영상 PNG (지형에 드레이프)
  qa?: QaResult | null; // 자동 QA findings — 결함 위치에 수직 핀 표시
  shadows?: ShadowData | null; // 일조·그림자 분석 (B-3) — 시간대별 그림자 폴리곤
}

type ColorMode = "height" | "flat";
type ViewMode = "solid" | "translucent" | "wireframe";

const C_BUILDING = 0x4682b4; // steel blue (단색 모드)
const C_FLAGGED = 0xd2783c; // orange — 층수 미확인
const C_TERRAIN = 0x6a9a55; // olive green
const C_EDGE = 0x27303a; // 건물 외곽선 (짙은 슬레이트)
const C_CADASTRAL = 0xd9a441; // sandy yellow — 대지경계 (.3dm cadastral 레이어와 통일)
const C_SHADOW = 0x1b2733; // dark slate — 일조 그림자 오버레이 (B-3)
const CADASTRAL_LIFT = 0.5; // 지형 위로 살짝 띄워 z-fighting 방지 (m)
const C_ROAD_FILL = 0x74797f; // 아스팔트 그레이 — 도로 노면 (R1b)
const C_ROAD_EDGE = 0x3a3f45; // 짙은 그레이 — 도로 외곽선
const C_SIDEWALK = 0xb0aca0; // 콘크리트 베이지그레이 — 보도 (R3)
const C_LANE = 0xe8c84a; // 노랑 — 차선/중심선 마킹 (R3)
const C_WATER = 0x3a6ea5; // 강물 블루 — 수계 (평면 수면)
// 지형이 제약 삼각화로 도로 경계에 정확히 맞물리므로(도로 밑 지형은 컬링) 리프트는 경계선
// z-fighting 방지용 아주 작은 값만. 크면 도로가 떠 보인다.
const ROAD_LIFT = 0.03; // 도로 노면 — 지면에 거의 flush
const SIDEWALK_LIFT = 0.08; // 보도는 도로보다 살짝 위(연석 느낌)
const LANE_LIFT = 0.12; // 차선은 노면 위
// 높이 그라디언트: 낮음(연한 스틸) → 높음(짙은 네이비). 스틸블루 정체성 유지.
const RAMP_LO = new THREE.Color(0xa9cfe8);
const RAMP_HI = new THREE.Color(0x1f3a5f);

// three는 Y-up, 데이터는 Z-up(z=높이) → 루트 그룹을 X축 -90° 회전해 맞춘다.
export default function Viewer3D({ geometry, orthoUrl, qa, shadows }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const sceneRefs = useRef<{
    buildings?: THREE.Group | null;
    terrain?: THREE.Object3D | null;
    cadastral?: THREE.Object3D | null;
    roads?: THREE.Object3D | null;
    sidewalks?: THREE.Object3D | null;
    lanes?: THREE.Object3D | null;
    water?: THREE.Object3D | null;
    qa?: THREE.Object3D | null;
    shadows?: THREE.Group | null;
    root?: THREE.Group | null;
    buildingMeshes: THREE.Mesh[];
    edges: THREE.LineSegments[];
    sun?: THREE.DirectionalLight;
  }>({ buildingMeshes: [], edges: [] });
  const [showBuildings, setShowBuildings] = useState(true);
  const [showTerrain, setShowTerrain] = useState(true);
  const [showCadastral, setShowCadastral] = useState(true);
  const [showRoads, setShowRoads] = useState(true);
  const [showSidewalks, setShowSidewalks] = useState(true);
  const [showLanes, setShowLanes] = useState(true);
  const [showWater, setShowWater] = useState(true);
  const [showQa, setShowQa] = useState(true);
  const [colorMode, setColorMode] = useState<ColorMode>("height");
  const [viewMode, setViewMode] = useState<ViewMode>("solid");
  const [showEdges, setShowEdges] = useState(true);
  const [showShadows, setShowShadows] = useState(true);
  const [showShadowAnalysis, setShowShadowAnalysis] = useState(true);
  const [shadowIdx, setShadowIdx] = useState(0);
  const [ssao, setSsao] = useState(true); // 주변광 차폐(GTAO) — 틈·밑동 음영으로 입체감
  const ssaoRef = useRef(true); // 렌더 루프가 최신 토글값을 읽도록(재설정 없이)
  const [error, setError] = useState<string | null>(null);

  // 높이 범위(범례·그라디언트 정규화용) — geometry 바뀔 때만 재계산.
  const heightRange = useMemo(() => {
    const hs = geometry.buildings.filter((b) => b.height > 0).map((b) => b.height);
    if (!hs.length) return null;
    return { min: Math.min(...hs), max: Math.max(...hs) };
  }, [geometry]);

  // 일조 분석(B-3): 주간 시간대만 / 그림자 지면 표고(건물 base 평균, 없으면 지형 최저).
  const daylight = useMemo(
    () => (shadows?.entries ?? []).filter((e) => e.sun_alt > 0),
    [shadows]
  );
  const groundZ = useMemo(() => {
    const bs = geometry.buildings.map((b) => b.base_z).filter((z) => Number.isFinite(z));
    if (bs.length) return bs.reduce((a, c) => a + c, 0) / bs.length;
    const tv = geometry.terrain?.vertices;
    if (tv?.length) return Math.min(...tv.map((v) => v[2]));
    return 0;
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

    // SSAO(주변광 차폐, GTAO) 포스트프로세싱 — 건물 밑동·틈에 부드러운 접촉 음영으로 입체감.
    // 기본 EffectComposer 타깃엔 MSAA가 없어(직접 렌더의 antialias와 불일치) 멀티샘플 타깃을 준다.
    const dpr = renderer.getPixelRatio();
    const composerRT = new THREE.WebGLRenderTarget(
      Math.max(1, Math.floor(width * dpr)),
      Math.max(1, Math.floor(height * dpr)),
      { type: THREE.HalfFloatType, samples: 4 }
    );
    const composer = new EffectComposer(renderer, composerRT);
    composer.addPass(new RenderPass(scene, camera));
    const gtao = new GTAOPass(scene, camera, width, height);
    gtao.output = GTAOPass.OUTPUT.Default; // 씬 + AO 합성
    gtao.blendIntensity = 1.0;
    composer.addPass(gtao);
    composer.addPass(new OutputPass());

    // Z-up → Y-up
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;
    scene.add(root);

    try {
      const { group: buildings, meshes, edges } = buildBuildings(geometry.buildings, heightRange);
      const terrain = buildTerrain(geometry.terrain, orthoUrl, geometry.ortho_extent_m);
      const cadastral = buildCadastral(geometry.cadastral);
      const roads = buildRoads(geometry.roads);
      const sidewalks = buildSurfaceMesh(geometry.sidewalks, C_SIDEWALK, SIDEWALK_LIFT);
      const lanes = buildLanes(geometry.lanes);
      const water = buildSurfaceMesh(geometry.water, C_WATER, 0);
      const qaMarkers = buildQaMarkers(qa, geometry);
      if (buildings) root.add(buildings);
      if (terrain) root.add(terrain);
      if (cadastral) root.add(cadastral);
      if (roads) root.add(roads);
      if (sidewalks) root.add(sidewalks);
      if (lanes) root.add(lanes);
      if (water) root.add(water);
      if (qaMarkers) root.add(qaMarkers);

      root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(root);

      // 지형이 없으면 그림자를 받을 바닥면을 깔아 건물 그림자가 보이게 한다.
      if (!terrain && !box.isEmpty()) root.add(shadowGround(box));

      sceneRefs.current = { buildings, terrain, cadastral, roads, sidewalks, lanes, water, qa: qaMarkers, buildingMeshes: meshes, edges, sun, root };
      if (!box.isEmpty()) {
        fitCamera(camera, controls, box);
        frameSunShadow(sun, box);
        // AO 반경은 월드 단위(m). 씬 크기에 비례하되 건물 밑동/틈 규모(수 m)로 클램프.
        const dim = box.getSize(new THREE.Vector3());
        gtao.updateGtaoMaterial({
          radius: THREE.MathUtils.clamp(Math.max(dim.x, dim.y, dim.z) * 0.012, 2, 12),
          scale: 1,
          thickness: 1,
          distanceExponent: 1,
          samples: 16,
          screenSpaceRadius: false,
        });
      }
    } catch (e) {
      console.error(e);
      setError("3D 미리보기를 그리지 못했습니다.");
    }

    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      controls.update();
      if (ssaoRef.current) composer.render();
      else renderer.render(scene, camera);
    };
    animate();

    const ro = new ResizeObserver(() => {
      width = mount.clientWidth || width;
      height = mount.clientHeight || height;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
      composer.setSize(width, height); // CSS 크기 → 내부에서 ×pixelRatio (passes 포함 리사이즈)
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
      gtao.dispose();
      composer.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [geometry, orthoUrl, heightRange, qa]);

  // SSAO 토글 → 렌더 루프가 읽는 ref 동기화 (composer 재생성 없이 즉시 반영)
  useEffect(() => {
    ssaoRef.current = ssao;
  }, [ssao]);

  // 표시/숨김 토글 (재빌드 없이 즉시 반영)
  useEffect(() => {
    if (sceneRefs.current.buildings) sceneRefs.current.buildings.visible = showBuildings;
    if (sceneRefs.current.terrain) sceneRefs.current.terrain.visible = showTerrain;
    if (sceneRefs.current.cadastral) sceneRefs.current.cadastral.visible = showCadastral;
    if (sceneRefs.current.roads) sceneRefs.current.roads.visible = showRoads;
    if (sceneRefs.current.sidewalks) sceneRefs.current.sidewalks.visible = showSidewalks;
    if (sceneRefs.current.lanes) sceneRefs.current.lanes.visible = showLanes;
    if (sceneRefs.current.water) sceneRefs.current.water.visible = showWater;
    if (sceneRefs.current.qa) sceneRefs.current.qa.visible = showQa;
  }, [showBuildings, showTerrain, showCadastral, showRoads, showSidewalks, showLanes, showWater, showQa]);

  // 색상 모드: 높이별 그라디언트 ↔ 단색 (미확인 건물은 항상 주황)
  useEffect(() => {
    for (const m of sceneRefs.current.buildingMeshes) {
      const mat = m.material as THREE.MeshStandardMaterial;
      const ud = m.userData as { verified?: boolean; rampColor?: THREE.Color };
      if (ud.verified === false) continue;   // 미검증 건물은 색상모드 무관 항상 주황 (A-2)
      mat.color.copy(colorMode === "height" && ud.rampColor ? ud.rampColor : new THREE.Color(C_BUILDING));
    }
  }, [colorMode]);

  // 뷰모드: 솔리드 / 반투명 / 와이어프레임 (건물에만 적용 — 지형은 컨텍스트로 솔리드 유지)
  useEffect(() => {
    for (const m of sceneRefs.current.buildingMeshes) {
      const mat = m.material as THREE.MeshStandardMaterial;
      if (viewMode === "wireframe") {
        mat.wireframe = true;
        mat.transparent = false;
        mat.opacity = 1;
        mat.depthWrite = true;
        mat.side = THREE.FrontSide;
      } else if (viewMode === "translucent") {
        // 반투명: 뒷면도 보이게 DoubleSide, depthWrite off로 겹친 매싱 투과.
        mat.wireframe = false;
        mat.transparent = true;
        mat.opacity = 0.4;
        mat.depthWrite = false;
        mat.side = THREE.DoubleSide;
      } else {
        mat.wireframe = false;
        mat.transparent = false;
        mat.opacity = 1;
        mat.depthWrite = true;
        mat.side = THREE.FrontSide;
      }
      mat.needsUpdate = true; // side 변경은 셰이더 재컴파일 필요
    }
  }, [viewMode]);

  // 외곽선 on/off
  useEffect(() => {
    for (const e of sceneRefs.current.edges) e.visible = showEdges;
  }, [showEdges]);

  // 그림자 on/off (태양 캐스팅만 토글 — 맵은 항상 유지)
  useEffect(() => {
    if (sceneRefs.current.sun) sceneRefs.current.sun.castShadow = showShadows;
  }, [showShadows]);

  // 일조 분석: shadows 로드 시 정오(12:00)로 기본 이동.
  useEffect(() => {
    if (!daylight.length) return;
    const noon = daylight.findIndex((e) => e.time === "12:00");
    setShadowIdx(noon >= 0 ? noon : Math.floor(daylight.length / 2));
  }, [daylight]);

  // 그림자 오버레이(B-3): 선택 시각 그림자 폴리곤을 지면 표고에 렌더. 시각/토글/지오메트리 변경 시 스왑.
  useEffect(() => {
    const root = sceneRefs.current.root;
    if (!root) return;
    const entry = daylight[Math.min(shadowIdx, daylight.length - 1)];
    const grp = buildShadowOverlay(entry, groundZ);
    if (grp) {
      grp.visible = showShadowAnalysis;
      root.add(grp);
      sceneRefs.current.shadows = grp;
    }
    return () => {
      if (grp) {
        root.remove(grp);
        disposeGroup(grp);
      }
      sceneRefs.current.shadows = null;
    };
  }, [geometry, shadows, shadowIdx, groundZ, daylight, showShadowAnalysis]);

  const nB = geometry.buildings.length;
  const nT = geometry.terrain?.triangles.length ?? 0;
  const nC = geometry.cadastral?.length ?? 0;
  const nR = geometry.roads?.outlines?.length ?? 0;
  const nSW = geometry.sidewalks?.outlines?.length ?? 0;
  const nL = geometry.lanes?.length ?? 0;
  const nWater = geometry.water?.outlines?.length ?? 0;
  const nQa = qa?.findings.length ?? 0;

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
          <input type="checkbox" checked={showCadastral} onChange={(e) => setShowCadastral(e.target.checked)} className="h-4 w-4" disabled={!nC} />
          지적 <span className="text-xs text-slate-400">({nC})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showRoads} onChange={(e) => setShowRoads(e.target.checked)} className="h-4 w-4" disabled={!nR} />
          도로 <span className="text-xs text-slate-400">({nR})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showSidewalks} onChange={(e) => setShowSidewalks(e.target.checked)} className="h-4 w-4" disabled={!nSW} />
          보도 <span className="text-xs text-slate-400">({nSW})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showLanes} onChange={(e) => setShowLanes(e.target.checked)} className="h-4 w-4" disabled={!nL} />
          차선 <span className="text-xs text-slate-400">({nL})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showWater} onChange={(e) => setShowWater(e.target.checked)} className="h-4 w-4" disabled={!nWater} />
          수계 <span className="text-xs text-slate-400">({nWater})</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={showQa} onChange={(e) => setShowQa(e.target.checked)} className="h-4 w-4" disabled={!nQa} />
          <span className="text-rose-600">QA</span> <span className="text-xs text-slate-400">({nQa})</span>
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
          <input type="checkbox" checked={showShadowAnalysis} onChange={(e) => setShowShadowAnalysis(e.target.checked)} className="h-4 w-4" disabled={!daylight.length} />
          일조분석 <span className="text-xs text-slate-400">({daylight.length}시각)</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={ssao} onChange={(e) => setSsao(e.target.checked)} className="h-4 w-4" />
          음영(AO)
        </label>
        <label className="flex items-center gap-1.5">
          뷰
          <select
            value={viewMode}
            onChange={(e) => setViewMode(e.target.value as ViewMode)}
            className="rounded border border-slate-300 bg-white px-1.5 py-0.5 text-xs text-slate-700"
          >
            <option value="solid">솔리드</option>
            <option value="translucent">반투명</option>
            <option value="wireframe">와이어</option>
          </select>
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
      {shadows && daylight.length > 0 && showShadowAnalysis && (
        <div className="mb-3 flex items-center gap-3 text-xs text-slate-600">
          <span className="font-medium text-slate-700">일조 {shadows.date}</span>
          <input
            type="range"
            min={0}
            max={daylight.length - 1}
            value={Math.min(shadowIdx, daylight.length - 1)}
            onChange={(e) => setShadowIdx(Number(e.target.value))}
            className="w-56"
          />
          <span className="tabular-nums text-slate-500">
            {daylight[Math.min(shadowIdx, daylight.length - 1)]?.time} · 태양고도{" "}
            {daylight[Math.min(shadowIdx, daylight.length - 1)]?.sun_alt.toFixed(0)}°
          </span>
        </div>
      )}
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
      color: b.verified ? rampColor : C_FLAGGED,   // 미검증(추정) 건물은 항상 주황 (A-2)
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
    mesh.userData = { verified: b.verified, rampColor };
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

// 대지경계(지적): 각 필지 외곽 링을 LineLoop로. z는 백엔드가 지형 표고로 드레이프(없으면 0).
function buildCadastral(parcels: Props["geometry"]["cadastral"]): THREE.Group | null {
  if (!parcels || !parcels.length) return null;
  const g = new THREE.Group();
  const mat = new THREE.LineBasicMaterial({ color: C_CADASTRAL });
  for (const p of parcels) {
    if (!p.ring || p.ring.length < 3) continue;
    const pos = new Float32Array(p.ring.length * 3);
    for (let i = 0; i < p.ring.length; i++) {
      pos[3 * i] = p.ring[i][0];
      pos[3 * i + 1] = p.ring[i][1];
      pos[3 * i + 2] = p.ring[i][2] + CADASTRAL_LIFT;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.add(new THREE.LineLoop(geo, mat));
  }
  return g;
}

// 일조 그림자 오버레이(B-3): 선택 시각 그림자 폴리곤을 지면 표고 평면에 반투명 면으로.
function buildShadowOverlay(
  entry: ShadowData["entries"][number] | undefined,
  groundZ: number
): THREE.Group | null {
  if (!entry || !entry.polygons.length) return null;
  const g = new THREE.Group();
  g.name = "shadows";
  const mat = new THREE.MeshBasicMaterial({
    color: C_SHADOW,
    transparent: true,
    opacity: 0.34,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  for (const ring of entry.polygons) {
    if (ring.length < 3) continue;
    const shape = new THREE.Shape(ring.map(([x, y]) => new THREE.Vector2(x, y)));
    const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), mat);
    mesh.position.z = groundZ + 0.3; // 지면 살짝 위 (z-fighting 방지)
    mesh.renderOrder = 2;
    g.add(mesh);
  }
  return g;
}

function disposeGroup(obj: THREE.Object3D) {
  const mats = new Set<THREE.Material>();
  obj.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.geometry) m.geometry.dispose();
    if (m.material) mats.add(m.material as THREE.Material);
  });
  mats.forEach((mat) => mat.dispose());
}

// 도로 노면(R1b): DEM 드레이프한 삼각 메시(회색 면) + 외곽선(짙은 라인). z-fighting 방지 리프트.
function buildRoads(roads: Props["geometry"]["roads"]): THREE.Group | null {
  if (!roads) return null;
  const g = new THREE.Group();

  // 노면 메시
  if (roads.vertices?.length && roads.triangles?.length) {
    const pos = new Float32Array(roads.vertices.length * 3);
    for (let i = 0; i < roads.vertices.length; i++) {
      pos[3 * i] = roads.vertices[i][0];
      pos[3 * i + 1] = roads.vertices[i][1];
      pos[3 * i + 2] = roads.vertices[i][2] + ROAD_LIFT;
    }
    const idx = new Uint32Array(roads.triangles.length * 3);
    for (let i = 0; i < roads.triangles.length; i++) {
      idx[3 * i] = roads.triangles[i][0];
      idx[3 * i + 1] = roads.triangles[i][1];
      idx[3 * i + 2] = roads.triangles[i][2];
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setIndex(new THREE.BufferAttribute(idx, 1));
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ color: C_ROAD_FILL, roughness: 0.96, metalness: 0, side: THREE.DoubleSide });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.receiveShadow = true;
    g.add(mesh);
  }

  // 외곽선(면 위에 살짝 더 띄워 크리스프하게)
  const lmat = new THREE.LineBasicMaterial({ color: C_ROAD_EDGE, transparent: true, opacity: 0.55 });
  for (const ring of roads.outlines || []) {
    if (!ring || ring.length < 3) continue;
    const pos = new Float32Array(ring.length * 3);
    for (let i = 0; i < ring.length; i++) {
      pos[3 * i] = ring[i][0];
      pos[3 * i + 1] = ring[i][1];
      pos[3 * i + 2] = ring[i][2] + ROAD_LIFT + 0.08;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.add(new THREE.LineLoop(geo, lmat));
  }
  return g;
}

// 보도(R3): DEM 드레이프 삼각 메시(콘크리트 색). 외곽선 없이 깔끔하게.
function buildSurfaceMesh(
  data: { vertices: [number, number, number][]; triangles: [number, number, number][] } | null | undefined,
  fillColor: number,
  lift: number
): THREE.Group | null {
  if (!data || !data.vertices?.length || !data.triangles?.length) return null;
  const g = new THREE.Group();
  const pos = new Float32Array(data.vertices.length * 3);
  for (let i = 0; i < data.vertices.length; i++) {
    pos[3 * i] = data.vertices[i][0];
    pos[3 * i + 1] = data.vertices[i][1];
    pos[3 * i + 2] = data.vertices[i][2] + lift;
  }
  const idx = new Uint32Array(data.triangles.length * 3);
  for (let i = 0; i < data.triangles.length; i++) {
    idx[3 * i] = data.triangles[i][0];
    idx[3 * i + 1] = data.triangles[i][1];
    idx[3 * i + 2] = data.triangles[i][2];
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: fillColor, roughness: 0.97, metalness: 0, side: THREE.DoubleSide }));
  mesh.receiveShadow = true;
  g.add(mesh);
  return g;
}

// 차선/중심선 마킹(R3): 드레이프된 폴리라인을 노란 라인으로.
function buildLanes(lanes: Props["geometry"]["lanes"]): THREE.Group | null {
  if (!lanes || !lanes.length) return null;
  const g = new THREE.Group();
  const mat = new THREE.LineBasicMaterial({ color: C_LANE });
  for (const line of lanes) {
    if (!line || line.length < 2) continue;
    const pos = new Float32Array(line.length * 3);
    for (let i = 0; i < line.length; i++) {
      pos[3 * i] = line[i][0];
      pos[3 * i + 1] = line[i][1];
      pos[3 * i + 2] = line[i][2] + LANE_LIFT;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.add(new THREE.Line(geo, mat));
  }
  return g;
}

// 자동 QA findings → 결함 위치(at)에 수직 핀 + 머리 마커. 경고=빨강, info=주황. z 스팬은 모델 범위.
function buildQaMarkers(qa: QaResult | null | undefined, geometry: SiteGeometry): THREE.Group | null {
  if (!qa || !qa.findings.length) return null;
  let zLo = Infinity;
  let zHi = -Infinity;
  const tv = geometry.terrain?.vertices;
  if (tv && tv.length) {
    const step = Math.max(1, Math.floor(tv.length / 3000)); // 대량 정점 샘플링(min/max 스택 회피)
    for (let i = 0; i < tv.length; i += step) {
      const z = tv[i][2];
      if (z < zLo) zLo = z;
      if (z > zHi) zHi = z;
    }
  }
  for (const b of geometry.buildings) {
    if (b.base_z < zLo) zLo = b.base_z;
    if (b.base_z + b.height > zHi) zHi = b.base_z + b.height;
  }
  if (!isFinite(zLo) || !isFinite(zHi)) {
    zLo = 0;
    zHi = 100;
  }
  const top = zHi + Math.max(zHi - zLo, 20) * 0.15; // 머리를 모델 위로 조금 띄움

  const g = new THREE.Group();
  const color: Record<string, number> = { warn: 0xdc2626, info: 0xf59e0b };
  const lines: Record<string, number[]> = { warn: [], info: [] };
  const heads: Record<string, number[]> = { warn: [], info: [] };
  for (const f of qa.findings) {
    if (!f.at) continue;
    const [x, y] = f.at;
    const sev = f.severity === "warn" ? "warn" : "info";
    lines[sev].push(x, y, zLo, x, y, top); // 수직 핀(바닥~머리)
    heads[sev].push(x, y, top);
  }
  for (const sev of ["info", "warn"] as const) {
    if (lines[sev].length) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(lines[sev], 3));
      g.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: color[sev], transparent: true, opacity: 0.8 })));
    }
    if (heads[sev].length) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.Float32BufferAttribute(heads[sev], 3));
      g.add(new THREE.Points(geo, new THREE.PointsMaterial({ color: color[sev], size: 9, sizeAttenuation: false })));
    }
  }
  return g;
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
