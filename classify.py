"""
DATA CLASSIFICATION / DLP (Box-Shield-style) — an APP-OWNED, ADVISORY overlay that scans
a document's TEXT for sensitive data, assigns a sensitivity LABEL, surfaces it, and (OPT-
IN) lets an admin GATE what may be sent to the EXTERNAL AI by sensitivity.

WHAT IT IS / IS NOT. It detects sensitive-data TYPES (IBAN, bank account, BIC/SWIFT,
credit-card-like, email, phone, VAT id, personal-name-ish) and maps the findings to an
ordered label `public < internal < confidential < restricted`. It NEVER stores or logs
the raw matched VALUES — only `{type, count}` + the label. It is best-effort and NEVER
raises into a caller.

PRIVACY (hard invariants):
  * scan_text() / classify_document() store ONLY the finding TYPE + COUNT and the label —
    NEVER the matched value (no IBAN string, no email, no card number is ever persisted or
    logged). The detectors run on text the app already holds; nothing leaves the server.
  * The AI gate is OPT-IN. The policy setting `ai_external_max_sensitivity` defaults to
    `restricted` = PERMISSIVE (allow everything) — so the DEFAULT does NOT block and the
    external-AI paths behave byte-identically. The admin must TIGHTEN it to gate.
  * The gate FAILS OPEN on a classification ERROR (never block on a scan failure) but FAILS
    CLOSED when a policy IS set and a document's label clearly EXCEEDS it.

DATA-PRODUCT BOUNDARY. Like metadata.py / retention.py this is APP DATA, not a product, so
it lives in its OWN app-owned DB (classify.db, gitignored), keyed by the stable `doc:<id>`
REFERENCE (`subject_ref`). It NEVER opens a product DB and NEVER adds a column to one.

OWN DB. Owns its SQLite file via connect() + db_migrate, audit-installed, db_tuning-tuned.
Rows carry a tenant_id (the tenancy seam, inert today): stamped with tenancy.write_tenant()
on INSERT, never filtered yet.
"""
import os
import re
import json
import sqlite3
import datetime

import applog
import audit
import db_tuning
import db_migrate
import tenancy

log = applog.get("classify")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned classification DB (gitignored). A module-level attr so tests can repoint it the
# same way they repoint metadata.DB / retention.DB.
DB = f"{WORKDIR}/classify.db"

# The ordered sensitivity scale (low -> high). The label of a document is the MAX over all
# the labels its findings imply. `restricted` is the most sensitive (and the permissive
# default for the AI gate = allow everything).
LABELS = ("public", "internal", "confidential", "restricted")
_RANK = {label: i for i, label in enumerate(LABELS)}

# The admin OPT-IN gate policy: the MAXIMUM sensitivity that may be sent to the external AI.
# Default = `restricted` = PERMISSIVE (allow every label) so the DEFAULT never blocks and
# the external-AI paths stay byte-identical.
POLICY_SETTING = "ai_external_max_sensitivity"
DEFAULT_MAX_SENSITIVITY = "restricted"

# Each detector maps its finding TYPE -> the sensitivity label it implies. Bank/card/IBAN
# material is `restricted`; emails/phones are `confidential`; the rest are `internal`.
_TYPE_LABEL = {
    "iban": "restricted",
    "bank_account": "restricted",
    "credit_card": "restricted",
    "bic_swift": "confidential",
    "vat_id": "confidential",
    "email": "confidential",
    "phone": "confidential",
    "personal_name": "internal",
}


def rank(label):
    """The ordinal of a label on the scale (unknown -> public=0). Never raises."""
    return _RANK.get((label or "").strip().lower(), 0)


