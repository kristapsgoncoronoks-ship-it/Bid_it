"""PERF-016 — the startup heap is frozen before the service serves, and the
perf harness measures that same configuration.

CI #545 failed the R15 growth gate at 12.24× on `transport_reliability`; CI
#544 had read 7.47× on the same commit. Profiling put the tail on the cyclic
garbage collector: a gen-2 pass over the imported app (335,660 tracked
objects) costs ~140 ms and fires about every third request that hydrates a
few thousand ORM rows. Frozen, the same pass costs ~1 ms and the ratio reads
2.87×. Three things must stay true for that to hold:

1. `freeze_startup_heap` really parks the live heap (functional).
2. The app's lifespan calls it LAST — after every import and every startup
   write — so nothing hot is left outside the permanent generation and
   nothing dead is parked in it (structural, on the source).
3. The harness calls it before measuring, in BOTH modes, because
   ASGITransport runs no lifespan and an unfrozen measurement would report a
   tail production does not have (structural, on the source).
"""

from __future__ import annotations

import gc
import re
from pathlib import Path

from app.core import gc_tuning

BACKEND = Path(__file__).resolve().parents[1]
MAIN = BACKEND / "app" / "main.py"
HARNESS = BACKEND / "scripts" / "perf_harness.py"


def test_freeze_parks_the_live_heap_in_the_permanent_generation():
    before = gc.get_freeze_count()
    keep = [{"i": i} for i in range(1000)]  # tracked containers alive across the call
    try:
        total = gc_tuning.freeze_startup_heap()
        assert total == gc.get_freeze_count()
        assert total - before >= len(keep), (
            f"froze {total - before} objects, fewer than the {len(keep)} alive containers"
        )
    finally:
        # Do not leave the test process partially frozen for the rest of the
        # suite: thaw back to exactly the state the test found.
        gc.unfreeze()
        if before:
            gc.freeze()  # pragma: no cover — only when a prior test froze something
    del keep


def test_the_lifespan_freezes_the_heap_after_every_startup_write():
    src = MAIN.read_text(encoding="utf-8")
    assert "from app.core.gc_tuning import freeze_startup_heap" in src
    lifespan = src[src.index("async def lifespan") : src.index("yield")]
    freeze_at = lifespan.index("freeze_startup_heap()")
    # After the schema step and the ECB seeding, before the "ready" line and the
    # yield — the last thing startup does.
    assert lifespan.index("create_all") < freeze_at
    assert lifespan.index("ensure_european_coverage") < freeze_at
    assert freeze_at < lifespan.index("ready (")


def test_the_harness_measures_the_frozen_configuration_in_both_modes():
    src = HARNESS.read_text(encoding="utf-8")
    assert "freeze_startup_heap" in src
    # Once per process, like a worker — `shape` runs `run` twice.
    assert "if gc.get_freeze_count() == 0:" in src
    for mode in ("async def run(", "async def concurrency("):
        body = src[src.index(mode) :]
        body = body[: re.search(r"\n(async )?def ", body[1:]).start() + 1]
        head = body[: body.index("AsyncClient(")]
        assert "from app.main import app" in head, mode
        assert "_freeze_like_production()" in head, f"{mode} measures without the freeze"
        # …and the freeze comes AFTER the app import, i.e. after the heap it is
        # meant to park exists — and BEFORE the workspace is seeded, so the
        # seed's garbage is never parked with it.
        assert head.index("from app.main import app") < head.index("_freeze_like_production()")
        seeded_at = body.index("await _prepare_workspace(client, scale)")
        collected_at = body.index("_collect_seed_garbage()")
        assert seeded_at < collected_at, f"{mode} measures with the seed's garbage still pending"


def test_the_growth_table_carries_the_median_beside_the_gated_tail():
    """The signal that separated PERF-016 from a real regression was the p50
    ratio disagreeing with the p95 ratio. The harness prints and records both;
    the gate stays on p95."""
    src = HARNESS.read_text(encoding="utf-8")
    assert re.search(r"^\s+p50_ratio: float", src, re.M)
    assert "p50 ratio" in src
    gate = src[src.index("async def shape(") : src.index("def _print_shape")]
    assert "within_ceiling=None if ceiling is None else ratio <= ceiling" in gate
    assert "p50_ratio <= ceiling" not in gate
