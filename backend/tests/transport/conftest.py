"""Shared fixtures/helpers for transport-vertical tests. Every value is
synthetic (`tests/factories/transport.py`) — never derived from client data."""

from __future__ import annotations

from app.models.issuer import IssuerProfile
from app.models.organization import Organization
from app.services import modules
from tests.factories.transport import synthetic_company_name, synthetic_iban, synthetic_vat_id


async def make_org(db_session, *, name: str | None = None, plan: str = "trial") -> Organization:
    org = Organization(name=name or "Transport Test Org", plan=plan, status="active")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


async def make_entity(
    db_session, org_id: str, *, country: str = "EE", seed: int | None = None
) -> IssuerProfile:
    """A synthetic 'our own legal entity' (the transport claimant) — reuses the
    existing issuer_profiles registry, see app/models/transport/vat_claim.py.

    Fully "clean" by default (registration_number/address_line1/iban all
    populated) so a claim built on this entity passes G2.10 slice 1's
    `customer_data`/`bank_account` checklist rules (WO-60) unless a test
    deliberately blanks a field to exercise the missing-data path."""
    entity = IssuerProfile(
        org_id=org_id,
        name=synthetic_company_name(seed=seed),
        legal_name=synthetic_company_name(seed=seed),
        vat_number=synthetic_vat_id(country, seed=seed),
        registration_number=f"REG-{(seed or 1):06d}",
        address_line1="1 Synthetic Test Street",
        iban=synthetic_iban(country, seed=seed),
        country=country,
    )
    db_session.add(entity)
    await db_session.flush()
    return entity


async def enable_transport(db_session, org_id: str) -> None:
    await modules.set_enabled(db_session, org_id, "transport", True)
