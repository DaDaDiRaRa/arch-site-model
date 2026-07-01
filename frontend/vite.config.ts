import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 개발 시 백엔드(uvicorn :8000)로 프록시. 프로덕션은 FastAPI가 정적파일과
// 같은 오리진에서 서빙하므로 상대경로(/api)로 그대로 동작.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
