#!/usr/bin/env node
/**
 * FE-004/FE-005 ratchet (audit 2026-09-05): a page that reads with `useQuery`
 * must be able to SAY it failed. 23 of 61 query pages rendered a 4xx or a
 * network error as "no data" — an empty table that looked like a true answer.
 *
 * This gate lists the pages still without any error handling. The list can
 * only SHRINK: a page that gains an `isError` branch, a `<QueryState>` or an
 * `<ErrorState>` is removed here in the same commit; a NEW page that queries
 * without one fails the build, because the way this class of defect grows is
 * one page at a time and nobody notices.
 *
 * Remove an entry when you fix the page. Never add one.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const pages = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "pages");

// The debt reached ZERO in P2 batch 8 (2026-09-09): every page that queries
// can now say it failed. The set stays, empty, so the rule keeps its shape —
// a new page that queries without an error branch fails the build, and the
// only legal edit here is a removal.
const REMAINING = new Set([]);

const HANDLES_ERRORS = /\bisError\b|<QueryState\b|<ErrorState\b/;

const offenders = [];
const fixed = [];
for (const name of readdirSync(pages).filter((n) => n.endsWith(".tsx")).sort()) {
  const src = readFileSync(join(pages, name), "utf-8");
  const queries = (src.match(/\buseQuery\b/g) ?? []).length;
  if (queries === 0) continue;
  const handles = HANDLES_ERRORS.test(src);
  if (!handles && !REMAINING.has(name)) offenders.push(name);
  if (handles && REMAINING.has(name)) fixed.push(name);
}

let failures = 0;
if (offenders.length) {
  failures++;
  console.error(
    `check-query-errors: ${offenders.length} page(s) query with no way to show a failure:\n` +
      offenders.map((n) => `  src/pages/${n}`).join("\n") +
      `\n  Render an ErrorState (or wrap the list in <QueryState>) when the query fails.` +
      `\n  A page may NOT be added to REMAINING in scripts/check-query-errors.mjs.`,
  );
}
if (fixed.length) {
  failures++;
  console.error(
    `check-query-errors: these pages now handle errors — remove them from REMAINING so the ratchet turns:\n` +
      fixed.map((n) => `  ${n}`).join("\n"),
  );
}
console.log(`check-query-errors: ${REMAINING.size} page(s) still on the FE-004 ratchet.`);
process.exit(failures ? 1 : 0);
