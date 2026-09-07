#!/usr/bin/env node
/**
 * FE-018 (audit 2026-09-05): ONE locale, in ONE place. Money was formatted
 * with en-IE, dates with en-GB, and twelve call sites used the browser's own
 * locale (`toLocaleString()` with no argument), so the same figure could read
 * three ways on one screen. `src/lib/format.ts` now owns `LOCALE` and every
 * formatter; this gate keeps it that way.
 *
 * Rule, everywhere under src/ except src/lib/format.ts:
 *   - no `toLocaleString(` / `toLocaleDateString(` / `toLocaleTimeString(`
 *     — call `formatNumber` / `formatDate` / `formatDateTime` / `formatTime`,
 *   - no `Intl.NumberFormat(` / `Intl.DateTimeFormat(` — add a formatter,
 *   - no locale literal (`"en-IE"`, `"en-GB"`, `"en-US"`, …) — read `LOCALE`.
 *
 * Whether the product should follow the user's locale is an owner decision
 * (docs/DECISIONS-NEEDED.md §21); this gate makes that decision a one-line
 * change when it is taken instead of a hunt through 69 pages.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src");
const OWNER = "src/lib/format.ts";

function* tsFiles(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* tsFiles(p);
    else if (name.endsWith(".ts") || name.endsWith(".tsx")) yield p;
  }
}

const RULES = [
  [/\.toLocale(?:Date|Time)?String\(/g, "toLocale*() — use the formatters in src/lib/format.ts"],
  [/\bIntl\.(?:NumberFormat|DateTimeFormat)\(/g, "Intl.* — add a formatter to src/lib/format.ts"],
  [/["'`]en-[A-Z]{2}["'`]/g, "a locale literal — read LOCALE from src/lib/format.ts"],
];

let failures = 0;
let files = 0;
for (const file of tsFiles(src)) {
  const rel = relative(root, file);
  if (rel === OWNER) continue;
  files++;
  const text = readFileSync(file, "utf-8");
  for (const [re, why] of RULES) {
    for (const m of text.matchAll(re)) {
      failures++;
      const line = text.slice(0, m.index).split("\n").length;
      console.error(`LOCALE OUTSIDE format.ts: ${rel}:${line} — ${m[0]} (${why})`);
    }
  }
}

if (failures) {
  console.error(`\ncheck-locale: ${failures} problem(s) in ${files} files.`);
  process.exit(1);
}
console.log(`check-locale: ${files} files read one locale from src/lib/format.ts.`);
