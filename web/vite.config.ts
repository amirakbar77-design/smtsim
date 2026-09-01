import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API is proxied under the same origin, so the browser never
// makes a cross-origin request and no CORS configuration is needed. The
// production compose stack does the same thing with nginx, for the same reason.
const API = process.env["SMTSIM_API_URL"] ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API,
        changeOrigin: true,
        ws: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
