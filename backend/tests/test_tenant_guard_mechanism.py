"""The tenant guard's MECHANISM (PERF-019, reference integration R5).

Until R5 `app.core.tenant._apply_tenant_scope` attached one `with_loader_criteria`
option PER registered model — 103 of them, plus 5 soft-delete criteria — to
every SELECT, and SQLAlchemy regenerated the statement's cache key over all
of them on every execute: measured at roughly half of every request's loop
time (`docs/perf/TENANT-GUARD-2026-09-08.md`). Now ONE option on the
declarative `Base` decides per class, by attribute through the mapper, what
the predicate is, with the tenant id as a typed bind parameter closed over.

These tests are the conditions the Security lens set for replacing the
mechanism (plan §6, R4 and R5 panels): two tenants share ONE compiled
statement (equal cache keys, differing bind values) and never each other's
rows; aliases — `User` included — and column selects stay scoped; the `User`
predicate is membership existence; eager-load joins carry the criteria in
their ON clause and separate relationship loads inherit the parent's org; a
lazy load across a cross-org foreign key returns nothing; the ONE option
scopes exactly the registry and reaches every mapper; an empty registry
attaches nothing (the parity self-test relies on it); the reserved bind name
is used nowhere else; the documented reach rule holds at compile level; and
the mechanism executes on Postgres (the suite's fixtures are SQLite — the
last test builds its own engine on `RLS_TEST_DATABASE_URL`, as `test_rls.py`
does, and runs in CI's `postgres` job).
"""

import os
import uuid

import pytest
from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import aliased, joinedload, selectinload

from app.core import tenant
from app.core.tenant import TENANT_BIND_NAME, include_deleted, reset_current_org, set_current_org
from app.models.base import Base
from app.models.invoice import Invoice
from app.models.membership import Membership
from app.models.user import User, UserRole
from app.models.vendor import Vendor

RLS_URL = os.environ.get("RLS_TEST_DATABASE_URL")
pg_only = pytest.mark.skipif(
    not RLS_URL, reason="set RLS_TEST_DATABASE_URL (a Postgres URL) to run the Postgres proof"
)

_INVOICE = {
    "issue_date": "2026-06-01",
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
}


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _register(client, org_name, email):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": org_name,
            "name": "Owner",
            "email": email,
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"], r.json()["organization"]["id"]


