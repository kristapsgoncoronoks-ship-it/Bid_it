"""Idempotent fuel-transaction ingestion (G1.2).

WO-49 shipped `claim.get_or_create_claim` as the one reachable entry point
into the claim tables; `ingest_transaction` is the equivalent for
`fuel_transactions` — the SINGLE service function any future parser/pipeline
calls to record a fuel-card / toll-network line item. It is INSERT-OR-NO-OP
on the natural key (`app/models/transport/fuel_transaction.py`'s
`uq_fuel_transactions_natural_key`), never a delete-and-reinsert (that is the
exact Fleet Fuel pattern section 8.1 item 6 identifies as dead weight) and
never an update-on-conflict (a second call with the same key is a REPLAY —
the ingestion source re-sending a file it already sent — not a correction; a
correction is a separate future concern, out of this order's scope).

This module deliberately does NOT: resolve `invoice_ref` to `invoice_id`
(future note-matching service, G2.4/G2.5), materialize a `VatRefundClaimLine`
(G2.4/G2.5), or run the monthly close (G1.3/G1.4). It ingests one row and
stops.

WO-88 — WHY THIS WRITER CHECKS FX PROVENANCE AGAINST THE EUR FIGURE
----------------------------------------------------------------------
Until WO-88 this function took `fx_source` as an optional argument and never
compared it with anything, so it would happily store a row asserting BOTH
"no exchange rate was available" (`fx_source="unknown"`, which
`app/models/fx.py` defines as *"no rate available → EUR figure is NULL, never a
guessed number"*) AND a non-NULL `net_eur`. `net_eur` is NOT NULL on this
table, so on this table the honest outcome of "no rate" is **no row** — exactly
the branch `statement_ingest._resolve_line` already takes. `contract_audit`
argues from *"a EUR figure exists for every stored row because ingestion
refuses a line it cannot convert"*; that was true of `statement_ingest` and
false of the writer underneath it, and WO-87 could only refuse the bad row at
the analysis boundary of three read-only analyses (`savings._require_eur_basis`)
— every other consumer of `net_eur`/`net_eur_eff` (claim lines, the overcharge
euro WO-83 prints as a payment demand, the tie-out, the close, the recovery
dashboard) summed it as a real conversion.

`_require_fx_provenance` closes it at the writer; `ck_fuel_transactions_fx_provenance`
(the same invariant as a table CHECK, WO-88's migration) closes it underneath
for any writer that never comes through here. Both layers, always — and
`savings`' refusal stays as the third, because it is the only one that can
explain to an operator *why* a comparison was refused.

WO-89 — AND THE ROW THAT DENIES IT EVER NEEDED A RATE
----------------------------------------------------------------------
WO-88 closed the two combinations where the euro denies that a rate was USED.
It deliberately left open the one where the euro denies that a rate was NEEDED:
a non-EUR document currency carrying `fx_source='eur'`, the identity provenance
(`app/models/fx.py`: *"the amount was already EUR (identity, rate 1)"*). That
row is not a hypothetical — before WO-89 THIS function accepted and stored it, a
PLN line asserting `net_eur = 1400.00` with the provenance "no conversion was
required". WO-88's gate passed it (its two clauses test `unknown` and a NULL
provenance), its CHECK passed it (`fx_source IS NOT NULL` satisfied the second
conjunct), and `savings._require_eur_basis` passes it too — so a fabricated
conversion reached the claim lines, the WO-83 demand letter, the tie-out, the
close and the recovery dashboard with nothing in its way.

WO-89 adds the third clause to the SAME predicate (never a rival one) and the
third conjunct to the SAME constraint, under a DISTINCT code —
`fx_provenance_inconsistent` — because the operator remedy is not "go and get
the rate". See `_require_fx_provenance` for the full reasoning.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.core.money import q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import audit, issuer, modules
from app.services.transport import queries
from app.services.transport.product_group import derive_product_group

# The FX provenance value meaning "no rate was available, so the EUR figure is
# not a converted euro" — `app/models/fx.py` / ADR-0010's platform-wide enum.
# Restated as a literal rather than imported: a transport service may only
# import `app.models.transport` / `app.models.base` (ADR-0023 rule 2, asserted
# by `tests/test_boundaries.py`), which is why `savings.py` and `rebate.py`
# state their own `fx_source` values the same way. `tests/transport/
# test_wo88_fx_provenance.py` pins this string against the real enum so the two
# cannot drift apart.
FX_SOURCE_UNKNOWN = "unknown"
# The provenance meaning "the amount was already EUR (identity, rate 1)" —
# `app/models/fx.py`. Only a EUR document currency can truthfully claim it
# (WO-89). Restated as a literal for the same ADR-0023 rule-2 reason as
# FX_SOURCE_UNKNOWN above, and pinned against the real enum by the same test.
FX_SOURCE_EUR = "eur"
# The currency that needs no conversion, and therefore no provenance.
CURRENCY_EUR = "EUR"


def _require_fx_provenance(currency: str, fx_source: str | None) -> None:
    """Refuse a row whose EUR figure contradicts its own FX provenance (§4.15).

    THE one predicate for this rule — extended by WO-89, never forked into a
    rival (WO-85's registry lesson, R3's one-gate precedent). Exactly three
    combinations make the stored euro a number no rate ever produced, and this
    gate refuses all three, fail CLOSED.

    Two of them deny that a rate was USED (WO-88):

    * ``fx_source == "unknown"`` — the caller positively recorded that no rate
      was available (`app/models/fx.py`: *"no rate available → EUR figure is
      NULL"*). `net_eur` is NOT NULL here, so the row cannot honour that; the
      honest outcome is no row, which is what `statement_ingest` does when
      `fx.to_eur` finds no rate.
    * a NON-EUR document currency with ``fx_source is None`` — a foreign amount
      with no recorded conversion at all, which is master-context §4.14's
      *"it never labels a foreign amount EUR"*.

    Both raise `ValidationError(code="fx_rate_unavailable")` — the code
    `statement_ingest`, `rebate` and `savings` already raise for a rate that is
    needed and missing.

    The third denies that a rate was NEEDED (WO-89):

    * a NON-EUR document currency with ``fx_source == "eur"`` — the identity
      provenance, which `app/models/fx.py` defines as *"the amount was already
      EUR (identity, rate 1)"*. If the document is denominated in zloty then the
      amount was not already euros, so the row asserts a conversion that never
      happened while claiming none was required. Until WO-89 this writer stored
      it: a PLN line with `net_eur = 1400.00` and `fx_source = 'eur'` satisfied
      every gate and every constraint, and flowed into the claim lines, the
      overcharge demand letter, the tie-out, the close and the recovery
      dashboard as if it were an honest euro.

    It raises `ValidationError(code="fx_provenance_inconsistent")` — a DISTINCT
    code, chosen rather than reusing `fx_rate_unavailable`, because the operator
    remedy differs: every `fx_rate_unavailable` message in this tree ends in an
    instruction to go and obtain the rate, and that instruction is wrong here.
    The rate is very likely cached already; what is broken is the claim that
    none was needed. The slug follows the tree's own `fx_stated_inconsistent`
    (`statement_ingest`) — same prefix, same suffix, same meaning of "two things
    this row says about itself cannot both be true". Additive to the wire
    contract, never a change to its shape (§4.20).

    A EUR-currency row with a NULL `fx_source` is ACCEPTED, and so is a
    EUR-currency row claiming `"eur"`: EUR is the identity and involves no rate
    at all. That carve-out is deliberate — it is what keeps every EUR line the
    tree has ever written valid, and `savings._require_eur_basis` makes the same
    carve-out for the same reason. Currency is compared case-insensitively, so a
    caller storing `"eur"` is not refused for a conversion it never had to make.
    """
    cur = (currency or "").strip().upper()
    if fx_source == FX_SOURCE_UNKNOWN:
        raise ValidationError(
            f"A {cur or '?'} fuel line was offered with fx_source='unknown' — that value "
            "means no exchange rate was available, so the line has no EUR figure to store. "
            "It is refused rather than stored at a guessed rate: resolve the rate and "
            "re-ingest.",
            code="fx_rate_unavailable",
        )
    if cur != CURRENCY_EUR and fx_source is None:
        raise ValidationError(
            f"A {cur or '?'} fuel line was offered with no recorded FX provenance — a "
            "foreign amount is never labelled EUR without one. Convert it (fx_source="
            "'ecb') or record the document's own conversion (fx_source='stated').",
            code="fx_rate_unavailable",
        )
    if cur != CURRENCY_EUR and fx_source == FX_SOURCE_EUR:
        raise ValidationError(
            f"A {cur or '?'} fuel line was offered with fx_source='eur' — that provenance "
            f"means the amount was ALREADY in euro and needed no conversion, which a "
            f"{cur or '?'} amount was not. The stored EUR figure would be a conversion "
            "nothing performed. Convert it (fx_source='ecb') or record the document's own "
            "conversion (fx_source='stated').",
            code="fx_provenance_inconsistent",
        )


async def ingest_transaction(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    period: str,
    line_seq: int,
    country: str,
    vehicle_ref: str,
    txn_date: date,
    station: str,
    product: str,
    qty: Decimal,
    currency: str,
    net_local: Decimal,
    vat_local: Decimal,
    gross_local: Decimal,
    net_eur: Decimal,
    vat_eur: Decimal,
    net_eur_eff: Decimal | None = None,
    txn_time: str = "",
    invoice_ref: str | None = None,
    provenance_note: str | None = None,
    fx_rate: Decimal | None = None,
    fx_ecb_rate: Decimal | None = None,
    fx_ecb_date: date | None = None,
    fx_source: str | None = None,
) -> FuelTransaction:
    """Insert-or-no-op on `(org, entity, supplier, period, line_seq)`.

    Calling this twice with the SAME natural key returns the SAME row, never
    a duplicate (the `uq_fuel_transactions_natural_key` constraint is the
    backstop if two callers ever race — see
    `tests/transport/test_g1_2_fuel_transactions.py::
    test_g1_2_natural_key_uniqueness_constraint_rejects_a_raw_duplicate_insert`).

    Gated on the `transport` module entitlement FIRST, before any query —
    identical reasoning to WO-49's `get_or_create_claim`: `modules.is_enabled`
    (a bool), never `modules.require_enabled` (which raises
    `fastapi.HTTPException`, the wrong shape for a service — see that
    function's docstring for the full rationale, unchanged here).

    Every monetary amount (`net_local`, `vat_local`, `gross_local`, `net_eur`,
    `vat_eur`, `net_eur_eff`) is quantized via `app.core.money.q2`
    (ROUND_HALF_UP) HERE, once, so no caller needs to remember to round.
    `qty` is passed through UNCHANGED — it is deliberately NOT
    money-quantized (the €/L denominator; master-context §4.9's own carve-out,
    `BA_fleet_fuel.md` section 4.2 row 9).

    `product_group` is always DERIVED from `product` via
    `derive_product_group()` — never accepted as a caller-supplied value, so
    there is exactly one place the precedence logic can live.

    Gate order, fails CLOSED: module entitlement → FX provenance → entity →
    the natural-key lookup. `_require_fx_provenance` is pure and touches no
    database, so a call carrying an impossible EUR figure is refused before the
    first query runs and provably writes nothing — no row, no audit event, not
    even a read (WO-88; see the module docstring for why the writer, not only
    the reader, has to check this).
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    _require_fx_provenance(currency, fx_source)

    country = country.upper()

    # Reads the AP/AR core through its OWN service (issuer.get_by_id), never a
    # raw cross-domain join (ADR-P3 rule 2 / VAT_HARVEST E.2) — identical
    # pattern to claim.get_or_create_claim.
    entity = await issuer.get_by_id(db, org_id, entity_id)
    if entity is None:
        raise NotFoundError("Entity not found", code="entity_not_found")

    existing = await db.scalar(
        queries.fuel_transaction_by_natural_key(
            org_id,
            entity_id=entity_id,
            supplier=supplier,
            period=period,
            line_seq=line_seq,
        )
    )
    if existing is not None:
        return existing

    txn = FuelTransaction(
        org_id=org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=vehicle_ref,
        txn_date=txn_date,
        txn_time=txn_time,
        station=station,
        product=product,
        product_group=derive_product_group(product),
        qty=qty,  # NOT quantized — see docstring.
        currency=currency,
        net_local=q2(net_local),
        vat_local=q2(vat_local),
        gross_local=q2(gross_local),
        net_eur=q2(net_eur),
        vat_eur=q2(vat_eur),
        net_eur_eff=q2(net_eur_eff if net_eur_eff is not None else net_eur),
        invoice_ref=invoice_ref,
        provenance_note=provenance_note,
        fx_rate=fx_rate,
        fx_ecb_rate=fx_ecb_rate,
        fx_ecb_date=fx_ecb_date,
        fx_source=fx_source,
    )
    db.add(txn)
    await db.flush()
    await audit.record(
        db,
        audit.A.FUEL_TRANSACTION_INGEST,
        target_type="fuel_transaction",
        target_id=txn.id,
        meta={
            "entity_id": entity_id,
            "supplier": supplier,
            "period": period,
            "line_seq": line_seq,
        },
        org_id=org_id,  # explicit — this service is also callable from a
        # worker/parser pipeline with no ambient request-scoped org context
        # (audit.record's `org_id or get_current_org()` fallback would
        # otherwise silently no-op, identical rationale to claim.py).
    )
    return txn
