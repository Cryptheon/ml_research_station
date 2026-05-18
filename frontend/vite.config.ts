import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  server: {
    port: 5173,
    // Proxy all API paths to the running FastAPI server in dev.
    // The frontend uses an empty API_BASE (same origin) so both dev and
    // production builds hit the correct host without hard-coding a port.
    proxy: {
      "/papers": "http://localhost:8080",
      "/system": "http://localhost:8080",
      "/ingest": "http://localhost:8080",
      "/users": "http://localhost:8080",
      "/chats": "http://localhost:8080",
      "/batch": "http://localhost:8080",
      "/workspace": "http://localhost:8080",
      "/web-screenshots": "http://localhost:8080",
      "/taxonomy": "http://localhost:8080",
      "/search": "http://localhost:8080",
      "/prompts": "http://localhost:8080",
      "/watches": "http://localhost:8080",
      "/processing": "http://localhost:8080",
      "/models": "http://localhost:8080",
      "/sources": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },

  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        // Keep D3 and KaTeX in separate chunks so the main bundle stays lean.
        manualChunks: {
          d3: ["d3"],
          katex: ["katex"],
        },
      },
    },
  },
});
