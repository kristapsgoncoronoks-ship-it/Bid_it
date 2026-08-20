"""Dynamic document templates — the phase-5 machinery.

The owner's trust model, pinned test by test:

1. The demo masters exist without any manual step (seed-on-first-read).
2. Only a PLATFORM OPERATOR can touch the masters; a company owner cannot.
3. A client adjusts a master into their OWN saved copy — multiple versions —
   and a later platform edit NEVER reaches into a saved copy.
4. Rendering fills known tokens and leaves unknown ones VISIBLY unreplaced.
5. Generating attaches a real PDF to the project's documents — the same slot
   the uploaded contract uses.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.user import User
from app.services import doc_templates


async def _project(client, code="TPL-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _issuer(client):
    r = await client.put(
        "/api/v1/issuer",
        json={
            "legal_name": "Acme OU",
            "reg_number": "12345678",
            "vat_number": "EE101234567",
            "address_line1": "Main 1",
            "city": "Tallinn",
            "postal_code": "10111",
            "country": "EE",
            "invoice_prefix": "ACM-",
        },
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_demo_masters_appear_without_any_manual_step(auth_client):
    r = await auth_client.get("/api/v1/templates")
    assert r.status_code == 200, r.text
    keys = {t["key"] for t in r.json()["platform"]}
    assert {"demo-contract", "demo-acceptance", "demo-offer-cover"} <= keys
    # Every demo says what it is — an example, not legal advice.
    for t in r.json()["platform"]:
        if t["key"].startswith("demo-"):
            assert "not legal advice" in t["body"]


@pytest.mark.asyncio
async def test_only_a_platform_operator_touches_the_masters(auth_client, db_session):
    """A company owner is NOT the server owner. The master documents are the
    operator's surface — the same boundary as every /platform route."""
    denied = await auth_client.put(
        "/api/v1/platform/templates/demo-contract",
        json={"kind": "contract", "name": "X", "body": "Y"},
    )
    assert denied.status_code == 403

    owner = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    owner.is_platform_admin = True
    await db_session.commit()

    allowed = await auth_client.put(
        "/api/v1/platform/templates/lawyer-contract",
        json={
            "kind": "contract",
            "name": "Standard contract",
            "body": "THE LAWYER'S TEXT {{company.legal_name}}",
        },
    )
    assert allowed.status_code == 200, allowed.text
    listed = await auth_client.get("/api/v1/platform/templates")
    assert "lawyer-contract" in {t["key"] for t in listed.json()}


@pytest.mark.asyncio
async def test_a_client_saves_multiple_adjusted_versions_and_platform_edits_never_reach_them(
    auth_client, db_session
):
    """The core of the owner's idea: adjust the main document, save it, keep
    several versions, choose freely — and the saved copy is FROZEN against
    later master edits."""
    masters = (await auth_client.get("/api/v1/templates")).json()["platform"]
    master = next(t for t in masters if t["key"] == "demo-contract")

    v1 = await auth_client.post(
        "/api/v1/templates",
        json={"name": "Our contract (strict)", "source_platform_id": master["id"]},
    )
    assert v1.status_code == 201, v1.text
    v2 = await auth_client.post(
        "/api/v1/templates",
        json={
            "name": "Our contract (lenient)",
            "source_platform_id": master["id"],
            "body": master["body"] + "\n\nSPECIAL LENIENT CLAUSE.",
        },
    )
    assert v2.status_code == 201, v2.text

    own = (await auth_client.get("/api/v1/templates")).json()["own"]
    assert {t["name"] for t in own} == {"Our contract (strict)", "Our contract (lenient)"}

    # The operator now replaces the master's body (the lawyer's text landing).
    owner = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    owner.is_platform_admin = True
    await db_session.commit()
    r = await auth_client.put(
        "/api/v1/platform/templates/demo-contract",
        json={"kind": "contract", "name": "Service contract", "body": "REPLACED ENTIRELY"},
    )
    assert r.status_code == 200

    own_after = (await auth_client.get("/api/v1/templates")).json()["own"]
    for t in own_after:
        assert "REPLACED ENTIRELY" not in t["body"], (
            "a platform edit reached into a client's saved copy"
        )


@pytest.mark.asyncio
async def test_render_fills_known_tokens_and_leaves_gaps_visible(auth_client):
    await _issuer(auth_client)
    project_id = await _project(auth_client, "TPL-RND")

    masters = (await auth_client.get("/api/v1/templates")).json()["platform"]
    master = next(t for t in masters if t["key"] == "demo-contract")
    r = await auth_client.post(
        "/api/v1/templates/render-preview",
        json={
            "template_scope": "platform",
            "template_id": master["id"],
            "project_id": project_id,
        },
    )
    assert r.status_code == 200, r.text
    text = r.json()["text"]
    assert "TPL-RND" in text, "known tokens are filled"
    assert "Acme OU" in text
    # No customer chosen and no accepted offer → those tokens stay VISIBLE.
    assert "{{customer.name}}" in text, "an unknown token must stay visible, not vanish"
    assert "{{offer.total}}" in text


@pytest.mark.asyncio
async def test_generate_attaches_a_real_pdf_to_the_project(auth_client):
    await _issuer(auth_client)
    project_id = await _project(auth_client, "TPL-GEN")
    masters = (await auth_client.get("/api/v1/templates")).json()["platform"]
    master = next(t for t in masters if t["key"] == "demo-contract")

    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/generate-document",
        json={"template_scope": "platform", "template_id": master["id"]},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["kind"] == "contract"
    assert doc["filename"].endswith(".pdf")

    listed = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/documents")).json()
    assert doc["id"] in {d["id"] for d in listed}, "the generated document lives with the rest"

    dl = await auth_client.get(
        f"/api/v1/masters/projects/{project_id}/documents/{doc['id']}/download"
    )
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF"), "a real PDF, not a text file wearing the extension"


@pytest.mark.asyncio
async def test_saving_template_text_needs_settings_not_just_write(auth_client, client):
    """Contract WORDING is org configuration (SETTINGS_MANAGE): a finance user
    who books costs all day still doesn't rewrite the org's contract text."""
    invite = await auth_client.post(
        "/api/v1/team/invites", json={"email": "finance@acme.io", "role": "finance_manager"}
    )
    token = invite.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "name": "Finance", "password": "supersecret"},
    )
    finance_token = acc.json()["token"]["access_token"]

    r = await client.post(
        "/api/v1/templates",
        json={"name": "X", "kind": "contract", "body": "Y"},
        headers={"Authorization": f"Bearer {finance_token}"},
    )
    assert r.status_code == 403


def test_render_is_pure_and_total():
    """The renderer itself: substitution is exact, whitespace-tolerant, and
    unknown tokens survive verbatim."""
    out = doc_templates.render(
        "A {{ project.code }} B {{unknown.thing}} C {{project.code}}",
        {"project.code": "JOB-9"},
    )
    assert out == "A JOB-9 B {{unknown.thing}} C JOB-9"
