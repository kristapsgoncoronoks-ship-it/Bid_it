"""DB-014 / DB-020 (audit 2026-09-05, P2 batch 8) — vendor bank identity.

DB-014  every stored IBAN is canonical (the write paths already were; the
        migration brings legacy rows in line and reports the invalid ones),
        and two vendors on ONE account are SHOWN — on the created vendor, on
        the list, on the pending change the approver reads, in the audit meta
        as vendor ids — never refused here (DECISIONS §26).
DB-020  `invoices.vendor_id` is the composite tenant-safe link: the SQLite
        suite runs with foreign keys enforced (QA-011), so an invoice carrying
        another workspace's vendor is refused by the database; the migration's
        pre-flight refuses a table that already holds one. The Postgres proof
        is `test_db020_vendor_composite_fk_pg.py`.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.models.audit import AuditEvent
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.vendor import Vendor

IBAN_A = "DE89370400440532013000"
IBAN_B = "NL91ABNA0417164300"
IBAN_A_SPACED = "de89 3704 0044 0532 0130 00"
IBAN_BAD_CHECK = "DE89370400440532013001"  # one digit off: fails MOD-97

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "a8b9c0d1e2f3_vendor_iban_and_link.py"


def _migration():
    spec = importlib.util.spec_from_file_location("_mig_batch8", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _vendor(auth_client, name, iban):
    r = await auth_client.post("/api/v1/vendors", json={"name": name, "iban": iban})
    assert r.status_code == 201, r.text
    return r.json()


async def _audit_meta(db_session, action, target_id):
    row = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == action, AuditEvent.target_id == target_id)
        .order_by(AuditEvent.seq.desc())
    )
    assert row is not None, f"no audit row {action} for {target_id}"
    return json.loads(row.meta or "{}")  # stored as a JSON string


# --------------------------------------------------------------------------- #
# DB-014 — the collision surface
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_second_vendor_on_the_same_account_is_named_never_refused(auth_client, db_session):
    steel = await _vendor(auth_client, "Steel GmbH", IBAN_A)
    assert steel["iban_shared_with"] == []

    trading = await _vendor(auth_client, "Steel Trading GmbH", IBAN_A)
    assert trading["status"] == "provisional"
    assert trading["iban_shared_with"] == [{"id": steel["id"], "name": "Steel GmbH"}]

    # The list read names each other on BOTH rows (one query, not N).
    rows = {v["name"]: v for v in (await auth_client.get("/api/v1/vendors")).json()}
    assert [s["name"] for s in rows["Steel GmbH"]["iban_shared_with"]] == ["Steel Trading GmbH"]
    assert [s["name"] for s in rows["Steel Trading GmbH"]["iban_shared_with"]] == ["Steel GmbH"]

    # The trail carries the collision as vendor ids and still no full IBAN.
    meta = await _audit_meta(db_session, "vendor.create", trading["id"])
    assert meta["iban_shared_with"] == [steel["id"]]
    assert IBAN_A not in json.dumps(meta)


@pytest.mark.asyncio
async def test_spaced_lower_case_input_is_stored_canonical_and_collides_as_such(
    auth_client, db_session
):
    cargo = await _vendor(auth_client, "Cargo BV", IBAN_A_SPACED)
    row = await db_session.scalar(select(Vendor).where(Vendor.id == cargo["id"]))
    assert row.iban == IBAN_A
    # Canonical storage is what makes the collision findable: the same account
    # typed differently is the same account.
    twin = await _vendor(auth_client, "Cargo Twin BV", IBAN_A)
    assert [s["id"] for s in twin["iban_shared_with"]] == [cargo["id"]]


@pytest.mark.asyncio
async def test_a_pending_iban_change_names_the_vendors_already_on_that_account(
    auth_client, client, db_session
):
    steel = await _vendor(auth_client, "Steel GmbH", IBAN_A)
    cargo = await _vendor(auth_client, "Cargo BV", IBAN_B)

    r = await auth_client.patch(f"/api/v1/vendors/{cargo['id']}", json={"iban": IBAN_A})
    assert r.status_code == 200, r.text
    assert r.json()["iban"] == IBAN_B  # unchanged — pending, as before
    assert r.json()["iban_shared_with"] == []  # the STORED account is not shared

    inbox = (await auth_client.get("/api/v1/vendors/changes")).json()
    (req,) = [c for c in inbox if c["vendor_id"] == cargo["id"]]
    assert req["field"] == "iban"
    assert req["shared_with"] == [{"id": steel["id"], "name": "Steel GmbH"}]
    meta = await _audit_meta(db_session, "vendor.change_requested", req["id"])
    assert meta["iban_shared_with"] == [steel["id"]]
    assert IBAN_A not in json.dumps(meta)

    # A second approver applies it; the decision response names the holders
    # too, and the applied row now collides on the list.
    approver = await _member(auth_client, client, "approver@acme.io")
    dec = await auth_client.post(
        f"/api/v1/vendors/changes/{req['id']}/approve", headers=_h(approver), json={}
    )
    assert dec.status_code == 200, dec.text
    assert dec.json()["status"] == "approved"
    assert [s["id"] for s in dec.json()["shared_with"]] == [steel["id"]]
    meta = await _audit_meta(db_session, "vendor.change_approved", cargo["id"])
    assert meta["iban_shared_with"] == [steel["id"]]
    rows = {v["name"]: v for v in (await auth_client.get("/api/v1/vendors")).json()}
    assert [s["name"] for s in rows["Cargo BV"]["iban_shared_with"]] == ["Steel GmbH"]


@pytest.mark.asyncio
async def test_the_rule_reads_the_field_not_the_value(auth_client):
    """`shared_with` answers a question about a BANK ACCOUNT. The fixture is
    deliberately adversarial: a tax id whose text is exactly another vendor's
    IBAN. Drop the `field == "iban"` half of the rule and this request would
    name that vendor — which is why the fixture looks odd."""
    steel = await _vendor(auth_client, "Steel GmbH", IBAN_A)
    r = await auth_client.post("/api/v1/vendors", json={"name": "Tax Co", "tax_id": "EE999999999"})
    vid = r.json()["id"]
    # A tax-id change whose new value collides with Steel's stored account.
    await auth_client.patch(f"/api/v1/vendors/{vid}", json={"tax_id": IBAN_A})
    (req,) = [
        c
        for c in (await auth_client.get("/api/v1/vendors/changes")).json()
        if c["vendor_id"] == vid
    ]
    assert req["field"] == "tax_id"
    assert req["new_value"] == IBAN_A  # the value DOES match a vendor's account
    assert req["shared_with"] == []  # …and it is still not asked about
    assert steel["id"] not in str(req["shared_with"])


@pytest.mark.asyncio
async def test_every_surface_answers_shared_with_the_same_way(auth_client, client):
    """One rule, four producers. The inbox, the copy nested in `GET /vendors`,
    an approved request and a rejected one all name the holders of the account
    the request points at — the version that shipped first had the inbox and
    approve populate it, reject empty it, and the nested copy always empty."""
    steel = await _vendor(auth_client, "Steel GmbH", IBAN_A)
    cargo = await _vendor(auth_client, "Cargo BV", IBAN_B)
    ref = [{"id": steel["id"], "name": "Steel GmbH"}]

    await auth_client.patch(f"/api/v1/vendors/{cargo['id']}", json={"iban": IBAN_A})
    (req,) = [
        c
        for c in (await auth_client.get("/api/v1/vendors/changes")).json()
        if c["vendor_id"] == cargo["id"]
    ]
    assert req["shared_with"] == ref  # 1. the inbox

    rows = {v["name"]: v for v in (await auth_client.get("/api/v1/vendors")).json()}
    assert rows["Cargo BV"]["pending_changes"][0]["shared_with"] == ref  # 2. nested

    approver = await _member(auth_client, client, "approver3@acme.io")
    rej = await auth_client.post(
        f"/api/v1/vendors/changes/{req['id']}/reject", headers=_h(approver), json={"note": "no"}
    )
    assert rej.json()["status"] == "rejected"
    assert rej.json()["shared_with"] == ref  # 3. a refused request still says so

    # 4. an approved one, on a second request for the same account
    await auth_client.patch(f"/api/v1/vendors/{cargo['id']}", json={"iban": IBAN_A})
    (again,) = [
        c
        for c in (await auth_client.get("/api/v1/vendors/changes")).json()
        if c["vendor_id"] == cargo["id"]
    ]
    dec = await auth_client.post(
        f"/api/v1/vendors/changes/{again['id']}/approve", headers=_h(approver), json={}
    )
    assert dec.json()["status"] == "approved" and dec.json()["shared_with"] == ref


@pytest.mark.asyncio
async def test_the_review_patch_refuses_another_workspaces_vendor(auth_client, db_session):
    """SEC (found by the batch-8 panel): `PATCH /invoices/{id}/review` took a
    client-supplied `vendor_id` and assigned it with no ownership check. Before
    DB-020 that COMMITTED a cross-workspace link — the exact hole DB-020 closes;
    after it, the flush died on the constraint as an opaque 500. The route now
    refuses it itself with the create path's opaque 404, so the database is the
    backstop and not the primary control."""
    me = (await auth_client.get("/api/v1/auth/me")).json()
    my_org = me["organization"]["id"]
    other = Organization(name="Other Haulage BV", plan="trial", status="active")
    db_session.add(other)
    await db_session.flush()
    theirs = Vendor(org_id=other.id, name="Their Steel GmbH")
    db_session.add(theirs)
    await db_session.commit()

    created = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "My Steel GmbH",
            "invoice_number": "INV-REVIEW-1",
            "issue_date": "2026-05-01",
            "currency": "EUR",
            "status": "pending",
            "line_items": [
                {
                    "description": "Diesel",
                    "category": "fuel",
                    "quantity": "1",
                    "unit_price": "100",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    inv = created.json()

    r = await auth_client.patch(
        f"/api/v1/invoices/{inv['id']}/review",
        json={"version": inv.get("version", 1), "vendor_id": theirs.id},
    )
    assert r.status_code == 404, r.text  # opaque: foreign and nonexistent alike
    assert "Vendor not found" in r.text

    # …and the invoice still points at its own workspace's supplier.
    row = await db_session.scalar(select(Invoice).where(Invoice.id == inv["id"]))
    assert row is not None
    mine = await db_session.scalar(select(Vendor).where(Vendor.id == row.vendor_id))
    assert mine is not None and mine.org_id == my_org


# --------------------------------------------------------------------------- #
# DB-014 — the migration's normaliser on a bare table
# --------------------------------------------------------------------------- #


def test_the_migration_normalises_legacy_rows_and_leaves_invalid_ones_named():
    mig = _migration()
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE vendors (id INTEGER PRIMARY KEY, iban TEXT)"))
        conn.execute(
            text("INSERT INTO vendors (iban) VALUES (:a), (:b), (:c), (:d), (NULL)"),
            {"a": IBAN_A_SPACED, "b": IBAN_B, "c": IBAN_BAD_CHECK, "d": IBAN_A_SPACED},
        )
        rewritten, invalid = mig.normalise_ibans(conn, "vendors")
        # Two rows carried the spaced form of one account; the canonical row
        # was already canonical; the bad-check-digit row is reported, untouched.
        assert rewritten == 2
        # sorted: neither dialect promises an order for SELECT DISTINCT.
        assert sorted(invalid) == ["…3001 (len 22)"]
        stored = sorted(
            r[0] or "" for r in conn.execute(text("SELECT iban FROM vendors")).fetchall()
        )
        assert stored == ["", IBAN_A, IBAN_A, IBAN_BAD_CHECK, IBAN_B]
        # Idempotent: a second pass rewrites nothing and reports the same row.
        rewritten2, invalid2 = mig.normalise_ibans(conn, "vendors")
        assert (rewritten2, sorted(invalid2)) == (0, ["…3001 (len 22)"])


# --------------------------------------------------------------------------- #
# DB-020 — the tenant-safe link, both halves
# --------------------------------------------------------------------------- #


def test_the_pre_flight_runs_before_the_migration_writes_anything():
    """Order matters, and only on SQLite — whose DDL is not transactional. With
    the index created first, a refused run left it behind and every retry died
    with "index ix_vendors_org_iban already exists", replacing the real reason
    with a meaningless one. Refusing before any write also keeps the log honest:
    a refused run no longer reports rewrites it then rolled back."""
    src = MIGRATION.read_text()
    body = src[src.index("def upgrade()") :]
    assert body.index("preflight(bind)") < body.index("normalise_ibans("), body[:400]
    assert body.index("preflight(bind)") < body.index("op.create_index("), body[:400]


def test_the_migration_refuses_a_cross_workspace_vendor_link():
    mig = _migration()
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE vendors (id TEXT PRIMARY KEY, org_id TEXT)"))
        conn.execute(
            text("CREATE TABLE invoices (id TEXT PRIMARY KEY, org_id TEXT, vendor_id TEXT)")
        )
        conn.execute(text("INSERT INTO vendors VALUES ('v1', 'org-a'), ('v2', 'org-b')"))
        conn.execute(text("INSERT INTO invoices VALUES ('i1', 'org-a', 'v1')"))
        assert mig.cross_workspace_vendor_links(conn) == 0
        mig.preflight(conn)  # clean: passes silently
        conn.execute(text("INSERT INTO invoices VALUES ('i2', 'org-a', 'v2')"))
        assert mig.cross_workspace_vendor_links(conn) == 1
        with pytest.raises(RuntimeError, match=r"\[DB-020\] 1 invoice"):
            mig.preflight(conn)


@pytest.mark.asyncio
async def test_an_invoice_cannot_carry_another_workspaces_vendor(auth_client, db_session):
    me = (await auth_client.get("/api/v1/auth/me")).json()
    my_org = me["organization"]["id"]
    other = Organization(name="Other Haulage BV", plan="trial", status="active")
    db_session.add(other)
    await db_session.flush()
    theirs = Vendor(org_id=other.id, name="Their Steel GmbH")
    mine = Vendor(org_id=my_org, name="My Steel GmbH")
    db_session.add_all([theirs, mine])
    await db_session.flush()

    def invoice(vendor_id: str, number: str) -> Invoice:
        return Invoice(
            org_id=my_org,
            vendor_id=vendor_id,
            invoice_number=number,
            issue_date=date(2026, 5, 1),
            due_date=date(2026, 6, 1),
            currency="EUR",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("21.00"),
            total=Decimal("121.00"),
        )

    db_session.add(invoice(mine.id, "INV-OWN"))
    await db_session.flush()  # the positive control: own vendor, accepted

    db_session.add(invoice(theirs.id, "INV-CROSS"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
