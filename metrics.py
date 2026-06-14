"""
METRICS — materialized per-period aggregates, settled at the monthly close.

The dashboard KPIs (diesel litres, fleet eff. €/L, net spend, reclaimable VAT) and
the avoidable-overpay figure are otherwise recomputed LIVE on every dashboard load,
each scanning the whole `transactions` table (the overpay loop in particular is the
expensive one — it pairs up every same-day, same-country supplier). This module
settles those totals ONCE, at the monthly close, into a `settled_metrics` table the
dashboard reads cheaply; an un-rebuilt period still renders via the live fallback.

Engine/app boundary (CLAUDE.md): `settled_metrics` lives in the ENGINE-OWNED
fuel_history.db. The ENGINE WRITES it — `rebuild()` opens a WRITABLE handle exactly
the way history.py does (`sqlite3.connect(DB)` + `db_tuning.tune`) and applies the
schema via `db_migrate.apply` on that writable handle. The APP only READS it, through
the read-only `dataproduct.connect("fuel_history")` window — a stray app write raises
OperationalError, like every other product-DB access. `read()`/`verify()` therefore
NEVER migrate and NEVER raise.

NOT a fork of the aggregation math: every figure here is produced by the CANONICAL
read functions in queries.py (`q_savings`, `q_kpis`) — `verify()` recomputes the same
way and compares, so a stale/buggy materialization is caught (the drift check).
"""
import json
import os
import sqlite3
import time

import applog
import dataproduct
import db_migrate
import db_tuning
import money
import queries

WORKDIR = os.path.dirname(os.path.abspath(__file__))
# Same on-disk file history.py owns; a module-level attr so tests can repoint it the
# same way they repoint history.DB.
DB = f"{WORKDIR}/fuel_history.db"

log = applog.get("metrics")

# settled_metrics: one row per (period, metric). `value` is the scalar (EUR quantized
# HALF_UP, or litres / €-per-L numeric); `detail` holds JSON for structured metrics
# (overpay carries its by_country / by_supplier breakdown). computed_at = ISO-ish now.
_MIGR = [
    "CREATE TABLE IF NOT EXISTS settled_metrics ("
    "period TEXT NOT NULL, metric TEXT NOT NULL, value REAL, detail TEXT, "
    "computed_at TEXT, PRIMARY KEY(period, metric))",
]

# The metric keys this module settles. Kept as a constant so read()/verify() and the
# dashboard agree on the names without re-typing string literals.
M_OVERPAY = "overpay_total"
M_LITRES = "diesel_litres"
M_EURL = "fleet_eurl"
M_NET = "net_spend"
M_VAT = "reclaimable_vat"
EUR_METRICS = (M_OVERPAY, M_NET, M_VAT)   # quantized / compared in EUR