def label_at_least(a, b):
    """True iff label `a` is at least as sensitive as `b`."""
    return rank(a) >= rank(b)


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "classify", _MIGRATIONS)
        audit.install_audit(con, ["classifications"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS classifications (
    id          INTEGER PRIMARY KEY,
    subject_ref TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT 'public',
    findings    TEXT,                            -- JSON list of {type, count} — NEVER values
    scanned_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
-- One classification per (subject) — re-scanning UPDATES it.
CREATE UNIQUE INDEX IF NOT EXISTS ux_classifications_ts
    ON classifications(tenant_id, subject_ref);
CREATE INDEX IF NOT EXISTS ix_classifications_subject ON classifications(subject_ref);
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = []

_SCHEMA_READY = set()   # DB files whose schema is set up this process


# ============================================================ detectors
# Each detector returns the COUNT of distinct sensitive items it found — never the values.
# Detection is conservative (a basic structural/sanity check) so a label is defensible; it
# is advisory and NEVER gates a legal check.

# An IBAN is 2 country letters + 2 check digits + up to 30 alphanumerics. We require word
# boundaries and then run a mod-97 sanity check so a random alphanumeric run isn't an IBAN.
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{11,30})\b")
# BIC/SWIFT: 4 bank + 2 country + 2 location (+ optional 3 branch).
_BIC_RE = re.compile(r"\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")
# Email.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone: an international/long number with the usual separators (kept loose, then length-
# checked) — requires 8..15 digits so a short invoice line number isn't a phone.
_PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d\s().-]{6,16}\d(?!\d)")
# A standalone "bank account number" — a long run of 8..17 digits (with optional spaces)
# NOT already part of an IBAN; conservative so invoice/qty figures don't trip it.
_ACCOUNT_RE = re.compile(r"(?<![\w])(\d[\d ]{6,21}\d)(?![\w])")
# A credit-card-like number: 13..19 digits in groups; validated with Luhn.
_CARD_RE = re.compile(r"(?<![\d])(\d[\d ]{11,21}\d)(?![\d])")
# EU VAT id: 2 country letters + 8..12 alphanumerics (the broad EU shape).
_VAT_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{8,12})\b")
# Personal-name-ish (best-effort): two consecutive Capitalised words. Deliberately last and
# only `internal` because it is weak; an UPPER-CASE supplier name won't match (needs Title
# Case), and obvious org tokens are excluded below.
_NAME_RE = re.compile(r"\b([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})\b")
# Tokens that look like a person-name pair but are organisations / labels — excluded so the
# weak name detector doesn't over-fire on headings.
_NAME_STOP = {"Invoice Number", "Total Amount", "Value Added", "Bank Account",
              "Account Number", "Due Date", "Issue Date", "Road Toll", "Fuel Card",
              "Credit Card"}


def _mod97_ok(iban):
    """ISO 7064 mod-97 check for an IBAN string. Never raises -> False on any oddity."""
    try:
        s = iban.replace(" ", "").upper()
        s = s[4:] + s[:4]
        digits = "".join(str(int(c, 36)) for c in s)
        return int(digits) % 97 == 1
    except Exception:
        return False


def _luhn_ok(number):
    """Luhn checksum for a card-like digit string. Never raises -> False."""
    try:
        ds = [int(c) for c in number if c.isdigit()]
        if not 13 <= len(ds) <= 19:
            return False
        total = 0
        for i, d in enumerate(reversed(ds)):
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0
    except Exception:
        return False


