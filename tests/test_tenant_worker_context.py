"""Multi-tenant P2 — worker tenant-context (audit finding G2).

Two halves under the `multitenant` switch:
  1. ENQUEUE stamps the enqueuing tenant onto the job row (tenancy.queue_tenant()).
  2. The WORKER re-binds that stamped tenant per job (mirrors the audit-actor
     set/reset) so the engine reads its handlers trigger scope correctly once
     those modules are P2-wired — and resets the context afterward.

CARDINAL PROPERTY: OFF (default) = byte-identical. queue_tenant() -> 'default',
set_tenant('default') with the switch OFF is inert (scope_clause -> ("", [])).
"""
import importlib

import pytest


@pytest.fixture()
def iq(tmp_path, monkeypatch):
    """A reloaded waiting_room pointed at a throwaway intake.db + a throwaway
    import_log.db, with auth/tenancy pointed at a throwaway security.db so the
    `multitenant` switch can be armed in isolation. Leaves the switch OFF and the
    thread context inert; resets the tenant context on teardown."""
    import auth
    import tenancy
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security.db"))
    monkeypatch.setattr(auth, "_SCHEMA_READY", set())
    monkeypatch.setattr(tenancy, "_SCHEMA_READY", set())

    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()

    import import_log
    importlib.reload(import_log)
    monkeypatch.setattr(import_log, "DB", str(tmp_path / "import_log.db"))
    import_log._READY.clear()

    try:
        yield waiting_room
    finally:
        tenancy.reset_tenant()


def _on():
    import auth, tenancy
    auth.set_setting("multitenant", "1")
    assert tenancy.multitenant_enabled() is True


def _job_tenant(iq, jid):
    """Read the RAW tenant_id column off the job row. get_job() deliberately drops
    tenant_id from its surfaced contract (_drop_tenant), so the stamp is inspected
    directly against the DB."""
    con = iq.connect()
    try:
        r = con.execute("SELECT tenant_id FROM intake_jobs WHERE id=?", (jid,)).fetchone()
    finally:
        con.close()
    return r["tenant_id"] if r else None


# ── queue_tenant() contract ──────────────────────────────────────────────────

def test_queue_tenant_off_is_default(iq):
    import tenancy
    assert tenancy.multitenant_enabled() is False
    tenancy.set_tenant("A")                       # bound but switch OFF
    assert tenancy.queue_tenant() == tenancy.DEFAULT_TENANT_ID
    tenancy.reset_tenant()


def test_queue_tenant_on_bound_is_that_tenant(iq):
    import tenancy
    _on()
    tenancy.set_tenant("A")
    assert tenancy.queue_tenant() == "A"
    tenancy.reset_tenant()


def test_queue_tenant_on_unbound_is_default_never_raises(iq):
    import tenancy
    _on()
    tenancy.reset_tenant()                        # ON, no tenant (scheduler/system)
    assert tenancy.queue_tenant() == tenancy.DEFAULT_TENANT_ID


# ── ENQUEUE stamps the bound tenant ──────────────────────────────────────────

def test_enqueue_stamps_bound_tenant_when_on(iq):
    import tenancy
    _on()
    tenancy.set_tenant("A")
    jr, _ = iq.enqueue_registration(
        {"supplier": "DEMO", "statement_ref": "S1", "period": "2026-01",
         "lines": []}, user="amy")
    jc, _ = iq.enqueue_close("2026-01", user="amy")
    jf, _ = iq.enqueue_fetch("demo", "EntA", user="amy")
    tenancy.reset_tenant()

    assert _job_tenant(iq, jr) == "A"
    assert _job_tenant(iq, jc) == "A"
    assert _job_tenant(iq, jf) == "A"


def test_enqueue_unbound_is_default_when_on(iq):
    import tenancy
    _on()
    tenancy.reset_tenant()                        # system/scheduler enqueue, no tenant
    jr, _ = iq.enqueue_registration(
        {"supplier": "DEMO", "statement_ref": "S2", "lines": []}, user="scheduler")
    jf, _ = iq.enqueue_fetch("demo", "EntB", user="scheduler")
    assert _job_tenant(iq, jr) == tenancy.DEFAULT_TENANT_ID
    assert _job_tenant(iq, jf) == tenancy.DEFAULT_TENANT_ID


def test_enqueue_off_is_default(iq):
    import tenancy
    assert tenancy.multitenant_enabled() is False
    tenancy.set_tenant("A")                       # bound but switch OFF -> inert
    jc, _ = iq.enqueue_close("2026-02", user="amy")
    tenancy.reset_tenant()
    assert _job_tenant(iq, jc) == tenancy.DEFAULT_TENANT_ID


# ── a re-queued job keeps its ORIGINAL tenant (the UPDATE branch doesn't touch it) ─

