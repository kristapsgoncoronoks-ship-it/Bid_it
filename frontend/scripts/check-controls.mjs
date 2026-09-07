#!/usr/bin/env node
/**
 * FE-011 (audit 2026-09-05) — the INVERSE of check-labels.mjs.
 *
 * check-labels proves every <label> labels something. This proves every
 * CONTROL is labelled by something: an <input>, <select> or <textarea> must
 * be reachable by an assistive technology (and by `getByLabel` in a test)
 * through one of
 *
 *   (a) `aria-label=` / `aria-labelledby=` on the control,
 *   (b) an `id` that a `<label htmlFor>` in the same file names (literal or the
 *       same `{expression}`),
 *   (c) a wrapping <label> in the same JSX, or
 *   (d) a wrapping component that takes a `label=` prop and renders the label
 *       itself (`<FormField label=…>`, the pages' local `<L label=…>`), or the
 *       FormField render-prop spread `{...f}` that carries the id.
 *
 * A placeholder is NOT a label (it vanishes on input and is not announced as
 * one) — that is the defect class this gate exists for.
 *
 * The audit counted ~114 unlabelled controls. This gate carries the debt as a
 * per-file count that can only SHRINK: fix a page and lower (or delete) its
 * entry in the same commit; a NEW unlabelled control anywhere fails the
 * build. Regex-based like its siblings, for the same reason: the JSX is
 * formulaic and the payoff is catching the pattern cheaply in CI.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src");

// The debt. P2 batch 6 (audit 2026-09-05) cleared all 91 the gate found, so
// the map is EMPTY and every control must be labelled; it exists so a page
// with a genuine, argued exception can carry a count — lower it when you fix
// the page, delete it at zero, never raise one.
const REMAINING = new Map(Object.entries({}));

function* tsxFiles(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* tsxFiles(p);
    else if (name.endsWith(".tsx")) yield p;
  }
}

/** Blank out comments so a control in a JSDoc example is not counted. */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/^\s*\/\/.*$/gm, (m) => m.replace(/[^\n]/g, " "));
}

const CONTROL = /<(input|select|textarea)\b/g;

/** The attribute text of the tag opening at `at`, brace-aware: a `>` inside an
 * arrow function in `onChange={(e) => …}` is not the end of the tag. */
function tagAttrs(text, at) {
  let i = at, depth = 0;
  for (; i < text.length; i++) {
    const c = text[i];
    if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (c === ">" && depth === 0) break;
  }
  return text.slice(at, i + 1);
}

function labelledIds(text) {
  const ids = new Set();
  for (const m of text.matchAll(/htmlFor=(?:["']([^"']+)["']|\{([^}]+)\})/g)) {
    ids.add((m[1] ?? m[2]).trim());
  }
  return ids;
}

function controlId(attrs) {
  const m = /\bid=(?:["']([^"']+)["']|\{([^}]+)\})/.exec(attrs);
  return m ? (m[1] ?? m[2]).trim() : null;
}

/** Is there an unclosed <label> or a `<Component label=…>` wrapper before `at`? */
function wrapped(text, at) {
  const before = text.slice(Math.max(0, at - 1500), at);
  if (before.lastIndexOf("<label") > before.lastIndexOf("</label>")) return true;
  const opens = [...before.matchAll(/<([A-Z][A-Za-z0-9]*)\b[^>]*\blabel=/g)];
  for (const m of opens.reverse()) {
    const name = m[1];
    const after = before.slice(m.index);
    const selfClosed = /^<[^>]*\/>/.test(after);
    if (selfClosed) continue;
    if (!after.includes(`</${name}>`)) return true;
  }
  return false;
}

const perFile = new Map();
let checked = 0;
for (const file of tsxFiles(src)) {
  const raw = readFileSync(file, "utf-8");
  const text = stripComments(raw);
  const rel = relative(root, file);
  const ids = labelledIds(text);
  let m;
  CONTROL.lastIndex = 0;
  while ((m = CONTROL.exec(text))) {
    const attrs = tagAttrs(text, m.index);
    if (/type=["']hidden["']/.test(attrs)) continue;
    checked++;
    if (/aria-label=|aria-labelledby=/.test(attrs)) continue;
    if (/\{\.\.\.f\}/.test(attrs)) continue; // FormField render-prop carries the id
    const id = controlId(attrs);
    if (id && ids.has(id)) continue;
    if (wrapped(text, m.index)) continue;
    const line = text.slice(0, m.index).split("\n").length;
    if (!perFile.has(rel)) perFile.set(rel, []);
    perFile.get(rel).push(line);
  }
}

let failures = 0;
for (const [rel, lines] of [...perFile.entries()].sort()) {
  const allowed = REMAINING.get(rel) ?? 0;
  if (lines.length > allowed) {
    failures++;
    console.error(
      `UNLABELLED CONTROLS: ${rel} has ${lines.length} (allowed ${allowed}) at lines ${lines.join(", ")}\n` +
        `  Give each an aria-label, a <label htmlFor> + id, or wrap it in a <label>.\n` +
        `  A placeholder is not a label.`,
    );
  }
}
for (const [rel, allowed] of REMAINING) {
  const actual = perFile.get(rel)?.length ?? 0;
  if (actual < allowed) {
    failures++;
    console.error(
      `RATCHET: ${rel} now has ${actual} unlabelled control(s), the gate allows ${allowed} — lower the entry in scripts/check-controls.mjs (delete it at zero).`,
    );
  }
}
const debt = [...perFile.values()].reduce((n, l) => n + l.length, 0);
if (failures) {
  console.error(`\ncheck-controls: ${failures} problem(s); ${debt} unlabelled of ${checked} controls.`);
  process.exit(1);
}
console.log(`check-controls: ${checked} controls checked, ${debt} carried as debt.`);