def scan_text(text):
    """Scan `text` for sensitive-data TYPES and return a result dict (NEVER raises):
        {"label": <one of LABELS>, "findings": [{"type": str, "count": int}, ...]}
    `findings` carries ONLY the type + a count of DISTINCT matches — never any matched
    VALUE. The label is the MAX sensitivity implied by the findings (or 'public' when none).

    Detection is conservative + sanity-checked (IBAN mod-97, card Luhn, phone/account
    length) so the label is defensible; it is advisory and never gates a legal check."""
    counts = {}
    try:
        t = text if isinstance(text, str) else ("" if text is None else str(text))
        if not t.strip():
            return {"label": "public", "findings": []}

        # IBAN (mod-97 validated). Track the matched spans so we don't also count the same
        # digits as a bank account / card.
        ibans = set()
        for m in _IBAN_RE.finditer(t):
            cand = m.group(1)
            if _mod97_ok(cand):
                ibans.add(cand.replace(" ", "").upper())
        if ibans:
            counts["iban"] = len(ibans)

        # BIC/SWIFT — exclude anything already counted as an IBAN/VAT prefix collision by
        # requiring it not to be a mod-97 IBAN.
        bics = set()
        for m in _BIC_RE.finditer(t):
            cand = m.group(1)
            if not _mod97_ok(cand):
                bics.add(cand.upper())
        if bics:
            counts["bic_swift"] = len(bics)

        emails = {m.group(0).lower() for m in _EMAIL_RE.finditer(t)}
        if emails:
            counts["email"] = len(emails)

        # VAT id: a country-prefixed alphanumeric that is NOT a valid IBAN and NOT an email
        # local part. Excludes ids already captured as BIC (pure-letter 8-run) is unlikely.
        vats = set()
        for m in _VAT_RE.finditer(t):
            cand = m.group(1).upper()
            if not _mod97_ok(cand):
                vats.add(cand)
        # a VAT id shape can also match a BIC; don't double count the identical token.
        vats -= bics
        if vats:
            counts["vat_id"] = len(vats)

        # Credit-card-like (Luhn). Strip spaces for the dedupe key.
        cards = set()
        for m in _CARD_RE.finditer(t):
            raw = m.group(1)
            digits = re.sub(r"\D", "", raw)
            if _luhn_ok(digits):
                cards.add(digits)
        if cards:
            counts["credit_card"] = len(cards)

        # Phone numbers FIRST (a phone carries a '+' or typical separators) so a phone digit
        # run is not also miscounted as a bare bank-account number below.
        phones = set()
        for m in _PHONE_RE.finditer(t):
            raw = m.group(0)
            digits = re.sub(r"\D", "", raw)
            if not 8 <= len(digits) <= 15:
                continue
            if "+" in raw or any(sep in raw for sep in (" ", "-", "(", ")", ".")):
                phones.add(digits)
        if phones:
            counts["phone"] = len(phones)

        # Bank account number: a long digit run that is NOT part of an IBAN, NOT a Luhn-valid
        # card, and NOT a detected phone (those are counted above). Conservative length
        # (8..18 digits).
        accounts = set()
        iban_digits = {re.sub(r"\D", "", x) for x in ibans}
        for m in _ACCOUNT_RE.finditer(t):
            digits = re.sub(r"\D", "", m.group(1))
            if not 8 <= len(digits) <= 18:
                continue
            if digits in cards or digits in phones \
                    or any(digits in d for d in iban_digits):
                continue
            accounts.add(digits)
        accounts -= cards | phones
        if accounts:
            counts["bank_account"] = len(accounts)

        names = set()
        for m in _NAME_RE.finditer(t):
            cand = m.group(1)
            if cand not in _NAME_STOP:
                names.add(cand)
        if names:
            counts["personal_name"] = len(names)
    except Exception as e:                       # detection must never raise into a caller
        log.warning("scan_text failed — returning empty (no values logged): %s", e)
        return {"label": "public", "findings": []}

    findings = [{"type": k, "count": int(v)} for k, v in sorted(counts.items())]
    label = "public"
    for k in counts:
        lbl = _TYPE_LABEL.get(k, "internal")
        if rank(lbl) > rank(label):
            label = lbl
    return {"label": label, "findings": findings}


# ============================================================ persistence
def _norm_ref(subject_ref):
    return (subject_ref or "").strip()


def classify_document(subject_ref, text):
    """Scan `text`, store the LABEL + findings ({type,count} only — NEVER the values) for
    `subject_ref` (a `doc:<id>`), and return the scan result dict. Best-effort: on any
    persistence error it still returns the scan result (the label exists for the caller),
    logging the failure. NEVER raises. NEVER stores or logs a matched value."""
    ref = _norm_ref(subject_ref)
    result = scan_text(text)
    if not ref:
        return result
    try:
        con = connect()
        try:
            con.execute(
                """INSERT INTO classifications (subject_ref, label, findings, scanned_at,
                                                tenant_id)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(tenant_id, subject_ref)
                   DO UPDATE SET label=excluded.label, findings=excluded.findings,
                                 scanned_at=excluded.scanned_at""",
                (ref, result["label"], json.dumps(result["findings"]),
                 datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
                 tenancy.write_tenant()))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.warning("classify_document persist failed for %r (label still returned): %s",
                    ref, e)
    return result


def classification(subject_ref):
    """The stored classification for `subject_ref`, or None when not yet classified.
    Returns a dict {subject_ref, label, findings:[{type,count}], scanned_at}. Never the raw
    values (none are ever stored). Never raises -> None."""
    ref = _norm_ref(subject_ref)
    if not ref:
        return None
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            row = con.execute(
                "SELECT * FROM classifications WHERE subject_ref=?" + frag
                + " LIMIT 1", [ref, *tp]).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        try:
            findings = json.loads(row["findings"] or "[]")
        except Exception:
            findings = []
        return {"subject_ref": row["subject_ref"], "label": row["label"],
                "findings": findings, "scanned_at": row["scanned_at"]}
    except Exception as e:
        log.warning("classification read failed for %r: %s", ref, e)
        return None


