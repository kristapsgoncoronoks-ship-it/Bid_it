#!/usr/bin/env node
/**
 * FE-013 (audit 2026-09-05): a button that fires a mutation is disabled while
 * that mutation is pending. 31 shipped without the guard (the audit counted
 * 30): a second click on a slow network
 * sent a second POST — a second payment run, a second credit note, a second
 * invitation — and the server's idempotency keys are not on every route.
 *
 * Rule: every `<button>` / `<Button>` whose `onClick` calls `X.mutate(…)`
 * carries `disabled={…}` or `loading={…}` on the same tag (the `Button`
 * component maps `loading` to `disabled` + `aria-busy`). The guard's
 * expression is not inspected — `disabled={busy}` and
 * `disabled={X.isPending}` both count — because the point is that the author
 * thought about it; the e2e spec proves the behaviour on a live page.
 *
 * Regex-based like its siblings, brace-aware so a `>` inside an arrow
 * function in `onClick={…}` does not end the tag.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src");

function* tsxFiles(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* tsxFiles(p);
    else if (name.endsWith(".tsx")) yield p;
  }
}

function tagAt(text, at) {
  let i = at, depth = 0;
  for (; i < text.length; i++) {
    const c = text[i];
    if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (c === ">" && depth === 0) break;
  }
  return text.slice(at, i + 1);
}

let checked = 0;
let failures = 0;
for (const file of tsxFiles(src)) {
  const text = readFileSync(file, "utf-8");
  const rel = relative(root, file);
  const open = /<(?:button|Button)\b/g;
  let m;
  while ((m = open.exec(text))) {
    const tag = tagAt(text, m.index);
    if (!/onClick=\{[\s\S]*?\.mutate\(/.test(tag)) continue;
    checked++;
    if (/\bdisabled=|\bloading=/.test(tag)) continue;
    failures++;
    const line = text.slice(0, m.index).split("\n").length;
    console.error(
      `UNGUARDED MUTATION BUTTON: ${rel}:${line}\n` +
        `  This button fires a mutation but is not disabled while it is pending —\n` +
        `  a second click sends a second request. Add disabled={X.isPending} (or loading={…}).`,
    );
  }
}

if (failures) {
  console.error(`\ncheck-pending: ${failures} of ${checked} mutation buttons unguarded.`);
  process.exit(1);
}
console.log(`check-pending: ${checked} mutation buttons guarded.`);
