# `docs/perf/` — recorded performance baselines

One file per recorded baseline, named `BASELINE-<date>.md`. Baselines are
**added, never overwritten**: the point of keeping the old one is being able to
say what changed and when, which a single file that is edited in place cannot
do.

| Baseline | Scope | Headline |
|---|---|---|
| [`BASELINE-2026-08-27.md`](BASELINE-2026-08-27.md) | Six read paths at 400 / 5,000 / 20,000 rows per fact table, Xeon 2.80 GHz × 4, Postgres 16.13 | Closes audit item **R15** (load + large dataset). The whole-history median walk §3.5 worried about grew **17× across 50× of data** — sub-linear. The fastest-growing read is the analytics `explore` group-by at 24.9×. |

## What belongs in a baseline file

A baseline is only useful if a later reader can tell whether their numbers are
comparable to it, so each one records:

1. **the machine** — CPU, memory, database version — because a millisecond
   figure means nothing without it;
2. **the numbers**, per scenario per dataset size;
3. **what they mean**, in prose, including which endpoint is the one to watch;
4. **what is NOT covered**, stated rather than left to be assumed covered.

## Producing one

```
make perf       PERF_URL=postgresql+asyncpg://user:pw@host:port/db SCALE=400
make perf-shape PERF_URL=postgresql+asyncpg://user:pw@host:port/db SCALE=2000
```

The URL must name a **migrated Postgres** database — the harness refuses SQLite
rather than print a comfortable figure about an engine this product never runs
on. The harness is `backend/scripts/perf_harness.py`; the gate that consumes it
is `backend/tests/test_perf_shape.py`.

## Why the gate is a ratio and not a stopwatch

An absolute budget in milliseconds has to be re-tuned for every machine it ever
runs on, and the tuning always drifts upward until it stops meaning anything. A
**growth ratio** — how much slower does this get when the data quadruples —
survives being measured somewhere else, because a slow runner inflates both
halves and cancels. It also happens to be the question R15 actually asked.
