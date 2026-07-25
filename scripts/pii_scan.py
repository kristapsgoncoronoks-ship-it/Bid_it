#!/usr/bin/env python3
"""Fleet Fuel PII quarantine scanner (WO-6, risk L-3).

Scans the Bid_it working tree (``--tree``) or the entire git object history
(``--history``) for identifiers that must never enter this repository:

1. **Deny-listed literals** — real identifiers harvested from the retired
   Fleet Fuel system. The deny-list (``scripts/pii_denylist.json``) stores
   **salted SHA-256 hashes** of each normalised token (lower-cased,
   whitespace-stripped), never the plaintext, so the gate can fail loudly
   without republishing the PII into the very repository it protects. The
   salt comes from the environment (``PII_SCAN_SALT``) / a CI secret and is
   never committed.
2. **Structural EU VAT ids** — country prefix + 8-12 digits.
3. **Structural IBANs** — candidates that pass the ISO 7064 MOD-97 check
   *and* the ISO 13616 per-country length (a random string will not, so the
   false-positive rate is low).

Known-synthetic values used by existing fixtures live in
``scripts/pii_allowlist.json`` (plaintext is fine there — they are fictional
by construction, each with a named verifier).

Masked placeholder forms used deliberately by the planning docs — e.g.
``«Client-EE» AS`` or ``EE1########0`` — are NOT findings: the structural
patterns require digits, and any token containing ``#`` or guillemets is
treated as masked.

Fail-closed policy (deliberate):
- deny-list file missing/unreadable/malformed  -> exit 2 (config error)
- allow-list file missing/unreadable/malformed -> exit 2 (config error)
- deny-list has entries but the salt env var is unset -> exit 2 — hashed
  entries cannot be verified without the salt, and "unverifiable" must not
  mean "passed".
- deny-list empty (owner input pending — see
  docs/transport/harvest-protocol.md): the salt is not required because
  there is nothing to hash against; the structural patterns still run and a
  loud notice is printed. This is the documented WO-6 adaptation: the Fleet
  Fuel repository was deleted on 2026-07-25 and the identifier list is held
  by the owner in the decommission archive.

Exit codes: 0 = clean, 1 = findings, 2 = configuration error (fail closed).

Findings NEVER print the matched value — only the deny-list label, or a
masked form (first two characters + length) for structural hits.

Dependency-free by design (stdlib only) so the CI job needs no ``pip
install``. The MOD-97/length logic intentionally mirrors
``backend/app/core/bank_id.py``; ``backend/tests/test_pii_scan.py`` asserts
the two implementations agree so they cannot drift silently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DENYLIST = REPO_ROOT / "scripts" / "pii_denylist.json"
DEFAULT_ALLOWLIST = REPO_ROOT / "scripts" / "pii_allowlist.json"
DEFAULT_CACHE = REPO_ROOT / ".pii_scan_cache.json"
SALT_ENV_DEFAULT = "PII_SCAN_SALT"

# Structural EU VAT id: country prefix + 8..12 digits (WO-6 spec pattern).
VAT_RE = re.compile(
    r"\b(EE|LT|LV|PL|CZ|DE|BE|FR|IT|ES|SE|DK|FI|NL|AT|SI|HU|HR|SK|RO|BG|PT|IE|LU|GR|EL)"
    r"\d{8,12}\b"
)

# IBAN candidate: 2 letters + 2 check digits + 11..30 alphanumerics. Confirmed
# only when MOD-97 == 1 AND the length matches the country's ISO 13616 entry.
IBAN_CANDIDATE_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")

# ISO 13616 length table — mirrors backend/app/core/bank_id.py (kept in sync
# by tests/test_pii_scan.py::test_scanner_iban_check_agrees_with_bank_id).
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22,
    "GI": 23, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18,
    "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24,
    "SM": 27, "VA": 22,
}

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "playwright-report", "test-results", "blob-report", ".idea", ".vscode",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip",
    ".gz", ".tar", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".pyc",
    ".db", ".sqlite3", ".so", ".dylib", ".dll", ".exe", ".map",
}
MAX_FILE_BYTES = 5 * 1024 * 1024  # anything larger is not hand-written source

# Characters that mark a token as a deliberate masked placeholder (the
# planning docs use «Client-EE» / EE1########0 forms on purpose).
_MASK_CHARS = ("#", "«", "»")


def _mod97(iban: str) -> int:
    """ISO 7064 MOD-97-10 over the rearranged IBAN (mirrors core/bank_id)."""
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97


def is_valid_iban(token: str) -> bool:
    """Structural IBAN check: known country, exact ISO length, MOD-97 == 1."""
    expected = IBAN_LENGTHS.get(token[:2])
    if expected is None or len(token) != expected:
        return False
    return _mod97(token) == 1


def normalize(token: str) -> str:
    """Canonical hashing form: lower-cased, all whitespace stripped."""
    return re.sub(r"\s+", "", token.lower())


def hash_token(salt: str, token: str) -> str:
    return hashlib.sha256((salt + normalize(token)).encode("utf-8")).hexdigest()


def _fail_config(msg: str) -> None:
    print(f"pii-scan: CONFIG ERROR (failing closed): {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_denylist(path: Path) -> tuple[list[dict], str]:
    """Load the deny-list; fail closed on anything unexpected.

    Returns (entries, salt). An empty entries list needs no salt (nothing to
    hash against); a populated list without the salt is a hard config error.
    """
    if not path.is_file():
        _fail_config(f"deny-list file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail_config(f"deny-list unreadable/malformed: {exc}")
    salt_env = data.get("salt_env", SALT_ENV_DEFAULT)
    entries = data.get("entries")
    if not isinstance(entries, list):
        _fail_config("deny-list has no 'entries' list")
    for e in entries:
        if not (isinstance(e, dict) and {"label", "len", "sha256", "kind"} <= e.keys()):
            _fail_config(f"malformed deny-list entry: {e!r:.80}")
    salt = os.environ.get(salt_env, "")
    if entries and not salt:
        _fail_config(
            f"deny-list has {len(entries)} hashed entries but ${salt_env} is unset — "
            "hashed literals cannot be verified without the salt"
        )
    if not entries:
        print(
            "pii-scan: NOTICE: deny-list is EMPTY — structural patterns only. "
            "The Fleet Fuel identifier list is pending owner input from the "
            "decommission archive (docs/transport/harvest-protocol.md)."
        )
    return entries, salt


def load_allowlist(path: Path) -> set[str]:
    """Load known-synthetic values (normalised). Fail closed if absent."""
    if not path.is_file():
        _fail_config(f"allow-list file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail_config(f"allow-list unreadable/malformed: {exc}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        _fail_config("allow-list has no 'entries' list")
    allowed: set[str] = set()
    for e in entries:
        if not (isinstance(e, dict) and e.get("value") and e.get("verified_by")):
            _fail_config(f"allow-list entry missing value/verified_by: {e!r:.80}")
        allowed.add(normalize(str(e["value"])))
    return allowed


def mask(token: str) -> str:
    """CI-log-safe representation — never the full value."""
    return f"{token[:2]}… (len {len(token)})"


def _is_masked_context(line: str, start: int, end: int) -> bool:
    """True when the match sits inside a deliberate placeholder token."""
    lo, hi = max(0, start - 2), min(len(line), end + 2)
    return any(c in line[lo:hi] for c in _MASK_CHARS)


def scan_text(
    text: str,
    entries: list[dict],
    salt: str,
    allowed: set[str],
    where: str,
) -> list[str]:
    """Scan one text blob; return finding strings (labels/masks, no values)."""
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in VAT_RE.finditer(line):
            token = m.group(0)
            if _is_masked_context(line, m.start(), m.end()):
                continue
            if normalize(token) in allowed:
                continue
            findings.append(
                f"{where}:{lineno}: structural-vat-id {mask(token)}"
            )
        for m in IBAN_CANDIDATE_RE.finditer(line):
            token = m.group(0)
            if not is_valid_iban(token):
                continue
            if _is_masked_context(line, m.start(), m.end()):
                continue
            if normalize(token) in allowed:
                continue
            findings.append(f"{where}:{lineno}: structural-iban {mask(token)}")
        if entries:
            nline = normalize(line)
            for e in entries:
                length = int(e["len"])
                if length <= 0 or len(nline) < length:
                    continue
                target = e["sha256"]
                for i in range(len(nline) - length + 1):
                    window = nline[i : i + length]
                    digest = hashlib.sha256(
                        (salt + window).encode("utf-8")
                    ).hexdigest()
                    if digest == target:
                        findings.append(
                            f"{where}:{lineno}: deny-listed literal "
                            f"[{e['label']}] kind={e['kind']}"
                        )
                        break
    return findings


def _iter_tree_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower() in SKIP_SUFFIXES:
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield p


def scan_tree(
    root: Path, entries: list[dict], salt: str, allowed: set[str]
) -> list[str]:
    findings: list[str] = []
    for path in _iter_tree_files(root):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue  # binary
        text = raw.decode("utf-8", errors="replace")
        rel = path.relative_to(root)
        findings.extend(scan_text(text, entries, salt, allowed, str(rel)))
    return findings


def _load_cache(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def scan_history(
    root: Path,
    entries: list[dict],
    salt: str,
    allowed: set[str],
    cache_path: Path | None,
) -> list[str]:
    """Scan every unique blob reachable from any ref, once.

    Blob-level dedup plus a clean-blob SHA cache keeps repeated nightly runs
    fast: a blob that was clean yesterday is content-addressed, so it is
    still clean today and is skipped.
    """

    def git(*args: str, data: bytes | None = None) -> bytes:
        res = subprocess.run(
            ["git", "-C", str(root), *args],
            input=data,
            capture_output=True,
            check=True,
        )
        return res.stdout

    out = git("rev-list", "--all", "--objects")
    path_hint: dict[str, str] = {}
    order: list[str] = []
    for line in out.decode("utf-8", errors="replace").splitlines():
        parts = line.split(" ", 1)
        sha = parts[0]
        if sha not in path_hint:
            order.append(sha)
            path_hint[sha] = parts[1] if len(parts) > 1 else ""

    # Keep only blobs (commits/trees/tags carry no file content).
    check = git(
        "cat-file", "--batch-check", data="\n".join(order).encode() + b"\n"
    )
    blobs: list[str] = []
    for line in check.decode().splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] == "blob" and int(fields[2]) <= MAX_FILE_BYTES:
            blobs.append(fields[0])

    clean = _load_cache(cache_path) if cache_path else set()
    todo = [s for s in blobs if s not in clean]
    findings: list[str] = []
    newly_clean: list[str] = []
    for sha in todo:
        raw = git("cat-file", "blob", sha)
        if b"\x00" in raw[:8192]:
            newly_clean.append(sha)
            continue
        text = raw.decode("utf-8", errors="replace")
        where = f"blob {sha[:12]} ({path_hint.get(sha) or 'no path hint'})"
        hits = scan_text(text, entries, salt, allowed, where)
        if hits:
            findings.extend(hits)
        else:
            newly_clean.append(sha)
    if cache_path and newly_clean:
        clean.update(newly_clean)
        try:
            cache_path.write_text(json.dumps(sorted(clean)), encoding="utf-8")
        except OSError:
            pass  # cache is an optimisation only; never a correctness input
    print(
        f"pii-scan: history scanned {len(todo)} of {len(blobs)} unique blobs "
        f"({len(blobs) - len(todo)} cached clean)"
    )
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tree", action="store_true", help="scan the working tree")
    mode.add_argument(
        "--history", action="store_true", help="scan every blob on every ref"
    )
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--denylist", type=Path, default=DEFAULT_DENYLIST)
    ap.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore and do not write the clean-blob cache (full history pass)",
    )
    args = ap.parse_args(argv)

    entries, salt = load_denylist(args.denylist)
    allowed = load_allowlist(args.allowlist)

    if args.tree:
        findings = scan_tree(args.root, entries, salt, allowed)
    else:
        cache = None if args.no_cache else args.cache
        findings = scan_history(args.root, entries, salt, allowed, cache)

    if findings:
        print(f"pii-scan: {len(findings)} finding(s):", file=sys.stderr)
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print(
            "pii-scan: FAILED — a value matching the Fleet Fuel PII quarantine "
            "was found. If it is verifiably synthetic, add it to "
            "scripts/pii_allowlist.json with a justification and a named "
            "verifier (docs/transport/harvest-protocol.md). NEVER disable "
            "this job.",
            file=sys.stderr,
        )
        return 1
    print("pii-scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
