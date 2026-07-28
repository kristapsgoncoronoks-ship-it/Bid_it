"""The 8 stored roles (the original 4-tier ladder + the 4 A1.5-reachable
business roles) + the system matrix (WO-47: per-PLAN usage limits, org-wide,
NOT per-role) + quota gating."""

import pytest


def _inv(n):
    return {
        "vendor_name": "Acme",
        "invoice_number": f"INV-{n}",
        "issue_date": "2026-07-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }


async def _member(auth_client, client, email, role):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    assert inv.status_code == 201, inv.text
    token = inv.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite", json={"token": token, "name": "M", "password": "supersecret"}
    )
    return acc.json()["token"]["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _make_platform_op(db_session, email="owner@acme.io"):
    """Promote a user to platform operator (the only role that may edit the global
    matrix). get_current_user reloads the user each request, so this takes effect
    immediately for the existing token."""
    from sqlalchemy import update

    from app.models.user import User

    await db_session.execute(update(User).where(User.email == email).values(is_platform_admin=True))
    await db_session.commit()


async def _set_org_plan(db_session, plan, org_email="owner@acme.io"):
    """Set the (single, in this fixture) organization's subscription plan."""
    from sqlalchemy import select, update

    from app.models.organization import Organization
    from app.models.user import User

    org_id = await db_session.scalar(select(User.org_id).where(User.email == org_email))
    await db_session.execute(
        update(Organization).where(Organization.id == org_id).values(plan=plan)
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_registrant_is_company_owner_not_sysadmin(auth_client):
    # The first registrant is the OWNER of their company, never a system admin.
    me = (await auth_client.get("/api/v1/auth/me")).json()
    assert me["user"]["role"] == "owner"
    assert me["user"]["is_platform_admin"] is False


@pytest.mark.asyncio
async def test_matrix_has_four_plans_with_defaults(auth_client, db_session):
    # WO-47: the quota matrix is keyed by PLAN (trial/starter/pro/enterprise —
    # `app.services.plans.PLANS`), not by the 8-role permission vocabulary.
    await _make_platform_op(db_session)
    m = {r["plan"]: r for r in (await auth_client.get("/api/v1/access/matrix")).json()}
    assert set(m) == {"trial", "starter", "pro", "enterprise"}
    assert m["trial"]["monthly_invoice_limit"] == 10
    assert m["trial"]["paid"] is False
    assert m["starter"]["paid"] is True
    assert m["starter"]["monthly_invoice_limit"] == 1000
    # The higher, business-oriented paid plans default unlimited.
    for plan in ("pro", "enterprise"):
        assert m[plan]["monthly_invoice_limit"] == 0
        assert m[plan]["monthly_upload_limit"] == 0
        assert m[plan]["paid"] is True


@pytest.mark.asyncio
async def test_finance_manager_can_create_invoices_under_the_org_plan(
    auth_client, client, db_session
):
    """A business role (finance_manager, A1.5) creates invoices fine and reads
    its usage through the ORG's plan — no crash, no per-role special case."""
    from sqlalchemy import select

    from app.models.organization import Organization

    org = await db_session.scalar(select(Organization.id))
    assert org is not None

    token = await _member(auth_client, client, "fm@acme.io", "finance_manager")
    created = await client.post("/api/v1/invoices", json=_inv(500), headers=_h(token))
    assert created.status_code == 201, created.text

    # Default org plan is "trial" (limit 10) — the finance_manager shares that
    # SAME org-wide cap; nothing about their role grants a different one.
    usage = (await client.get("/api/v1/access/usage", headers=_h(token))).json()
    assert usage["plan"] == "trial"
    assert usage["unlimited"] is False and usage["invoice_limit"] == 10


@pytest.mark.asyncio
async def test_only_platform_operator_edits_the_global_matrix(auth_client, client, db_session):
    # A plain user cannot edit the matrix.
    u = await _member(auth_client, client, "u@acme.io", "user")
    assert (
        await client.put(
            "/api/v1/access/matrix/trial",
            json={"monthly_invoice_limit": 5, "monthly_upload_limit": 5},
            headers=_h(u),
        )
    ).status_code == 403

    # NEITHER can a company OWNER — the matrix is global, not their company's.
    denied = await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 2, "monthly_upload_limit": 9},
    )
    assert denied.status_code == 403

    # Only a platform operator can.
    await _make_platform_op(db_session)
    ok = await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 2, "monthly_upload_limit": 9},
    )
    assert ok.status_code == 200
    assert ok.json()["monthly_invoice_limit"] == 2


