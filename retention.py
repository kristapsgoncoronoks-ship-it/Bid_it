"""
DOCUMENT RETENTION POLICIES + LEGAL HOLD (A5) — the records-management table-stake
that the document-management incumbents (OpenKM / LogicalDOC) paywall. An APP-OWNED
ADVISORY overlay over every vaulted document, keyed by the same stable `doc:<id>`
REFERENCE the A2 search index, A3 metadata and A4 versioning already mint
(`subject_ref`).

SAFETY POSTURE — THIS MODULE IS ADVISORY ONLY. It FLAGS records for a HUMAN
disposition review when their retention period has elapsed; it NEVER auto-deletes
anything and it NEVER deletes a vaulted document's bytes. A retention `action` of
'dispose_review' STILL only flags — the word "dispose" names the review queue, not a
deletion. A document under a LEGAL HOLD is excluded from every "due for review" list
and can never be flagged for disposition while the hold stands (legal hold OVERRIDES
retention everywhere). This is a finance/compliance system: the conservative default
retention is 10 YEARS (EU VAT records under Directive 2008/9/EC and national VAT law
are typically retained ~10 years) and is configurable per policy.

RECORDS-MANAGEMENT CONTROLS (the GDPR Art. 5(1)(e) "storage limitation" / ISO 27001
A.5 records-management intent made explicit):
  * a RETENTION SCHEDULE — per-policy retain_years over a chosen basis date, resolved
    per document via its A3 tags (a tag-scoped policy, else an 'all' policy);
  * a LEGAL HOLD — a named, audited suspension of disposition that OVERRIDES retention;
  * an AUDIT TRAIL — every policy change, hold place/release, and any disposition-review
    action is captured (changed_by) by the same audit triggers as every other module.
Together (schedule + legal hold + audit) these are the records-management controls a
SOC 2 / ISO 27001 records-retention control objective expects.

DATA-PRODUCT BOUNDARY. The data-processing ENGINE owns and WRITES the product DBs
(fuel_history.db = transactions/master; vat_claims.db = invoice_documents index is
compliance-owned). This overlay is APP DATA, not a product, so it lives in its OWN
app-owned DB (retention.db, gitignored) and NEVER opens a product DB writable and
NEVER adds a column to one. To resolve which documents exist and their dates it reads
the invoice_documents index READ-ONLY through vat_refund.connect() (the same
read-only path /doc/<id> already uses), and resolves applicable policies via A3 tags
(metadata.py). It records ONLY policies + holds — never a pointer that could delete.

OWN DB. Like every other app module this owns its SQLite file via connect() +
db_migrate, audit-installed, db_tuning-tuned. Rows carry a tenant_id (the tenancy
seam, inert today): stamped with tenancy.write_tenant() on INSERT, never filtered yet.

BEST-EFFORT / NEVER-RAISE. Every public API is best-effort and returns AS A VALUE
((obj, "") / (None, err) / [] / {} / bool) — it never raises into the caller,
mirroring metadata.py / versioning.py / tenancy.py read paths. A bad/missing date is
handled gracefully (a value, never an exception). Failures are logged via applog.

APPLICABLE-POLICY RESOLUTION (the SAFE rule — never under-retain):
  * a document is matched by a TAG-scoped policy iff it carries that policy's tag
    (A3 metadata.tags_for); an 'all' policy matches every document.
  * among ALL applicable policies the LONGEST retain_years WINS (so we never under-retain
    a record by picking a shorter policy); a tag-scoped policy only "wins over an 'all'
    policy" as the TIEBREAK when retentions are equal.
  * retain_until = basis_date + retain_years (basis ∈ {doc_date, registered_date}); a
    document is DUE FOR REVIEW once asof >= retain_until AND it is NOT on legal hold.
"""
import os
import sqlite3
import datetime

import applog
import audit
import db_tuning
import db_migrate
import tenancy

log = applog.get("retention")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned retention DB (gitignored). A module-level attr so tests can repoint it the
# same way they repoint metadata.DB / versioning.DB.
DB = f"{WORKDIR}/retention.db"

# The conservative EU-VAT default: records are typically retained ~10 years. A policy
# may set any value; this is only the suggested/seed default surfaced in the UI.
DEFAULT_RETAIN_YEARS = 10

APPLIES_TO = ("all", "tag")
# 'dispose_review' STILL only flags for a human review queue — it never deletes.
ACTIONS = ("review", "dispose_review")
BASES = ("doc_date", "registered_date")