async def _invoice(client, tok, number, vendor):
    r = await client.post(
        "/api/v1/invoices",
        json={**_INVOICE, "invoice_number": number, "vendor_name": vendor},
        headers=_h(tok),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
async def two_orgs(client):
    a_tok, a_org = await _register(client, "Alpha Haulage", "a-owner@alpha.io")
    b_tok, b_org = await _register(client, "Beta Cargo", "b-owner@beta.io")
    a_inv = await _invoice(client, a_tok, "A-1", "Alpha Fuels")
    b_inv = await _invoice(client, b_tok, "B-1", "Beta Fuels")
    return {"client": client, "a": a_org, "b": b_org, "a_inv": a_inv, "b_inv": b_inv}


async def _under(org, coro_factory):
    tok = set_current_org(org)
    try:
        return await coro_factory()
    finally:
        reset_current_org(tok)


def _guarded_sql(stmt, org="org-x") -> str:
    """The SQL the guard would issue for `stmt` under `org`, at compile level."""
    opts = tenant._tenant_options(org, tenant.TENANT_MODELS)
    return " ".join(str(stmt.options(*opts)).split())


# --------------------------------------------------------------------------- #
# Two tenants, one compiled statement
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_tenants_share_one_compiled_statement_and_never_rows(two_orgs, db_session):
    """The leak the previous docstring feared from the callable form: a tenant
    id baked into a cached statement. Alternating orgs on the SAME statement in
    ONE process must return each org's rows — the row assertions are the
    proof — and the compiled cache must not grow per tenant."""
    assert db_session.bind is not None
    cache = db_session.bind.sync_engine._compiled_cache
    stmt = select(Invoice.invoice_number)

    async def run():
        return sorted((await db_session.execute(stmt)).scalars().all())

    first_a = await _under(two_orgs["a"], run)
    first_b = await _under(two_orgs["b"], run)
    size_after_first_pair = len(cache)
    seen = []
    for org in (two_orgs["a"], two_orgs["b"]) * 8:
        seen.append((org, await _under(org, run)))
    assert first_a == ["A-1"] and first_b == ["B-1"]
    assert all(rows == (["A-1"] if org == two_orgs["a"] else ["B-1"]) for org, rows in seen)
    assert len(cache) == size_after_first_pair, "the compiled cache grew per tenant"


def test_the_options_of_two_tenants_have_equal_cache_keys_and_different_bind_values():
    """The exact property behind the test above, stated directly: the option's
    cache key is STRUCTURAL and the tenant id travels as an extracted bind."""
    stmt_a = select(Invoice.id).options(*tenant._tenant_options("org-A", tenant.TENANT_MODELS))
    stmt_b = select(Invoice.id).options(*tenant._tenant_options("org-B", tenant.TENANT_MODELS))
    key_a = stmt_a._generate_cache_key()
    key_b = stmt_b._generate_cache_key()
    assert key_a is not None and key_b is not None
    assert key_a.key == key_b.key
    values_a = [b.value for b in key_a.bindparams if b.key == TENANT_BIND_NAME]
    values_b = [b.value for b in key_b.bindparams if b.key == TENANT_BIND_NAME]
    assert values_a == ["org-A"] and values_b == ["org-B"]


@pytest.mark.asyncio
async def test_a_third_tenant_after_warm_up_hits_the_same_compiled_entry(two_orgs, db_session):
    assert db_session.bind is not None
    cache = db_session.bind.sync_engine._compiled_cache
    stmt = select(Invoice.invoice_number)
    await _under(two_orgs["a"], lambda: db_session.execute(stmt))
    await _under(two_orgs["b"], lambda: db_session.execute(stmt))
    size = len(cache)
    c_tok, c_org = await _register(two_orgs["client"], "Gamma Freight", "c-owner@gamma.io")
    await _invoice(two_orgs["client"], c_tok, "C-1", "Gamma Fuels")
    rows = await _under(c_org, lambda: db_session.execute(stmt))
    assert rows.scalars().all() == ["C-1"]
    assert len(cache) == size


# --------------------------------------------------------------------------- #
# Reach: aliases, joins, relationship loads, the soft-delete toggle
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_aliases_and_column_selects_stay_scoped(two_orgs, db_session):
    inv_alias = aliased(Invoice)

    async def via_alias():
        return (await db_session.execute(select(inv_alias.invoice_number))).scalars().all()

    async def vendors():
        return sorted((await db_session.execute(select(Vendor.name))).scalars().all())

    assert await _under(two_orgs["b"], via_alias) == ["B-1"]
    assert await _under(two_orgs["a"], vendors) == ["Alpha Fuels"]
    assert await _under(two_orgs["b"], vendors) == ["Beta Fuels"]


@pytest.mark.asyncio
async def test_user_visibility_is_membership_existence_not_the_active_org(two_orgs, db_session):
    """B1.5: a person can belong to several workspaces; `users.org_id` is only
    the ACTIVE-org pointer. Alpha's owner is given a membership in Beta while
    the pointer stays on Alpha: scoped to Beta they must be VISIBLE (the
    pointer-based predicate would hide them), scoped to Alpha unchanged —
    through the class, through an alias (the R5 panel found the alias getting
    the pointer predicate) and through a join on an alias."""
    a_owner_id = await db_session.scalar(select(User.id).where(User.email == "a-owner@alpha.io"))
    assert a_owner_id is not None
    db_session.add(
        Membership(
            id=str(uuid.uuid4()),
            org_id=two_orgs["b"],
            user_id=a_owner_id,
            role=UserRole.user,
            status="active",
        )
    )
    await db_session.commit()

    async def emails():
        return sorted((await db_session.execute(select(User.email))).scalars().all())

    user_alias = aliased(User)

    async def emails_via_alias():
        return sorted((await db_session.execute(select(user_alias.email))).scalars().all())

    async def emails_via_joined_alias():
        stmt = (
            select(user_alias.email)
            .select_from(Membership)
            .join(user_alias, user_alias.id == Membership.user_id)
        )
        return sorted(set((await db_session.execute(stmt)).scalars().all()))

    both = ["a-owner@alpha.io", "b-owner@beta.io"]
    assert await _under(two_orgs["a"], emails) == ["a-owner@alpha.io"]
    assert await _under(two_orgs["b"], emails) == both
    assert await _under(two_orgs["b"], emails_via_alias) == both
    assert await _under(two_orgs["b"], emails_via_joined_alias) == both
    # The alias gets the MEMBERSHIP predicate, not the pointer, at compile level.
    sql = _guarded_sql(select(user_alias.email))
    assert "memberships.user_id" in sql and "users_1.org_id" not in sql


@pytest.mark.asyncio
async def test_a_lazy_relationship_load_cannot_cross_tenants(two_orgs, db_session):
    """`invoices.vendor_id` is a plain FK (DB-020), so a cross-org reference is
    structurally possible. The guard's criteria propagate from the parent
    statement to the relationship load, so under org A the foreign vendor
    is NOT loaded — lazily and via `selectinload` — and under its own org it
    is (the control)."""
    b_vendor_id = await _under(
        two_orgs["b"],
        lambda: db_session.scalar(select(Vendor.id).where(Vendor.name == "Beta Fuels")),
    )
    assert b_vendor_id is not None
    await db_session.execute(
        text("UPDATE invoices SET vendor_id = :v WHERE id = :i"),
        {"v": b_vendor_id, "i": two_orgs["a_inv"]},
    )
    await db_session.commit()
    db_session.expire_all()

    async def lazy_vendor_of_a_invoice():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == two_orgs["a_inv"]))
        assert inv is not None
        return await db_session.run_sync(lambda s: inv.vendor)

    assert await _under(two_orgs["a"], lazy_vendor_of_a_invoice) is None
    db_session.expire_all()

    async def selectin_vendor_of_a_invoice():
        inv = await db_session.scalar(
            select(Invoice)
            .options(selectinload(Invoice.vendor))
            .where(Invoice.id == two_orgs["a_inv"])
        )
        assert inv is not None
        return inv.vendor

    assert await _under(two_orgs["a"], selectin_vendor_of_a_invoice) is None
    db_session.expire_all()

    async def vendor_of_b_invoice():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == two_orgs["b_inv"]))
        assert inv is not None
        return await db_session.run_sync(lambda s: inv.vendor.name)

    assert await _under(two_orgs["b"], vendor_of_b_invoice) == "Beta Fuels"


