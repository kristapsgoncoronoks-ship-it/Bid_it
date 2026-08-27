"""The performance gate (WO-R, audit item R15).

Two layers, and they cost very different amounts:

1. **Structural**, always on. The harness's own contract: every measured
   scenario carries both budgets, the SQLite refusal is real, and no scenario
   can be added without declaring what "too slow" and "grew too fast" mean for
   it. These run in the default SQLite suite in milliseconds.

2. **Measured**, needs a Postgres. The real thing: seed a dataset at scale S and
   at scale 4·S against a MIGRATED Postgres and assert each endpoint's p95 did
   not grow faster than its ceiling. It runs in CI's `postgres` job against the
   same database as the RLS gates (~17 seconds at `PERF_TEST_SCALE=1200`); it
   skips anywhere `PERF_TEST_DATABASE_URL` is unset, which is the default
   SQLite suite.

Why the measured layer runs the harness as a SUBPROCESS rather than importing
it: `settings.database_url` is read at import time, so a test that wanted a
Postgres URL would have to poison the whole suite's configuration to get one —
and running the default suite against Postgres produces dialect artifacts that
have already cost this project a bad certification once. A subprocess gets its
own environment, its own settings, and cannot contaminate the parent run.

WHY A GROWTH RATIO AND NOT A MILLISECOND BUDGET is argued in the harness
docstring; the short version is that a ratio survives being measured on a
different machine and a millisecond figure does not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
HARNESS = BACKEND / "scripts" / "perf_harness.py"

PERF_URL = os.environ.get("PERF_TEST_DATABASE_URL")
perf_only = pytest.mark.skipif(
    not PERF_URL,
    reason="set PERF_TEST_DATABASE_URL (a MIGRATED Postgres URL) to run the perf gate",
)


def _harness():
    """Import the harness by path — `scripts/` is not an importable package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_perf_harness", HARNESS)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves a field's annotation through
    # `sys.modules[cls.__module__]`, which is None for a module loaded by path
    # and never registered.
    sys.modules["_perf_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Layer 1 — structural. Always runs.
# --------------------------------------------------------------------------- #


def test_every_scenario_declares_both_kinds_of_too_slow():
    """A scenario without a budget and a ceiling is measured but not GATED, and a
    measurement nobody checks is a number in a log file. Adding a scenario must
    force the author to say what too-slow and grew-too-fast mean for it."""
    h = _harness()
    names = [name for name, _ in h.SCENARIOS]
    assert len(names) == len(set(names)), f"duplicate scenario name: {names}"
    missing_budget = [n for n in names if n not in h.BUDGETS_MS]
    missing_ceiling = [n for n in names if n not in h.GROWTH_CEILING]
    assert not missing_budget, f"scenarios with no p95 budget: {missing_budget}"
    assert not missing_ceiling, f"scenarios with no growth ceiling: {missing_ceiling}"
    # …and nothing budgeted that is never measured, which would read as coverage
    # the harness does not have.
    assert not set(h.BUDGETS_MS) - set(names), (
        f"budget for no scenario: {set(h.BUDGETS_MS) - set(names)}"
    )
    assert not set(h.GROWTH_CEILING) - set(names), (
        f"ceiling for no scenario: {set(h.GROWTH_CEILING) - set(names)}"
    )


def test_no_ceiling_is_loose_enough_to_admit_a_quadratic_endpoint():
    """The whole point of the growth gate is that O(n²) fails it. At
    SHAPE_FACTOR=4 a quadratic endpoint grows 16×, so a ceiling at or above 16
    would let the exact regression R15 is about walk straight through."""
    h = _harness()
    quadratic = h.SHAPE_FACTOR**2
    too_loose = {n: c for n, c in h.GROWTH_CEILING.items() if c >= quadratic}
    assert not too_loose, (
        f"these ceilings would pass a quadratic endpoint ({quadratic}× at "
        f"{h.SHAPE_FACTOR}× data): {too_loose}"
    )


def test_the_harness_refuses_to_measure_on_sqlite():
    """Not a style preference: SQLite and Postgres plan the whole-history scan in
    §3.5 differently, so a green number from SQLite would be an answer to a
    question nobody asked. The refusal is executed here, not assumed."""
    proc = subprocess.run(
        [sys.executable, str(HARNESS), "--scale", "1", "--reps", "1"],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///./perf-should-never-run.db"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode != 0, "the harness measured SQLite instead of refusing"
    assert "MIGRATED Postgres" in (proc.stdout + proc.stderr)
    assert not (BACKEND / "perf-should-never-run.db").exists(), (
        "the harness touched a SQLite file before refusing"
    )


def test_the_percentile_is_a_measurement_that_actually_happened():
    """Nearest-rank, no interpolation — a reported p95 must be one of the
    observed timings, so a figure in `docs/perf/` is always something the
    product did rather than something arithmetic invented between two things it
    did."""
    h = _harness()
    values = [10.0, 20.0, 30.0, 40.0, 100.0]
    assert h._pct(values, 95) in values
    assert h._pct(values, 50) in values
    assert h._pct([], 95) == 0.0
    # At the default repetition count the p95 must NOT collapse onto the max,
    # or every cold-start blip is reported as a tail.
    ordered = [float(i) for i in range(h.DEFAULT_REPS)]
    assert h._pct(ordered, 95) < max(ordered)


# --------------------------------------------------------------------------- #
# Layer 2 — measured. Opt-in via PERF_TEST_DATABASE_URL.
# --------------------------------------------------------------------------- #


@perf_only
def test_no_endpoint_grows_faster_than_its_data():
    """R15, answered by measurement: quadruple the dataset and no read may slow
    down by more than its declared ceiling."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "shape.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--shape",
                "--scale",
                os.environ.get("PERF_TEST_SCALE", "2000"),
                "--json",
                str(out),
            ],
            cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": PERF_URL},
            capture_output=True,
            text=True,
            timeout=3600,
        )
        assert out.exists(), f"harness produced no result\n{proc.stdout}\n{proc.stderr}"
        growth = json.loads(out.read_text())

    over = [g for g in growth if g["within_ceiling"] is False]
    assert not over, "endpoints grew faster than their ceiling allows: " + ", ".join(
        f"{g['name']} {g['ratio']}× (ceiling {g['ceiling']}×, "
        f"{g['small_p95_ms']}ms → {g['large_p95_ms']}ms)"
        for g in over
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
