"""SAML SP scaffolding (Phase 4, ADR-0021): the offline-provable request side
(AuthnRequest, redirect binding, SP metadata) + the deliberate assertion-
consumption boundary. Assertion VALIDATION is intentionally not implemented — it
needs a vetted XML-DSig library + a real IdP (the documented finish line)."""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.services import saml

# --- pure builders ---------------------------------------------------------


def test_authn_request_structure():
    xml = saml.build_authn_request(
        request_id="_abc",
        issue_instant="2026-07-21T00:00:00Z",
        sp_entity_id="https://sp/acme",
        acs_url="https://sp/acs",
        idp_sso_url="https://idp/sso",
    )
    assert "<samlp:AuthnRequest" in xml and 'ID="_abc"' in xml and 'Version="2.0"' in xml
    assert "<saml:Issuer>https://sp/acme</saml:Issuer>" in xml
    assert 'Destination="https://idp/sso"' in xml


def test_redirect_binding_roundtrip():
    xml = saml.build_authn_request(
        request_id="_r",
        issue_instant="2026-07-21T00:00:00Z",
        sp_entity_id="sp",
        acs_url="acs",
        idp_sso_url="https://idp/sso",
    )
    url = saml.redirect_binding_url("https://idp/sso", xml, relay_state="acme")
    q = parse_qs(urlparse(url).query)
    assert "SAMLRequest" in q and q["RelayState"] == ["acme"]
    # DEFLATE+base64 is reversible back to the original AuthnRequest.
    assert saml.decode_redirect_saml(q["SAMLRequest"][0]) == xml


def test_sp_metadata_structure():
    md = saml.sp_metadata_xml(sp_entity_id="https://sp/acme", acs_url="https://sp/acs")
    assert 'entityID="https://sp/acme"' in md
    assert 'WantAssertionsSigned="true"' in md
    assert 'Location="https://sp/acs"' in md


def test_consume_assertion_is_the_boundary():
    with pytest.raises(saml.SamlNotReady):
        saml.consume_assertion()


# --- routes ----------------------------------------------------------------


async def _saml_conn(db, org_id, **over):
    fields = dict(
        org_id=org_id,
        slug="acme",
        protocol="saml",
        enabled=True,
        default_role="user",
        saml_sso_url="https://idp.example.com/sso",
    )
    fields.update(over)
    c = SsoConnection(**fields)
    db.add(c)
    await db.commit()
    return c


@pytest.mark.asyncio
async def test_metadata_route(auth_client, db_session):
    org_id = await db_session.scalar(select(Organization.id))
    await _saml_conn(db_session, org_id)
    r = await auth_client.get("/api/v1/auth/sso/acme/saml/metadata")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "EntityDescriptor" in r.text and "AssertionConsumerService" in r.text


@pytest.mark.asyncio
async def test_saml_login_redirects_to_idp(auth_client, db_session):
    org_id = await db_session.scalar(select(Organization.id))
    await _saml_conn(db_session, org_id)
    r = await auth_client.get("/api/v1/auth/sso/acme/saml/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://idp.example.com/sso?") and "SAMLRequest=" in loc


@pytest.mark.asyncio
async def test_saml_login_400_when_unconfigured(auth_client, db_session):
    org_id = await db_session.scalar(select(Organization.id))
    await _saml_conn(db_session, org_id, saml_sso_url=None)
    r = await auth_client.get("/api/v1/auth/sso/acme/saml/login", follow_redirects=False)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_acs_returns_not_implemented(auth_client):
    r = await auth_client.post("/api/v1/auth/sso/saml/acs")
    assert r.status_code == 501
    assert "ADR-0021" in r.json()["detail"] or "not enabled" in r.json()["detail"].lower()
