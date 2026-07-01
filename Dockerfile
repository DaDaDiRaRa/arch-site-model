# arch-site-model — 프론트엔드(React) + 백엔드(FastAPI) 단일 이미지.
# GCP Cloud Run / 사내 도커 어디든 동일 이미지로 배포(다른 앱과 동일 단일 서비스 패턴).
#
# 빌드:  docker build -t arch-site-model .
# 로컬:  docker run -p 8080:8080 --env-file .env arch-site-model
#        → http://localhost:8080  (프론트) · /docs (API 문서)
#
# Cloud Run: PORT 주입. 키는 --set-secrets VWORLD_KEY 등으로 런타임 주입(이미지에 넣지 말 것).
# 주의: Dockerfile 명령어 줄 끝에 인라인 주석(#) 금지 — COPY/RUN 인자로 오해됨. 주석은 별도 줄.

# ---- Stage 1: 프론트엔드 빌드 (React + Vite) ----
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: 파이썬 런타임 (FastAPI + GDAL/rasterio) ----
# rasterio·pyproj·rhino3dm 은 GDAL/PROJ 번들 wheel이지만, GDAL이 시스템의
# libexpat(XML 파서)에 링크돼 있어 slim 이미지엔 libexpat1을 별도 설치해야 함
# (없으면 지형 요청 시 ImportError: libexpat.so.1 → 500).
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY geo_store/ ./geo_store/
# 빌드된 프론트를 FastAPI가 루트에서 서빙
COPY --from=frontend /fe/dist ./frontend/dist

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
