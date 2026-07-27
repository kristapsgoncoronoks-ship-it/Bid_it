"""Cost-allocation masters API (WO-14 / F1.1): structural authz (read = any
role, manage = admin), opaque cross-tenant 404, duplicate-code and stale-version
conflicts, the project transition rules over the wire, and §4.16 audit coverage
of every master mutation."""

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.costing import Department, Project
from app.models.organization import Organization
from app.services import costing


async def _org(db):
    return await db.scalar(select(Organization.id))


@pytest.mark.asyncio
async def test_admin_creates_and_lists_each_master(auth_client):
    for path, body in (
        ("departments", {"code": "OPS", "name": "Operations"}),
        ("cost-centers", {"code": "CC-100", "name": "Fleet"}),
        ("projects", {"code": "PRJ-1", "name": "Rollout", "start_date": "2026-01-01"}),
    ):
        made = await auth_client.post(f"/api/v1/masters/{path}", json=body)
        assert made.status_code == 201, made.text
        assert made.json()["status"] == "active" and made.json()["version"] == 1
        listed = await auth_client.get(f"/api/v1/masters/{path}")
        assert listed.status_code == 200
        assert [r["code"] for r in listed.json()] == [body["code"]]


@pytest.mark.asyncio
async def test_read_only_role_can_list_but_not_create(role_client):
    ro = await role_client("user_free")
    assert (await ro.get("/api/v1/masters/departments")).status_code == 200
    denied = await ro.post("/api/v1/masters/departments", json={"code": "X", "name": "x"})
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_patch_is_opaque_404(auth_client, db_session):
    """Tenant B's id probed through tenant A's session → 404, never 403 — and
    the fixtures overlap (same code/name) so a broken filter cannot pass by
    accident."""
    org_a = await _org(db_session)
    org_b = Organization(name="Other Co")
    db_session.add(org_b)
    await db_session.flush()
    ours = await costing.create_department(db_session, org_a, code="OPS", name="Operations")
    theirs = await costing.create_department(db_session, org_b.id, code="OPS", name="Operations")

    r = await auth_client.patch(
        f"/api/v1/masters/departments/{theirs.id}", json={"name": "Stolen", "version": 1}
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]

    # A's list shows zero of B's rows despite identical-looking data.
    listed = await auth_client.get("/api/v1/masters/departments")
    assert [row["id"] for row in listed.json()] == [ours.id]


@pytest.mark.asyncio
async def test_duplicate_code_conflicts(auth_client):
    assert (
        await auth_client.post("/api/v1/masters/projects", json={"code": "PRJ-1", "name": "One"})
    ).status_code == 201
    dup = await auth_client.post("/api/v1/masters/projects", json={"code": "prj-1", "name": "Two"})
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_stale_version_conflicts_and_row_unchanged(auth_client):
    made = (
        await auth_client.post("/api/v1/masters/cost-centers", json={"code": "CC-1", "name": "A"})
    ).json()
    stale = await auth_client.patch(
        f"/api/v1/masters/cost-centers/{made['id']}", json={"name": "B", "version": 99}
    )
    assert stale.status_code == 409
    after = (await auth_client.get("/api/v1/masters/cost-centers")).json()[0]
    assert after["name"] == "A" and after["version"] == 1


@pytest.mark.asyncio
async def test_project_transition_rules_over_the_wire(auth_client, db_session):
    made = (
        await auth_client.post("/api/v1/masters/projects", json={"code": "P", "name": "P"})
    ).json()
    pid = made["id"]

    closed = await auth_client.patch(
        f"/api/v1/masters/projects/{pid}", json={"status": "closed", "version": 1}
    )
    assert closed.status_code == 200 and closed.json()["status"] == "closed"

    archived = await auth_client.patch(
        f"/api/v1/masters/projects/{pid}", json={"status": "archived", "version": 2}
    )
    assert archived.status_code == 200
    # Archived rows leave the default list but stay under include_archived.
    assert (await auth_client.get("/api/v1/masters/projects")).json() == []
    assert [
        r["code"]
        for r in (await auth_client.get("/api/v1/masters/projects?include_archived=true")).json()
    ] == ["P"]

    # archived → closed is not a legal move (only archived → active is).
    bad = await auth_client.patch(
        f"/api/v1/masters/projects/{pid}", json={"status": "closed", "version": 3}
    )
    assert bad.status_code == 409
    row = await db_session.scalar(select(Project).where(Project.id == pid))
    assert row.status == "archived"


@pytest.mark.asyncio
async def test_department_cannot_archive_to_closed(auth_client):
    """`closed` exists only for projects; a department refuses it (409)."""
    made = (
        await auth_client.post("/api/v1/masters/departments", json={"code": "D", "name": "D"})
    ).json()
    bad = await auth_client.patch(
        f"/api/v1/masters/departments/{made['id']}", json={"status": "closed", "version": 1}
    )
    assert bad.status_code == 409


@pytest.mark.asyncio
async def test_cost_center_department_must_be_same_org(auth_client, db_session):
    org_b = Organization(name="Other Co")
    db_session.add(org_b)
    await db_session.flush()
    foreign = await costing.create_department(db_session, org_b.id, code="F", name="Foreign")
    r = await auth_client.post(
        "/api/v1/masters/cost-centers",
        json={"code": "CC-X", "name": "X", "department_id": foreign.id},
    )
    assert r.status_code == 409
    assert (await auth_client.get("/api/v1/masters/cost-centers")).json() == []


@pytest.mark.asyncio
async def test_costing_mutations_are_audited(auth_client, db_session):
    """§4.16: create, rename and archive each append an audit event, with
    old→new meta where a value changed."""
    made = (
        await auth_client.post("/api/v1/masters/departments", json={"code": "OPS", "name": "Ops"})
    ).json()
    await auth_client.patch(
        f"/api/v1/masters/departments/{made['id']}", json={"name": "Operations", "version": 1}
    )
    await auth_client.patch(
        f"/api/v1/masters/departments/{made['id']}", json={"status": "archived", "version": 2}
    )

    events = list(
        await db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id == made["id"])
            .order_by(AuditEvent.seq.asc())
        )
    )
    assert [e.action for e in events] == ["master.create", "master.update", "master.update"]
    create_meta, rename_meta, archive_meta = (json.loads(e.meta) for e in events)
    assert create_meta["code"] == "OPS" and create_meta["kind"] == "department"
    assert rename_meta["changed"]["name"] == {"old": "Ops", "new": "Operations"}
    assert archive_meta["changed"]["status"] == {"old": "active", "new": "archived"}

    row = await db_session.scalar(select(Department).where(Department.id == made["id"]))
    assert row.status == "archived" and row.version == 3
