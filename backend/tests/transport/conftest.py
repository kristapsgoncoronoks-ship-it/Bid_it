"""Shared fixtures/helpers for transport-vertical tests. Every value is
synthetic (`tests/factories/transport.py`) — never derived from client data."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from app.models.issuer import IssuerProfile
from app.models.organization import Organization
from app.models.transport.claimant_document import VatClaimantDocument
from app.models.transport.customer_lifecycle import VatCountryActivation, VatCustomerLifecycle
from app.services import modules
from tests.factories.transport import synthetic_company_name, synthetic_iban, synthetic_vat_id


async def make_org(db_session, *, name: str | None = None, plan: str = "trial") -> Organization:
    org = Organization(name=name or "Transport Test Org", plan=plan, status="active")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


# A synthetic NACE Rev. 2 code for freight road transport — the activity every
# fixture claimant plausibly carries out. Presence is all the `nace` rule checks
# (`checklist._verify_nace`), so this is a shape-realistic placeholder, not a
# classification decision.
FIXTURE_NACE_CODE = "H49.41"


async def give_claimant_documents(
    db_session, org_id: str, entity_id: str, *, countries=None
) -> None:
    """Put the customer-scope documents (contract, trade register) and a power
    of attorney per refund country on file, with NO stated expiry.

    Rows are inserted DIRECTLY rather than through
    `claimant_documents.record`, exactly as `make_entity` writes an
    `IssuerProfile` directly: a fixture establishes state, it does not exercise
    the service under test (and `record` is module-gated, which would make this
    helper depend on call order relative to `enable_transport`).

    The digests are synthetic and per-(entity, kind, country) unique — the
    unique key carries `sha256`, so a shared constant would collide the moment
    one claimant held two countries' PoAs."""
    from app.services.transport.capture_review import COUNTRIES

    codes = COUNTRIES if countries is None else countries
    rows = [("signed_contract", ""), ("trade_registry", "")]
    rows += [("power_of_attorney", c) for c in sorted(codes)]
    for kind, country in rows:
        db_session.add(
            VatClaimantDocument(
                org_id=org_id,
                entity_id=entity_id,
                kind=kind,
                country=country,
                sha256=hashlib.sha256(f"fixture:{entity_id}:{kind}:{country}".encode()).hexdigest(),
                size=1024,
                mime="application/pdf",
                filename=f"{kind}{('-' + country) if country else ''}.pdf",
            )
        )
    await db_session.flush()


async def make_entity(
    db_session,
    org_id: str,
    *,
    country: str = "EE",
    seed: int | None = None,
    documents: bool = True,
) -> IssuerProfile:
    """A synthetic 'our own legal entity' (the transport claimant) — reuses the
    existing issuer_profiles registry, see app/models/transport/vat_claim.py.

    Fully "clean" by default (registration_number/address_line1/iban/nace_code
    all populated, and every §3.E document on file) so a claim built on this
    entity passes the WHOLE G2.10 checklist unless a test deliberately blanks a
    field or passes `documents=False` to exercise a missing path.

    WO-AB RAISED THIS FIXTURE rather than weakening any assertion — the same
    move, and for the same reason, as WO-95 seeding the standard fee rate in
    `enable_transport` below: slice 2 seeds four more checklist rules, so a
    fixture claimant that was "fully clean" under two rules has to be fully
    clean under six or every pre-existing stage assertion would silently start
    measuring the fixture instead of the code."""
    entity = IssuerProfile(
        org_id=org_id,
        name=synthetic_company_name(seed=seed),
        legal_name=synthetic_company_name(seed=seed),
        vat_number=synthetic_vat_id(country, seed=seed),
        registration_number=f"REG-{(seed or 1):06d}",
        address_line1="1 Synthetic Test Street",
        iban=synthetic_iban(country, seed=seed),
        nace_code=FIXTURE_NACE_CODE,
        country=country,
    )
    db_session.add(entity)
    await db_session.flush()
    if documents:
        await give_claimant_documents(db_session, org_id, entity.id)
    return entity


# The synthetic standard contingency rate every transport fixture org carries.
# NOT a product default and NOT the harvested Appendix B figure repackaged: the
# real percentage is an open owner decision (`docs/DECISIONS-NEEDED.md` §10) and
# `app/services/transport/fee.py` refuses to invent one. These are test values,
# chosen so a €400 claim (the Art. 17 quarterly threshold most fixtures sit just
# above) clears the minimum comfortably and the arithmetic stays checkable by
# hand.
FIXTURE_FEE_PCT = Decimal("10.00")
FIXTURE_FEE_MIN = Decimal("25.00")


async def enable_transport(db_session, org_id: str, *, fee_rate: bool = True) -> None:
    """Activate the vertical, and (by default) configure the org's STANDARD
    contingency-fee rate.

    WHY THE RATE IS SEEDED HERE (WO-95, G2.9)
    -----------------------------------------
    Since WO-95, `lock.submit_claim` freezes a service fee alongside the VAT
    base (C10) and REFUSES (`fee_rate_not_configured`) when no rate is
    configured — a fee figure is what a client is charged, so the engine never
    defaults one. Seeding the standard rate in this shared helper is the WO-73
    `activate_entity` / WO-60 `make_entity` precedent applied at the one place
    every transport fixture org already passes through: fixtures are RAISED to
    satisfy a new gate, no assertion anywhere is weakened, and no existing test
    module changes.

    It also does something WO-95 needed for its own reasons: every pre-existing
    submitted-claim fixture in the tree now carries REAL frozen
    `fee_pct`/`fee_min`/`fee_eur` values, which is what makes WO-93's
    client-surface guarantee (R39 — no fee reaches the client wire) provable
    rather than vacuously true over three NULL columns.

    Pass `fee_rate=False` for a test that must exercise the refusal itself.
    """
    await modules.set_enabled(db_session, org_id, "transport", True)
    if fee_rate:
        await seed_fee_rate(db_session, org_id)


