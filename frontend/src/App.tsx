import { useState } from "react";
import Viewer3D, { type SiteGeometry } from "./Viewer3D";

// 데이터 신뢰도 리포트 (A-1) — src/trust_report.py 산출
interface TrustReport {
  address: string | null;
  buildings: { total: number; measured: number; estimated: number; measured_pct: number };
  terrain: { source: string | null; max_error_m: number; elev_range_m: [number, number] | null; triangles: number } | null;
  orthophoto: { source: string | null; zoom: number | null; missing_tiles: number | null } | null;
  qa: { total: number; warnings: number; by_kind: Record<string, number> } | null;
  meta: {
    fetched_at: string | null;
    radius_m: number | null;
    missing_floors_policy: string | null;
    floor_height_m: number | null;
    crs: string;
    building_src: string | null;
  };
  caveats: string[];
}

// 용도지역 (arch-law-graph /api/zoning 연동)
interface ZoningInfo {
  zone_name: string;
  zone_key: string | null;
  sido: string | null;
  sigungu: string | null;
  address: string | null;
  src: string;
}

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
  qa: {
    findings: { severity: string; kind: string; label: string; message: string; at: [number, number] | null; name: string | null }[];
    summary: { total: number; warnings: number; passed: boolean; stamp: string; by_kind: Record<string, number> };
  } | null;
  trust_report: TrustReport | null;
  zoning: ZoningInfo | null;
}

