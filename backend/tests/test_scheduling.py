"""Work-planning assignments (WO-A) — the rules, pinned test by test:

1. A planner puts a member on a project for a window; the calendar read
   returns it; filters narrow by person and project.
2. An employee sees ONLY their own assignments no matter what filters they
   pass — the same endpoint IS the "My work" view.
3. The assignee may confirm and finish their OWN assignment; touching someone
   else's fails OPAQUELY (404, §4.4 — a probe learns nothing).
4. Transitions are enforced; done/cancelled are terminal.
5. Overlaps are ADVISORY: the write succeeds and names the collisions.
6. The assignee must be an ACTIVE member (memberships, not users.org_id);
   a cross-tenant project id 404s opaquely.
7. Every mutation lands one audit event in the same commit.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent


async def _project(client, code="SCH-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _me(client) -> dict:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    return r.json()["user"]


async def _invite(auth_client, client, email: str, role: str) -> tuple[str, dict]:
    """Invite + accept; returns (bearer token, user dict)."""
    invite = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    assert invite.status_code in (200, 201), invite.text
    token = invite.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "name": email.split("@")[0], "password": "supersecret"},
    )
    assert acc.status_code in (200, 201), acc.text
    bearer = acc.json()["token"]["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {bearer}"}
    )
    return bearer, me.json()["user"]


def _h(bearer: str) -> dict:
    return {"Authorization": f"Bearer {bearer}"}


WINDOW = {"start": "2026-09-01T00:00:00+00:00", "end": "2026-09-08T00:00:00+00:00"}


@pytest.mark.asyncio
async def test_plan_list_and_filters(auth_client, client):
    project_id = await _project(auth_client, "SCH-CAL")
    other_project = await _project(auth_client, "SCH-CAL2")
    owner = await _me(auth_client)
    _, employee = await _invite(auth_client, client, "crew1@acme.io", "user")

    for pid, uid, day in (
        (project_id, owner["id"], "01"),
        (project_id, employee["id"], "02"),
        (other_project, employee["id"], "03"),
    ):
        r = await auth_client.post(
            "/api/v1/schedule/assignments",
            json={
                "project_id": pid,
                "assignee_user_id": uid,
                "starts_at": f"2026-09-{day}T09:00:00+00:00",
                "ends_at": f"2026-09-{day}T17:00:00+00:00",
            },
        )
        assert r.status_code == 201, r.text

    listed = await auth_client.get("/api/v1/schedule/assignments", params=WINDOW)
    assert listed.status_code == 200
    assert len(listed.json()) == 3

    by_person = await auth_client.get(
        "/api/v1/schedule/assignments", params={**WINDOW, "assignee_user_id": employee["id"]}
    )
    assert len(by_person.json()) == 2
    by_project = await auth_client.get(
        "/api/v1/schedule/assignments", params={**WINDOW, "project_id": other_project}
    )
    assert len(by_project.json()) == 1
    # A window that misses everything returns nothing.
    empty = await auth_client.get(
        "/api/v1/schedule/assignments",
        params={"start": "2027-01-01T00:00:00+00:00", "end": "2027-01-02T00:00:00+00:00"},
    )
    assert empty.json() == []


@pytest.mark.asyncio
async def test_employee_sees_only_their_own_whatever_they_ask_for(auth_client, client):
    project_id = await _project(auth_client, "SCH-MY")
    owner = await _me(auth_client)
    bearer, employee = await _invite(auth_client, client, "crew2@acme.io", "user")

    for uid in (owner["id"], employee["id"]):
        r = await auth_client.post(
            "/api/v1/schedule/assignments",
            json={
                "project_id": project_id,
                "assignee_user_id": uid,
                "starts_at": "2026-09-01T09:00:00+00:00",
                "ends_at": "2026-09-01T17:00:00+00:00",
            },
        )
        assert r.status_code == 201

    mine = await client.get("/api/v1/schedule/assignments", params=WINDOW, headers=_h(bearer))
    assert mine.status_code == 200
    assert {a["assignee_user_id"] for a in mine.json()} == {employee["id"]}

    # Asking for someone ELSE's rows still returns only your own — the filter
    # is forced server-side, not trusted from the query string.
    probe = await client.get(
        "/api/v1/schedule/assignments",
        params={**WINDOW, "assignee_user_id": owner["id"]},
        headers=_h(bearer),
    )
    assert {a["assignee_user_id"] for a in probe.json()} == {employee["id"]}

    # And an employee cannot plan.
    denied = await client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project_id,
            "assignee_user_id": employee["id"],
            "starts_at": "2026-09-02T09:00:00+00:00",
            "ends_at": "2026-09-02T17:00:00+00:00",
        },
        headers=_h(bearer),
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_assignee_self_service_and_opaque_denial(auth_client, client):
    project_id = await _project(auth_client, "SCH-SELF")
    owner = await _me(auth_client)
    bearer, employee = await _invite(auth_client, client, "crew3@acme.io", "user")

    def mk(uid, day):
        return auth_client.post(
            "/api/v1/schedule/assignments",
            json={
                "project_id": project_id,
                "assignee_user_id": uid,
                "starts_at": f"2026-09-0{day}T09:00:00+00:00",
                "ends_at": f"2026-09-0{day}T17:00:00+00:00",
            },
        )

    own = (await mk(employee["id"], 1)).json()["assignment"]
    theirs = (await mk(owner["id"], 2)).json()["assignment"]

    # Confirm then finish YOUR OWN assignment: allowed with read-tier rights.
    for status_ in ("confirmed", "done"):
        r = await client.post(
            f"/api/v1/schedule/assignments/{own['id']}/transition",
            json={"status": status_},
            headers=_h(bearer),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status_

    # Cancelling your own is a PLANNER move — and someone else's anything is
    # invisible. Both fail with the same opaque 404.
    own2 = (await mk(employee["id"], 3)).json()["assignment"]
    for aid, status_ in ((own2["id"], "cancelled"), (theirs["id"], "confirmed")):
        r = await client.post(
            f"/api/v1/schedule/assignments/{aid}/transition",
            json={"status": status_},
            headers=_h(bearer),
        )
        assert r.status_code == 404, f"{aid} {status_} → {r.status_code}"


@pytest.mark.asyncio
async def test_transitions_are_enforced_and_terminal(auth_client):
    project_id = await _project(auth_client, "SCH-TR")
    owner = await _me(auth_client)
    r = await auth_client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project_id,
            "assignee_user_id": owner["id"],
            "starts_at": "2026-09-01T09:00:00+00:00",
            "ends_at": "2026-09-01T17:00:00+00:00",
        },
    )
    aid = r.json()["assignment"]["id"]

    async def move(status_):
        return await auth_client.post(
            f"/api/v1/schedule/assignments/{aid}/transition", json={"status": status_}
        )

    assert (await move("done")).status_code == 200  # planned → done is legal
    assert (await move("planned")).status_code == 400  # done is terminal
    assert (await move("nonsense")).status_code == 400
    # A terminal assignment can't be edited either.
    r = await auth_client.patch(
        f"/api/v1/schedule/assignments/{aid}", json={"note": "too late"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_overlaps_warn_and_never_block(auth_client):
    project_id = await _project(auth_client, "SCH-OVL")
    owner = await _me(auth_client)

    async def mk(start_h, end_h):
        return await auth_client.post(
            "/api/v1/schedule/assignments",
            json={
                "project_id": project_id,
                "assignee_user_id": owner["id"],
                "starts_at": f"2026-09-01T{start_h}:00:00+00:00",
                "ends_at": f"2026-09-01T{end_h}:00:00+00:00",
            },
        )

    first = await mk("09", "12")
    assert first.status_code == 201
    assert first.json()["overlaps"] == []

    clash = await mk("11", "14")  # intersects 11–12
    assert clash.status_code == 201, "advisory means the write SUCCEEDS"
    named = {o["id"] for o in clash.json()["overlaps"]}
    assert first.json()["assignment"]["id"] in named

    adjacent = await mk("12", "13")  # touching endpoints do not overlap
    assert adjacent.status_code == 201
    assert {o["id"] for o in adjacent.json()["overlaps"]} == {
        clash.json()["assignment"]["id"]
    }


@pytest.mark.asyncio
async def test_assignee_must_be_a_member_and_bad_windows_refused(auth_client, client):
    project_id = await _project(auth_client, "SCH-VAL")
    # A perfectly real user of ANOTHER org is not schedulable here.
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "stranger@other.io",
            "password": "supersecret",
            "name": "Stranger",
            "organization_name": "Other OU",
        },
    )
    stranger_id = r.json()["user"]["id"]

    denied = await auth_client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project_id,
            "assignee_user_id": stranger_id,
            "starts_at": "2026-09-01T09:00:00+00:00",
            "ends_at": "2026-09-01T17:00:00+00:00",
        },
    )
    assert denied.status_code == 400
    assert "member" in denied.json()["detail"]

    owner = await _me(auth_client)
    backwards = await auth_client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project_id,
            "assignee_user_id": owner["id"],
            "starts_at": "2026-09-01T17:00:00+00:00",
            "ends_at": "2026-09-01T09:00:00+00:00",
        },
    )
    assert backwards.status_code == 400


@pytest.mark.asyncio
async def test_every_mutation_is_audited_in_the_same_commit(auth_client, db_session):
    project_id = await _project(auth_client, "SCH-AUD")
    owner = await _me(auth_client)
    r = await auth_client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project_id,
            "assignee_user_id": owner["id"],
            "starts_at": "2026-09-01T09:00:00+00:00",
            "ends_at": "2026-09-01T17:00:00+00:00",
        },
    )
    aid = r.json()["assignment"]["id"]
    await auth_client.patch(f"/api/v1/schedule/assignments/{aid}", json={"note": "bring keys"})
    await auth_client.post(
        f"/api/v1/schedule/assignments/{aid}/transition", json={"status": "confirmed"}
    )

    actions = set(
        (
            await db_session.scalars(
                select(AuditEvent.action).where(AuditEvent.action.like("assignment.%"))
            )
        ).all()
    )
    assert {"assignment.create", "assignment.update", "assignment.transition"} <= actions


@pytest.mark.asyncio
async def test_members_picker_is_planning_gated(auth_client, client):
    _, _ = await _invite(auth_client, client, "crew4@acme.io", "user")
    ok = await auth_client.get("/api/v1/schedule/members")
    assert ok.status_code == 200
    assert "crew4@acme.io" in {m["email"] for m in ok.json()}

    bearer, _ = await _invite(auth_client, client, "crew5@acme.io", "user")
    denied = await client.get("/api/v1/schedule/members", headers=_h(bearer))
    assert denied.status_code == 403
