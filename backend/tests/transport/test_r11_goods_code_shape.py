"""R11 — Art. 9 Dir. 2008/9/EC / Reg. 1174/2009 & 79/2012 Annex III: an
unknown product group must default to goods code "10" (Other), NEVER "9"
(luxuries, amusements and entertainment — the archetypal non-deductible
category; the historical Fleet Fuel bug this rule exists to prevent).

WO-49 does not build the goods_codes mapping module yet (that is future M3
work, G2.8 in `docs/plan/plan-a/ARCH_plan.md`) — but the schema-level
belt-and-braces `ck_vat_claim_lines_goods_code_never_9` CHECK constraint on
`vat_claim_lines.goods_code` ships NOW, in the same migration as the table
itself, so "9" can never reach storage even via a raw path once the mapping
module exists. This test proves that constraint is real, not decorative.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.transport.vat_claim import VatRefundClaimLine
from tests.transport.conftest import enable_transport, make_entity, make_org


@pytest.mark.asyncio
async def test_r11_goods_code_9_is_rejected_at_the_database_level(db_session):
    from app.services.transport import claim

    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    c = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2025-Q4"
    )
    await db_session.commit()

    db_session.add(
        VatRefundClaimLine(
            org_id=org.id,
            claim_id=c.id,
            invoice_ref="FB/00012345",
            goods_code="9",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_r11_goods_code_10_and_null_are_both_accepted(db_session):
    """ "10" (Other, the correct unknown-default per R11) and NULL
    (not-yet-classified) both pass the constraint."""
    from app.services.transport import claim

    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    c = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2025-Q4"
    )
    await db_session.commit()

    db_session.add(
        VatRefundClaimLine(org_id=org.id, claim_id=c.id, invoice_ref="FB/00012345", goods_code="10")
    )
    db_session.add(
        VatRefundClaimLine(org_id=org.id, claim_id=c.id, invoice_ref="FB/00012346", goods_code=None)
    )
    await db_session.commit()  # no raise
