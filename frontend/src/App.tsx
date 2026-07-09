import { useState } from "react";
import Viewer3D, { type SiteGeometry } from "./Viewer3D";

// 백엔드 /api/generate 응답 형태
interface GenerateResult {
  ok: boolean;
  job_id: string;
  files: { "3dm"?: string; ortho_png?: string };
  geometry: SiteGeometry | null;
  stats: {
    buildings: number;
    solids: number;
    with_floors: number;
    cadastral_parcels: number;
    elev_range_m: [number, number] | null;
  };
  provenance: Record<string, unknown>;
  warnings: string[];
}

export default function App() {
  const [address, setAddress] = useState("대전광역시 서구 괴정동 358");
  const [radius, setRadius] = useState(250);
  const [terrain, setTerrain] = useState(true);
  const [orthophoto, setOrthophoto] = useState(true);
  const [cadastral, setCadastral] = useState(false);
  const [roads, setRoads] = useState(false);
  const [water, setWater] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateResult | null>(null);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address,
          radius_m: radius,
          layers: { buildings: true, terrain, orthophoto: terrain && orthophoto, cadastral, roads, water: terrain && water },
          outputs: ["3dm"],
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "생성에 실패했습니다.");
      } else {
        setResult(data as GenerateResult);
      }
    } catch (err) {
      setError(`요청 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-100 text-slate-800">
      <div className="mx-auto max-w-3xl px-4 py-10">
        <header className="mb-8">
          <h1 className="text-2xl font-bold text-slate-900">대지모델 생성기</h1>
          <p className="mt-1 text-sm text-slate-500">
            주소를 입력하면 주변 지형·건물 3D 모델(.3dm)에 정사영상을 입혀 생성합니다.
          </p>
        </header>

        {/* 입력 폼 */}
        <form
          onSubmit={handleGenerate}
          className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
        >
          <label className="block text-sm font-medium text-slate-700">대지 주소</label>
          <input
            type="text"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="예: 대전광역시 서구 괴정동 358"
            required
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                       focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />

          <div className="mt-4 flex flex-wrap items-center gap-6">
            <div>
              <label className="block text-sm font-medium text-slate-700">반경(m)</label>
              <input
                type="number"
                min={10}
                max={2000}
                value={radius}
                onChange={(e) => setRadius(Number(e.target.value))}
                className="mt-1 w-28 rounded-lg border border-slate-300 px-3 py-2 text-sm
                           focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            </div>
            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={terrain}
                onChange={(e) => setTerrain(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              지형(TIN)
            </label>
            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={orthophoto}
                disabled={!terrain}
                onChange={(e) => setOrthophoto(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300 disabled:opacity-40"
              />
              정사영상 텍스처
              {!terrain && <span className="text-xs text-slate-400">(지형 필요)</span>}
            </label>
            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={cadastral}
                onChange={(e) => setCadastral(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              지적(대지경계)
            </label>
            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={roads}
                onChange={(e) => setRoads(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              도로(노면)
            </label>
            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={water}
                onChange={(e) => setWater(e.target.checked)}
                disabled={!terrain}
                className="h-4 w-4 rounded border-slate-300"
              />
              수계(하천·호소)
            </label>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="mt-6 w-full rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold
                       text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed
                       disabled:bg-slate-300"
          >
            {loading ? "생성 중… (타일 다운로드 포함, 최대 30초)" : "모델 생성"}
          </button>
        </form>

        {/* 오류 */}
        {error && (
          <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            {error}
          </div>
        )}

        {/* 결과 */}
        {result && (
          <div className="mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">생성 완료</h2>

            <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat label="건물" value={`${result.stats.buildings} 동`} />
              <Stat label="솔리드" value={`${result.stats.solids}`} />
              <Stat
                label="표고 범위"
                value={
                  result.stats.elev_range_m
                    ? `${result.stats.elev_range_m[0].toFixed(0)}–${result.stats.elev_range_m[1].toFixed(0)} m`
                    : "—"
                }
              />
              <Stat label="지적" value={`${result.stats.cadastral_parcels}`} />
            </dl>

            {result.geometry && (result.geometry.buildings.length > 0 || result.geometry.terrain) && (
              <div className="mt-6">
                <Viewer3D geometry={result.geometry} orthoUrl={result.files.ortho_png} />
              </div>
            )}

            <div className="mt-6 flex flex-wrap gap-3">
              {result.files["3dm"] && (
                <a
                  href={result.files["3dm"]}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white
                             transition hover:bg-slate-700"
                >
                  .3dm 다운로드
                </a>
              )}
              {result.files.ortho_png && (
                <a
                  href={result.files.ortho_png}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold
                             text-slate-700 transition hover:bg-slate-50"
                >
                  정사영상 원본 열기
                </a>
              )}
            </div>

            {result.files.ortho_png && (
              <img
                src={result.files.ortho_png}
                alt="정사영상 미리보기"
                className="mt-4 w-full rounded-lg border border-slate-200"
              />
            )}

            {result.warnings.length > 0 && (
              <div className="mt-4 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
                <p className="font-semibold">경고</p>
                <ul className="mt-1 list-disc pl-5">
                  {result.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            <p className="mt-4 text-xs text-slate-400">
              .3dm은 Rhino에서 열 수 있습니다. 정사영상 텍스처는 같은 폴더의 PNG를 참조하므로
              둘을 함께 두세요.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm font-semibold text-slate-900">{value}</dd>
    </div>
  );
}
