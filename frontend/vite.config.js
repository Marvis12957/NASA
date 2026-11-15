import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// API calls are same-origin relative paths; in production the Vite preview
// server proxies them to the FastAPI backend on the same machine, so the
// public Cloudflare tunnel only ever exposes one origin.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    allowedHosts: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  preview: {
    port: 5173,
    host: true,
    // Quick-tunnel hostnames rotate; the preview sits behind Cloudflare only.
    allowedHosts: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