SCHEMA = """
CREATE TABLE IF NOT EXISTS retention_policies (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    applies_to   TEXT NOT NULL DEFAULT 'all',   -- 'all' | 'tag'
    tag_id       INTEGER,                        -- A3 tag id when applies_to='tag'
    retain_years INTEGER NOT NULL DEFAULT 10,
    action       TEXT NOT NULL DEFAULT 'review', -- 'review' | 'dispose_review' (FLAGS only)
    basis        TEXT NOT NULL DEFAULT 'doc_date', -- 'doc_date' | 'registered_date'
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    tenant_id    TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS legal_holds (
    id          INTEGER PRIMARY KEY,
    subject_ref TEXT NOT NULL,                   -- the held document's `doc:<id>`
    reason      TEXT,
    placed_by   TEXT,
    placed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    released_at TEXT,                            -- NULL while the hold is ACTIVE
    released_by TEXT,
    tenant_id   TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS ix_legal_holds_subject ON legal_holds(subject_ref);
"""

# Versioned migrations: APPEND new statements at the END (positions are stable).
_MIGRATIONS = []

_SCHEMA_READY = set()   # DB files whose schema is set up this process


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    audit.bind(con)      # audit triggers call ffs_actor(); register it every connect
    if DB == ":memory:" or DB not in _SCHEMA_READY:
        con.executescript(SCHEMA)
        db_migrate.apply(con, "retention", _MIGRATIONS)
        audit.install_audit(con, ["retention_policies", "legal_holds"])
        con.commit()
        _SCHEMA_READY.add(DB)
    return con


# ============================================================ helpers
def _norm_ref(subject_ref):
    return (subject_ref or "").strip()


