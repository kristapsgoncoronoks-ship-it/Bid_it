"""The one garbage-collector setting this service makes, and the measurement
behind it (PERF-016, 2026-09-06).

WHAT WAS MEASURED
-----------------
CI #545's growth gate (R15) failed on `transport_reliability` at 12.24× for 4×
the data, forty minutes after CI #544 read 7.47× on the SAME commit; three
local runs read 4.25×, 6.19×, 6.21×. The endpoint had not changed since
2026-08-27. Profiling the service call alone, at 2,387 rows, with the cyclic
collector instrumented:

    tracked objects after importing the app        335,660
    gen-2 collection, imported heap                131–146 ms EACH
    report(): p50 47 ms, p95 203 ms (gen-2 fired in 6 of 24 calls)
    report() at 600 rows: p50 16 ms, p95 17 ms (fired in 1 of 24)
    → p95 ratio 11.8× — the CI failure, reproduced without HTTP

    after gc.freeze() of that heap:
    gen-2 collection                               0.6–1.6 ms each
    report(): p50 47 ms, p95 53 ms; at 600 rows p50 16, p95 18
    → p95 ratio 2.87×; p50 ratio unchanged at 3.0×

So the tail was never the endpoint's: a full collection walks every object the
process holds — modules, mappers, routers, schemas — and its cost scales with
the size of THAT heap, while its frequency scales with allocation, i.e. with
how many rows a request hydrates. Any read that materialises a few thousand ORM
rows therefore pays a ~140 ms stop-the-world stall about every third call, per
worker, and under concurrency that stall lands on every request in flight.

WHAT `freeze_startup_heap` DOES
-------------------------------
`gc.freeze()` (CPython ≥ 3.7) moves everything currently tracked into a
permanent generation the collector never walks again. Called once, at the end
of startup, it parks the static object graph — which never becomes garbage —
so later collections traverse only what the process allocated since boot. It
adds no dependency, changes no behaviour, and is the documented use of the API
(a large long-lived heap, frozen before serving). A `gc.collect()` runs first
so nothing already dead is parked for the life of the process.

Called from `app.main.lifespan` (what uvicorn runs, per worker) and from
`scripts/perf_harness.py` before it measures — httpx's ASGITransport runs no
lifespan, and a harness that measured the unfrozen heap would keep reporting a
tail production does not have.
"""

from __future__ import annotations

import gc
import logging

log = logging.getLogger("invoiceiq")


def freeze_startup_heap() -> int:
    """Park every object alive right now in the permanent generation.

    Returns how many objects are frozen in total afterwards, for the startup
    log. Idempotent in effect: calling it again freezes whatever was allocated
    in between, which is harmless — nothing frozen is ever collected, so call
    it only once startup work is done.
    """
    gc.collect()
    gc.freeze()
    frozen = gc.get_freeze_count()
    log.info("Startup heap frozen: %d objects parked outside the cyclic collector", frozen)
    return frozen


__all__ = ["freeze_startup_heap"]
