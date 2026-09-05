"""The refund-estimate funnel over HTTP (WO-AC; G4.8, R43, R53).

AUTHENTICATED, AND THAT IS A DECISION RATHER THAN AN OVERSIGHT
----------------------------------------------------------------
`BA_fleet_fuel.md` §2.3 calls `/estimate` an *acquisition wedge* and does not
mark it LOGIN-ONLY the way it marks `/value` one line above — which reads as a
public marketing-site page in the harvested system.

This deployment is not that one. Every entry in this codebase's public-route
allowlist (`app.core.authz`) is either an infrastructure probe that touches no
tenant data or TOKEN-authenticated, where the token is the credential and
serves only its own owner's data. An anonymous `/estimate` would be the first
route here where an unauthenticated stranger makes the server parse bytes they
supply — on a system that deploys to production automatically on every push to
`main`.

So the funnel ships behind `VAT_READ`, with every behaviour R43 specifies
intact, and the workflow it serves is real: a salesperson inside the workspace
runs the estimate for a lead they are onboarding and hands off to a prospect
record in one call. The anonymous variant, and the controls it would need
(IP rate limit, a tighter size cap, a retention answer, its own allowlist entry
with a written justification), is an OWNER DECISION recorded in
`docs/DECISIONS-NEEDED.md` §17 — not something to infer from the spec's
silence. **Owner decision 2026-09-05: keep it authenticated; no public variant.**

WHY THIS ROUTE DOES NOT COMMIT
---------------------------------
`estimate.estimate` writes nothing, and this handler must not turn that into a
lie by committing a session that might carry something else. It is the same
no-commit discipline `claims.get_checklist` states for the advisory checklist
(§4.19), and `test_wo_ac_estimate.py` proves the absence rather than asserting
this paragraph.

THE PROSPECT HANDOFF IS NOT BUILT HERE, BECAUSE IT ALREADY EXISTS
--------------------------------------------------------------------
§2.3 calls for an *optional* prospect handoff. `POST /transport/customers/
{entity_id}/prospect` (WO-77) is that handoff and has been since G2.11: it
calls `customer_lifecycle.add_prospect`, which is idempotent and NEVER
downgrades a real client of any status (F1). This slice adds no second route
and no second way to create a prospect — the estimate screen links to the one
that exists.

Optional is also the operative word, and it is why the handoff is a separate
CLICK rather than a step of the estimate: creating a lifecycle row is a WRITE,
and folding it in would mean every preview left a trace of someone who never
became a customer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.schemas.transport_estimate import CountryEstimateOut, EstimateOut
from app.services import filesec
from app.services.transport import estimate as estimate_svc

router = APIRouter(
    prefix="/transport/estimate",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)

#: The same CSV-only allow-list `statements.py` states its reason for: every
#: shipped parser's `handles()` decodes UTF-8 and matches a literal marker on
#: line 1, so no other kind can produce a statement, and an allow-list wider
#: than the parsers is attack surface with no user.
STATEMENT_KINDS = frozenset({"csv"})


@router.post("", response_model=EstimateOut)
async def estimate_refund(
    current: CurrentUser,
    db: DbSession,
    file: UploadFile,
    period: str = Form(
        description="The claim period to frame the estimate as — YYYY-Q1..Q4 or "
        "YYYY-YEAR. It selects the Art. 17 threshold (€400 quarterly, €50 for a "
        "full year), so the wrong one flags the wrong countries as too small."
    ),
):
    """Parse a fuel-card statement in memory and report the VAT sitting in it,
    per refund country, with the Art. 17 minimum flagged.

    **Nothing is stored.** No transaction rows, no document bytes, no claim —
    R43's own rule, because a sales preview that created rows would put a
    prospect's data into real tables before anyone decided to become a
    customer. The only database access is a READ of the ECB rate cache.

    The security gate runs on the bytes before any parser sees them, exactly as
    on the real statement route: this file is no more trusted for being a
    preview.
    """
    content = await file.read()
    if len(content) > filesec.max_bytes():
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, filesec.too_large_message())
    try:
        filesec.check(file.filename or "statement.csv", content, allowed=STATEMENT_KINDS)
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    result = await estimate_svc.estimate(
        db,
        current.org_id,
        filename=file.filename or "statement.csv",
        content=content,
        period=period,
    )
    # No commit, deliberately — see the module docstring.
    return EstimateOut(
        network=result.network,
        period=result.period,
        lines=result.lines,
        countries=[
            CountryEstimateOut(
                country=c.country,
                lines=c.lines,
                litres=c.litres,
                vat_eur=c.vat_eur,
                vat_local=c.vat_local,
                currency=c.currency,
                below_minimum=c.below_minimum,
                threshold=c.threshold,
                threshold_currency=c.threshold_currency,
                unconverted_lines=c.unconverted_lines,
            )
            for c in result.countries
        ],
        recoverable_eur=result.recoverable_eur,
        unconverted_lines=result.unconverted_lines,
        warnings=result.warnings,
        caveat=result.caveat,
    )