def _as_int_or_none(v):
    if v in (None, "", "__keep__"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_date(s):
    """Parse a date/datetime string in a few common formats to a date, or None. Accepts
    the ISO forms invoice_documents.uploaded_at (CURRENT_TIMESTAMP -> 'YYYY-MM-DD
    HH:MM:SS') and supplier_invoices.invoice_date use. Never raises."""
    if s is None:
        return None
    if isinstance(s, datetime.datetime):
        return s.date()
    if isinstance(s, datetime.date):
        return s
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # last-ditch: a leading ISO date inside a longer string
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _add_years(d, years):
    """`d` + `years` calendar years, clamped for 29-Feb (-> 28-Feb). Never raises."""
    try:
        years = int(years)
    except (TypeError, ValueError):
        years = DEFAULT_RETAIN_YEARS
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # 29 Feb -> 28 Feb in a non-leap target year
        return d.replace(year=d.year + years, day=28)


def _today():
    return datetime.date.today()


def _coerce_asof(asof):
    """Resolve the as-of date for a due/status check: None -> today; otherwise parsed
    (a bad value falls back to today rather than raising)."""
    if asof is None:
        return _today()
    d = _parse_date(asof) if not isinstance(asof, datetime.date) else asof
    return d or _today()


# ============================================================ policies
def define_policy(name, applies_to="all", tag_id=None, retain_years=DEFAULT_RETAIN_YEARS,
                  action="review", basis="doc_date"):
    """Define a retention policy. Returns (policy_dict, "") or (None, error). Never
    raises. A 'tag' policy requires a tag_id; retain_years must be a non-negative int;
    action/basis must be one of the allowed values."""
    name = (name or "").strip()
    if not name:
        return None, "a policy name is required"
    applies_to = (applies_to or "all").strip()
    if applies_to not in APPLIES_TO:
        return None, f"applies_to must be one of {APPLIES_TO}"
    action = (action or "review").strip()
    if action not in ACTIONS:
        return None, f"action must be one of {ACTIONS}"
    basis = (basis or "doc_date").strip()
    if basis not in BASES:
        return None, f"basis must be one of {BASES}"
    tid = _as_int_or_none(tag_id)
    if applies_to == "tag" and tid is None:
        return None, "a tag is required for a tag-scoped policy"
    if applies_to == "all":
        tid = None
    try:
        yrs = int(retain_years)
    except (TypeError, ValueError):
        return None, "retain_years must be a whole number of years"
    if yrs < 0:
        return None, "retain_years cannot be negative"
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO retention_policies
                       (name, applies_to, tag_id, retain_years, action, basis, tenant_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, applies_to, tid, yrs, action, basis, tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM retention_policies WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("define_policy failed for name=%r", name)
        return None, f"could not define policy ({str(e)[:80]})"


def list_policies():
    """All retention policies (newest first). Each is a row dict. Never raises -> []."""
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM retention_policies ORDER BY id DESC").fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("list_policies failed: %s", e)
        return []


def delete_policy(policy_id):
    """Delete a retention policy. Returns (True, "") or (False, error). Idempotent,
    best-effort, never raises. Deleting a POLICY never touches a document — it only
    removes a retention SCHEDULE entry."""
    try:
        con = connect()
        try:
            con.execute("DELETE FROM retention_policies WHERE id=?", (policy_id,))
            con.commit()
        finally:
            con.close()
        return True, ""
    except Exception as e:
        log.exception("delete_policy failed for %s", policy_id)
        return False, f"could not delete policy ({str(e)[:80]})"


# ============================================================ legal holds
def place_hold(subject_ref, reason, actor):
    """Place an ACTIVE legal hold on `subject_ref` (a `doc:<id>`). A held document is
    excluded from every disposition-review list and can never be flagged for disposition
    while the hold stands. IDEMPOTENT: if an active hold already exists for this subject,
    it is returned unchanged (no duplicate). Returns (hold_dict, "") or (None, error).
    Audited (changed_by). Never raises."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return None, "a document reference is required"
    try:
        con = connect()
        try:
            audit.set_actor(con, actor or "system")
            existing = con.execute(
                "SELECT * FROM legal_holds WHERE subject_ref=? AND released_at IS NULL "
                "ORDER BY id DESC LIMIT 1", (subject_ref,)).fetchone()
            if existing is not None:
                return dict(existing), ""   # idempotent — one active hold per subject
            cur = con.execute(
                """INSERT INTO legal_holds (subject_ref, reason, placed_by, tenant_id)
                   VALUES (?,?,?,?)""",
                (subject_ref, (reason or "").strip() or None, actor or "system",
                 tenancy.write_tenant()))
            con.commit()
            row = con.execute("SELECT * FROM legal_holds WHERE id=?",
                              (cur.lastrowid,)).fetchone()
        finally:
            audit.reset_actor(con)
            con.close()
        return (dict(row) if row else None), ""
    except Exception as e:
        log.exception("place_hold failed for %r", subject_ref)
        return None, f"could not place hold ({str(e)[:80]})"


def release_hold(hold_id, actor):
    """Release an active legal hold (stamping released_at/released_by). The hold ROW is
    kept (the records of placement + release are append-only history). Returns (True, "")
    or (False, error). Audited (changed_by). Idempotent, never raises."""
    hid = _as_int_or_none(hold_id)
    if hid is None:
        return False, "no such hold"
    try:
        con = connect()
        try:
            audit.set_actor(con, actor or "system")
            row = con.execute("SELECT * FROM legal_holds WHERE id=?", (hid,)).fetchone()
            if row is None:
                return False, "no such hold"
            if row["released_at"] is not None:
                return True, ""   # already released — idempotent
            con.execute(
                "UPDATE legal_holds SET released_at=CURRENT_TIMESTAMP, released_by=? "
                "WHERE id=?", (actor or "system", hid))
            con.commit()
        finally:
            audit.reset_actor(con)
            con.close()
        return True, ""
    except Exception as e:
        log.exception("release_hold failed for %s", hold_id)
        return False, f"could not release hold ({str(e)[:80]})"


def holds_for(subject_ref):
    """Every legal hold ever recorded for `subject_ref` (active + released), newest
    first. Each is a row dict (released_at is NULL for an active hold). Never raises -> []."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return []
    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT * FROM legal_holds WHERE subject_ref=? ORDER BY id DESC",
                (subject_ref,)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("holds_for failed for %r: %s", subject_ref, e)
        return []


def is_on_hold(subject_ref):
    """True iff `subject_ref` has an ACTIVE (un-released) legal hold. Never raises ->
    False. This is the gate that OVERRIDES retention everywhere — a True here removes a
    document from every disposition-review list."""
    subject_ref = _norm_ref(subject_ref)
    if not subject_ref:
        return False
    try:
        con = connect()
        try:
            row = con.execute(
                "SELECT 1 FROM legal_holds WHERE subject_ref=? AND released_at IS NULL "
                "LIMIT 1", (subject_ref,)).fetchone()
        finally:
            con.close()
        return row is not None
    except Exception as e:
        log.warning("is_on_hold failed for %r: %s", subject_ref, e)
        return False


# ============================================================ policy resolution
def _applicable_policy(subject_ref, policies=None, tag_ids=None):
    """Resolve THE policy that applies to `subject_ref` (a `doc:<id>`), or None.

    A tag-scoped policy matches iff the document carries that tag (A3 metadata); an
    'all' policy matches every document. Among ALL applicable policies the LONGEST
    retain_years WINS (never under-retain); a tag-scoped policy only wins over an 'all'
    policy as the TIEBREAK when retentions are equal.

    `policies`/`tag_ids` may be passed to avoid re-querying in a batch (due_for_review
    resolves them once). Never raises -> None."""
    try:
        if policies is None:
            policies = list_policies()
        if not policies:
            return None
        if tag_ids is None:
            try:
                import metadata as MD
                tag_ids = {t["id"] for t in MD.tags_for(subject_ref)}
            except Exception as e:
                log.warning("_applicable_policy tag lookup failed for %r: %s",
                            subject_ref, e)
                tag_ids = set()
        tag_cands = [p for p in policies
                     if p.get("applies_to") == "tag" and p.get("tag_id") in tag_ids]
        all_cands = [p for p in policies if p.get("applies_to") == "all"]
        pool = tag_cands + all_cands
        if not pool:
            return None
        # The SAFE rule (never under-retain): pick the LONGEST retention among ALL
        # applicable policies. A tag-scoped policy only "wins over 'all'" as the
        # tiebreak when retentions are equal (so we never shorten a record's retention
        # by preferring a tag policy that retains for fewer years than an 'all' policy).
        return max(pool, key=lambda p: (int(p.get("retain_years") or 0),
                                        1 if p.get("applies_to") == "tag" else 0,
                                        p.get("id") or 0))
    except Exception as e:
        log.warning("_applicable_policy failed for %r: %s", subject_ref, e)
        return None


def retain_until(policy, doc_date, registered_date=None):
    """basis_date + retain_years for `policy`. The basis date is doc_date or
    registered_date per policy['basis'] (registered_date falls back to doc_date if the
    chosen basis is missing). Returns a date or None when no usable basis date exists.
    Never raises."""
    try:
        if not policy:
            return None
        basis = policy.get("basis") or "doc_date"
        base = registered_date if basis == "registered_date" else doc_date
        base_d = _parse_date(base)
        if base_d is None:
            base_d = _parse_date(doc_date if basis == "registered_date" else registered_date)
        if base_d is None:
            return None
        return _add_years(base_d, policy.get("retain_years") or DEFAULT_RETAIN_YEARS)
    except Exception as e:
        log.warning("retain_until failed: %s", e)
        return None


def retention_status(subject_ref, doc_date, registered_date=None, asof=None,
                     policies=None, tag_ids=None):
    """The retention picture for one document. Returns a dict (never raises):
        {policy, retain_until, days_remaining, past_due, on_hold}
      * policy        — the applicable policy dict (or None if none applies);
      * retain_until  — ISO date string the retention elapses (or None);
      * days_remaining— (retain_until - asof).days (negative once past due; None if no
                        retain_until);
      * past_due      — True iff asof >= retain_until (False if no retain_until);
      * on_hold       — True iff an ACTIVE legal hold stands (OVERRIDES retention: a held
                        document is NEVER treated as due for disposition).
    """
    asof_d = _coerce_asof(asof)
    on_hold = is_on_hold(subject_ref)
    pol = _applicable_policy(subject_ref, policies=policies, tag_ids=tag_ids)
    until = retain_until(pol, doc_date, registered_date) if pol else None
    days_remaining = (until - asof_d).days if until is not None else None
    # legal hold OVERRIDES retention: a held document is never "past due" for disposition
    past_due = bool(until is not None and not on_hold and asof_d >= until)
    return {
        "policy": pol,
        "retain_until": until.isoformat() if until is not None else None,
        "days_remaining": days_remaining,
        "past_due": past_due,
        "on_hold": on_hold,
    }


# ============================================================ document enumeration (read-only)
def _iter_documents():
    """Yield the candidate documents to evaluate for retention, READ-ONLY through
    vat_refund.connect() (the same read path /doc/<id> uses). Each is a dict:
        {subject_ref, doc_id, entity, supplier, invoice_ref, filename,
         doc_date, registered_date}
    doc_date is the invoice date (supplier_invoices.invoice_date when resolvable),
    registered_date is invoice_documents.uploaded_at. This module NEVER writes a product
    DB. Best-effort: any failure logs and yields nothing. A separate _DOC_SOURCE hook lets
    tests inject documents without a product DB."""
    src = globals().get("_DOC_SOURCE")
    if src is not None:
        for d in (src() or []):
            yield d
        return
    try:
        import vat_refund as VR
        con = VR.connect()
        try:
            doc_rows = con.execute(
                "SELECT id, entity, supplier, invoice_ref, filename, uploaded_at "
                "FROM invoice_documents ORDER BY id").fetchall()
        finally:
            con.close()
    except Exception as e:
        log.warning("_iter_documents: could not read invoice_documents: %s", e)
        return
    # resolve invoice dates (doc_date basis) from the supplier master, best-effort.
    inv_dates = {}
    try:
        import supplier_master as SM
        scon = SM.connect()
        try:
            for r in scon.execute(
                    "SELECT supplier, invoice_no, invoice_date FROM supplier_invoices"):
                inv_dates[(r["supplier"], r["invoice_no"])] = r["invoice_date"]
        finally:
            scon.close()
    except Exception as e:
        log.warning("_iter_documents: could not read supplier_invoices: %s", e)
    for d in doc_rows:
        doc_date = inv_dates.get((d["supplier"], d["invoice_ref"])) or d["uploaded_at"]
        yield {
            "subject_ref": f"doc:{d['id']}", "doc_id": d["id"],
            "entity": d["entity"], "supplier": d["supplier"],
            "invoice_ref": d["invoice_ref"], "filename": d["filename"],
            "doc_date": doc_date, "registered_date": d["uploaded_at"],
        }


def due_for_review(asof=None):
    """The ADVISORY worklist: every document whose retention has ELAPSED and that is NOT
    on legal hold. Returns a list of dicts (never raises -> []):
        {subject_ref, doc_id, entity, supplier, invoice_ref, filename,
         policy, retain_until, days_remaining, action}
    Resolution: the applicable policy per document via A3 tags (a tag-scoped policy wins,
    else an 'all' policy; among ties the LONGEST retention wins); retain_until from the
    policy's basis date + retain_years. A document under an ACTIVE legal hold is EXCLUDED
    (legal hold OVERRIDES retention). This is a HUMAN worklist — it deletes nothing."""
    asof_d = _coerce_asof(asof)
    try:
        policies = list_policies()
        if not policies:
            return []
        out = []
        for d in _iter_documents():
            ref = d["subject_ref"]
            st = retention_status(
                ref, d.get("doc_date"), d.get("registered_date"), asof=asof_d,
                policies=policies)
            if st["past_due"] and not st["on_hold"]:
                pol = st["policy"] or {}
                row = dict(d)
                row.update({
                    "policy": pol,
                    "retain_until": st["retain_until"],
                    "days_remaining": st["days_remaining"],
                    "action": pol.get("action") or "review",
                })
                out.append(row)
        # most-overdue first
        out.sort(key=lambda r: (r["days_remaining"]
                                if r["days_remaining"] is not None else 0))
        return out
    except Exception as e:
        log.warning("due_for_review failed: %s", e)
        return []


if __name__ == "__main__":
    # offline smoke (uses the live retention.db): define -> status -> hold -> release.
    import tempfile
    DB = tempfile.mktemp(prefix="retention_", suffix=".db")
    _SCHEMA_READY = set()
    p, err = define_policy("EU VAT 10y", "all", retain_years=10, basis="doc_date")
    print("policy:", (p or {}).get("id"), err or "ok")
    st = retention_status("doc:1", "2010-01-01")
    print("status doc:1 (2010):", st["past_due"], st["retain_until"], st["on_hold"])
    h, _ = place_hold("doc:1", "litigation X", "smoke")
    print("hold ->", (h or {}).get("id"), "on_hold:", is_on_hold("doc:1"))
    st = retention_status("doc:1", "2010-01-01")
    print("status under hold: past_due=", st["past_due"], "on_hold=", st["on_hold"])
    release_hold((h or {}).get("id"), "smoke")
    print("after release on_hold:", is_on_hold("doc:1"))
