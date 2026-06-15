"""Multi-tenancy P1 — schema plumbing (slice A2: finance.db, confidence.db,
import_log.db, intake.db).

Extends the proven CRM + A1 slices (test_tenant_schema.py / test_tenant_schema_a1.py)
to the remaining APP-OWNED DBs. Same cardinal invariant: adding a
`tenant_id TEXT NOT NULL DEFAULT 'default'` column that NO query reads/filters changes
no behavior. The `multitenant` switch stays OFF and scope_clause is NOT wired into any
query (that's P2). Existing + new rows backfill to the default tenant via the column
DEFAULT.

Each section: (a) every named tenant table carries the tenant_id column with the
default; (b) an INSERT that doesn't name tenant_id backfills 'default'; (c) where a
SELECT* dict-contract is surfaced, the exposed dict excludes tenant_id; (d) a second
connect on the SAME file is idempotent (the ALTER runs once).

The waiting_room section additionally proves ZERO REGRESSION on the machinery the
column sits next to: a representative enqueue + a rate-limit row both stamp 'default',
and _claim / record_outcome / the limiter still behave (the full test_rate_limiter.py
+ test_waiting_room.py suites are the broader regression guard).
"""
import importlib

import pytest


def _assert_tenant_col(con, table):
    cols = {r["name"]: r for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    assert "tenant_id" in cols, f"{table} missing tenant_id"
    col = cols["tenant_id"]
    assert col["type"] == "TEXT", f"{table}.tenant_id type {col['type']!r}"
    assert col["notnull"] == 1, f"{table}.tenant_id should be NOT NULL"
    assert col["dflt_value"] == "'default'", \
        f"{table}.tenant_id default {col['dflt_value']!r}"


# ── finance.py → finance.db ────────────────────────────────────────────────────

FINANCE_TABLES = ["advances"]


def _fresh_finance(tmp_path, monkeypatch):
    import finance
    importlib.reload(finance)
    monkeypatch.setattr(finance, "DB", str(tmp_path / "finance.db"))
    monkeypatch.setattr(finance, "_READY", set())
    return finance


def test_finance_tables_have_tenant_id_default(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    con = fin.connect()
    try:
        for table in FINANCE_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_finance_default_backfills_unspecified_insert(tmp_path, monkeypatch):
    """An advances row inserted without tenant_id backfills 'default'."""
    fin = _fresh_finance(tmp_path, monkeypatch)
    con = fin.connect()
    try:
        con.execute("""INSERT INTO advances (claim_key, amount_eur, fee_eur, provider,
                       status) VALUES ('E:Belgium:2026-Q1', 1000.0, 20.0, 'none',
                       'no_provider')""")
        con.commit()
        assert con.execute(
            "SELECT tenant_id FROM advances").fetchone()[0] == "default"
    finally:
        con.close()


def test_finance_list_advances_excludes_no_keys_but_stamps_default(tmp_path, monkeypatch):
    """list_advances() returns its explicit-column dict (no tenant_id) while the
    stored row is stamped 'default' — record_advance path stays behavior-preserving."""
    fin = _fresh_finance(tmp_path, monkeypatch)
    con = fin.connect()
    try:
        con.execute("""INSERT INTO advances (claim_key, amount_eur, fee_eur, provider,
                       status) VALUES ('K', 5.0, 0.1, 'none', 'no_provider')""")
        con.commit()
    finally:
        con.close()
    rows = fin.list_advances()
    assert rows and rows[0]["claim_key"] == "K"
    assert "tenant_id" not in rows[0], "list_advances leaked tenant_id"


def test_finance_idempotent_second_connect(tmp_path, monkeypatch):
    fin = _fresh_finance(tmp_path, monkeypatch)
    fin.connect().close()
    monkeypatch.setattr(fin, "_READY", set())
    con = fin.connect()   # must NOT raise (db_migrate skips the applied ALTER)
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(advances)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()


# ── confidence.py → confidence.db ──────────────────────────────────────────────

CONFIDENCE_TABLES = ["supplier_trust", "validation_events"]


def _fresh_confidence(tmp_path, monkeypatch):
    import confidence
    importlib.reload(confidence)
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    return confidence


def test_confidence_tables_have_tenant_id_default(tmp_path, monkeypatch):
    conf = _fresh_confidence(tmp_path, monkeypatch)
    con = conf.connect()
    try:
        for table in CONFIDENCE_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_confidence_default_backfills_via_record_validation(tmp_path, monkeypatch):
    """record_validation() writes both tables; neither names tenant_id, so both
    backfill 'default' — and the public read API (scoreboard/recent_events) stays
    behavior-preserving (no tenant_id leaks into its explicit-column dicts)."""
    conf = _fresh_confidence(tmp_path, monkeypatch)
    conf.record_validation("DEMO", "LV", clean=True, source="t")
    con = conf.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM supplier_trust").fetchone()[0] == "default"
        assert con.execute(
            "SELECT tenant_id FROM validation_events").fetchone()[0] == "default"
    finally:
        con.close()
    assert all("tenant_id" not in r for r in conf.scoreboard())
    assert all("tenant_id" not in r for r in conf.recent_events())


def test_confidence_idempotent_second_connect(tmp_path, monkeypatch):
    conf = _fresh_confidence(tmp_path, monkeypatch)
    conf.connect().close()
    con = conf.connect()   # must NOT raise
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(supplier_trust)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()


# ── import_log.py → import_log.db ──────────────────────────────────────────────

IMPORT_LOG_TABLES = ["import_log"]


def _fresh_import_log(tmp_path, monkeypatch):
    import import_log
    importlib.reload(import_log)
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    monkeypatch.setattr(import_log, "_READY", set())
    return import_log


def test_import_log_table_has_tenant_id_default(tmp_path, monkeypatch):
    il = _fresh_import_log(tmp_path, monkeypatch)
    con = il.connect()
    try:
        for table in IMPORT_LOG_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_import_log_log_then_recent_excludes_tenant_and_stamps_default(tmp_path, monkeypatch):
    """log() (explicit-column INSERT) stamps 'default'; recent() (SELECT *) surfaces
    the row dict WITHOUT tenant_id (the /imports template consumes these by key)."""
    il = _fresh_import_log(tmp_path, monkeypatch)
    rid = il.log("upload", "x.pdf", "received", actor="tester", supplier="DEMO")
    assert rid is not None
    rows = il.recent()
    assert rows and rows[0]["source_name"] == "x.pdf"
    assert "tenant_id" not in rows[0], "recent() leaked tenant_id"
    con = il.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM import_log").fetchone()[0] == "default"
    finally:
        con.close()


def test_import_log_idempotent_second_connect(tmp_path, monkeypatch):
    il = _fresh_import_log(tmp_path, monkeypatch)
    il.connect().close()
    monkeypatch.setattr(il, "_READY", set())
    con = il.connect()   # must NOT raise
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(import_log)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()


# ── waiting_room.py → intake.db ────────────────────────────────────────────────

INTAKE_TABLES = ["intake_jobs", "supplier_rate_limits", "supplier_rate_state"]


def _fresh_waiting_room(tmp_path, monkeypatch):
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    return waiting_room


def test_intake_tables_have_tenant_id_default(tmp_path, monkeypatch):
    iq = _fresh_waiting_room(tmp_path, monkeypatch)
    con = iq.connect()
    try:
        for table in INTAKE_TABLES:
            _assert_tenant_col(con, table)
    finally:
        con.close()


def test_intake_health_samples_is_unstamped(tmp_path, monkeypatch):
    """The DLQ-size health-sample table is GLOBAL queue telemetry, not tenant data,
    so it deliberately carries NO tenant_id (a judgment call recorded by this test)."""
    iq = _fresh_waiting_room(tmp_path, monkeypatch)
    con = iq.connect()
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(intake_health_samples)").fetchall()]
        assert "tenant_id" not in cols
    finally:
        con.close()


def test_intake_enqueue_stamps_default_and_get_job_excludes_tenant(tmp_path, monkeypatch):
    """A representative enqueue lands with tenant_id='default' on the stored row,
    while get_job()/jobs() (SELECT* dict contract) surface the row WITHOUT tenant_id
    (the monitoring panel consumes these dicts by key)."""
    iq = _fresh_waiting_room(tmp_path, monkeypatch)
    jid, _ = iq.enqueue(b"%PDF-1.4 demo", "demo.pdf", backend="DEMO", period="2026-05")
    job = iq.get_job(jid)
    assert job["status"] == "queued" and "tenant_id" not in job
    assert all("tenant_id" not in j for j in iq.jobs())
    con = iq.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM intake_jobs WHERE id=?", (jid,)).fetchone()[0] \
            == "default"
    finally:
        con.close()


def test_intake_rate_limit_row_stamps_default_and_get_excludes_tenant(tmp_path, monkeypatch):
    """set_supplier_limit() stamps both the limit + state rows with tenant_id='default';
    get_supplier_limit() (SELECT* dict contract) excludes it, and the resolved dict
    keeps its existing keys (behavior-preserving for the limiter config API)."""
    iq = _fresh_waiting_room(tmp_path, monkeypatch)
    resolved = iq.set_supplier_limit("X", max_concurrent=1, min_interval_s=0)
    assert "tenant_id" not in resolved
    got = iq.get_supplier_limit("X")
    assert got["max_concurrent"] == 1 and got["enabled"] == 1
    assert "tenant_id" not in got, "get_supplier_limit leaked tenant_id"
    con = iq.connect()
    try:
        assert con.execute(
            "SELECT tenant_id FROM supplier_rate_limits WHERE supplier='X'"
        ).fetchone()[0] == "default"
        assert con.execute(
            "SELECT tenant_id FROM supplier_rate_state WHERE supplier='X'"
        ).fetchone()[0] == "default"
    finally:
        con.close()


def test_intake_claim_and_record_outcome_still_behave(tmp_path, monkeypatch):
    """ZERO-REGRESSION proof for the machinery next to the new column: _claim still
    takes a governed supplier's job and stamps the limiter state, and record_outcome
    still drives the breaker — neither references tenant_id."""
    iq = _fresh_waiting_room(tmp_path, monkeypatch)
    iq.set_supplier_limit("X", max_concurrent=5, min_interval_s=0, breaker_threshold=2)
    jid, _ = iq.enqueue(b"%PDF-1.4 x1", "x1.pdf", backend="X")
    con = iq.connect()
    try:
        row = iq._claim(con)
        assert row is not None and row["id"] == jid
        # the governed claim stamped last_start_at on the state row
        assert con.execute(
            "SELECT last_start_at FROM supplier_rate_state WHERE supplier='X'"
        ).fetchone()[0] is not None
    finally:
        con.close()
    # breaker still trips after breaker_threshold consecutive failures
    iq.record_outcome("X", ok=False)
    iq.record_outcome("X", ok=False)
    assert iq.breaker_state("X")["open"] is True
    iq.record_outcome("X", ok=True)
    assert iq.breaker_state("X")["open"] is False


def test_intake_idempotent_second_connect(tmp_path, monkeypatch):
    iq = _fresh_waiting_room(tmp_path, monkeypatch)
    iq.connect().close()
    iq._SCHEMA_READY.clear()
    con = iq.connect()   # must NOT raise
    try:
        cols = [r["name"] for r in
                con.execute("PRAGMA table_info(intake_jobs)").fetchall()]
        assert cols.count("tenant_id") == 1
    finally:
        con.close()
