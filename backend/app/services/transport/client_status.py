"""The client claim-status portal (G4.4; R39; `BA_fleet_fuel.md` §1.2, §2.4,
§3.D, §4.5).

The one transport surface written for the CLIENT rather than for the operator.
Its question is a single sentence — *"where are my claims?"* — and the whole
design problem is what it must NOT answer.

WHAT THE HARVESTED SPEC ACTUALLY SAYS
--------------------------------------
`BA_fleet_fuel.md` §3.D, verbatim: *"**Client-facing translation
(`CLIENT_STAGES`, `/claim-status`):** internal 1A..5 codes map to plain-language
stages **prep -> ready -> filed -> awaiting -> refunded** (plus "needs
attention"). **NO codes, no actions, no fees are shown to the `user` role.**
This is a deliberate competitive differentiator (§4a build B: incumbents rarely
self-serve a live client status view)."*

§4.5's lifecycle table restates the same six values (*"VAT claim
(client-facing): `prep -> ready -> filed -> awaiting -> refunded` (+ needs
attention)"*), §1.2 defines the role that reads it (*"`user` | **The client.**
Read-only base. Sees only "open" read-only pages: `/value`, `/fees`,
`/claim-status`, home. No codes, no fees, no actions."*), §2.4 gives the
surface's business question (*"`/claim-status` | *"Where are my claims?"*
(client-facing) | Plain-language stages only — **no codes, no actions, no
fees**"*), and R39 states the requirement with an acceptance line that is
entirely an ABSENCE:

    "**Client-facing claim-status portal** with plain-language stages only
     (prep -> ready -> filed -> awaiting -> refunded, + needs attention).
     **No internal codes, no actions, no fees** are exposed to the client role."
    Acceptance: "A client-role session cannot see a status code or a fee
     anywhere."

`CLIENT_STAGES` below is that vocabulary and nothing else: six slugs, in the
spec's own order, with no seventh and no friendlier synonym (master-context §9
— actual vocabulary only, §10 — nothing invented).

WHY THE LABELS LIVE HERE AND NOT IN THE SPA
---------------------------------------------
A stage slug is not a sentence a client can read, so something must name it.
That naming is done HERE, on the server, and crosses the wire — for the same
reason `excise.ELIGIBILITY_STATEMENT` does: a string the SPA owns is a string
the SPA can re-word, and the whole point of R39 is that this surface's
vocabulary is not a matter of careful template-writing. One definition, scanned
by this module's own vocabulary tests, rendered verbatim. `frontend/src/lib/
transportClaimStatus.ts` deliberately holds no stage label at all, and a test
asserts the label text appears in no file under `frontend/src/`.

THE MAPPING, AND THE INTERPRETATION IN IT
-------------------------------------------
The spec names the six stages and §3.D's table names the fifteen internal codes,
but it never assigns one to the other. The assignment is therefore an
INTERPRETATION, stated here in full the way `recovery.py` states its four:

    1A  Missing documents                    -> needs_attention
    1B  Documents received, period not ended -> prep
    1C  Can be submitted (with a caveat)     -> prep
    1E  Ready to submit                      -> ready
    2   Submitted                            -> filed
    2A  Successfully submitted               -> filed
    2B  Document request received            -> needs_attention
    3   Decision received                    -> awaiting
    3A  Money received                       -> refunded
    3B  Rejection                            -> needs_attention
    3C  Confiscation by government           -> needs_attention
    3D  Under appeal                         -> needs_attention
    4   Ready to invoice fee                 -> refunded
    4A  Ready to invoice credit              -> refunded
    5   Closed                               -> refunded

Five readings in that table, each deliberate:

1. **`1A` is `needs_attention`, not `prep`.** "Missing documents" is the one
   AUTO stage where something must be supplied before anything can move. The
   spec adds "needs attention" to the five-step ladder precisely for the claims
   that have fallen off it; a 1A claim reported as "in preparation" would tell a
   client everything is fine while their refund sits blocked.
2. **`1B` and `1C` are both `prep`.** Neither has anything a client can act on:
   1B is waiting for its own period to close, and 1C is a caveat the client is
   deliberately not shown the internals of (which caveat it is — an Art. 17
   threshold or a receipt-control waiver — is operator information, and
   `/recovery-dashboard` is where an operator sees it). Calling 1C "ready" would
   be wrong; inventing a seventh stage for it would be invented vocabulary.
3. **`filed` and `awaiting` split at the DECISION, not at the money.** The
   spec's own arrow order puts them in sequence, so they cannot both mean "we
   are waiting". `filed` = with the refunding member state, no decision yet
   (2/2A); `awaiting` = decided, money not yet arrived (3). That is the one
   split that makes the ladder five distinct facts rather than four.
4. **`3B`/`3C`/`3D` are `needs_attention`.** A rejection, a confiscation and an
   appeal are adverse outcomes. None is "awaiting" (nothing routine is pending)
   and none is "refunded" (no money arrived). `2B` joins them for the reason
   §3.D's own table gives it: it *"carries `action_deadline`"* — something must
   happen by a date.
5. **`4`/`4A`/`5` are `refunded`.** All three are engine `paid` (D2's
   `ENGINE_OF`), so the client's money has arrived. What they actually describe
   is the FEE ladder — "ready to invoice fee", "ready to invoice credit",
   "closed" — and the fee is exactly what R39 forbids this surface from showing.
   Mapping them onto the money-arrived stage states the true fact about the
   client's refund and states nothing about our invoice.

DISPATCH IS ON THE ENGINE STATUS FIRST, THEN THE CODE
-------------------------------------------------------
A `draft` claim has no `status_code` at all (the AUTO codes are DERIVED, never
stored — D1/R17), so its stage comes from `status.derive_stage`. A filed claim
(`submitted`/`approved`/`paid`) uses its stored code when the code is one this
module knows, and otherwise `ENGINE_STAGE` — a claim whose code column is NULL
or carries a value from a future vocabulary still reports something true rather
than raising.

Everything else — `withdrawn`, `rejected`, or any status this module has not met
— is NOT SHOWN, and counted in `not_shown_claims`. Three reasons: none of them
is one of the six stages; a seventh stage would be invented vocabulary (§10);
and a silent drop would make the row count lie (`sum(stage.claims) +
not_shown_claims == total_claims` holds exactly, and is asserted). Unlike
`recovery.py`'s `excluded`, no REASON is emitted — `"withdrawn"`/`"rejected"`
are internal engine vocabulary, and this is the surface that does not show it.

Status-first also makes this module immune to a real defect in the tree:
`lock.withdraw_claim` sets `status = "withdrawn"` but leaves `status_code`
populated, while §3.D **D7** says withdrawal *"also NULLs `status_code`"*. That
is a G2.7 gap (recorded in `docs/plan/plan-a/wo/WO-93-client-claim-status.md`,
not fixed here — §4.20 additive). Because the engine status is read first, a
withdrawn claim never reaches the code map, and a test pins that immunity so a
future fix cannot silently change this surface.

MONEY: NET EUR, AND WHY `None` IS A REAL ANSWER
-------------------------------------------------
Every euro here is `vat_eur` — the reclaimable VAT, the column
`BA_fleet_fuel.md` calls *"the VAT-refund north star"*. A DRAFT claim's own
`vat_eur` column is NULL by construction (WO-49: frozen only at submission), so
its figure comes from `freeze.preview_vat_base` — the same preview
`lock.submit_claim` gates on. A FILED claim's figure is its own FROZEN column
(R13/C10), never a re-derivation.

Either can be genuinely unavailable, and this module says so with `None` rather
than with a number:

* a draft whose lines span currencies makes `preview_vat_base` refuse
  (`claim_currency_mismatch`, §4.14). There is no single-currency VAT base to
  state, so the claim reports `vat_eur = None` and contributes nothing to any
  total. `0.00` would be a wrong number in front of a client and a foreign
  amount labelled EUR would be a §4.14 violation dressed as a total;
* a filed claim whose frozen column is NULL reports `None` for the same reason.

`vat_local`, `currency` and `paid_amount` are NEVER read (asserted by an AST
scan): they are per-refund-state / currency-ambiguous, exactly as
`recovery.py`'s docstring records. Nor is any `fee_*` column — that one is R39
itself.

R39'S ABSENCES ARE STRUCTURAL, NOT EDITORIAL
----------------------------------------------
`tests/transport/test_wo93_client_surface.py` proves, each with a
seeded-violation self-test so the scan itself cannot quietly stop working:

* no string VALUE anywhere in a real response equals any member of
  `status.AUTO_CODES + status.MANUAL_CODES` (the tuples are IMPORTED by the
  test, so a code added there is covered without touching it);
* no field name in this module's dataclasses or in
  `app/schemas/transport_client_status.py` carries code, fee or action-verb
  vocabulary — a `status_code` or a `fee_eur` field would be read the moment a
  client rendered it, whatever the prose beside it said;
* no string this module OWNS (the labels and descriptions it puts on the wire)
  carries action vocabulary — this surface offers nothing to do.

READ-ONLY, ORG-SCOPED, FAIL-CLOSED, NEVER RAISES ON MISSING DATA
------------------------------------------------------------------
No `db.add`, no model attribute assignment, no `audit.record`, and the route
issues no commit. The claim set comes from `claim.list_claims(..., year=...)` —
THE claims-listing query, whose `year` filter WO-81 put there rather than in a
caller *"because R38's own acceptance line forbids forking the claims query: one
claim-listing query, one filter, every consumer."* This module holds no
`select()` of its own over `vat_refund_claims`, and a test asserts it.

The `transport` module entitlement is checked INSIDE this service before any
query (ADR-P3 rule 3) and fails CLOSED. A year with no claims returns the six
empty stages and zeroes — not a 404 and not an exception. Only a malformed year
refuses (422 `invalid_year`, through `recovery.validate_year` — imported, not
re-written), because silently returning an empty year for a typo would read to a
client as *"you have no claims"*, a materially different and more alarming
answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, PermissionError
from app.core.money import q2
from app.services import issuer, modules
from app.services.transport.claim import list_claims
from app.services.transport.freeze import preview_vat_base
from app.services.transport.recovery import validate_year
from app.services.transport.status import derive_stage

# `BA_fleet_fuel.md` §3.D / §4.5 / R39, in the spec's own order. Six values, no
# seventh, and no synonym: "needs attention" is written with an underscore
# because it is a wire slug, and its human wording is `STAGE_LABELS` below.
CLIENT_STAGES: tuple[str, ...] = (
    "prep",
    "ready",
    "filed",
    "awaiting",
    "refunded",
    "needs_attention",
)

# The plain-language name of each stage — server-owned so the SPA cannot re-word
# it (see the module docstring). Deliberately free of action vocabulary: this
# surface describes where a claim IS, never what anyone should do about it.
STAGE_LABELS: dict[str, str] = {
    "prep": "In preparation",
    "ready": "Ready to file",
    "filed": "Filed with the tax authority",
    "awaiting": "Decision received, money on its way",
    "refunded": "Refunded",
    "needs_attention": "Needs attention",
}

# One sentence per stage, in the client's language. Same ownership rule as the
# labels, and the same vocabulary ban: no internal code is named, no fee is
# mentioned, and nothing here reads as an instruction.
STAGE_DESCRIPTIONS: dict[str, str] = {
    "prep": "We are still putting this claim together for you.",
    "ready": "Everything this claim needs is in place.",
    "filed": "This claim is with the refunding country's tax authority.",
    "awaiting": "The country has decided. The money has not arrived yet.",
    "refunded": "The refund for this claim has arrived.",
    "needs_attention": "Something is holding this claim up. We will be in touch.",
}

# The interpretation table from the module docstring, as code. TOTAL over every
# code a claim can actually CARRY — `status.AUTO_CODES + status.MANUAL_CODES`
# plus `"2"`, which `MANUAL_CODES` deliberately excludes because that transition
# only ever happens through `lock.submit_claim` (which stamps `"2"` itself, and
# which `set_status_code` refuses to accept). A code that gained no entry here
# would fall through to `ENGINE_STAGE` silently, so a test asserts completeness
# against those tuples rather than against this dict's own keys.
STAGE_BY_CODE: dict[str, str] = {
    # AUTO — system-derived for a draft claim (D1/R17), never stored.
    "1A": "needs_attention",
    "1B": "prep",
    "1C": "prep",
    "1E": "ready",
    # Manual workflow codes (D2).
    "2": "filed",
    "2A": "filed",
    "2B": "needs_attention",
    "3": "awaiting",
    "3A": "refunded",
    "3B": "needs_attention",
    "3C": "needs_attention",
    "3D": "needs_attention",
    "4": "refunded",
    "4A": "refunded",
    "5": "refunded",
}

# The fallback for a filed claim whose `status_code` is NULL or carries a value
# this module has not met. Reading the coarse engine state is always true even
# when the workflow label is absent — `lock.submit_claim` stamps `"2"`, but a
# claim moved by any other path must still report something honest.
ENGINE_STAGE: dict[str, str] = {
    "submitted": "filed",
    "approved": "awaiting",
    "paid": "refunded",
}


@dataclass(frozen=True)
class ClientClaim:
    """One claim, as its owner sees it. Carries no id: there is nothing to open,
    and the R1 grain (entity x country x period) already identifies the row.

    `vat_eur` is `None` when no single-currency figure can be stated for this
    claim yet — see the module docstring. It is never `0.00` as a stand-in."""

    entity_name: str
    refund_country: str
    period: str
    stage: str
    vat_eur: Decimal | None


@dataclass(frozen=True)
class ClientStage:
    """One of the six harvested stages: how many claims are in it, and how much
    reclaimable VAT they carry. Always present, including when empty."""

    stage: str
    label: str
    description: str
    claims: int
    vat_eur: Decimal


@dataclass(frozen=True)
class ClientClaimStatus:
    """The whole surface. `currency` is a constant `"EUR"` because every figure
    here is a `vat_eur` amount — stated rather than assumed, so a consumer can
    never render a foreign amount with a EUR sign (§4.14)."""

    year: int
    currency: str
    total_claims: int
    stages: tuple[ClientStage, ...]
    claims: tuple[ClientClaim, ...]
    # Claims in no stage at all (an engine state outside the ladder). Counted so
    # the arithmetic is honest; unnamed because the name would be the internal
    # vocabulary this surface exists not to show.
    not_shown_claims: int


def stage_for_code(code: str | None) -> str | None:
    """The client stage for one internal workflow code, or `None` when the code
    is absent or unknown. Pure and total over `AUTO_CODES + MANUAL_CODES` — the
    single place the translation happens, so a test can walk every code."""
    if code is None:
        return None
    return STAGE_BY_CODE.get(code)


async def _entity_names(db: AsyncSession, org_id: str, entity_ids: set[str]) -> dict[str, str]:
    """Display names for the claimant entities in scope, read through the issuer
    SERVICE (`issuer.get_by_id`) rather than a raw cross-domain join — ADR-P3
    rule 2, the path `excise.py`/`claim.py`/`fuel.py` all take. One lookup per
    DISTINCT entity, not per claim. An entity that cannot be resolved falls back
    to its id rather than to an invented name (§10)."""
    out: dict[str, str] = {}
    for entity_id in sorted(entity_ids):
        profile = await issuer.get_by_id(db, org_id, entity_id)
        name = None if profile is None else (profile.legal_name or profile.name)
        out[entity_id] = name or entity_id
    return out


async def _draft_stage_and_figure(
    db: AsyncSession, org_id: str, claim_id: str, *, today: date | None
) -> tuple[str, Decimal | None]:
    """One DRAFT claim: its client stage and its previewed VAT, or `None` for the
    figure when no single-currency base exists.

    The preview runs FIRST because it is the one step that can refuse: a claim
    whose lines span currencies raises `claim_currency_mismatch` (§4.14) and
    there is then no figure to state — but the claim itself is still real and
    still belongs on the client's screen, in the stage that says a human must
    look at it. Any other `ConflictError` is a genuine fault and propagates.
    """
    try:
        vat_eur, _vat_local, _currency = await preview_vat_base(db, org_id, claim_id)
    except ConflictError as exc:
        if exc.code != "claim_currency_mismatch":
            raise
        return "needs_attention", None

    code = await derive_stage(db, org_id, claim_id, today=today)
    stage = stage_for_code(code)
    # `derive_stage` returns only `AUTO_CODES`, every one of which is mapped —
    # the `or` is the belt-and-braces path for a code added there later, and it
    # names the stage that gets a human to look rather than one that reassures.
    return stage or "needs_attention", vat_eur


async def client_claim_status(
    db: AsyncSession,
    org_id: str,
    year: int,
    *,
    today: date | None = None,
) -> ClientClaimStatus:
    """R39 — every claim of the refund `year`, in the six plain-language client
    stages. Read-only; see the module docstring for every definition, citation
    and interpretation.

    `today` is a TEST SEAM (defaults to the real date inside
    `status.derive_stage`), mirroring `recovery.recovery_dashboard`'s own — and,
    like that one, deliberately NOT exposed on the wire: a caller that could
    post its own clock could change which stage a claim reports.

    Gate order, the transport-wide entry-point pattern, fails CLOSED:
    1. the `transport` module entitlement (ADR-P3 rule 3 — never delegated to
       the route, which carries its own structural permission check);
    2. `year` validation (422 `invalid_year`, `recovery.validate_year`).

    Cost note: drafts are evaluated one at a time because `derive_stage` /
    `preview_vat_base` are the CANONICAL per-claim evaluators and forking them
    into a set-based query is exactly what R38/R51 forbid. A refund year holds
    one claim per (entity x refund country x period), so the walk is bounded by
    the portfolio's own shape; if it ever stops being, the fix is a batch form
    of those evaluators used by BOTH callers — never a second implementation.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    validate_year(year)

    claims = await list_claims(db, org_id, year=year)
    names = await _entity_names(db, org_id, {c.entity_id for c in claims})

    counts: dict[str, int] = dict.fromkeys(CLIENT_STAGES, 0)
    euros: dict[str, Decimal] = {stage: Decimal("0") for stage in CLIENT_STAGES}
    rows: list[ClientClaim] = []
    not_shown = 0

    for claim in claims:
        vat_eur: Decimal | None
        if claim.status == "draft":
            stage, vat_eur = await _draft_stage_and_figure(db, org_id, claim.id, today=today)
        elif claim.status in ENGINE_STAGE:
            # The stored workflow label when it is one we know, else the coarse
            # engine state — which is always true even when the label is absent.
            stage = stage_for_code(claim.status_code) or ENGINE_STAGE[claim.status]
            vat_eur = claim.vat_eur
        else:
            # `withdrawn`, `rejected`, or a status this module has not met: no
            # stage fits, none is invented, and the count keeps the arithmetic
            # honest. The REASON is internal vocabulary and is not emitted.
            not_shown += 1
            continue

        counts[stage] += 1
        if vat_eur is not None:
            euros[stage] += vat_eur
        rows.append(
            ClientClaim(
                entity_name=names[claim.entity_id],
                refund_country=claim.refund_country,
                period=claim.ref_period,
                stage=stage,
                vat_eur=None if vat_eur is None else q2(vat_eur),
            )
        )

    return ClientClaimStatus(
        year=year,
        currency="EUR",
        total_claims=len(claims),
        stages=tuple(
            ClientStage(
                stage=stage,
                label=STAGE_LABELS[stage],
                description=STAGE_DESCRIPTIONS[stage],
                claims=counts[stage],
                vat_eur=q2(euros[stage]),
            )
            for stage in CLIENT_STAGES
        ),
        claims=tuple(rows),
        not_shown_claims=not_shown,
    )


__all__ = [
    "CLIENT_STAGES",
    "ENGINE_STAGE",
    "STAGE_BY_CODE",
    "STAGE_DESCRIPTIONS",
    "STAGE_LABELS",
    "ClientClaim",
    "ClientClaimStatus",
    "ClientStage",
    "client_claim_status",
    "stage_for_code",
]
