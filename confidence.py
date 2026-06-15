"""
CONFIDENCE-LEARNING MODEL — a per-(supplier × country) TRUST score that grows with each
clean validation and decays on a discrepancy, plus an append-only validation-event ledger.

CARDINAL RULE: confidence reduces redundant WORK, it NEVER bypasses a legal gate. The ONLY
consumer permitted to act on trust is the ADVISORY AI review (`ai_review`, default-OFF,
which never mutates or gates a figure). Trust must NEVER skip or alter any deterministic
legal gate (checklist, thresholds, locks, period-end, document presence, synthetic-line
refusal). Here that means: a high trust score lets the advisory AI panel be SKIPPED (a
cost saving) — the human still confirms and every deterministic gate still runs.

DB ownership: this is an APP-OWNED runtime DB (`confidence.db`), like `benchmark.db` — NOT
an engine product DB. Schema goes through `db_migrate.apply`; logging via applog.

The public functions NEVER raise: they log and fall back to a SAFE answer. In particular
`should_skip_ai` fails toward DOING the review (returns False on any error) — never toward
skipping it.

Update rule (backlog PROPOSED CONSTANTS — no decision needed):
  init  0.50
  clean    t = min(CEIL, t + GROWTH_K*(GROWTH_TARGET - t))   (growth toward 0.95)
  flagged  t = max(FLOOR, t - DECAY)                          (flat decay)
  floor 0.10, ceil 0.95
  skip-AI when trust >= 0.85 ; recommend a human look when trust < 0.30
"""
import os
import sqlite3
import datetime

import applog
import db_migrate
import tenancy

log = applog.get("confidence")

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# App-owned runtime DB (the seam is a module attribute so tests can monkeypatch it).
DB = f"{WORKDIR}/confidence.db"

# --- learning constants (backlog PROPOSED CONSTANTS) -------------------------------
INIT = 0.50               # trust of an unseen (supplier, country) pair
GROWTH_K = 0.25           # learning rate toward GROWTH_TARGET on a clean validation
GROWTH_TARGET = 0.95      # the asymptote a stream of clean validations approaches
DECAY = 0.30              # flat trust loss on a flagged/discrepant validation
FLOOR = 0.10              # trust never drops below this
CEIL = 0.95               # trust never rises above this
SKIP_AI_TRUST = 0.85      # trust >= -> the advisory AI review may be SKIPPED
HUMAN_REVIEW_TRUST = 0.30 # trust <  -> recommend a human look

