"""WO-P (R19) — the derived getting-started checklist.

What must hold:
- a FRESH workspace shows all five steps undone, not dismissed;
- each step flips to done when the real thing exists — through the same
  screens/endpoints a user would use, never by poking the checklist itself
  (there is nothing to poke: the card is a derivation);
- dismissal is org-wide, SETTINGS_MANAGE-gated, idempotent, and audited with
  what was still undone at the time;
- the payload tells the SPA whether the caller may dismiss, so no button is
  offered that the API would refuse.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.organization import Organization

pytestmark = pytest.mark.asyncio

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


async def _card(auth_client) -> dict:
    r = await auth_client.get("/api/v1/dashboard/onboarding")
    assert r.status_code == 200, r.text
    return r.json()


def _done(card: dict) -> dict[str, bool]:
    return {s["key"]: s["done"] for s in card["steps"]}


async def test_fresh_workspace_shows_every_step_undone(auth_client):
    card = await _card(auth_client)
    assert [s["key"] for s in card["steps"]] == [
        "issuer",
        "modules",
        "team",
        "customer",
        "invoice",
    ]
    assert _done(card) == dict.fromkeys(_done(card), False)
    assert card["done_count"] == 0
    assert card["complete"] is False
    assert card["dismissed"] is False
    assert card["can_dismiss"] is True  # the registering owner
    # Every step points somewhere a user can act.
    assert all(s["href"].startswith("/") for s in card["steps"])


async def test_steps_derive_from_the_real_rows(auth_client):
    # Modules: enabling one flips the step.
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200
    assert _done(await _card(auth_client))["modules"] is True

    # Issuer profile.
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert _done(await _card(auth_client))["issuer"] is True

    # Team: a PENDING invite counts — the setup act is inviting.
    r = await auth_client.post(
        "/api/v1/team/invites", json={"email": "driver@acme.io", "role": "user"}
    )
    assert r.status_code == 201, r.text
    assert _done(await _card(auth_client))["team"] is True

    # Customer: a partner row counts.
    r = await auth_client.post("/api/v1/partners", json={"name": "Globex SARL"})
    assert r.status_code == 201, r.text
    assert _done(await _card(auth_client))["customer"] is True

    # Invoice: the first ISSUED invoice counts (either side of the house does).
    r = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Globex SARL",
            "issue_date": "2026-08-01",
            "lines": [
                {"description": "Haulage", "quantity": "1", "unit_price": "100", "vat_rate": "0"}
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    card = await _card(auth_client)
    assert _done(card)["invoice"] is True
    assert card["complete"] is True
    assert card["done_count"] == 5


async def test_prod011_the_team_step_points_at_team_and_only_a_live_invite_ticks_it(
    auth_client, db_session
):
    """PROD-011 (audit 2026-09-05): the step linked to /settings, where there is
    no invite form (Team is /team), and ANY invitation row ticked it — one
    expired invite nobody could act on kept the step green for good."""
    from datetime import UTC, datetime, timedelta

    from app.models.invitation import Invitation

    card = await _card(auth_client)
    team = next(s for s in card["steps"] if s["key"] == "team")
    assert team["href"] == "/team"
    assert team["done"] is False

    r = await auth_client.post(
        "/api/v1/team/invites", json={"email": "driver@acme.io", "role": "user"}
    )
    assert r.status_code == 201, r.text
    assert _done(await _card(auth_client))["team"] is True

    # The invite lapses: nobody can act on it, so it no longer counts.
    inv = await db_session.scalar(select(Invitation).where(Invitation.email == "driver@acme.io"))
    inv.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()
    assert _done(await _card(auth_client))["team"] is False

    # A live invite again → ticked again (the derivation notices, nothing is stored).
    inv.expires_at = datetime.now(UTC) + timedelta(days=13)
    await db_session.commit()
    assert _done(await _card(auth_client))["team"] is True


async def test_dismiss_is_gated_stamped_idempotent_and_audited(auth_client, db_session):
    r = await auth_client.post("/api/v1/dashboard/onboarding/dismiss")
    assert r.status_code == 200, r.text
    assert r.json()["dismissed"] is True

    org = await db_session.scalar(select(Organization))
    first_stamp = org.onboarding_dismissed_at
    assert first_stamp is not None

    # Idempotent: a second dismiss keeps the FIRST stamp.
    assert (await auth_client.post("/api/v1/dashboard/onboarding/dismiss")).status_code == 200
    await db_session.refresh(org)
    assert org.onboarding_dismissed_at == first_stamp

    # Audited with what was still undone.
    audit = await auth_client.get("/api/v1/audit", params={"limit": 50})
    assert audit.status_code == 200
    events = audit.json()["items"] if isinstance(audit.json(), dict) else audit.json()
    dismissals = [e for e in events if e.get("action") == "onboarding.dismissed"]
    assert dismissals, events
    assert "invoice" in str(dismissals[0].get("meta"))


async def test_dismiss_requires_settings_manage(role_client):
    accountant = await role_client("accountant")
    # The card itself is readable…
    card = await accountant.get("/api/v1/dashboard/onboarding")
    assert card.status_code == 200
    assert card.json()["can_dismiss"] is False
    # …but dismissal is refused with the same authority the API declared.
    assert (await accountant.post("/api/v1/dashboard/onboarding/dismiss")).status_code == 403
