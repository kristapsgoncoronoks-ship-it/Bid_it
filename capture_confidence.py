"""
CAPTURE-CONFIDENCE / LEARNING LOOP — a per-(supplier × field) CAPTURE-ACCURACY ledger.

Learn, from the two ground-truth correction signals the pipeline already produces, how
often the AI/OCR CAPTURE was WRONG for a given supplier and field, so the intake review
screen can flag the fields a supplier tends to get mis-read and show a per-supplier
capture-accuracy score.

CARDINAL RULE (identical in spirit to `confidence.py`): this is ADVISORY ONLY. It guides
HUMAN ATTENTION on the review screen — it NEVER gates, skips, or alters a deterministic
legal/VAT check (checklist, thresholds, locks, period-end, document presence, synthetic-
line refusal) and it NEVER mutates a captured figure. It writes NO product/claims DB; it
owns its OWN app-owned runtime DB (`capture_confidence.db`, gitignored), like `confidence.db`.

The two TRAINING SIGNALS (both best-effort, never raise):
  1. AI-CORRECTION signal — when `ai_verify.apply_corrections` writes a PDF-authoritative
     value back over a captured field, that field was CAPTURED WRONG -> was_correct=False.
  2. HUMAN-EDIT-AT-CONFIRM signal — at /extract/confirm we diff the CAPTURED draft against
     the values the human confirmed: a field left UNCHANGED is was_correct=True, a field the
     human EDITED is was_correct=False. Line fields map to normalized names (`line.vat`,
     `line.net`, `invoice.statement_ref`, ...).

Aggregation + smoothing:
  - `field_accuracy(supplier, field)` -> {"rate": 0.0-1.0, "n": int}; rate = correct/n.
  - `supplier_accuracy(supplier)` -> overall {"rate", "n"} across all fields.
  - `weak_fields(supplier, threshold=WEAK_THRESHOLD, min_n=MIN_SAMPLES)` -> the fields with
    ENOUGH samples (n >= min_n) whose accuracy is BELOW the threshold. Under-sampled fields
    (n < min_n) are NEVER flagged — a single miss must not raise a red flag.

Every public function NEVER raises: it logs via applog and falls back to a SAFE/empty
answer (no hints rather than a crash). Writes are tenant-stamped (queue_tenant) and audited.
"""
import os
import sqlite3
import datetime

import applog
import db_migrate
import tenancy

log = applog.get("capture_confidence")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned runtime DB (module attribute so tests can monkeypatch it).
DB = f"{WORKDIR}/capture_confidence.db"

# --- smoothing / flagging constants ------------------------------------------------
MIN_SAMPLES = 3        # don't flag a field with fewer than this many outcomes (smoothing)
WEAK_THRESHOLD = 0.80  # accuracy strictly below this (with enough samples) is "weak"

_DDL = [
    # One aggregate row per (tenant, supplier, field): running correct/total counts, so a
    # rate is a cheap n_correct/n_total read. An append-only event table is not needed for
    # the advisory surface (counts suffice); aggregate keeps the table small + fast.
    """CREATE TABLE IF NOT EXISTS capture_accuracy (
        supplier TEXT, field TEXT,
        n_correct INTEGER DEFAULT 0, n_total INTEGER DEFAULT 0,
        updated_at TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (tenant_id, supplier, field))""",
]