_DDL = [
    """CREATE TABLE IF NOT EXISTS supplier_trust (
        supplier TEXT, country TEXT, trust REAL,
        n_clean INTEGER DEFAULT 0, n_flagged INTEGER DEFAULT 0,
        updated_at TEXT,
        PRIMARY KEY (supplier, country))""",
    """CREATE TABLE IF NOT EXISTS validation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier TEXT, country TEXT, clean INTEGER,
        source TEXT, detail TEXT, created_at TEXT)""",
    # P1 multi-tenancy (schema plumbing only): stamp the app-owned confidence tables
    # with a tenant_id; existing rows backfill to DEFAULT_TENANT_ID via the column
    # DEFAULT, new rows default too. NO query reads this column yet (the `multitenant`
    # switch is OFF and scope_clause is unwired until P2), so this is a pure
    # no-behavior-change addition. TEXT is audit-safe. APPEND-ONLY — keep at END.
    *tenancy.tenant_column_ddls(["supplier_trust", "validation_events"]),

    # ── PK RE-KEY (multi-tenant): tenant-qualified PRIMARY KEY for supplier_trust ──
    # supplier_trust already carries a tenant_id column (the P1 tenant_column_ddls spread
    # above) and tenant-scoped reads / stamped writes (P2). But its PRIMARY KEY did NOT
    # include tenant_id, so record_validation()'s INSERT … ON CONFLICT(supplier, country)
    # resolved on the NATURAL key only — under the `multitenant` switch ON two tenants'
    # trust rows for the SAME (supplier, country) would COLLIDE/merge (cross-tenant data
    # corruption). SQLite cannot ALTER a PRIMARY KEY in place, so the table is REBUILT:
    # create a __rekey twin with tenant_id FIRST in the PK (every other column, type and
    # DEFAULT preserved), copy all rows (explicit column list — never rely on column
    # order), drop the old, rename the twin. This runs as a LATER one-time db_migrate
    # migration (versioned, runs exactly once per DB) that supersedes the PK on both fresh
    # and existing confidence.db files. confidence.db is UNAUDITED (connect() installs NO
    # audit triggers) and supplier_trust carries NO secondary indexes, so there are no
    # triggers or indexes to drop/reinstate. APPEND-ONLY — keep at the END (positions are
    # stable); do NOT reorder the entries above. OFF-by-default is byte-identical: with a
    # single 'default' tenant the qualified PK behaves exactly as the natural PK did.
    # validation_events has a surrogate id PRIMARY KEY (no collision risk) and is left as is.
    """CREATE TABLE IF NOT EXISTS supplier_trust__rekey (
        supplier TEXT, country TEXT, trust REAL,
        n_clean INTEGER DEFAULT 0, n_flagged INTEGER DEFAULT 0,
        updated_at TEXT,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        PRIMARY KEY (tenant_id, supplier, country))""",
    """INSERT INTO supplier_trust__rekey
        (supplier, country, trust, n_clean, n_flagged, updated_at, tenant_id)
        SELECT supplier, country, trust, n_clean, n_flagged, updated_at, tenant_id
        FROM supplier_trust""",
    "DROP TABLE supplier_trust",
    "ALTER TABLE supplier_trust__rekey RENAME TO supplier_trust",
]