def _writable_connect():
    """Open the WRITABLE engine handle to fuel_history.db, exactly as history.py does
    (plain sqlite3.connect + db_tuning.tune for WAL + busy_timeout). This is the engine
    writer path — NOT dataproduct's read-only window."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    db_tuning.tune(con)   # WAL + busy_timeout; idempotent
    return con


def _compute(con, period):
    """Compute the settled metric rows for `period` via the CANONICAL queries (no forked
    math). Returns a list of (metric, value, detail_obj-or-None). EUR values quantized
    HALF_UP (money.f2); litres / €-per-L left numeric (display-rounded downstream)."""
    sv = queries.q_savings(con, period)            # {total, by_country, by_supplier}
    k = queries.q_kpis(con, period)                # net, vat, gross, litres, eurl
    return [
        (M_OVERPAY, money.f2(sv["total"]),
         {"by_country": sv["by_country"], "by_supplier": sv["by_supplier"]}),
        (M_LITRES, (k["litres"] if k["litres"] is not None else 0.0), None),
        (M_EURL, (k["eurl"] if k["eurl"] is not None else 0.0), None),
        (M_NET, money.f2(k["net"] or 0), None),
        (M_VAT, money.f2(k["vat"] or 0), None),
    ]


def rebuild(period, con=None):
    """ENGINE entrypoint: recompute the settled aggregates for `period` and REPLACE them
    in `settled_metrics`. Opens a WRITABLE fuel_history handle when `con` is None (the
    engine writer path, NOT the read-only app window), ensures the schema via db_migrate
    on that writable handle, and writes one row per metric.

    Idempotent / restartable: INSERT OR REPLACE on (period, metric), so a re-run after a
    failed close overwrites cleanly with no duplication. Returns {period, metrics: n}.
    """
    own = con is None
    if own:
        con = _writable_connect()
    try:
        db_migrate.apply(con, "metrics", _MIGR)   # writable handle only
        rows = _compute(con, period)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        con.executemany(
            "INSERT OR REPLACE INTO settled_metrics (period, metric, value, detail, "
            "computed_at) VALUES (?,?,?,?,?)",
            [(period, m, v, (json.dumps(d) if d is not None else None), now)
             for (m, v, d) in rows])
        con.commit()
        log.info("settled %d metrics for period %s", len(rows), period)
        return {"period": period, "metrics": len(rows)}
    finally:
        if own:
            con.close()


def read(period, con=None):
    """READ the settled metrics for `period` into {metric: {value, detail}}. `detail` is
    the parsed JSON object (or None). Opens the READ-ONLY app window when `con` is None.

    Tolerant of a PRE-MIGRATION DB (no settled_metrics table yet) — returns {} rather
    than raising or migrating; the read path stays strictly read-only. Never raises."""
    own = con is None
    if own:
        con = dataproduct.connect("fuel_history")
    try:
        out = {}
        for r in con.execute(
                "SELECT metric, value, detail FROM settled_metrics WHERE period=?",
                (period,)):
            detail = None
            if r["detail"]:
                try:
                    detail = json.loads(r["detail"])
                except (ValueError, TypeError) as e:
                    log.warning("settled_metrics detail JSON unreadable for %s/%s: %s",
                                period, r["metric"], e)
            out[r["metric"]] = {"value": r["value"], "detail": detail}
        return out
    except sqlite3.OperationalError as e:
        # no such table: settled_metrics — a period closed before this module shipped.
        log.debug("settled_metrics not readable for %s (pre-migration?): %s", period, e)
        return {}
    except Exception as e:  # noqa: BLE001 - read path must never raise into the caller
        log.warning("metrics.read failed for %s: %s", period, e)
        return {}
    finally:
        if own:
            con.close()


def verify(period, con=None):
    """DRIFT CHECK: recompute the metrics LIVE via the canonical queries on a READ
    connection and compare to the stored settled values. Returns a list of drifts
    [{metric, stored, live, delta}] for any metric whose stored vs live differ beyond a
    cent (EUR metrics compared with money.q2; litres / €-per-L compared at full numeric
    precision). Empty list = the materialization matches a fresh recompute.

    The correctness guard against a stale or buggy settle. Never raises (logs, returns
    [] on error) — opens the read-only app window when `con` is None."""
    own = con is None
    try:
        if own:
            con = dataproduct.connect("fuel_history")
        stored = read(period, con)
        if not stored:
            return []   # nothing settled for this period -> nothing to drift against
        live = {m: v for (m, v, _d) in _compute(con, period)}
        drifts = []
        for m, lv in live.items():
            sv = stored.get(m, {}).get("value")
            if sv is None:
                continue
            if m in EUR_METRICS:
                differ = money.q2(sv) != money.q2(lv)
            else:
                differ = abs((sv or 0.0) - (lv or 0.0)) > 1e-6
            if differ:
                drifts.append({"metric": m, "stored": sv, "live": lv,
                               "delta": (lv or 0.0) - (sv or 0.0)})
        return drifts
    except Exception as e:  # noqa: BLE001 - drift check must never raise
        log.warning("metrics.verify failed for %s: %s", period, e)
        return []
    finally:
        if own and con is not None:
            con.close()