def connect():
    """Read-write handle to the app-owned capture-confidence DB. Schema applied once per DB
    via db_migrate (APPEND-ONLY; positions are stable)."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        import db_tuning
        db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    except Exception as e:
        log.debug("db_tuning unavailable, continuing untuned: %s", e)
    db_migrate.apply(con, "capture_confidence", _DDL)
    return con


def _key(supplier, field):
    """Normalise a (supplier, field) accuracy key. Empty -> '' (a stable bucket rather than
    mis-attributing to a wrong pair)."""
    return (supplier or "").strip(), (field or "").strip()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def record_outcome(supplier, field, was_correct, source="", detail=""):
    """Record ONE capture-accuracy outcome for (supplier, field): bumps n_total always and
    n_correct when `was_correct`. Returns the new {"rate", "n"} ({"rate":None,"n":0} on
    failure). Best-effort TELEMETRY — NEVER raises (a logging failure must not break capture/
    confirm). Tenant-stamped via queue_tenant(); audited via audit.record_event."""
    sup, fld = _key(supplier, field)
    if not sup or not fld:
        # nothing to attribute to — silently no-op (no spurious '' bucket rows)
        return {"rate": None, "n": 0}
    correct = bool(was_correct)
    now = _now()
    try:
        con = connect()
        try:
            qt = tenancy.queue_tenant()
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT n_correct, n_total FROM capture_accuracy "
                "WHERE supplier=? AND field=?" + frag, [sup, fld, *tp]).fetchone()
            n_correct = 0 if row is None else int(row["n_correct"] or 0)
            n_total = 0 if row is None else int(row["n_total"] or 0)
            n_total += 1
            if correct:
                n_correct += 1
            con.execute(
                "INSERT INTO capture_accuracy "
                "(supplier, field, n_correct, n_total, updated_at, tenant_id) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(tenant_id, supplier, field) DO UPDATE SET "
                "n_correct=excluded.n_correct, n_total=excluded.n_total, "
                "updated_at=excluded.updated_at",
                (sup, fld, n_correct, n_total, now, qt))
            con.commit()
            _audit(con, sup, fld, correct, source, detail)
            return {"rate": (n_correct / n_total) if n_total else None, "n": n_total}
        finally:
            con.close()
    except Exception as e:
        log.warning("record_outcome failed for %r/%r (correct=%s) — telemetry only: %s",
                    sup, fld, correct, e)
        return {"rate": None, "n": 0}


def _audit(con, supplier, field, was_correct, source, detail):
    """Audit one accuracy outcome (advisory telemetry, no figure mutated). Best-effort —
    the capture-confidence DB is unaudited (no triggers), so we log an EXPLICIT event into
    security.db's audit_log. Never raises out of record_outcome."""
    try:
        import audit
        scon = _auth_connect()
        if scon is None:
            return
        try:
            audit.record_event(scon, "capture_accuracy", f"{supplier}/{field}",
                               "CAPTURE_OUTCOME",
                               {"supplier": supplier, "field": field,
                                "was_correct": bool(was_correct),
                                "source": source or "", "detail": detail or ""})
        finally:
            scon.close()
    except Exception as e:
        log.debug("capture-confidence audit write skipped: %s", e)


def _auth_connect():
    """A handle to the audited security DB, or None when auth isn't available (e.g. a unit
    test with no security.db). Never raises."""
    try:
        import auth
        return auth.connect()
    except Exception as e:
        log.debug("auth.connect unavailable for capture-confidence audit: %s", e)
        return None


def field_accuracy(supplier, field):
    """Capture accuracy for one (supplier, field): {"rate": 0.0-1.0 or None, "n": int}.
    rate = n_correct/n_total; None when n == 0 (unseen). Never raises (-> {"rate":None,"n":0})."""
    sup, fld = _key(supplier, field)
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT n_correct, n_total FROM capture_accuracy "
                "WHERE supplier=? AND field=?" + frag, [sup, fld, *tp]).fetchone()
        finally:
            con.close()
        if row is None or not int(row["n_total"] or 0):
            return {"rate": None, "n": 0}
        n_total = int(row["n_total"])
        n_correct = int(row["n_correct"] or 0)
        return {"rate": n_correct / n_total, "n": n_total}
    except Exception as e:
        log.warning("field_accuracy lookup failed for %r/%r — empty: %s", sup, fld, e)
        return {"rate": None, "n": 0}


