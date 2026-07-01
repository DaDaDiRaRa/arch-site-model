# arch-site-model 백엔드 — FastAPI + GDAL/rasterio 스택.
# GCP Cloud Run / 사내 도커 어디든 동일 이미지로 배포.
#
# rasterio·pyproj·rhino3dm 은 GDAL/PROJ 를 번들한 manylinux wheel 이라
# 별도 시스템 GDAL 설치 불필요(python:slim 로 충분).
#
# 빌드:  docker build -t arch-site-model .
# 로컬:  docker run -p 8080:8080 --env-file .env arch-site-model
# 문서:  http://localhost:8080/docs
#
# Cloud Run: PORT 환경변수를 주입하므로 그대로 사용. 키는 --set-env-vars 또는
#            Secret Manager 로 VWORLD_KEY / NGII_KEY / ORTHO_SOURCE 주입(이미지에 넣지 말 것).

FROM python:3.11-slim

WORKDIR /app

# 의존성 먼저(레이어 캐시)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 + 지형 비축(DEM). geo_store 는 이미지에 포함(현재 대전 단일 도엽).
# 지역 확장 시 용량 커지면 GCS 버킷 마운트로 전환 고려.
COPY src/ ./src/
COPY geo_store/ ./geo_store/

# Cloud Run 은 PORT(기본 8080)를 주입. 로컬은 8080 기본.
ENV PORT=8080
EXPOSE 8080

# exec 형태로 시그널 전파 + $PORT 확장.
CMD ["sh", "-c", "exec uvicorn src.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
