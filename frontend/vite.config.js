import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The console talks to the FastAPI control plane. In development Vite proxies
// /api and /health so the browser sees one origin; in production the built
// assets are served by FastAPI itself, so the same relative paths just work.
const API = process.env.GLEANER_API || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true },
      "/health": { target: API, changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
