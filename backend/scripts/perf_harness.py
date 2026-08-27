"""The load / large-dataset harness (WO-R, audit item R15).

WHAT R15 ACTUALLY ASKS FOR, AND WHAT THIS ANSWERS
---------------------------------------------------
`docs/RELEASE-READINESS.md` §3.5, verbatim: *"Performance is untested beyond
current fixture scale. `expected_rebate` loads a tenant's whole transaction
history into memory to learn medians — fine now, unmeasured at scale."* So the
requirement is two things, and the second is the one with teeth:

1. **latency** on the read paths a real workspace hits every day, and
2. **large-dataset behaviour** — what happens to a query that is O(history)
   when the history stops being a fixture.

This harness answers both by driving the REAL ASGI app over the REAL router
stack (auth, the tenant guard, RLS-shaped scoping, the service layer) against a
dataset it scales itself. Nothing is mocked: a number this prints is a number
the product produced.

WHY NO k6 AND NO LOCUST (a deliberate deviation from the plan)
----------------------------------------------------------------
`docs/plan/DEVELOPMENT-PLAN-2026-08.md` sized WO-R as *"k6 or locust"*. Both
were rejected after looking at what they would cost here:

- **k6** is a Go binary with no presence in this toolchain; CI would have to
  fetch and pin it, and it cannot drive an ASGI app in-process, so every run
  would need a live server plus a live database — a second, weaker environment
  to keep true.
- **locust** installs cleanly but drags `gevent`, `flask` and `werkzeug` into a
  backend that has added no dependency without cause all arc, and its worker
  model conflicts with an async SQLAlchemy session.

`httpx` + `asyncio` are already in `requirements.txt` and already drive every
API test in this suite. So the harness is dependency-free, runs anywhere the
tests run, and measures the same stack the tests measure. The deviation is
recorded in the plan rather than made quietly.

WHAT IT MEASURES, AND WHAT A NUMBER HERE MEANS
------------------------------------------------
Per scenario: p50, p95 and max wall-clock over N repetitions of one real HTTP
call, plus the dataset size that produced them. p95 is the budgeted figure
because a mean hides exactly the tail a user notices.

These are **relative** numbers, honest about it: a CI runner and a laptop are
different machines, so an absolute millisecond figure is only meaningful
against a baseline recorded on the same class of machine. What the budgets
catch is a REGRESSION — an endpoint that was linear becoming quadratic, or a
new N+1 — which is machine-independent in shape even when it is not in
magnitude. `docs/perf/` holds the recorded baseline and the machine it came
from.

POSTGRES ONLY, AND THAT IS THE POINT
--------------------------------------
`DATABASE_URL` must name a MIGRATED Postgres database. The SQLite harness the
unit suite uses is a different engine with a different planner: a number
measured there would describe a database this product never runs on, and the
one finding R15 actually cares about — a whole-history scan — is exactly the
shape whose cost differs most between the two. The harness refuses to run
against SQLite rather than print a comfortable, meaningless figure.

USAGE (from backend/, or `make perf` / `make perf-shape` from the repo root)
    DATABASE_URL=postgresql+asyncpg://user:pw@host:port/db \\
        python scripts/perf_harness.py                 # default scale
    ... --scale 5000                                   # large-dataset run
    ... --shape                                        # THE GATE: 4× the data,
                                                       #   cap the slowdown
    ... --json out.json                                # machine-readable

The recorded baseline, the machine it came from, and what the numbers mean live
in `docs/perf/`. The gate that consumes this is `tests/test_perf_shape.py`.
"""

# ruff: noqa: T201 — a CLI tool talks through stdout
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

#: The p95 ceiling per scenario, in milliseconds, at DEFAULT_SCALE on the
#: baseline machine. Deliberately generous — a millisecond figure is only
#: comparable against the same class of machine, so this is a SMOKE check
#: ("nothing became pathological"), not the real gate. The real gate is
#: GROWTH_CEILING below. Raise one only WITH a reason, in the same commit.
BUDGETS_MS: dict[str, float] = {
    "dashboard": 1500.0,
    "invoice_list": 1200.0,
    "ap_aging": 1500.0,
    "cash_position": 1500.0,
    "explore_by_vendor": 2000.0,
    "transport_reliability": 2500.0,
}

