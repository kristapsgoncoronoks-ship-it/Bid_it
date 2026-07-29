"""The monthly close as a durable, idempotent, restartable job (G1.3;
`BA_fleet_fuel.md` R31/R60).

WHAT "THE CLOSE" MEANS IN THIS CODEBASE, VS FLEET FUEL
---------------------------------------------------------
Fleet Fuel's monthly close was `consolidate -> build_master -> history ->
run_control -> backup`: a whole ETL pipeline that re-derived its analytics
store from raw supplier files, `process_lock`-guarded, with a period-stamped
pickle hand-off between stages. That entire pipeline is explicitly NOT
ported (master-context §10 — no copying Fleet Fuel code; `docs/plan/plan-a/
ARCH_plan.md` names `consolidate`/`build_master`/`history` as Fleet-Fuel-
specific, not spec) and there is nothing to consolidate FROM yet in this
codebase — `fuel_transactions` ingestion (WO-50, G1.2) is already
insert-or-no-op on a structural natural key, not Fleet Fuel's DELETE-by-
period-then-reinsert, so there is no equivalent "rebuild the analytics store"
step to redo. What DOES translate, and is what this order builds, is R31's
actual property list: **an independent, DURABLE, idempotent-by-(org,period),
restartable orchestrator with one audit trail that halts on the first
failure** — the mechanism, not Fleet Fuel's specific business steps.

The one thing in THIS codebase that a period boundary legitimately needs to
(re)finalize is exactly what G2.4 built: a draft claim's LIVE claim lines.
`run_close` (re)builds `app.services.transport.claim_lines.build_claim_lines`
for every `draft` claim whose scope (`claim_lines.period_months`) includes
the closed month — the same reason a period "closes" at all: it is the point
where the transactions posted to it should be reflected in whatever claim
they belong to. This is a documented, honest translation of R31/R60's
DURABILITY property onto this codebase's ACTUAL current capability — not an
invented business rule — flagged here exactly like WO-50's `line_seq`
disclosure and WO-52's heuristic-interpretation disclosure.

WHY THIS RUNS ON THE EXISTING `jobs` FRAMEWORK, NOT A NEW ONE
------------------------------------------------------------------
This codebase already has a durable, tenant-scoped, retrying, dead-letter
job queue (`app.services.jobs`) used by `dunning.run`/`recurring.generate`/
`retention.purge`/etc — R31/R60's "replaces Fleet Fuel's `process_lock` +
period-stamped pickle hand-off" is exactly what THIS framework already is:
- **Idempotent by (org, period)**: `enqueue_close` keys the job on
  `idempotency_key=f"transport.close:{period}"`, scoped per `org_id` by the
  job table's own `UNIQUE(org_id, kind, idempotency_key)` constraint — a
  second enqueue for the SAME period returns the SAME job row, exactly R31's
  "idempotent... hand-off" property, with no new mechanism.
- **Restartable**: a job that exhausts its retries lands `dead`;
  `jobs.retry()` (pre-existing) requeues it — "re-running a failed close
  requires no manual cleanup" (R31's acceptance line) falls straight out of
  the existing framework.
- **Halts on the first failure**: `run_close` never catches its own
  exceptions and never commits internally (it only flushes, via
  `build_claim_lines`) — `jobs.run_once()` already rolls back the WHOLE
  session on any handler exception before marking the job failed/dead
  (`app/services/jobs.py::run_once`), so a failure partway through the
  claims loop discards EVERY claim-line rebuild from that run, not just the
  one that raised. Nothing partially applied, matching R31's "halts on the
  first failure" literally, not just "skips the bad one and keeps going."
- **Never inline in a web request** (R60): there is no `api/routes/
  transport/*` route yet (out of scope, future work) that could call
  `run_close` synchronously even by mistake; the only reachable entry point
  is `enqueue_close` -> the worker's `jobs.run_once`.

G1.4 ("locked lines are protected from a re-close") IS ALREADY STRUCTURALLY
TRUE HERE, NOT A SEPARATE MECHANISM
------------------------------------------------------------------------------
`build_claim_lines` (G2.4) already refuses a non-`draft` claim and only ever
replaces `frozen_at IS NULL` rows — `run_close` inherits BOTH guarantees for
free by calling it: a claim that has left `draft` (the future G2.5 freeze
target) is invisible to `run_close`'s own `status == "draft"` query filter
in the first place, so the close never even attempts to touch a locked
claim's lines, let alone delete one. `VatClaimedInvoice`'s existing
`RESTRICT` FK into `fuel_transactions` (WO-51) is the second, independent
layer: even a hypothetical future code path that tried to delete a LOCKED
transaction row would be refused by the database itself. See
`tests/transport/test_g1_4_close_protects_locked_lines.py` for both proofs.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError, ValidationError
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit, jobs, modules
from app.services.transport.claim_lines import build_claim_lines, period_months

# The job kind registered with app.services.jobs / app.services.job_handlers.
CLOSE_KIND = "transport.close"

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_close_period(period: str) -> None:
    """`"YYYY-MM"` only — refused at the service boundary before it ever
    reaches a query (mirrors `claim.validate_ref_period`)."""
    if not _PERIOD_RE.match(period):
        raise ValidationError(
            f"'{period}' is not a valid close period — use YYYY-MM", code="invalid_close_period"
        )


async def enqueue_close(db: AsyncSession, org_id: str, period: str, *, commit: bool = True) -> str:
    """Enqueue (or return the existing) `transport.close` job id for
    (org, period) — idempotent per the module docstring. Returns the job's
    `id` (a plain string), never the `Job` ORM row itself: a transport
    service never returns a foreign domain's model instance (mirrors
    `invoice_match.MatchedInvoice` — the same reason
    `tests/test_boundaries.py::test_transport_services_do_not_import_
    other_domain_models` holds for `app.models.job` too, even though `jobs`
    is platform infrastructure, not another BUSINESS domain)."""
    validate_close_period(period)
    job = await jobs.enqueue(
        db,
        CLOSE_KIND,
        {"period": period},
        org_id=org_id,
        idempotency_key=f"{CLOSE_KIND}:{period}",
        commit=commit,
    )
    return job.id


async def run_close(db: AsyncSession, org_id: str, period: str) -> dict:
    """The close itself — see module docstring for what it does and why.
    Gated on the `transport` module entitlement first, identical pattern to
    every other transport service. Never catches an exception from
    `build_claim_lines`: letting it propagate is what makes `jobs.run_once`'s
    existing rollback-then-fail behaviour into "halts on the first failure,
    nothing partially applied" for free (see module docstring).
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    validate_close_period(period)

    claims = list(
        await db.scalars(
            select(VatRefundClaim).where(
                VatRefundClaim.org_id == org_id, VatRefundClaim.status == "draft"
            )
        )
    )

    processed: list[dict] = []
    for claim in claims:
        if period not in period_months(claim.ref_period):
            continue
        lines = await build_claim_lines(db, org_id, claim.id)
        processed.append({"claim_id": claim.id, "line_count": len(lines)})

    await audit.record(
        db,
        audit.A.TRANSPORT_CLOSE_RUN,
        # No single target row — a whole-org batch summary, same pattern as
        # `retention.purge`'s own audit call (org_id + meta, no target_type/id).
        meta={"period": period, "claims_processed": len(processed)},
        org_id=org_id,  # explicit — runs inside the job worker, not a request.
    )
    return {"period": period, "claims_processed": len(processed), "claims": processed}
