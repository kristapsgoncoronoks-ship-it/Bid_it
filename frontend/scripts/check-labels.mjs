#!/usr/bin/env node
/**
 * Structural a11y gate (WO-N): every `<label>` in the SPA must actually label
 * something. The defect class shipped — the sign-in fields rendered visual
 * labels that no screen reader (and no `getByLabel` test) could associate,
 * and the 2026-08-12 release runbook carried them as known-broken. A label is
 * associated when it either
 *
 *   (a) carries `htmlFor=...` on its opening tag, or
 *   (b) WRAPS its control — an <input>/<select>/<textarea> appears before the
 *       closing </label> (implicit association, e.g. the all-day checkbox).
 *
 * Text that merely captions a non-control (a button group, a <code> display)
 * must not be a <label> at all — use `<div className="label">`, which keeps
 * the styling and drops the false promise. Rare legitimate exceptions carry
 * `a11y-label-exempt: <reason>` on the opening-tag line.
 *
 * Regex-based like check-e2e.mjs, and for the same reason: the payoff is
 * catching the pattern cheaply in CI, and the codebase's JSX is formulaic.
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

const CONTROL = /<(?:input|select|textarea)\b/;
let failures = 0;
let checked = 0;

for (const file of tsxFiles(src)) {
  const text = readFileSync(file, "utf-8");
  const rel = relative(root, file);
  const open = /<label\b/g;
  let m;
  while ((m = open.exec(text))) {
    checked++;
    const tagEnd = text.indexOf(">", m.index);
    const close = text.indexOf("</label>", m.index);
    const tag = text.slice(m.index, tagEnd === -1 ? m.index + 200 : tagEnd + 1);
    const body = text.slice(m.index, close === -1 ? m.index + 2000 : close);
    const line = text.slice(0, m.index).split("\n").length;
    if (tag.includes("htmlFor=")) continue;
    if (CONTROL.test(body)) continue; // wrapping association
    if (text.split("\n")[line - 1]?.includes("a11y-label-exempt:")) continue;
    failures++;
    console.error(
      `UNASSOCIATED LABEL: ${rel}:${line}\n` +
        `  This <label> has no htmlFor and wraps no control, so it labels\n` +
        `  nothing. Associate it (htmlFor + id, or wrap the control), or if it\n` +
        `  captions a non-control, make it <div className="label">.`,
    );
  }
}

if (failures) {
  console.error(`\ncheck-labels: ${failures} problem(s) in ${checked} labels.`);
  process.exit(1);
}
console.log(`check-labels: ${checked} labels associated.`);