def supplier_accuracy(supplier):
    """Overall capture accuracy for a supplier across ALL fields:
    {"rate": 0.0-1.0 or None, "n": int}. rate = sum(n_correct)/sum(n_total); None when the
    supplier is unseen. Never raises (-> {"rate":None,"n":0})."""
    sup = (supplier or "").strip()
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT COALESCE(SUM(n_correct),0) c, COALESCE(SUM(n_total),0) t "
                "FROM capture_accuracy WHERE supplier=?" + frag, [sup, *tp]).fetchone()
        finally:
            con.close()
        n_total = int(row["t"] or 0)
        if not n_total:
            return {"rate": None, "n": 0}
        return {"rate": int(row["c"] or 0) / n_total, "n": n_total}
    except Exception as e:
        log.warning("supplier_accuracy lookup failed for %r — empty: %s", sup, e)
        return {"rate": None, "n": 0}


def weak_fields(supplier, threshold=WEAK_THRESHOLD, min_n=MIN_SAMPLES):
    """The fields a supplier tends to get MIS-READ: those with ENOUGH samples (n >= min_n)
    whose accuracy is strictly BELOW `threshold`. Returns a list of
    {"field", "rate", "n"} sorted worst-first. Under-sampled fields are NEVER returned
    (smoothing). Never raises (-> [])."""
    sup = (supplier or "").strip()
    try:
        min_n = max(1, int(min_n))
    except (TypeError, ValueError):
        min_n = MIN_SAMPLES
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = WEAK_THRESHOLD
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            rows = con.execute(
                "SELECT field, n_correct, n_total FROM capture_accuracy "
                "WHERE supplier=? AND n_total>=?" + frag,
                [sup, min_n, *tp]).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            n_total = int(r["n_total"] or 0)
            if n_total < min_n:
                continue
            rate = int(r["n_correct"] or 0) / n_total
            if rate < thr:
                out.append({"field": r["field"], "rate": rate, "n": n_total})
        out.sort(key=lambda d: (d["rate"], d["field"]))
        return out
    except Exception as e:
        log.warning("weak_fields lookup failed for %r — empty: %s", sup, e)
        return []


def scoreboard():
    """The full accuracy table for an admin surface: list of dicts sorted worst-first.
    Never raises (-> [])."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            rows = con.execute(
                "SELECT supplier, field, n_correct, n_total, updated_at "
                "FROM capture_accuracy WHERE 1=1" + frag, tp).fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            n_total = int(r["n_total"] or 0)
            out.append({"supplier": r["supplier"], "field": r["field"],
                        "rate": (int(r["n_correct"] or 0) / n_total) if n_total else None,
                        "n": n_total, "updated_at": r["updated_at"] or ""})
        out.sort(key=lambda d: (d["rate"] if d["rate"] is not None else 1.0,
                                d["supplier"], d["field"]))
        return out
    except Exception as e:
        log.warning("scoreboard failed — returning empty: %s", e)
        return []


# ---------------------------------------------------------------- field-name mapping
# Map the AI-verify verdict field names (ai_verify._resolve output domain — 'line[2].vat',
# 'invoice.due_date', 'supplier.name', 'totals.gross') to the NORMALIZED supplier-field
# name we attribute accuracy to. Line index is dropped (accuracy is per FIELD across all
# lines: a supplier mis-reads 'line.vat', not 'line[2].vat'). Pure; never raises.
import re as _re
_VERIFY_LINE_RE = _re.compile(r"^lines?\[\d+\]\.(.+)$")


def normalize_verify_field(name):
    """Normalize an ai_verify verdict field name to a stable supplier-field key, or None
    when it can't be normalized (the caller then skips it). Pure; never raises."""
    n = (name or "").strip().lower()
    if not n:
        return None
    m = _VERIFY_LINE_RE.match(n)
    if m:
        return "line." + m.group(1).strip()
    # header.x.y -> x.y ; keep totals.* and obj.key as-is (already stable, scannable)
    if n.startswith("header."):
        n = n[len("header."):]
    return n or None