def test_requeue_keeps_original_tenant(iq):
    import tenancy
    _on()
    # First enqueue as tenant A.
    tenancy.set_tenant("A")
    jid, _ = iq.enqueue_fetch("demo", "EntA", user="amy")
    tenancy.reset_tenant()
    assert _job_tenant(iq, jid) == "A"

    # Re-confirm the SAME fetch while bound to tenant B -> hits the UPDATE branch.
    tenancy.set_tenant("B")
    jid2, st2 = iq.enqueue_fetch("demo", "EntA", user="bob")
    tenancy.reset_tenant()
    assert jid2 == jid and st2 == "queued"        # same row re-queued, not a dup
    # The re-queue UPDATE must LEAVE tenant_id unchanged: still A, not B.
    assert _job_tenant(iq, jid) == "A"
    assert iq.get_job(jid)["uploaded_by"] == "bob"   # payload/actor DID refresh


# ── WORKER binds the job's tenant per job, then resets ───────────────────────

def test_worker_binds_tenant_for_fetch(iq, monkeypatch):
    import tenancy, portal_scraper
    _on()
    iq.set_supplier_limit("DEMO", max_concurrent=5, min_interval_s=0)
    seen = {}

    def stub_scrape(supplier, entity, date_from=None, date_to=None):
        seen["tenant"] = tenancy.current_tenant()    # record the bound tenant
        return {"supplier": supplier, "entity": entity, "fetched": 1, "loaded": 1}
    monkeypatch.setattr(portal_scraper, "scrape", stub_scrape)

    tenancy.set_tenant("A")
    jid, _ = iq.enqueue_fetch("demo", "EntA", user="amy")
    tenancy.reset_tenant()                        # worker must re-bind from the row

    assert iq.process_one() == (jid, "done")
    assert seen["tenant"] == "A"                  # the worker bound the job's tenant
    assert tenancy.current_tenant() is None       # and reset it afterward


def test_worker_binds_tenant_for_close(iq, monkeypatch):
    import tenancy
    import engine_close
    _on()
    seen = {}

    def stub_close(period, actor=None):
        seen["tenant"] = tenancy.current_tenant()
        return {"period": period}
    monkeypatch.setattr(engine_close, "close", stub_close)

    tenancy.set_tenant("A")
    jid, _ = iq.enqueue_close("2026-03", user="amy")
    tenancy.reset_tenant()

    assert iq.process_one() == (jid, "done")
    assert seen["tenant"] == "A"
    assert tenancy.current_tenant() is None


def test_worker_binds_tenant_for_register(iq, monkeypatch):
    import tenancy
    import invoice_control
    _on()
    seen = {}

    def stub_register(supplier, statement_ref, period, statement_date, lines,
                      notes=None, customer=None):
        seen["tenant"] = tenancy.current_tenant()
        return 1
    monkeypatch.setattr(invoice_control, "register_statement", stub_register)

    tenancy.set_tenant("A")
    jid, _ = iq.enqueue_registration(
        {"supplier": "DEMO", "statement_ref": "S9", "period": "2026-03",
         "lines": []}, user="amy")
    tenancy.reset_tenant()

    assert iq.process_one() == (jid, "done")
    assert seen["tenant"] == "A"
    assert tenancy.current_tenant() is None


def test_worker_resets_tenant_on_failure(iq, monkeypatch):
    """The reset is in the SAME finally as audit.reset_actor(): even when the inner
    call raises, the context is cleared (no leak across jobs/threads)."""
    import tenancy, portal_scraper
    _on()
    iq.set_supplier_limit("DEMO", max_concurrent=5, min_interval_s=0,
                          breaker_threshold=5)
    monkeypatch.setattr(iq, "BACKOFF_BASE", 0)
    monkeypatch.setattr(iq, "BACKOFF_MAX", 0)

    def boom(supplier, entity, date_from=None, date_to=None):
        assert tenancy.current_tenant() == "A"   # bound at call time
        raise RuntimeError("portal login refused")
    monkeypatch.setattr(portal_scraper, "scrape", boom)

    tenancy.set_tenant("A")
    jid, _ = iq.enqueue_fetch("demo", "EntA", user="amy")
    tenancy.reset_tenant()

    assert iq.process_one() == (jid, "retry")    # failed/re-queued
    assert tenancy.current_tenant() is None       # context still reset


# ── OFF regression: worker runs with no tenant bound (inert) ──────────────────

def test_worker_off_runs_with_no_tenant_bound(iq, monkeypatch):
    import tenancy, portal_scraper
    assert tenancy.multitenant_enabled() is False
    iq.set_supplier_limit("DEMO", max_concurrent=5, min_interval_s=0)
    seen = {}

    def stub_scrape(supplier, entity, date_from=None, date_to=None):
        # OFF: the job stamps 'default'; set_tenant('default') binds it but the
        # switch is OFF so scope_clause is inert. current_tenant() reflects the bind.
        seen["tenant"] = tenancy.current_tenant()
        return {"supplier": supplier, "entity": entity, "fetched": 1, "loaded": 1}
    monkeypatch.setattr(portal_scraper, "scrape", stub_scrape)

    jid, _ = iq.enqueue_fetch("demo", "EntA", user="amy")
    assert iq.process_one() == (jid, "done")
    assert seen["tenant"] == tenancy.DEFAULT_TENANT_ID
    assert tenancy.current_tenant() is None
