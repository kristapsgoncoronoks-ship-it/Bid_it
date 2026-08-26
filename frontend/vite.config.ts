import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The chart stack, by exact package name. Everything else from node_modules
// goes to "vendor". Two rules matter here, both learned from shipped defects:
//
// 1. Match the PACKAGE NAME segment, not a substring of the whole path.
//    `id.includes("recharts")` also matched nested paths like
//    node_modules/recharts/node_modules/react-is — a module the ENTRY graph
//    shares — so the entry chunk grew a static import edge into the recharts
//    chunk and index.html modulepreloaded 415 kB of charts on first paint
//    (the ~773 kB known-broken entry in DEPLOY-RUNBOOK-2026-08-12.md).
// 2. A name LIST for vendor rots: react-router-dom's code actually lives in
//    the react-router / @remix-run packages, which the old list never
//    matched, so the router fell out of "vendor" into an anonymous chunk.
//    "everything not charts" cannot rot.
// recharts@3's full dependency closure (checked against the app's non-chart
// closure: zero overlap, so nothing here is reachable from the entry graph).
// If the app ever starts importing one of these directly (say clsx), the
// entry would import it FROM the chart chunk and drag charts onto first
// paint — scripts/check-bundle.mjs fails the build on exactly that.
const CHART_PACKAGES = new Set([
  "recharts",
  "@reduxjs/toolkit",
  "@standard-schema/spec",
  "@standard-schema/utils",
  "clsx",
  "d3-array",
  "d3-color",
  "d3-ease",
  "d3-format",
  "d3-interpolate",
  "d3-path",
  "d3-scale",
  "d3-shape",
  "d3-time",
  "d3-time-format",
  "d3-timer",
  "decimal.js-light",
  "es-toolkit",
  "eventemitter3",
  "immer",
  "internmap",
  "react-redux",
  "redux",
  "redux-thunk",
  "reselect",
  "tiny-invariant",
  "use-sync-external-store",
  "victory-vendor",
]);

function packageName(id: string): string | undefined {
  // Last node_modules segment wins, so a nested copy (node_modules/recharts/
  // node_modules/react-is) is classified as ITS OWN package, not its host's.
  const parts = id.split("node_modules/");
  const tail = parts[parts.length - 1];
  if (parts.length < 2 || !tail) return undefined;
  const m = tail.match(/^((?:@[^/]+\/)?[^/]+)/);
  return m?.[1];
}

// Dev server proxies /api to the FastAPI backend so there are no CORS surprises
// during local development. In production the SPA is served statically and talks
// to the API via VITE_API_BASE_URL.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "recharts",
              priority: 10,
              test: (id: string) => {
                const pkg = packageName(id);
                return pkg !== undefined && CHART_PACKAGES.has(pkg);
              },
            },
            {
              name: "vendor",
              priority: 100,
              test: (id: string) => {
                const pkg = packageName(id);
                return pkg !== undefined && !CHART_PACKAGES.has(pkg);
              },
            },
          ],
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