def counts_by_label():
    """A {label: n} map of how many documents carry each sensitivity label, for the admin
    overview. Never raises -> {}."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause("tenant_id")
            rows = con.execute(
                "SELECT label, COUNT(*) AS n FROM classifications WHERE 1=1" + frag
                + " GROUP BY label", tp).fetchall()
        finally:
            con.close()
        return {r["label"]: r["n"] for r in rows}
    except Exception as e:
        log.warning("counts_by_label failed: %s", e)
        return {}


# ============================================================ AI gate (OPT-IN)
def max_sensitivity():
    """The admin policy: the MAX label that may be sent to the external AI. Defaults to
    `restricted` = PERMISSIVE (allow everything). An unknown/blank value is treated as the
    permissive default. Never raises -> the permissive default."""
    try:
        import auth
        v = (auth.get_setting(POLICY_SETTING, DEFAULT_MAX_SENSITIVITY)
             or DEFAULT_MAX_SENSITIVITY).strip().lower()
        return v if v in _RANK else DEFAULT_MAX_SENSITIVITY
    except Exception as e:
        log.warning("max_sensitivity read failed — treating as permissive default: %s", e)
        return DEFAULT_MAX_SENSITIVITY


def external_ai_allowed_for_label(label):
    """The OPT-IN gate evaluated against an IN-MEMORY label (no stored record needed) — used
    by the in-request extraction path that has just scanned a draft's text but has no
    `doc:<id>` yet. Returns (allowed, info). BLOCKS only when `label` clearly EXCEEDS the
    policy; FAILS OPEN on any error / when `label` is None. Never raises -> allow."""
    mx = max_sensitivity()
    info = {"label": label, "max": mx, "blocked": False, "reason": ""}
    try:
        if not label:
            return True, info
        if rank(label) > rank(mx):
            info["blocked"] = True
            info["reason"] = (f"blocked by DLP policy: document classified {label} exceeds "
                              f"the external-AI limit {mx} — keep on-server only")
            return False, info
        return True, info
    except Exception as e:
        log.warning("external_ai_allowed_for_label failed — failing OPEN: %s", e)
        return True, info


def external_ai_allowed(subject_ref):
    """OPT-IN gate: may the document `subject_ref` be sent to the EXTERNAL AI?

    Returns (allowed: bool, info: dict). `info` carries {label, max, blocked, reason}.
    Policy: a document is BLOCKED when its stored sensitivity label EXCEEDS the configured
    `ai_external_max_sensitivity`. The DEFAULT policy is `restricted` = PERMISSIVE, so by
    default nothing is blocked (byte-identical). FAIL-OPEN on a classification ERROR / when
    the document was never classified (don't block on a scan failure); FAIL-CLOSED only when
    a policy is set AND the doc's label is clearly OVER the limit. Never raises -> allow."""
    mx = max_sensitivity()
    info = {"label": None, "max": mx, "blocked": False, "reason": ""}
    try:
        rec = classification(subject_ref)
        if rec is None:
            # never classified -> fail OPEN (don't block on a missing/failed scan)
            return True, info
        label = rec.get("label") or "public"
        info["label"] = label
        if rank(label) > rank(mx):
            info["blocked"] = True
            info["reason"] = (f"blocked by DLP policy: document classified {label} exceeds "
                              f"the external-AI limit {mx} — keep on-server only")
            return False, info
        return True, info
    except Exception as e:                       # FAIL OPEN on a classification error
        log.warning("external_ai_allowed failed for %r — failing OPEN: %s", subject_ref, e)
        info["reason"] = ""
        return True, info


def blocked_result(info):
    """The clear, structured 'blocked by DLP policy' note for the external-AI entry points
    to surface (a visible note) when the gate refuses to send. Pure; never raises."""
    label = (info or {}).get("label") or "unknown"
    mx = (info or {}).get("max") or DEFAULT_MAX_SENSITIVITY
    return (f"blocked by DLP policy: document classified {label} exceeds the external-AI "
            f"limit {mx} — keep on-server only")


if __name__ == "__main__":
    # offline smoke (uses the live classify.db): scan -> store -> read -> gate.
    sample = ("Pay to IBAN DE89 3704 0044 0532 0130 00, BIC COBADEFFXXX. "
              "Contact john@example.com or +49 30 1234567. Card 4111 1111 1111 1111.")
    r = scan_text(sample)
    print("scan:", json.dumps(r, indent=2))
    classify_document("doc:smoke", sample)
    print("stored:", classification("doc:smoke"))
    print("allowed (default permissive):", external_ai_allowed("doc:smoke")[0])
