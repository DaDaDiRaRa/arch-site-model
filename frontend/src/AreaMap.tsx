import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// [minlon, minlat, maxlon, maxlat]
export type BBox = [number, number, number, number];

// 서버가 받는 한 변 범위(m) — src/api.py _MAX_AREA_SIDE_M 와 같게 유지
export const AREA_MIN_M = 20;
export const AREA_MAX_M = 4000;

const R = 6371008.8;
const rad = (d: number) => (d * Math.PI) / 180;

/** 영역 가로·세로(m) — 중심 위도 기준 근사(수 km 영역에서 오차 무시 가능) */
export function bboxSizeM(b: BBox): [number, number] {
  const midLat = rad((b[1] + b[3]) / 2);
  return [rad(b[2] - b[0]) * R * Math.cos(midLat), rad(b[3] - b[1]) * R];
}

/** 중심 + 반경(m) → 한 변 2r 정사각형 (백엔드 bbox_from_point와 같은 모양) */
export function squareAround(lon: number, lat: number, radiusM: number): BBox {
  const dLat = (radiusM / R) * (180 / Math.PI);
  const dLon = dLat / Math.cos(rad(lat));
  return [lon - dLon, lat - dLat, lon + dLon, lat + dLat];
}

interface Props {
  area: BBox | null;
  /** 지도 클릭 = 클릭 지점 중심 정사각형(반경), 드래그 그리기 = 자유 사각형 */
  onPick: (area: BBox, how: "click" | "draw") => void;
  radius: number;
  flyTo: { lon: number; lat: number; key: number } | null;
}

export default function AreaMap({ area, onPick, radius, flyTo }: Props) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const rectRef = useRef<L.Rectangle | null>(null);
  const [drawing, setDrawing] = useState(false);
  const drawingRef = useRef(false);
  const radiusRef = useRef(radius);
  radiusRef.current = radius;
  drawingRef.current = drawing;
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;

  // 지도 1회 생성 — 배경은 백엔드 중계 타일(/api/basemap, VWorld 키는 서버에만)
  useEffect(() => {
    if (!divRef.current || mapRef.current) return;
    const map = L.map(divRef.current, { minZoom: 6, maxZoom: 19, zoomControl: true }).setView([36.35, 127.38], 15);
    const base = L.tileLayer("/api/basemap/base/{z}/{x}/{y}", {
      maxZoom: 19,
      attribution: "© VWorld",
    }).addTo(map);
    const sat = L.layerGroup([
      L.tileLayer("/api/basemap/satellite/{z}/{x}/{y}", { maxZoom: 19, attribution: "© VWorld" }),
      L.tileLayer("/api/basemap/hybrid/{z}/{x}/{y}", { maxZoom: 19 }),
    ]);
    L.control.layers({ 일반지도: base, 위성: sat }, {}, { position: "topright" }).addTo(map);
    mapRef.current = map;

    // 클릭 → 반경 정사각형 (그리기 모드가 아닐 때)
    map.on("click", (e: L.LeafletMouseEvent) => {
      if (drawingRef.current) return;
      onPickRef.current(squareAround(e.latlng.lng, e.latlng.lat, radiusRef.current), "click");
    });

    // 드래그 그리기: 누른 점 ~ 뗀 점 사각형
    let start: L.LatLng | null = null;
    let temp: L.Rectangle | null = null;
    map.on("mousedown", (e: L.LeafletMouseEvent) => {
      if (!drawingRef.current) return;
      start = e.latlng;
      temp = L.rectangle(L.latLngBounds(start, start), { color: "#059669", weight: 2, dashArray: "4" }).addTo(map);
    });
    map.on("mousemove", (e: L.LeafletMouseEvent) => {
      if (start && temp) temp.setBounds(L.latLngBounds(start, e.latlng));
    });
    map.on("mouseup", (e: L.LeafletMouseEvent) => {
      if (!start || !temp) return;
      const b = L.latLngBounds(start, e.latlng);
      temp.remove();
      start = null;
      temp = null;
      setDrawing(false);
      map.dragging.enable();
      onPickRef.current([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], "draw");
    });
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // 그리기 모드 동안은 지도 끌기를 끈다(드래그가 사각형이 되도록)
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (drawing) map.dragging.disable();
    else map.dragging.enable();
    divRef.current!.style.cursor = drawing ? "crosshair" : "";
  }, [drawing]);

  // 선택 영역 표시
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    rectRef.current?.remove();
    rectRef.current = null;
    if (area) {
      const [w, s, e, n] = area;
      const [wm, hm] = bboxSizeM(area);
      const ok = Math.max(wm, hm) <= AREA_MAX_M && Math.min(wm, hm) >= AREA_MIN_M;
      rectRef.current = L.rectangle(
        [[s, w], [n, e]],
        { color: ok ? "#059669" : "#dc2626", weight: 2, fillOpacity: 0.08, interactive: false },
      ).addTo(map);
    }
  }, [area]);

  // 주소 검색 결과로 이동
  useEffect(() => {
    if (flyTo && mapRef.current) mapRef.current.flyTo([flyTo.lat, flyTo.lon], 16, { duration: 0.6 });
  }, [flyTo]);

  const size = area ? bboxSizeM(area) : null;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-600">
        <button
          type="button"
          onClick={() => setDrawing((d) => !d)}
          className={`rounded-md px-3 py-1.5 font-semibold ring-1 transition ${
            drawing ? "bg-emerald-600 text-white ring-emerald-600" : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50"
          }`}
        >
          {drawing ? "지도에서 드래그하세요…" : "▭ 사각형으로 그리기"}
        </button>
        <span>또는 지도를 클릭하면 반경 {radius}m 정사각형이 놓입니다.</span>
        {size && (
          <span className="ml-auto tabular-nums font-medium text-slate-700">
            {Math.round(size[0]).toLocaleString()} × {Math.round(size[1]).toLocaleString()} m
          </span>
        )}
      </div>
      <div ref={divRef} className="h-80 w-full overflow-hidden rounded-lg ring-1 ring-slate-300" />
    </div>
  );
}