@pytest.mark.asyncio
async def test_unknown_plan_key_is_404(auth_client, db_session):
    await _make_platform_op(db_session)
    r = await auth_client.put(
        "/api/v1/access/matrix/not-a-real-plan",
        json={"monthly_invoice_limit": 1, "monthly_upload_limit": 1},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trial_plan_invoice_limit_enforced_org_wide(auth_client, client, db_session):
    # Tighten the trial plan's limit to 2, then the org hits it on the 3rd
    # invoice — the default org plan is "trial" (Organization.plan default).
    await _make_platform_op(db_session)
    await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 2, "monthly_upload_limit": 5},
    )

    assert (await auth_client.post("/api/v1/invoices", json=_inv(1))).status_code == 201
    assert (await auth_client.post("/api/v1/invoices", json=_inv(2))).status_code == 201
    blocked = await auth_client.post("/api/v1/invoices", json=_inv(3))
    assert blocked.status_code == 402
    assert "limit" in blocked.json()["detail"].lower()

    # Usage endpoint reflects the cap (counts the org's 2 invoices this month).
    usage = (await auth_client.get("/api/v1/access/usage")).json()
    assert (
        usage["invoice_limit"] == 2
        and usage["invoices_remaining"] == 0
        and usage["unlimited"] is False
    )


@pytest.mark.asyncio
async def test_two_different_roles_share_one_org_level_cap(auth_client, client, db_session):
    """WO-47's core acceptance criterion: two members of the SAME org, holding
    DIFFERENT roles, share the SAME org-wide cap and the SAME running count —
    a low-privilege member's invoices count against a high-privilege member's
    remaining quota, and vice versa. This is the exact bug the role-keyed
    model had: previously an `admin`'s own role carried a separate (usually
    unlimited) limit, letting them blow straight through a cap a `user_free`
    teammate in the identical org had already hit."""
    await _make_platform_op(db_session)
    await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 3, "monthly_upload_limit": 5},
    )
    free = await _member(auth_client, client, "free@acme.io", "user_free")
    admin = await _member(auth_client, client, "admin3@acme.io", "admin")

    # free-tier member uses 2 of the org's 3.
    assert (
        await client.post("/api/v1/invoices", json=_inv(10), headers=_h(free))
    ).status_code == 201
    assert (
        await client.post("/api/v1/invoices", json=_inv(11), headers=_h(free))
    ).status_code == 201
    # admin (a role that was unconditionally unlimited before WO-47) uses the
    # LAST slot of the SAME org cap...
    assert (
        await client.post("/api/v1/invoices", json=_inv(12), headers=_h(admin))
    ).status_code == 201
    # ...and is now blocked too — the cap is the ORG's, not per-role.
    blocked = await client.post("/api/v1/invoices", json=_inv(13), headers=_h(admin))
    assert blocked.status_code == 402

    # Both members see the identical, shared usage figure.
    for token in (free, admin):
        usage = (await client.get("/api/v1/access/usage", headers=_h(token))).json()
        assert usage["invoices_used"] == 3 and usage["invoice_limit"] == 3


@pytest.mark.asyncio
async def test_plan_governs_not_role_any_role_unlimited_on_an_unlimited_plan(
    auth_client, client, db_session
):
    """The flip side: on a plan with an UNLIMITED cap (`pro`), even the LOWEST
    permission role (`user_free`) is unlimited — proving the gate reads the
    org's plan, never the caller's role."""
    await _set_org_plan(db_session, "pro")
    free = await _member(auth_client, client, "free2@acme.io", "user_free")
    for n in range(3):
        r = await client.post("/api/v1/invoices", json=_inv(200 + n), headers=_h(free))
        assert r.status_code == 201, r.text
    usage = (await client.get("/api/v1/access/usage", headers=_h(free))).json()
    assert usage["plan"] == "pro"
    assert usage["unlimited"] is True and usage["invoices_remaining"] is None


