# Fleet Fuel harvest protocol (WO-6 — binding on every Epic-G PR)

The transport vertical (EU cross-border VAT refunds, fuel/toll analytics,
diesel excise) is harvested as a **specification** from the retired Fleet Fuel
system. Its rules survive as requirements **R1–R76** in
`docs/plan/shared/specs/BA_fleet_fuel.md`; its bytes do not survive at all.
This document is the binding protocol for how that harvest is allowed to work,
and the operating manual for the PII quarantine that enforces it
(`scripts/pii_scan.py`). The rule ledger it feeds is
[`docs/transport/rules.md`](rules.md).

## Status — documented adaptation (2026-07-25)

**The Fleet Fuel repository was deleted on 2026-07-25**, after an owner-held
decommission archive was made (deletion owner-confirmed 2026-08-26; Part F
F.4 verification re-run the same day — no config/code references, PII scans
clean over tree and full history). Consequences, recorded here so nobody
re-derives them wrong:

- **The deny-list is pending owner input.** WO-6 Step 1 ("read Fleet Fuel and
  extract every real identifier") could not run against the live repo. The
  committed `scripts/pii_denylist.json` therefore ships with an **empty
  `entries` list**. The owner holds `identifiers_for_denylist.txt` inside the
  decommission archive and populates the deny-list later — see
  [Populating the deny-list](#populating-the-deny-list) below.
- **The structural patterns are active now** and do not depend on the
  deny-list: EU VAT ids (country prefix + 8–12 digits) and IBAN candidates
  that pass ISO 7064 MOD-97 with the correct ISO 13616 country length are
  flagged on every PR regardless.
- Both the working tree and the **entire git history** of Bid_it were scanned
  on 2026-07-25 with the structural patterns (2,119 unique blobs across all
  refs): **clean**.
- The masked placeholders the planning docs use deliberately (`«Client-EE»
  AS`, `EE1########0`, …) are **not** findings — the structural patterns
  require real digits, and hash/guillemet-masked tokens are explicitly
  ignored. `backend/tests/test_pii_scan.py` asserts this.

## The six binding rules

1. **Read Fleet Fuel for rules; never copy its code.** The stacks differ
   (Flask + SQLite + procedural vs FastAPI + async SQLAlchemy + Postgres). A
   copied line is both a licence-of-origin question and a design regression.
2. **Never copy configuration, constants, fixtures or database files.** The
   retired system carried real client identifiers as module constants and
   committed live databases; none of it may enter this repository — not in
   code, tests, fixtures, docs, comments, commit messages or scratch files.
   Git history is permanent; a reverted commit does not undo the exposure.
3. **Every harvested rule arrives as three artifacts**: (a) a typed model or
   pure function; (b) a test named `test_r{n}_{slug}` whose docstring cites
   the R-number **and** the legal source (Directive article, CJEU case,
   Regulation); (c) a row in [`docs/transport/rules.md`](rules.md) mapping
   R-number → module → test → legal source.
4. **A G-task PR that does not include its R-test does not merge.**
5. **All fixtures come from `backend/tests/factories/transport.py`** —
   synthetic, generated, never derived from client data. Realistic in shape
   (correct VAT prefix/length, MOD-97-valid IBANs over a test-only bank
   code), fictional in content.
6. **Any doubt about whether a value is real: treat it as real.**

## The PII quarantine gate

`scripts/pii_scan.py` — dependency-free (stdlib only). Exit codes: `0` clean,
`1` findings, `2` configuration error (**fail closed**).

| Mode | What it scans | Where it runs |
|---|---|---|
| `--tree` | the working tree (skipping `.git`, `node_modules`, `.venv`, `dist`, binaries) | the required `pii-scan` job in `.github/workflows/ci.yml`, on every PR |
| `--history` | every unique blob reachable from any ref, cached by blob SHA (`--no-cache` forces a full pass) | nightly + on-demand, `.github/workflows/pii-history.yml` |

Three detection layers:

1. **Deny-listed literals** — salted SHA-256 hashes of normalised tokens
   (lower-cased, whitespace-stripped). A hit reports the entry's **label**,
   never the value, so the gate cannot republish the PII it protects.
2. **Structural EU VAT ids** — `\b(EE|LT|LV|…)\d{8,12}\b`.
3. **Structural IBANs** — candidates passing MOD-97 **and** the ISO 13616
   country length (a random string will not, keeping false positives rare).

**Fail-closed rules** (deliberate, tested):

- deny-list or allow-list file missing/malformed → exit 2, red build;
- deny-list has hashed entries but the salt env var is unset → exit 2 —
  unverifiable must never mean passed;
- an **empty** deny-list needs no salt (there is nothing to hash against);
  the scan proceeds on structural patterns and prints a loud notice. This is
  the current, documented state.

### The salt

The hashes are salted so the deny-list cannot be dictionary-attacked back to
the identifiers. The salt:

- lives in the environment variable **`PII_SCAN_SALT`**;
- in CI it is the repository secret `PII_SCAN_SALT` (see `docs/DEPLOYMENT.md`
  §2 note) — **never committed, never printed**;
- is generated once by the owner
  (`python -c "import secrets; print(secrets.token_hex(32))"`), stored in the
  team password manager, and set as the GitHub Actions secret;
- changing the salt requires re-running `scripts/pii_denylist_build.py` over
  the identifier file — the committed hashes are salt-specific.

### Populating the deny-list

Owner-only procedure (the only person with the decommission archive):

1. Extract `identifiers_for_denylist.txt` from the archive to a path
   **outside** the repository (the filename is `.gitignore`d defensively, but
   do not rely on that). Format: one identifier per line,
   `kind<TAB>label<TAB>value` — labels must be non-identifying
   (`customer-1-name`, `supplier-3-iban`, …).
2. Run: `PII_SCAN_SALT=<the secret> python scripts/pii_denylist_build.py
   /secure/path/identifiers_for_denylist.txt`
3. Review `scripts/pii_denylist.json` — it must contain **only**
   `{label, len, sha256, kind}` entries, no plaintext
   (`backend/tests/test_pii_scan.py::test_the_committed_denylist_contains_no_plaintext_identifier`
   enforces this).
4. Commit the deny-list; delete the local identifier file; run the nightly
   history workflow once by hand (`workflow_dispatch`) for a full sweep.
5. Ensure the `PII_SCAN_SALT` secret is set in GitHub Actions **and**
   exported wherever the backend tests run, so
   `test_synthetic_vat_ids_are_not_on_the_denylist` can do real hash
   comparisons (without the salt that test requires the list to be empty).

### False positives

Resolved **only** by adding the specific value to
`scripts/pii_allowlist.json` with a `justification` and a named
`verified_by`. Removing the scan, skipping the job or weakening a pattern to
unblock a build is prohibited — this is a legal control (risk L-3), not a
lint. The current allow-list holds only published documentation examples
(VIES example VAT id, ISO 13616 registry example IBANs) and this repo's own
obviously-synthetic seed/design fixtures.

## The separate legal item

The deletion of the Fleet Fuel repository does **not** end the exposure: the
owner-held decommission archive still contains the full git history with real
client identifiers and three committed live databases. Its retention,
redaction or destruction is a decision for counsel — raised with an owner and
a date in `docs/DECISIONS-NEEDED.md` (§ "Fleet Fuel decommission archive").
Nothing in this repository acts on that decision.