def connect():
    """Read-write handle to the app-owned confidence DB. Schema applied once per DB
    via db_migrate (APPEND-ONLY; positions are stable)."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        import db_tuning
        db_tuning.tune(con)  # WAL + busy_timeout for safe multi-process access
    except Exception as e:
        log.debug("db_tuning unavailable, continuing untuned: %s", e)
    db_migrate.apply(con, "confidence", _DDL)
    return con


def _key(supplier, country):
    """Normalise a (supplier, country) trust key. Empty -> '' (a stable bucket rather
    than mis-attributing to a wrong pair)."""
    return (supplier or "").strip(), (country or "").strip()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def trust(supplier, country):
    """Current trust for a (supplier, country) pair, INIT if unseen. Never raises."""
    sup, ctry = _key(supplier, country)
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT trust FROM supplier_trust WHERE supplier=? AND country=?" + frag,
                [sup, ctry, *tp]).fetchone()
        finally:
            con.close()
        if row is None or row["trust"] is None:
            return INIT
        return float(row["trust"])
    except Exception as e:
        log.warning("trust lookup failed for %r/%r — defaulting to INIT: %s",
                    sup, ctry, e)
        return INIT


def should_skip_ai(supplier, country):
    """True when the (supplier, country) pair is trusted enough to SKIP the advisory AI
    review. Fails SAFE: any error -> False (i.e. DO the review, never skip it)."""
    try:
        return trust(supplier, country) >= SKIP_AI_TRUST
    except Exception as e:
        log.warning("should_skip_ai failed for %r/%r — defaulting to False (do review): %s",
                    supplier, country, e)
        return False


def should_human_review(supplier, country):
    """True when the pair is untrusted enough to recommend a human look. Fails toward
    recommending a review (True) on error — the cautious answer."""
    try:
        return trust(supplier, country) < HUMAN_REVIEW_TRUST
    except Exception as e:
        log.warning("should_human_review failed for %r/%r — recommending review: %s",
                    supplier, country, e)
        return True


def record_validation(supplier, country, clean, source="", detail=""):
    """Append a ledger row and update the (supplier, country) trust score. `clean` True
    grows trust toward GROWTH_TARGET and bumps n_clean; False decays it and bumps
    n_flagged. Returns the new trust (INIT on failure). Never raises — best-effort
    telemetry that must not break its caller."""
    sup, ctry = _key(supplier, country)
    clean = bool(clean)
    now = _now()
    try:
        con = connect()
        try:
            # Validation/worker-path writes: stamp the tenant via queue_tenant() (system
            # context, never raises — a tenant-less validation defaults to DEFAULT_TENANT_ID).
            qt = tenancy.queue_tenant()
            con.execute(
                "INSERT INTO validation_events "
                "(supplier, country, clean, source, detail, created_at, tenant_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (sup, ctry, 1 if clean else 0, source or "", detail or "", now, qt))
            frag, tp = tenancy.scope_clause()
            row = con.execute(
                "SELECT trust, n_clean, n_flagged FROM supplier_trust "
                "WHERE supplier=? AND country=?" + frag, [sup, ctry, *tp]).fetchone()
            t = INIT if (row is None or row["trust"] is None) else float(row["trust"])
            n_clean = 0 if row is None else int(row["n_clean"] or 0)
            n_flagged = 0 if row is None else int(row["n_flagged"] or 0)
            if clean:
                t = min(CEIL, t + GROWTH_K * (GROWTH_TARGET - t))
                n_clean += 1
            else:
                t = max(FLOOR, t - DECAY)
                n_flagged += 1
            con.execute(
                "INSERT INTO supplier_trust "
                "(supplier, country, trust, n_clean, n_flagged, updated_at, tenant_id) "
                "VALUES (?,?,?,?,?,?,?) "
                # PK now tenant-qualified — conflict target matches PRIMARY KEY
                # (tenant_id, supplier, country) rebuilt in the rekey migration above.
                "ON CONFLICT(tenant_id, supplier, country) DO UPDATE SET "
                "trust=excluded.trust, n_clean=excluded.n_clean, "
                "n_flagged=excluded.n_flagged, updated_at=excluded.updated_at",
                (sup, ctry, t, n_clean, n_flagged, now, qt))
            con.commit()
            return t
        finally:
            con.close()
    except Exception as e:
        log.warning("record_validation failed for %r/%r (clean=%s) — telemetry only: %s",
                    sup, ctry, clean, e)
        return INIT


def scoreboard():
    """Trust table for the admin surface: list of dicts sorted by trust desc. Never
    raises (-> [])."""
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            rows = con.execute(
                "SELECT supplier, country, trust, n_clean, n_flagged, updated_at "
                "FROM supplier_trust WHERE 1=1" + frag
                + " ORDER BY trust DESC, supplier, country", tp).fetchall()
        finally:
            con.close()
        return [{"supplier": r["supplier"], "country": r["country"],
                 "trust": float(r["trust"]) if r["trust"] is not None else INIT,
                 "n_clean": int(r["n_clean"] or 0),
                 "n_flagged": int(r["n_flagged"] or 0),
                 "updated_at": r["updated_at"] or ""} for r in rows]
    except Exception as e:
        log.warning("scoreboard failed — returning empty: %s", e)
        return []


def recent_events(limit=100):
    """Recent ledger rows (newest first) for the admin surface. Never raises (-> [])."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 100
    try:
        con = connect()
        try:
            frag, tp = tenancy.scope_clause()
            rows = con.execute(
                "SELECT supplier, country, clean, source, detail, created_at "
                "FROM validation_events WHERE 1=1" + frag
                + " ORDER BY id DESC LIMIT ?", [*tp, limit]).fetchall()
        finally:
            con.close()
        return [{"supplier": r["supplier"], "country": r["country"],
                 "clean": bool(r["clean"]), "source": r["source"] or "",
                 "detail": r["detail"] or "", "created_at": r["created_at"] or ""}
                for r in rows]
    except Exception as e:
        log.warning("recent_events failed — returning empty: %s", e)
        return []
