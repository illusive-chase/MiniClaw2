import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendUrl = process.env.MINICLAW_BACKEND_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = backendUrl.replace(/^http/, "ws");
const reloadEnabled = process.env.MINICLAW_RELOAD !== "0";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    hmr: reloadEnabled,
    proxy: {
      "/global-state": backendUrl,
      "/model-presets": backendUrl,
      "/principles": backendUrl,
      "/sessions": backendUrl,
      "/skills": backendUrl,
      "/templates": backendUrl,
      "/user-templates": backendUrl,
      "/ws": {
        target: backendWsUrl,
        ws: true,
      },
    },
  },
});
