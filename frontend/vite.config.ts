import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Kept in one place so the chunking rule reads as a list, not a regex.
const VENDOR_CHUNK = [
  "react",
  "react-dom",
  "react-router-dom",
  "@tanstack/react-query",
  "axios",
];

// Dev server proxies /api to the FastAPI backend so there are no CORS surprises
// during local development. In production the SPA is served statically and talks
// to the API via VITE_API_BASE_URL.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Split heavy, rarely-changing deps into their own cacheable chunks so
        // the main bundle stays small and charts load only where needed.
        //
        // The FUNCTION form, deliberately: Vite 8 bundles with rolldown, which
        // accepts only a function here and fails the build with "manualChunks is
        // not a function" on the object form. Rollup (Vite 6/7) accepts both, so
        // this one shape builds on either bundler and survives the upgrade.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("recharts")) return "recharts";
          for (const dep of VENDOR_CHUNK) {
            if (id.includes(`node_modules/${dep}/`)) return "vendor";
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