def test_an_eager_load_join_carries_the_criteria_in_its_on_clause():
    sql = _guarded_sql(select(Invoice).options(joinedload(Invoice.vendor)))
    on_clause = sql.split("LEFT OUTER JOIN vendors AS vendors_1 ON", 1)[1].split("WHERE", 1)[0]
    assert "vendors_1.org_id" in on_clause


@pytest.mark.asyncio
async def test_include_deleted_alternating_orgs_stays_scoped(two_orgs, db_session):
    async def numbers():
        return sorted((await db_session.execute(select(Invoice.invoice_number))).scalars().all())

    async def numbers_incl_deleted():
        with include_deleted():
            return await numbers()

    assert await _under(two_orgs["a"], numbers_incl_deleted) == ["A-1"]
    assert await _under(two_orgs["b"], numbers_incl_deleted) == ["B-1"]
    assert await _under(two_orgs["a"], numbers) == ["A-1"]


# --------------------------------------------------------------------------- #
# The ONE option: coverage, reach, the reserved name, the documented rule
# --------------------------------------------------------------------------- #
def test_the_guard_covers_exactly_the_registry():
    """The per-class decision is made by attribute (a mapper column `org_id`),
    and the registration tests keep the registry equal to that set — so the
    classes the ONE option scopes are exactly `TENANT_MODELS`, and the seven
    mapped classes without `org_id` get `true()`. Public API only: the
    compiled SQL for `select(cls)` either binds the tenant or does not."""
    mapped = [m.class_ for m in Base.registry.mappers]
    covered = {cls for cls in mapped if f":{TENANT_BIND_NAME}" in _guarded_sql(select(cls))}
    assert covered == set(tenant.TENANT_MODELS)
    assert len(mapped) - len(covered) == 7


def test_the_option_reaches_every_mapper():
    """`with_loader_criteria(Base, ...)` walks `Base.__subclasses__()`; an
    imperatively mapped class would be missed silently. Today every mapper is
    a direct subclass — pinned here."""
    option = tenant._tenant_options("org-x", tenant.TENANT_MODELS)[0]
    assert set(option._all_mappers()) == set(Base.registry.mappers)


def test_one_option_per_registry():
    assert len(tenant._tenant_options("org-x", tenant.TENANT_MODELS)) == 1
    assert tenant._tenant_options("org-x", ()) == ()
    assert len(tenant._deleted_options(tenant.SOFT_DELETE_MODELS)) == 1
    assert tenant._deleted_options(()) == ()


def test_the_reserved_bind_name_is_used_nowhere_else():
    """An application bind of the guard's name would be overwritten with the
    tenant id, silently (R5 panel S4)."""
    import pathlib

    app_dir = pathlib.Path(tenant.__file__).resolve().parents[1]
    hits = [
        p
        for p in app_dir.rglob("*.py")
        if p.name != "tenant.py" and TENANT_BIND_NAME in p.read_text()
    ]
    assert hits == [], hits


def test_the_documented_reach_rule_holds_at_compile_level():
    """The rule both mechanisms obey (ADR-0004 addendum): entities in the
    columns clause and explicit joins are scoped; a table that enters only
    through a WHERE clause — a Core `exists()` — is not, and RLS or the
    service must scope it; an ORM-enabled nested select is."""
    joined = _guarded_sql(select(Vendor.name).join(Invoice, Invoice.vendor_id == Vendor.id))
    assert "vendors.org_id" in joined and "invoices.org_id" in joined

    core_exists = _guarded_sql(
        select(Vendor.name).where(exists().where(Invoice.vendor_id == Vendor.id))
    )
    assert "vendors.org_id" in core_exists and "invoices.org_id" not in core_exists

    orm_subquery = _guarded_sql(select(Vendor.name).where(Vendor.id.in_(select(Invoice.vendor_id))))
    assert "vendors.org_id" in orm_subquery and "invoices.org_id" in orm_subquery