#: The REAL gate, and the one R15 actually asked for. Measure the same endpoint
#: at scale S and at scale S·SHAPE_FACTOR, and cap how much slower the big one
#: may be. This is MACHINE-INDEPENDENT in a way a millisecond budget is not: a
#: slow runner makes both numbers bigger and leaves the RATIO alone. A ceiling
#: of 8 against a 4× data increase says "grow linearly, with room for constant
#: overhead and noise — but a quadratic endpoint (16×) fails here."
#:
#: The recorded figures behind each ceiling are in `docs/perf/`. Where a
#: scenario already grows faster than its data, the ceiling says so out loud
#: rather than being quietly widened.
SHAPE_FACTOR = 4
GROWTH_CEILING: dict[str, float] = {
    # Windowed and indexed: barely notices the data.
    "dashboard": 4.0,
    # Paginated — page 1 of 50 costs the same whatever the table holds, modulo
    # the COUNT.
    "invoice_list": 5.0,
    "ap_aging": 4.0,
    "cash_position": 4.0,
    # A whole-table group-by with a sort — the fastest-growing of the analytics
    # reads. Measured 2.5–2.8× at 2k→8k and 6.3× at 5k→20k: superlinear, not
    # quadratic. The ceiling sits above the worst measurement, not at it.
    "explore_by_vendor": 9.0,
    # THE §3.5 SCENARIO: `expected_rebate` walking a supplier's whole history.
    # Across 50× of data (400 → 20,000 rows) the p95 grew 17× — SUBLINEAR, so
    # the fear the audit recorded is not realised. Per-4×-step it measured
    # between 2.6× and 5.9×, the spread coming from Postgres picking different
    # plans at different table sizes rather than from the algorithm. Hence a
    # ceiling of 8: comfortably above the observed spread, comfortably below the
    # 16× a genuinely quadratic walk would produce.
    "transport_reliability": 8.0,
}

DEFAULT_SCALE = 400
#: Enough repetitions that the p95 is not simply the max. Nearest-rank p95 of N
#: samples is the round(0.95·N)-th slowest, so at N=4 the "p95" IS the max and
#: reports a cold-start artifact as a tail; at N=12 it is the second-slowest,
#: which is a tail.
DEFAULT_REPS = 12
#: Discarded before measuring. The FIRST call to an endpoint pays for lazy
#: imports, the connection pool filling and Postgres planning a statement it has
#: never seen — real costs, but paid once per process, not once per user.
DEFAULT_WARMUP = 2


@dataclass
class ScenarioResult:
    name: str
    scale: int
    reps: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    status: int
    budget_ms: float | None
    within_budget: bool | None