export default function App() {
  const [address, setAddress] = useState("대전광역시 서구 괴정동 358");
  const [radius, setRadius] = useState(250);
  const [terrain, setTerrain] = useState(true);
  const [orthophoto, setOrthophoto] = useState(true);
  const [cadastral, setCadastral] = useState(false);
  const [roads, setRoads] = useState(false);
  const [water, setWater] = useState(false);
  const [qa, setQa] = useState(false);
  const [zoning, setZoning] = useState(false);

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
          layers: { buildings: true, terrain, orthophoto: terrain && orthophoto, cadastral, roads, water: terrain && water, qa, zoning },
          outputs: ["3dm"],
        }),
      });
      const text = await res.text();
      let data: any = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = null; // 비-JSON 응답(502·타임아웃 HTML 등) — res.json() 예외로 오해 메시지 방지
      }
      if (!res.ok) {
        setError(data?.detail ?? `생성 실패 (HTTP ${res.status})`);
      } else if (!data) {
        setError(`서버 응답을 해석할 수 없습니다 (HTTP ${res.status})`);
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
            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={qa}
                onChange={(e) => setQa(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              자동 QA(검증)
            </label>            <label className="mt-5 flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={zoning}
                onChange={(e) => setZoning(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              용도지역
            </label>          </div>

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

            {result.zoning && (
              <div className="mt-2 inline-flex flex-wrap items-center gap-2 rounded-md bg-indigo-50 px-2.5 py-1 text-xs text-indigo-700 ring-1 ring-indigo-200">
                용도지역 <span className="font-semibold">{result.zoning.zone_name}</span>
                {result.zoning.sigungu && (
                  <span className="text-indigo-400">· {result.zoning.sido} {result.zoning.sigungu}</span>
                )}
              </div>
            )}

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

            {result.trust_report && <TrustPanel report={result.trust_report} />}

            {result.geometry && (result.geometry.buildings.length > 0 || result.geometry.terrain) && (
              <div className="mt-6">
                <Viewer3D geometry={result.geometry} orthoUrl={result.files.ortho_png} qa={result.qa} />
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

            {result.qa && (
              <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-700 ring-1 ring-slate-200">
                <p className={`font-semibold ${result.qa.summary.passed ? "text-emerald-700" : "text-rose-700"}`}>
                  자동 QA — {result.qa.summary.stamp}
                </p>
                {result.qa.summary.total > 0 && (
                  <ul className="mt-2 max-h-40 list-disc overflow-y-auto pl-5">
                    {result.qa.findings.slice(0, 30).map((f, i) => (
                      <li key={i} className={f.severity === "warn" ? "text-rose-700" : "text-slate-500"}>
                        <span className="font-medium">{f.label}</span> · {f.message}
                      </li>
                    ))}
                  </ul>
                )}
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

// 데이터 신뢰도 리포트 패널 (A-1) — "임원이 믿을 만한가"에 답하는 한 장.
function TrustPanel({ report }: { report: TrustReport }) {
  const b = report.buildings;
  const pct = b.measured_pct;
  const fetched = report.meta.fetched_at
    ? report.meta.fetched_at.slice(0, 16).replace("T", " ")
    : "—";
  return (
    <section className="mt-6 overflow-hidden rounded-xl border border-emerald-200 ring-1 ring-emerald-100">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 bg-emerald-50/70 px-5 py-3">
        <h3 className="text-sm font-bold text-slate-900">데이터 신뢰도 리포트</h3>
        <span className="text-xs tabular-nums text-slate-500">
          취득 {fetched} · 반경 {report.meta.radius_m ?? "—"}m · {report.meta.crs}
        </span>
      </div>

      <div className="bg-white px-5 py-4">
        {/* 실측 vs 추정 층수 */}
        <div className="flex items-baseline justify-between text-sm">
          <span className="font-medium text-slate-700">실측 층수</span>
          <span className="text-slate-500">
            건물 {b.total}동 중 <span className="font-semibold text-emerald-700">실측 {b.measured}</span>
            {b.estimated > 0 && <span className="text-amber-600"> · 추정 {b.estimated}</span>}
          </span>
        </div>
        <div className="mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-amber-200">
          <div className="h-full rounded-full bg-emerald-500" style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-1 text-right text-xs font-semibold tabular-nums text-emerald-700">{pct}% 실측</p>

        {/* 소스 3종 */}
        <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SourceCard title="지형(DEM)">
            {report.terrain ? (
              <>
                <div className="font-medium text-slate-700">수직오차 ±{report.terrain.max_error_m}m</div>
                <div className="text-slate-500">
                  {report.terrain.elev_range_m
                    ? `표고 ${report.terrain.elev_range_m[0].toFixed(0)}–${report.terrain.elev_range_m[1].toFixed(0)}m`
                    : "—"}
                  {report.terrain.triangles > 0 && ` · TIN ${report.terrain.triangles.toLocaleString()}`}
                </div>
                <div className="truncate text-slate-400" title={report.terrain.source ?? ""}>
                  {report.terrain.source ?? "NGII 5m"}
                </div>
              </>
            ) : (
              <span className="text-slate-400">미생성</span>
            )}
          </SourceCard>

          <SourceCard title="정사영상">
            {report.orthophoto ? (
              <>
                <div className="font-medium text-slate-700">{report.orthophoto.source ?? "—"}</div>
                <div className="text-slate-500">
                  {report.orthophoto.zoom != null && `zoom ${report.orthophoto.zoom}`}
                  {report.orthophoto.missing_tiles != null && ` · 결측 ${report.orthophoto.missing_tiles}`}
                </div>
              </>
            ) : (
              <span className="text-slate-400">미생성</span>
            )}
          </SourceCard>

          <SourceCard title="자동 QA">
            {report.qa ? (
              report.qa.warnings === 0 ? (
                <div className="font-semibold text-emerald-700">검수 통과 ✓</div>
              ) : (
                <div className="font-semibold text-rose-700">
                  경고 {report.qa.warnings}건 / 결함 {report.qa.total}
                </div>
              )
            ) : (
              <span className="text-slate-400">미실행</span>
            )}
          </SourceCard>
        </dl>

        {/* 정직한 한계 고지 */}
        {report.caveats.length > 0 && (
          <div className="mt-4 rounded-lg bg-slate-50 px-4 py-3 ring-1 ring-slate-200">
            <p className="text-xs font-semibold text-slate-600">데이터 한계 고지</p>
            <ul className="mt-1 space-y-0.5">
              {report.caveats.map((c, i) => (
                <li key={i} className="text-xs text-slate-500">⚠ {c}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function SourceCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 text-xs">
      <dt className="font-medium uppercase tracking-wide text-slate-400">{title}</dt>
      <dd className="mt-1 space-y-0.5">{children}</dd>
    </div>
  );
}