@pytest.mark.asyncio
async def test_admin_role_no_longer_bypasses_a_tight_org_cap(auth_client, client, db_session):
    """The regression this order fixes, stated as its own test: before WO-47 an
    `admin`'s role alone made them unconditionally unlimited (LIMIT_DEFAULTS
    keyed `admin: (0, 0)`), regardless of the org's plan. Now, on a org whose
    PLAN has a tight cap, an admin is blocked exactly like anyone else in that
    org once the org-wide count is reached."""
    await _make_platform_op(db_session)
    await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 1, "monthly_upload_limit": 1},
    )
    admin = await _member(auth_client, client, "admin4@acme.io", "admin")
    assert (
        await client.post("/api/v1/invoices", json=_inv(300), headers=_h(admin))
    ).status_code == 201
    blocked = await client.post("/api/v1/invoices", json=_inv(301), headers=_h(admin))
    assert blocked.status_code == 402
    usage = (await client.get("/api/v1/access/usage", headers=_h(admin))).json()
    assert usage["unlimited"] is False and usage["invoices_remaining"] == 0


@pytest.mark.asyncio
async def test_a_blocked_upload_never_stores_the_file_never_loses_it(
    auth_client, client, db_session
):
    """The guardrail: 'never lose a document because of a limit'. The chosen
    overage policy is BLOCK-AT-THE-CAP (auto-charge needs live billing — an
    owner-blocked decision). Proven here as: hitting the upload cap returns
    402 BEFORE anything is persisted — exactly the ONE upload that was under
    the cap left a trace; the refused second upload left none at all, so
    nothing was ever accepted and then discarded."""
    from sqlalchemy import select

    from app.models.extraction_run import ExtractionRun
    from app.models.organization import Organization

    await _make_platform_op(db_session)
    r = await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 100, "monthly_upload_limit": 1},
    )
    assert r.status_code == 200

    org_id = await db_session.scalar(select(Organization.id))

    csv_a = b"description,category,quantity,unit_price,tax_rate,vendor,invoice_number,issue_date\n"
    files = {"file": ("a.csv", csv_a, "text/csv")}
    ok = await auth_client.post("/api/v1/invoices/upload", files=files)
    assert ok.status_code == 202, ok.text

    files2 = {"file": ("b.csv", csv_a, "text/csv")}
    blocked = await auth_client.post("/api/v1/invoices/upload", files=files2)
    assert blocked.status_code == 402
    assert "limit" in blocked.json()["detail"].lower()

    # Exactly one extraction run exists for the org (from the FIRST, allowed
    # upload) — the refused second upload never queued or stored anything.
    runs = (
        (await db_session.execute(select(ExtractionRun).where(ExtractionRun.org_id == org_id)))
        .scalars()
        .all()
    )
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_admin_can_manage_users_but_plain_user_cannot(auth_client, client, db_session):
    # Matrix-aligned authz: MEMBER_MANAGE is held by OWNER and ADMINISTRATOR, so an
    # admin can now manage users; EMPLOYEE (plain 'user') cannot.
    from sqlalchemy import update

    from app.models.user import User, UserRole

    admin = await _member(auth_client, client, "admin2@acme.io", "admin")
    # Admin can reach the admin panel (e.g. toggle a module) …
    assert (
        await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(admin))
    ).status_code == 200
    # … and now manage users/roles (MEMBER_MANAGE).
    invite = await client.post(
        "/api/v1/team/invites", json={"email": "x@acme.io", "role": "user"}, headers=_h(admin)
    )
    assert invite.status_code == 201
    # Demote that same account to a plain user → it loses MEMBER_MANAGE (403 at the
    # authz gate, before any seat check).
    await db_session.execute(
        update(User).where(User.email == "admin2@acme.io").values(role=UserRole.user)
    )
    await db_session.commit()
    denied = await client.post(
        "/api/v1/team/invites", json={"email": "z@acme.io", "role": "user"}, headers=_h(admin)
    )
    assert denied.status_code == 403