async def seed_fee_rate(
    db_session,
    org_id: str,
    *,
    entity_id: str | None = None,
    country: str = "",
    fee_pct: Decimal = FIXTURE_FEE_PCT,
    fee_min: Decimal = FIXTURE_FEE_MIN,
) -> None:
    """Insert one `VatFeeRate` row DIRECTLY, without going through
    `fee.set_rate`.

    WHY DIRECTLY, AND NOT THROUGH THE SERVICE
    -----------------------------------------
    This is exactly the `activate_entity` convention, and for the same two
    reasons. First, a fixture's job is to put the world in a state, not to
    re-prove a transition — the audited `set_rate` path has its own suite
    (`tests/transport/test_wo95_fee_rates.py`).

    Second, and decisively: `fee.set_rate` EMITS AN AUDIT EVENT, and several
    pre-existing suites assert the EXACT list of `transport.*` audit events an
    org has accumulated (`test_wo88_fx_provenance.py` and
    `test_wo89_fx_wrong_provenance.py` prove a refused ingestion writes NO audit
    event; `test_wo91_excise_rates.py` pins the excise CRUD's own three-event
    trail). Seeding through the service would inject a
    `transport.fee_rate_set` event into every one of those windows and force
    those assertions to be loosened — which is precisely the thing that must
    never happen (master-context §9: raise the fixture, never weaken the
    assertion). Inserting the row leaves the audit trail exactly as those tests
    already describe it.
    """
    from app.models.transport.fee_rate import VatFeeRate

    db_session.add(
        VatFeeRate(
            org_id=org_id,
            entity_id=entity_id,
            country=country,
            fee_pct=fee_pct,
            fee_min=fee_min,
        )
    )
    await db_session.flush()


async def activate_entity(db_session, org_id: str, entity_id: str, *countries: str) -> None:
    """Raise the entity past WO-73's R44 activation gate: an `active`
    lifecycle row + an `active` country-activation row per named refund
    country (the WO-60 `make_entity` precedent — fixtures raised to satisfy
    a new gate, assertions never weakened). Inserts the rows directly (the
    `make_entity` convention); the legal transition chain itself is proven
    by tests/transport/test_g2_11_customer_lifecycle.py."""
    db_session.add(VatCustomerLifecycle(org_id=org_id, entity_id=entity_id, status="active"))
    for country in countries:
        db_session.add(
            VatCountryActivation(
                org_id=org_id, entity_id=entity_id, country=country.upper(), status="active"
            )
        )
    await db_session.flush()


async def register_documented_invoice(
    db_session,
    org_id: str,
    *,
    supplier: str = "Q8",
    invoice_number: str = "INV-0001",
    country: str | None = "LV",
    issue_date: date | None = None,
    subtotal: Decimal = Decimal("2000.00"),
    tax_amount: Decimal = Decimal("420.00"),
):
    """Raise a fixture past BOTH line-level submission gates in one call
    (the WO-73 `activate_entity` precedent — fixtures raised to satisfy a
    gate, assertions never weakened): a registered vendor+invoice so a
    transaction carrying `invoice_number` RESOLVES (never an `UNMATCHED`
    line — WO-75's R3 lock gate), plus a vaulted document linked to it so
    the resolved line passes R10 (WO-58's document-presence gate). Returns
    the AP `Invoice` row. Idempotent per (supplier, invoice_number): reuses
    an existing vendor/invoice rather than duplicating them."""
    from app.models.invoice import Invoice
    from app.models.vendor import Vendor
    from app.services import documents, extraction, vendors
    from app.services import invoices as ap_invoices

    vendor = await vendors.get_by_name(db_session, org_id, supplier)
    if vendor is None:
        vendor = Vendor(org_id=org_id, name=supplier, country=country)
        db_session.add(vendor)
        await db_session.flush()

    inv = next(
        (
            r
            for r in await ap_invoices.list_by_vendor(db_session, org_id, vendor.id)
            if r.invoice_number == invoice_number
        ),
        None,
    )
    if inv is None:
        inv = Invoice(
            org_id=org_id,
            vendor_id=vendor.id,
            invoice_number=invoice_number,
            issue_date=issue_date or date(2026, 5, 1),
            currency="EUR",
            subtotal=subtotal,
            tax_amount=tax_amount,
            total=subtotal + tax_amount,
        )
        db_session.add(inv)
        await db_session.flush()

    sha, _size = await documents.store(
        documents.UPLOADS,
        org_id,
        f"%PDF-1.4 synthetic fixture {supplier} {invoice_number}".encode(),
    )
    run = await extraction.record(
        db_session,
        org_id,
        filename=f"{supplier.lower()}-{invoice_number.lower()}.pdf",
        sha256=sha,
        method="pdf-text",
        status="parsed",
    )
    await extraction.link_to_invoice(db_session, org_id, run.id, inv.id)
    await db_session.flush()
    return inv
