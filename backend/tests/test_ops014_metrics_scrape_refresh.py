"""OPS-014 — the queue gauges are computed for the scrape that asks.

`metrics.set_queue_metrics` writes the default Prometheus registry of the
PROCESS that calls it, and this deployment runs no multiprocess mode. Every
uvicorn worker therefore held its own copy of `invoiceiq_jobs`,
`invoiceiq_jobs_oldest_pending_seconds` and `invoiceiq_jobs_dead{kind}`,
showing whatever that worker last computed — whenever it last served
`/health/queue`. A scrape hit one worker at random; the worker PROCESS, which
refreshes every tick, serves no HTTP and is never scraped.

The scrape now refreshes first. These tests pin the three properties that makes
it safe: it really recomputes, a database that cannot answer does NOT take
`/metrics` down with it, and the staleness of a failed refresh is visible
rather than silent.
"""

from __future__ import annotations

import pytest

from app.core import metrics


def _sample(name: str, labels: dict[str, str] | None = None) -> float | None:
    """Read one value straight out of the default registry."""
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels or {})


@pytest.mark.asyncio
async def test_a_scrape_recomputes_the_queue_gauges(auth_client, monkeypatch):
    """The headline: the numbers belong to the moment the scrape asked.

    The refresh is exercised through a STUBBED `queue_health.snapshot` rather
    than the real one, because `/metrics` deliberately opens its own session
    from the global `SessionLocal` — in production that is the real database,
    but in this harness it is not the one the `db_session` fixture writes to,
    so a scrape cannot see rows a test just inserted. (Asserting through that
    gap is what made the first draft of this test pass alone and fail in the
    full suite.) What matters here is the WIRING: a scrape calls the refresh,
    and whatever the refresh publishes replaces the stale values."""
    from app.services import queue_health

    calls: list[str] = []

    async def _fresh(db, **kw):
        calls.append("snapshot")
        metrics.set_queue_metrics({"queued": 7}, 12.0, {})
        return None

    # A stale value, as a worker registry that last computed hours ago would hold.
    metrics.set_queue_metrics({"queued": 999}, 4242.0, {})
    assert _sample("invoiceiq_jobs", {"status": "queued"}) == 999

    monkeypatch.setattr(queue_health, "snapshot", _fresh)
    resp = await auth_client.get("/metrics")
    assert resp.status_code == 200

    assert calls == ["snapshot"], "the scrape must refresh exactly once"
    assert _sample("invoiceiq_jobs", {"status": "queued"}) == 7, "the stale 999 must be replaced"
    assert _sample("invoiceiq_jobs_oldest_pending_seconds") == 12.0


@pytest.mark.asyncio
async def test_a_database_that_cannot_answer_does_not_break_the_scrape(auth_client, monkeypatch):
    """The property the whole design turns on.

    Metrics matter MOST when the database is unwell, so a refresh that fails
    must serve the previous values rather than a 500 — which would also take
    the HTTP counters and every other gauge with it, blinding the dashboard at
    exactly the wrong moment."""
    from app.services import queue_health

    async def _boom(*a, **kw):
        raise RuntimeError("database is not answering")

    monkeypatch.setattr(queue_health, "snapshot", _boom)

    resp = await auth_client.get("/metrics")
    assert resp.status_code == 200, "a failed refresh must not fail the scrape"
    assert "invoiceiq_jobs" in resp.text, "the previously-published gauges must still be served"


@pytest.mark.asyncio
async def test_a_failed_refresh_leaves_the_staleness_visible(auth_client, monkeypatch):
    """A best-effort refresh is only honest if its failure is observable.

    The timestamp must NOT advance when the refresh raised, so an operator can
    alert on its age instead of trusting a number nobody recomputed."""
    from app.services import queue_health

    async def _fresh(db, **kw):
        metrics.set_queue_metrics({"queued": 1}, 0.0, {})
        return None

    monkeypatch.setattr(queue_health, "snapshot", _fresh)
    await auth_client.get("/metrics")  # one GOOD scrape sets the timestamp
    good = _sample("invoiceiq_queue_metrics_updated_timestamp_seconds")
    assert good is not None and good > 0

    async def _boom(*a, **kw):
        raise RuntimeError("database is not answering")

    monkeypatch.setattr(queue_health, "snapshot", _boom)
    resp = await auth_client.get("/metrics")
    assert resp.status_code == 200

    after = _sample("invoiceiq_queue_metrics_updated_timestamp_seconds")
    assert after == good, "a failed refresh must not claim the gauges are current"


def test_the_refresh_is_injected_rather_than_imported_by_core():
    """`core` must not know about `services` (test_boundaries), so the refresh
    is a callable `app.main` passes in. If someone later imports queue_health
    inside observability.py the boundary test bites — this asserts the seam
    that keeps it from being tempting."""
    import inspect

    from app.core import observability

    sig = inspect.signature(observability.setup_metrics)
    assert "before_scrape" in sig.parameters, "the scrape hook is the seam core exposes"
    src = inspect.getsource(observability)
    assert "queue_health" not in src, "core must not reach into services"
