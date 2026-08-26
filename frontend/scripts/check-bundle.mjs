#!/usr/bin/env node
/**
 * First-load budget gate (WO-O). Two promises, both broken once:
 *
 * 1. THE CHART STACK STAYS OFF FIRST PAINT. Under Vite 8/rolldown the entry
 *    graph silently grew a static edge into the recharts chunk (rolldown
 *    hosted shared CommonJS wrappers — React itself — inside it), so
 *    index.html modulepreloaded ~415 kB of charts nobody had rendered and
 *    the critical path roughly doubled (~329 kB → ~773 kB, the 2026-08-12
 *    known-broken entry). Charts are lazy-page-only; any asset named
 *    recharts* in the entry's preload set is a regression, whatever its size.
 *
 * 2. THE TOTAL HAS A CEILING. Budgets are set ~10% above the measured
 *    first-load at WO-O (415 kB raw / 122 kB gzip), so ordinary growth fits
 *    but another doubling cannot land silently. If a deliberate feature
 *    legitimately needs more, raise the budget IN THE SAME COMMIT and say
 *    why — the point is that the number changes in review, not in the dark.
 *
 * Reads dist/ — run `npm run build` first (CI does).
 */

import { readFileSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");

const RAW_BUDGET_KB = 460;
const GZIP_BUDGET_KB = 135;

let html;
try {
  html = readFileSync(join(dist, "index.html"), "utf-8");
} catch {
  console.error("check-bundle: dist/index.html not found — run `npm run build` first.");
  process.exit(1);
}

// The first-load set: the entry <script src>, every modulepreload, every
// stylesheet. That is exactly what the browser fetches before first paint.
const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);

let failures = 0;
let raw = 0;
let gz = 0;
for (const a of assets) {
  const p = join(dist, a);
  const bytes = readFileSync(p);
  raw += statSync(p).size;
  gz += gzipSync(bytes, { level: 9 }).length;
  if (/\/recharts-/.test(a)) {
    failures++;
    console.error(
      `CHARTS ON FIRST PAINT: ${a} is in index.html's preload set.\n` +
        `  The chart stack must be reachable only from lazy pages. Something\n` +
        `  eager now imports recharts (or one of CHART_PACKAGES in\n` +
        `  vite.config.ts) — follow the import chain and cut the eager edge.`,
    );
  }
}

const rawKb = raw / 1024;
const gzKb = gz / 1024;
console.log(
  `check-bundle: first load = ${rawKb.toFixed(1)} kB raw / ${gzKb.toFixed(1)} kB gzip ` +
    `across ${assets.length} assets (budget ${RAW_BUDGET_KB} / ${GZIP_BUDGET_KB})`,
);
if (rawKb > RAW_BUDGET_KB || gzKb > GZIP_BUDGET_KB) {
  failures++;
  console.error(
    `FIRST-LOAD BUDGET EXCEEDED. If this growth is deliberate, raise the\n` +
      `budget in scripts/check-bundle.mjs in the same commit and say why.`,
  );
}

process.exit(failures ? 1 : 0);