def _pct(values: list[float], pct: float) -> float:
    """The pct-th percentile, nearest-rank — no interpolation, so a reported
    figure is always a measurement that actually happened."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered))) - 1))
    return ordered[k]


async def _seed_scale(db, org_id: str, entity_id: str, scale: int) -> None:
    """A dataset big enough to change an algorithm's mind.

    Deliberately SYNTHETIC and deliberately shaped: `scale` invoices spread
    over two years (so date-ranged reads cannot trivially scan nothing) and
    `scale` fuel transactions for ONE supplier (so `expected_rebate`'s
    whole-history median walk — the specific §3.5 concern — actually has a
    history to walk).
    """
    from app.models.base import new_uuid
    from app.models.invoice import Invoice, InvoiceStatus, LineItem
    from app.models.transport.fuel_transaction import FuelTransaction
    from app.models.vendor import Vendor

    vendors = [Vendor(org_id=org_id, name=f"Perf Vendor {i:03d}") for i in range(10)]
    db.add_all(vendors)
    await db.flush()

    today = date.today()
    # The id is assigned HERE rather than left to the column default, which
    # SQLAlchemy only applies at INSERT time. Naming it up front lets a line
    # item point at its invoice without a flush per row — the difference
    # between seeding 5,000 invoices in seconds and in minutes.
    for i in range(scale):
        v = vendors[i % len(vendors)]
        age = i % 730
        inv = Invoice(
            id=new_uuid(),
            org_id=org_id,
            vendor_id=v.id,
            invoice_number=f"PERF-{i:06d}",
            issue_date=today - timedelta(days=age),
            due_date=today - timedelta(days=age - 30),
            currency="EUR",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("21.00"),
            total=Decimal("121.00"),
            # A spread across the aging states, so ap-aging and cash-position
            # both have something to bucket rather than one degenerate column.
            status=(InvoiceStatus.paid if i % 3 == 0 else InvoiceStatus.pending),
            amount_paid=(Decimal("121.00") if i % 3 == 0 else Decimal("0.00")),
            paid_date=(today - timedelta(days=max(age - 10, 0)) if i % 3 == 0 else None),
        )
        db.add(inv)
        db.add(
            # No org_id: line_items is reachable only through its org-scoped
            # invoice, by design (see the model's note).
            LineItem(
                invoice_id=inv.id,
                description="Perf line",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                amount=Decimal("100.00"),
                tax_rate=Decimal("21.00"),
            )
        )
        if i % 500 == 0:
            await db.flush()
    await db.flush()

    for i in range(scale):
        d = today - timedelta(days=i % 730)
        db.add(
            FuelTransaction(
                org_id=org_id,
                entity_id=entity_id,
                supplier="PERFCARD",
                period=f"{d.year:04d}-{d.month:02d}",
                line_seq=i,
                country="LV",
                vehicle_ref=f"Perf-{i % 40:03d}",
                txn_date=d,
                station="Perf Station",
                product="DIESEL",
                product_group="Diesel",
                qty=Decimal("500.000"),
                currency="EUR",
                net_local=Decimal("700.00"),
                vat_local=Decimal("147.00"),
                gross_local=Decimal("847.00"),
                net_eur=Decimal("700.00"),
                vat_eur=Decimal("147.00"),
                net_eur_eff=Decimal("700.00"),
                fx_source="eur",
            )
        )
        if i % 200 == 0:
            await db.flush()
    await db.commit()


SCENARIOS: list[tuple[str, str]] = [
    ("dashboard", "/api/v1/dashboard"),
    ("invoice_list", "/api/v1/invoices?page=1&page_size=50"),
    ("ap_aging", "/api/v1/analytics/ap-aging"),
    ("cash_position", "/api/v1/analytics/cash-position"),
    ("explore_by_vendor", "/api/v1/analytics/explore?measure=net&dim=vendor"),
    ("transport_reliability", "/api/v1/transport/reliability"),
]


def require_postgres() -> None:
    """Refuse to measure on SQLite. See the module docstring."""
    from app.core.config import settings

    if "postgresql" not in settings.database_url:
        raise SystemExit(
            "perf_harness needs a MIGRATED Postgres DATABASE_URL — measuring on "
            "SQLite would describe an engine this product never runs on.\n"
            f"got: {settings.database_url.split('://')[0]}://…"
        )


async def run(scale: int, reps: int, warmup: int) -> list[ScenarioResult]:
    from httpx import ASGITransport, AsyncClient

    require_postgres()

    from app.core.database import SessionLocal
    from app.core.tenant import reset_current_org, set_current_org
    from app.main import app
    from app.models.issuer import IssuerProfile
    from app.services import modules as modules_svc

    # Every run gets its OWN workspace. Two reasons, and the second matters
    # more: a re-run must not fail on a duplicate email, and measuring inside a
    # database that already holds other tenants' rows is the honest case — the
    # tenant guard and the RLS predicates are part of what is being timed, and
    # they cost nothing to evaluate against an empty neighbourhood.
    suffix = uuid4().hex[:10]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://perf", timeout=120.0
    ) as client:
        reg = await client.post(
            "/api/v1/auth/register",
            json={
                "organization_name": f"Perf Workspace {suffix}",
                "name": "Perf Owner",
                "email": f"perf-{suffix}@invoiceiq.app",
                "password": "supersecret",
            },
        )
        assert reg.status_code == 201, reg.text
        client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
        org_id = reg.json()["organization"]["id"]

        # Seed AS the tenant. Without this the writes go out on a pooled
        # connection whose `app.current_org` GUC still names whichever org last
        # used it, and Postgres refuses the INSERT — which is RLS doing its job,
        # and is how the two-scale `--shape` mode found this in the first place.
        token = set_current_org(org_id)
        try:
            async with SessionLocal() as db:
                await modules_svc.set_enabled(db, org_id, "transport", True)
                entity = IssuerProfile(
                    org_id=org_id, name="Perf Entity", legal_name="Perf Entity OU"
                )
                db.add(entity)
                await db.commit()
                await db.refresh(entity)
                print(f"seeding {scale} invoices + {scale} fuel transactions…", flush=True)
                t0 = time.perf_counter()
                await _seed_scale(db, org_id, entity.id, scale)
                print(f"seeded in {time.perf_counter() - t0:.1f}s", flush=True)
        finally:
            reset_current_org(token)

        results: list[ScenarioResult] = []
        for name, path in SCENARIOS:
            timings: list[float] = []
            status = 0
            for _ in range(warmup):
                await client.get(path)
            for _ in range(reps):
                t = time.perf_counter()
                r = await client.get(path)
                timings.append((time.perf_counter() - t) * 1000)
                status = r.status_code
            # A fast 500 is not a fast endpoint. Refuse to report a timing for a
            # call that did not do the work.
            if status != 200:
                raise SystemExit(f"scenario {name!r} returned HTTP {status} for {path}")
            budget = BUDGETS_MS.get(name)
            p95 = _pct(timings, 95)
            results.append(
                ScenarioResult(
                    name=name,
                    scale=scale,
                    reps=reps,
                    p50_ms=round(statistics.median(timings), 1),
                    p95_ms=round(p95, 1),
                    max_ms=round(max(timings), 1),
                    status=status,
                    budget_ms=budget,
                    within_budget=None if budget is None else p95 <= budget,
                )
            )
        return results


@dataclass
class GrowthResult:
    name: str
    small_scale: int
    large_scale: int
    small_p95_ms: float
    large_p95_ms: float
    ratio: float
    ceiling: float | None
    within_ceiling: bool | None


async def shape(scale: int, factor: int, reps: int, warmup: int) -> list[GrowthResult]:
    """Measure the same endpoints at two dataset sizes and report the ratio.

    Each `run` seeds a NEW workspace, so the large measurement happens in a
    database that also holds the small run's rows. That is deliberate and it
    does not distort the ratio: every query in every scenario is org-scoped, so
    the neighbouring tenant costs an index predicate, not a scan — and if it
    ever stopped costing only that, this harness is exactly where it should
    show up.
    """
    small = {r.name: r for r in await run(scale, reps, warmup)}
    large = {r.name: r for r in await run(scale * factor, reps, warmup)}

    out: list[GrowthResult] = []
    for name, _path in SCENARIOS:
        s, big = small[name], large[name]
        # A sub-millisecond denominator would turn noise into a huge ratio;
        # floor it so the gate measures growth, not timer resolution.
        ratio = big.p95_ms / max(s.p95_ms, 1.0)
        ceiling = GROWTH_CEILING.get(name)
        out.append(
            GrowthResult(
                name=name,
                small_scale=s.scale,
                large_scale=big.scale,
                small_p95_ms=s.p95_ms,
                large_p95_ms=big.p95_ms,
                ratio=round(ratio, 2),
                ceiling=ceiling,
                within_ceiling=None if ceiling is None else ratio <= ceiling,
            )
        )
    return out


def _print_shape(results: list[GrowthResult], factor: int) -> int:
    print(
        f"\ngrowth in p95 for {factor}× the data "
        f"({results[0].small_scale} → {results[0].large_scale} rows per fact table)"
    )
    print(
        f"\n{'scenario':<24}{'small ms':>10}{'large ms':>10}{'ratio':>10}{'ceiling':>10}  verdict"
    )
    print("-" * 78)
    breached = 0
    for r in results:
        verdict = "—"
        if r.within_ceiling is not None:
            verdict = "OK" if r.within_ceiling else "GREW TOO FAST"
            breached += 0 if r.within_ceiling else 1
        ceiling = "—" if r.ceiling is None else f"{r.ceiling:.1f}"
        print(
            f"{r.name:<24}{r.small_p95_ms:>10.1f}{r.large_p95_ms:>10.1f}"
            f"{r.ratio:>10.2f}{ceiling:>10}  {verdict}"
        )
    if breached:
        print(f"\n{breached} scenario(s) grew faster than their ceiling allows.")
    return breached


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scale", type=int, default=DEFAULT_SCALE)
    ap.add_argument("--reps", type=int, default=DEFAULT_REPS)
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument(
        "--shape",
        action="store_true",
        help=(
            "measure at --scale and --scale × SHAPE_FACTOR and gate on the growth "
            "ratio instead of absolute milliseconds"
        ),
    )
    args = ap.parse_args()

    if args.shape:
        growth = asyncio.run(shape(args.scale, SHAPE_FACTOR, args.reps, args.warmup))
        breached = _print_shape(growth, SHAPE_FACTOR)
        if args.json:
            Path(args.json).write_text(json.dumps([asdict(g) for g in growth], indent=2))
            print(f"\nwrote {args.json}")
        return 1 if breached else 0

    results = asyncio.run(run(args.scale, args.reps, args.warmup))

    print(f"\n{'scenario':<24}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}{'budget':>10}  verdict")
    print("-" * 78)
    breached = 0
    for r in results:
        verdict = "—"
        if r.within_budget is not None:
            verdict = "OK" if r.within_budget else "OVER BUDGET"
            breached += 0 if r.within_budget else 1
        budget = "—" if r.budget_ms is None else f"{r.budget_ms:.0f}"
        print(
            f"{r.name:<24}{r.p50_ms:>10.1f}{r.p95_ms:>10.1f}{r.max_ms:>10.1f}{budget:>10}  {verdict}"
            + ("" if r.status == 200 else f"  (HTTP {r.status})")
        )
    if args.json:
        Path(args.json).write_text(json.dumps([asdict(r) for r in results], indent=2))
        print(f"\nwrote {args.json}")
    if breached:
        print(f"\n{breached} scenario(s) over budget.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
