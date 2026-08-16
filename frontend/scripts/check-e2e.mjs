#!/usr/bin/env node
/**
 * Two structural checks over the e2e suite. Both exist because each defect
 * class actually shipped here — neither is hypothetical.
 *
 * 1. ANCHORLESS NEGATIVE ASSERTIONS (docs/audit/2026-08-16-bug-scan.md, P2-3).
 *    A test whose first observable step is a negative assertion —
 *    `toHaveCount(0)`, `not.toBeVisible()`, `not.toBeAttached()` — passes
 *    vacuously against a page that has not finished rendering: the absence is
 *    true because NOTHING is there yet. One such test shipped and stayed green
 *    with its violation deliberately seeded (the archive notice-window test).
 *    Tests that survive today only because their fixtures happen to work are
 *    one fixture-break away from the same fate, so the rule is structural:
 *    before the first negative assertion there must be an ANCHOR — a positive
 *    assertion, or an auto-waiting interaction (click/fill/…), either of which
 *    proves the page actually reached the state whose absence is being tested.
 *
 * 2. SPEC-FILE COVERAGE. `package.json`'s `test:e2e` enumerates spec files by
 *    hand, and hand-kept lists rot: five spec files existed but were not in the
 *    list, so `npm run test:e2e` reported green while never running them. Every
 *    `e2e/*.spec.ts` must be in the list or in the declared exclusions below.
 *
 * Deliberately regex-based, not an AST: the payoff is catching the pattern in
 * CI cheaply, and the patterns are formulaic in this codebase. If a legitimate
 * test cannot satisfy the rule (rare — the fix is one added line), it can be
 * excused with an inline `// e2e-anchor-exempt: <reason>` on the test line.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const e2eDir = join(root, "e2e");

// Visual-regression snapshots run under their own npm script (`test:vr`).
const COVERAGE_EXEMPT = new Set(["visual.spec.ts"]);

const NEGATIVE = /toHaveCount\(\s*0\s*\)|not\.toBeVisible\(\)|not\.toBeAttached\(\)/;
const EXPECT = /await\s+expect(?:\.poll)?\s*\(/g;
// Auto-waiting interactions: Playwright waits for the element to be actionable,
// so reaching past one proves the page rendered — a valid anchor.
const INTERACTION = /\.(?:click|dblclick|fill|type|press|check|uncheck|selectOption|setInputFiles|hover)\(/g;

let failures = 0;

function* testBlocks(src) {
  // Each `test("name", ...)` to the next `test(` (or EOF). Coarse, but test
  // declarations are never nested in this suite.
  const decl = /\ntest(?:\.\w+)?\(\s*(["'`])([\s\S]*?)\1/g;
  const starts = [];
  let m;
  while ((m = decl.exec(src))) starts.push({ name: m[2], at: m.index });
  for (let i = 0; i < starts.length; i++) {
    const end = i + 1 < starts.length ? starts[i + 1].at : src.length;
    yield { name: starts[i].name, body: src.slice(starts[i].at, end) };
  }
}

function firstObservableStepIsNegative(body) {
  if (body.includes("e2e-anchor-exempt:")) return false;
  // Find the earliest anchor (positive expect or interaction) and the earliest
  // negative expect; the test is at risk when the negative comes first.
  let firstAnchor = Infinity;
  let firstNegative = Infinity;

  for (const m of body.matchAll(EXPECT)) {
    // The assertion expression runs to the end of its statement; a line is
    // enough — these are one-liners or matcher-chained across two lines.
    const stmt = body.slice(m.index, body.indexOf("\n", body.indexOf(";", m.index)) + 1 || undefined);
    if (NEGATIVE.test(stmt)) {
      if (m.index < firstNegative) firstNegative = m.index;
    } else if (m.index < firstAnchor) {
      firstAnchor = m.index;
    }
  }
  for (const m of body.matchAll(INTERACTION)) {
    if (m.index < firstAnchor) firstAnchor = m.index;
  }
  return firstNegative < firstAnchor;
}

// --- Check 1: anchorless negatives -----------------------------------------
const specs = readdirSync(e2eDir).filter((f) => f.endsWith(".spec.ts"));
for (const file of specs) {
  const src = readFileSync(join(e2eDir, file), "utf-8");
  for (const t of testBlocks(src)) {
    if (firstObservableStepIsNegative(t.body)) {
      failures++;
      console.error(
        `ANCHORLESS NEGATIVE: ${file} › "${t.name}"\n` +
          `  Its first observable step asserts absence. Assert something PRESENT first\n` +
          `  (or interact with the page) so the absence cannot be true vacuously.`,
      );
    }
  }
}

// --- Check 2: every spec is actually run ------------------------------------
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf-8"));
const e2eScript = pkg.scripts?.["test:e2e"] ?? "";
for (const file of specs) {
  if (COVERAGE_EXEMPT.has(file)) continue;
  if (!e2eScript.includes(`e2e/${file}`)) {
    failures++;
    console.error(
      `UNLISTED SPEC: e2e/${file} exists but is not in package.json's test:e2e.\n` +
        `  It never runs under npm run test:e2e — add it to the list (or to\n` +
        `  COVERAGE_EXEMPT in scripts/check-e2e.mjs with a reason).`,
    );
  }
}

if (failures) {
  console.error(`\ncheck-e2e: ${failures} problem(s).`);
  process.exit(1);
}
console.log(`check-e2e: ${specs.length} spec files clean.`);
