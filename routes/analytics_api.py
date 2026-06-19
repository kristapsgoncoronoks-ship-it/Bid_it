"""
routes/analytics_api.py — read-only JSON analytics API (first blueprint slice).

These are the lightweight JSON twins of the analytics dashboard pages. They are
READ-ONLY (SELECT-only, over the engine-owned fuel_history.db opened read-only via
dataproduct), login-only (classified in app.OPEN_ENDPOINTS), and consumed by the
vanilla client JS via hardcoded fetch("/api/...") URLs — so the URL PATHS here are
byte-identical to their former @app.route paths.

ENDPOINT NAMES: registering this blueprint as "analytics_api" makes each view's
endpoint name "analytics_api.<func>", e.g. api_periods -> "analytics_api.api_periods".
app.OPEN_ENDPOINTS carries the dotted names; the URL paths are unchanged. See
routes/__init__.py for the full split pattern.

NO IMPORT CYCLE: this module imports only the LEAF data modules (dataproduct,
queries) and Flask primitives — never app.py. The global before/after hooks
(_guard, CSRF, actor, origin lock, coverage check) are registered on the Flask
`app` object and therefore apply to these blueprint routes exactly as before.
"""
from flask import Blueprint, request, jsonify
import dataproduct
from queries import (q_periods, q_benchmark, q_compare, q_headtohead, q_entities)

bp = Blueprint("analytics_api", __name__)


def _db():
    # fuel_history.db is OWNED and written by the data-processing engine; the app only
    # READS it, so open a read-only handle (mirrors app.DB()). queries.py SELECTs work
    # unchanged on a read-only handle.
    return dataproduct.connect("fuel_history")


@bp.route("/api/periods")
def api_periods():
    con = _db(); out = q_periods(con); con.close(); return jsonify(out)


@bp.route("/api/benchmark")
def api_benchmark():
    con = _db(); ps = q_periods(con); period = request.args.get("period", ps[0] if ps else None)
    out = [dict(r) for r in q_benchmark(con, period)] if period else []
    con.close(); return jsonify(out)


@bp.route("/api/compare")
def api_compare():
    con = _db(); out = [dict(r) for r in q_compare(con, request.args)]; con.close(); return jsonify(out)


@bp.route("/api/headtohead")
def api_h2h():
    con = _db(); ps = q_periods(con); period = request.args.get("period", ps[0] if ps else None)
    out = q_headtohead(con, period) if period else []; con.close(); return jsonify(out)


@bp.route("/api/entities")
def api_entities():
    con = _db(); ps = q_periods(con); period = request.args.get("period", ps[0] if ps else None)
    out = [dict(r) for r in q_entities(con, period)] if period else []
    con.close(); return jsonify(out)