@pytest.mark.asyncio
async def test_no_org_context_is_still_unscoped(two_orgs, db_session):
    """Bootstrap / platform-operator paths run with the context at None and
    must keep reading across tenants (unchanged behaviour)."""
    rows = sorted((await db_session.execute(select(Invoice.invoice_number))).scalars().all())
    assert rows == ["A-1", "B-1"]


# --------------------------------------------------------------------------- #
# Postgres: the mechanism's SQL executes there (typed bind, membership subquery)
# --------------------------------------------------------------------------- #
@pg_only
@pytest.mark.asyncio
async def test_the_mechanism_executes_on_postgres():
    """The suite's fixtures are SQLite. Here the same ORM statements run
    through an `AsyncSession` on Postgres under the guard: the UUID-typed
    tenant bind, the membership subquery, the alias predicate and an
    eager-load join all execute and return each tenant's rows. (RLS is also
    on for this engine — layer 3 agrees with layer 2.) Rows are removed in
    `finally` so the run is repeatable on a persistent database."""
    engine = create_async_engine(RLS_URL)
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    uid_a, uid_b = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        async with engine.begin() as conn:
            for oid, name in ((org_a, "A"), (org_b, "B")):
                await conn.execute(
                    text(
                        "INSERT INTO organizations (id, name, ai_validation_enabled, "
                        "human_validation_enabled, plan, status, created_at, updated_at) "
                        "VALUES (:id, :n, false, false, 'trial', 'active', now(), now())"
                    ),
                    {"id": oid, "n": f"Guard Org {name}"},
                )
                await conn.execute(
                    text(
                        "INSERT INTO vendors (id, org_id, name, created_at, updated_at) "
                        "VALUES (:id, :org, :n, now(), now())"
                    ),
                    {"id": str(uuid.uuid4()), "org": oid, "n": f"Vendor {name}"},
                )
            for uid, oid, email in ((uid_a, org_a, "guard-a@x.io"), (uid_b, org_b, "guard-b@x.io")):
                await conn.execute(
                    text(
                        "INSERT INTO users (id, org_id, email, name, hashed_password, role, "
                        "is_active, email_verified, is_platform_admin, is_expense_approver, "
                        "failed_login_count, created_at, updated_at) "
                        "VALUES (:id, :org, :email, 'G', 'h', 'user', true, true, false, false, "
                        "0, now(), now())"
                    ),
                    {"id": uid, "org": oid, "email": email},
                )
            # A's user: pointer on A, memberships in A AND B; B's user: B only.
            for uid, oid in ((uid_a, org_a), (uid_a, org_b), (uid_b, org_b)):
                await conn.execute(
                    text(
                        "INSERT INTO memberships (id, org_id, user_id, role, is_expense_approver, "
                        "status, created_at, updated_at) "
                        "VALUES (:id, :org, :uid, 'user', false, 'active', now(), now())"
                    ),
                    {"id": str(uuid.uuid4()), "org": oid, "uid": uid},
                )

        sessions = async_sessionmaker(engine, expire_on_commit=False)
        user_alias = aliased(User)

        async def read(org):
            tok = set_current_org(org)
            try:
                async with sessions() as db:
                    vendors = sorted((await db.execute(select(Vendor.name))).scalars().all())
                    users = sorted((await db.execute(select(User.email))).scalars().all())
                    aliased_users = sorted(
                        (await db.execute(select(user_alias.email))).scalars().all()
                    )
                    joined = (
                        (await db.execute(select(Invoice).options(joinedload(Invoice.vendor))))
                        .scalars()
                        .all()
                    )
                    return vendors, users, aliased_users, joined
            finally:
                reset_current_org(tok)

        for _ in range(2):  # alternate, so a baked-in tenant would show
            assert await read(org_a) == (["Vendor A"], ["guard-a@x.io"], ["guard-a@x.io"], [])
            assert await read(org_b) == (
                ["Vendor B"],
                ["guard-a@x.io", "guard-b@x.io"],
                ["guard-a@x.io", "guard-b@x.io"],
                [],
            )
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM memberships WHERE org_id IN (:a, :b)"), {"a": org_a, "b": org_b}
            )
            await conn.execute(
                text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": uid_a, "b": uid_b}
            )
            await conn.execute(
                text("DELETE FROM vendors WHERE org_id IN (:a, :b)"), {"a": org_a, "b": org_b}
            )
            await conn.execute(
                text("DELETE FROM organizations WHERE id IN (:a, :b)"), {"a": org_a, "b": org_b}
            )
        await engine.dispose()
