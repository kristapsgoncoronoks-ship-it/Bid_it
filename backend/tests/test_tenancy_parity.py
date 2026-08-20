"""Tenancy parity (WO-10): behavioural isolation over the REAL query path.

For every tenant-scoped table (built programmatically from the same
`app.core.tenant.TENANT_MODELS` registry `tests/test_rls.py` uses, so a newly
added tenant table is automatically in scope) this suite:

1. Seeds tenant A and tenant B with OVERLAPPING data — the same names, the same
   invoice numbers, the same amounts. Overlap is the point: non-overlapping data
   can pass a broken filter by luck; identical business values force the filter
   to discriminate on tenancy alone (row ids are the discriminator).
2. Binds tenant A's context by authenticating as A over HTTP and calls the REAL
   read path the product uses — the route (which runs the per-query `org_id`
   filter AND the `do_orm_execute` ORM guard together, exactly as production
   does). Never a hand-written `select()` — a hand-written query would test the
   test, not the app.
3. Asserts A's rows are present and ZERO of B's are; then mirrors with B bound.
4. Where the table has a by-id route, asserts a cross-tenant fetch by id returns
   an opaque **404, never 403** (invariant §4.4 — object-id guessing must yield
   zero information).

Tables with no route-reachable read path are named in `EXEMPT` with a reason,
and `test_exemption_list_has_no_stale_entries` asserts the probe map and the
exemption list together cover the registry EXACTLY (both directions — a stale
exemption or an unclassified new table fails by name, the WO-1 discipline).

The checker proves itself: `test_parity_check_catches_a_deliberately_unscoped_query`
drops one service's org filter AND the ORM guard and asserts the vendors probe
FAILS. A test that cannot fail proves nothing.

Required CI check: this file runs in the default (SQLite) backend job — it needs
no Postgres, because it exercises app-layer isolation (layers 1+2); the RLS
backstop (layer 3) is proven separately by tests/test_rls.py on the Postgres job.

If a probe here goes red, THE CODE IS WRONG — never adjust the probe to pass
(see docs/plan/plan-a/wo/WO-10.md and ADR-0004).
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.tenant import TENANT_MODELS

# --------------------------------------------------------------------------- #
# Shared fixtures + seed payloads (identical for both tenants — overlap by design)
# --------------------------------------------------------------------------- #

SECRET = "parity-inbound-secret"


@pytest.fixture(autouse=True)
def _inbound_secret(monkeypatch):
    """The inbound-email webhook secret is mandatory (WO-5); configure it so the
    email probes can post."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "inbound_email_secret", SECRET)


ISSUER = {
    "legal_name": "Parity Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@parity.test",
}

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)

INVOICE_BODY = {
    "vendor_name": "Overlap Supplies GmbH",
    "invoice_number": "INV-OVERLAP-1",
    "issue_date": "2026-06-01",
    "currency": "EUR",
    "line_items": [
        {
            "description": "Widgets",
            "category": "goods",
            "quantity": "1",
            "unit_price": "100",
            "tax_rate": "0",
        }
    ],
}

ISSUED_BODY = {
    "buyer_name": "Overlap Buyer SA",
    "buyer_email": "ap@overlap-buyer.example",
    "issue_date": "2026-06-01",
    "due_date": "2026-06-30",
    "lines": [{"description": "Service", "quantity": "1", "unit_price": "500", "vat_rate": "0"}],
}

EXPENSE_BODY = {
    "title": "Overlap trip",
    "currency": "EUR",
    "items": [
        {
            "spend_date": "2026-05-01",
            "category": "travel",
            "description": "Flight",
            "amount": "300.00",
            "vat_amount": "0",
        }
    ],
}


@dataclass
class Org:
    """One tenant's HTTP identity: a shared transport client + this org's auth
    headers, plus the ids the register/bootstrap flow returned."""

    client: AsyncClient
    headers: dict[str, str]
    org_id: str
    user_id: str
    name: str

    async def get(self, path: str, **kw):
        return await self.client.get(path, headers=self.headers, **kw)

    async def post(self, path: str, **kw):
        return await self.client.post(path, headers=self.headers, **kw)

    async def put(self, path: str, **kw):
        return await self.client.put(path, headers=self.headers, **kw)

    async def patch(self, path: str, **kw):
        return await self.client.patch(path, headers=self.headers, **kw)

    async def delete(self, path: str, **kw):
        return await self.client.delete(path, headers=self.headers, **kw)

    async def member(self, email: str, role: str = "admin") -> tuple[dict[str, str], str]:
        """Invite + accept a second member of this org; return (headers, user_id)."""
        inv = await self.post("/api/v1/team/invites", json={"email": email, "role": role})
        assert inv.status_code == 201, inv.text
        acc = await self.client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
        )
        assert acc.status_code == 201, acc.text
        body = acc.json()
        return (
            {"Authorization": f"Bearer {body['token']['access_token']}"},
            body["user"]["id"],
        )


@dataclass
class Ctx:
    a: Org
    b: Org
    db: object  # AsyncSession for the rare direct-seed probe (never for verification)


async def _register(client: AsyncClient, org_name: str, email: str) -> Org:
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
    body = r.json()
    org = Org(
        client=client,
        headers={"Authorization": f"Bearer {body['token']['access_token']}"},
        org_id=body["organization"]["id"],
        user_id=body["user"]["id"],
        name=org_name,
    )
    # Activate the module surface most probes need; both orgs identically, so a
    # 404 can never be explained by a module gate.
    assert (await org.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    for mod in ("issuing", "expenses"):
        assert (
            await org.put(f"/api/v1/modules/{mod}", json={"enabled": True})
        ).status_code == 200, mod
    return org


@pytest_asyncio.fixture
async def ctx(client, db_session) -> Ctx:
    a = await _register(client, "Alpha Parity Ltd", "owner@alpha-parity.io")
    b = await _register(client, "Beta Parity Ltd", "owner@beta-parity.io")
    return Ctx(a=a, b=b, db=db_session)


# --------------------------------------------------------------------------- #
# Assertion helpers
# --------------------------------------------------------------------------- #


def _assert_isolated(table: str, a_ids: set[str], b_ids: set[str], seen_by_a: set[str]) -> None:
    """A's own rows must be visible via the real path; ZERO of B's may be."""
    assert a_ids, f"{table}: the probe seeded nothing for A — the probe is broken"
    assert b_ids, f"{table}: the probe seeded nothing for B — the probe is broken"
    missing = a_ids - seen_by_a
    assert not missing, f"{table}: A's own rows missing via the real read path: {missing}"
    leaked = seen_by_a & b_ids
    assert not leaked, f"{table}: TENANT LEAK — B's rows visible to A: {leaked}"


def _assert_404(resp, what: str) -> None:
    assert resp.status_code != 403, (
        f"{what}: cross-tenant fetch returned 403 — confirms the object exists "
        "(invariant §4.4 requires an opaque 404)"
    )
    assert resp.status_code == 404, f"{what}: expected opaque 404, got {resp.status_code}"


# --------------------------------------------------------------------------- #
# Probe registry — table name -> coroutine(ctx). One probe may prove several
# tables when the read path is genuinely shared (parent/child routes).
# --------------------------------------------------------------------------- #

PROBES: dict[str, Callable[[Ctx], Awaitable[None]]] = {}


def probe(*tables: str):
    def deco(fn):
        for t in tables:
            PROBES[t] = fn
        return fn

    return deco


# Tables with NO route-reachable read path. Each entry needs a reason; the
# exemption test asserts this list and PROBES partition TENANT_MODELS exactly.
EXEMPT: dict[str, str] = {
    "auth_tokens": (
        "Email verification / password-reset tokens. No route lists or fetches "
        "them; the token itself is the credential, consumed by POST "
        "/auth/verify-email and /auth/reset-password (single-use, hashed at rest)."
    ),
    "departments": (
        "Costing master with no CRUD route yet (dual-read transition): rows are "
        "created/resolved only inside invoice-line writes via "
        "services/costing.resolve_link_id, and read only through already-scoped "
        "invoice/analytics queries. Gains a probe when the master gets a route."
    ),
    "cost_centers": "Same as departments — costing master, no route-reachable read path.",
    "projects": "Same as departments — costing master, no route-reachable read path.",
    "billing_payments": (
        "Written only by the provider checkout/return/webhook flow and re-read "
        "there BY PROVIDER REFERENCE (idempotency), never listed to a tenant; "
        "GET /billing exposes plan/subscription state, not these rows."
    ),
    "usage_counters": (
        "Metered upload counters written by access.record_usage and read only "
        "by the org-scoped quota gates (access.enforce_upload_quota / usage_count); "
        "the GET /access/usage response model (UsageOut) exposes only "
        "Invoice-derived counts, so no route returns these rows' content."
    ),
    "vat_claimed_invoices": (
        "G2.2 (WO-51): the one-invoice-one-submission lock, written only "
        "through services/transport/lock.submit_claim/withdraw_claim "
        "(tenant-scoped there via the same ORM guard this probe would "
        "exercise, AND proven on real Postgres with a genuine concurrent-"
        "writer race — tests/test_transport_lock_concurrency.py). The WO-76 "
        "submit/withdraw routes CREATE and DELETE lock rows but no route "
        "returns their content (the claim-lifecycle route suite asserts "
        "their lifecycle directly); WO-77 added no lock-reading surface "
        "either, and WO-79 routed the fuel TRANSACTIONS, not the locks over "
        "them. Gains a probe when a route lists them."
    ),
    "fuel_extraction_baselines": (
        "G3.3 slice 2 (WO-70): the anti-drift extraction baseline, written "
        "only through services/transport/extraction_baseline.record/"
        "rebaseline/remove_baseline (tenant-scoped there via the same ORM "
        "guard this "
        "probe would exercise, AND proven on real Postgres RLS via "
        "tests/test_rls.py's set-equality check). No route reads THESE rows "
        "after WO-77/WO-79 either — the baseline is consulted inside statement "
        "ingestion, never served (WO-79 routed the fuel transactions the "
        "baseline is computed OVER, not the baseline). Gains a probe when a "
        "capture-diagnostics route slice exposes them (ARCH_plan.md)."
    ),
    "vat_fee_rates": (
        "G2.9 (WO-95): the configured contingency-fee rate (C11's three rungs), "
        "written only through services/transport/fee.set_rate/remove_rate and "
        "read only by fee.resolve_fee_rate inside lock.submit_claim (tenant-"
        "scoped there via the same ORM guard this probe would exercise, AND "
        "proven on real Postgres RLS via tests/test_rls.py's set-equality "
        "check). No route reads or writes THESE rows: WO-95 ships the service "
        "surface only, because the rate a client is charged has no admin screen "
        "yet and the percentage itself is still an open owner decision "
        "(docs/DECISIONS-NEEDED.md §10). The FROZEN copy a claim carries is "
        "route-reachable and already probed via vat_refund_claims. Gains a "
        "probe in the same commit that gives the rate a route."
    ),
    "supplier_vat_registrations": (
        "G3.1 slice 1 (WO-61): the per-country supplier legal-entity "
        "registration (R21/R22), written only through services/transport/"
        "supplier_entity.set_registration/learn_registration (tenant-"
        "scoped there via the same ORM guard this "
        "probe would exercise, AND proven on real Postgres RLS via "
        "tests/test_rls.py's set-equality check). No route reads THESE rows "
        "after WO-77/WO-79 either — WO-77 routed the CLAIM-side admin surfaces "
        "(waivers, checklist rules, cadences, note overrides, tie-out "
        "expectations, lifecycle) and WO-79 the fuel transactions, not the "
        "supplier-entity registry. Gains a probe when a supplier-master route "
        "slice exposes them (ARCH_plan.md)."
    ),
}


# --- Core money surfaces ------------------------------------------------------


@probe("vendors")
async def _p_vendors(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/vendors", json={"name": "Overlap Supplies GmbH"})
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/vendors")
        assert listing.status_code == 200, listing.text
        seen = {v["id"] for v in listing.json()}
        _assert_isolated("vendors", {ids[me.name]}, {ids[other.name]}, seen)
    # No GET /vendors/{id}; the by-id surface is PATCH — must stay an opaque 404.
    _assert_404(
        await ctx.b.patch(f"/api/v1/vendors/{ids[ctx.a.name]}", json={"name": "Hijack"}),
        "vendors PATCH by id",
    )


@probe("vendor_change_requests")
async def _p_vendor_changes(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        v = await org.post(
            "/api/v1/vendors", json={"name": "Overlap Payee BV", "iban": "NL91ABNA0417164300"}
        )
        assert v.status_code == 201, v.text
        upd = await org.patch(
            f"/api/v1/vendors/{v.json()['id']}", json={"iban": "DE89370400440532013000"}
        )
        assert upd.status_code == 200, upd.text
        pend = upd.json()["pending_changes"]
        assert len(pend) == 1
        ids[org.name] = pend[0]["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/vendors/changes")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated("vendor_change_requests", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.post(f"/api/v1/vendors/changes/{ids[ctx.a.name]}/approve", json={}),
        "vendor change request approve by id",
    )


@probe("invoices")
async def _p_invoices(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/invoices")
        assert listing.status_code == 200, listing.text
        seen = {i["id"] for i in listing.json()["items"]}
        _assert_isolated("invoices", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(await ctx.b.get(f"/api/v1/invoices/{ids[ctx.a.name]}"), "invoices GET by id")


@probe("issued_invoices")
async def _p_issued(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/issued", json=ISSUED_BODY)
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/issued")
        assert listing.status_code == 200, listing.text
        seen = {i["id"] for i in listing.json()["items"]}
        _assert_isolated("issued_invoices", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(await ctx.b.get(f"/api/v1/issued/{ids[ctx.a.name]}"), "issued GET by id")


@probe("issued_invoice_attachments")
async def _p_issued_attachments(ctx: Ctx) -> None:
    inv_ids, att_ids = {}, {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/issued", json=ISSUED_BODY)
        assert inv.status_code == 201, inv.text
        inv_ids[org.name] = inv.json()["id"]
        up = await org.post(
            f"/api/v1/issued/{inv.json()['id']}/attachments",
            files={"file": ("overlap.png", _PNG, "image/png")},
        )
        assert up.status_code == 201, up.text
        att_ids[org.name] = up.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/issued/{inv_ids[me.name]}/attachments")
        assert listing.status_code == 200, listing.text
        seen = {a["id"] for a in listing.json()}
        _assert_isolated(
            "issued_invoice_attachments", {att_ids[me.name]}, {att_ids[other.name]}, seen
        )
    _assert_404(
        await ctx.b.get(f"/api/v1/issued/{inv_ids[ctx.a.name]}/attachments"),
        "issued attachments via A's invoice",
    )
    # B's own invoice + A's attachment id: the child id must not resolve either.
    _assert_404(
        await ctx.b.get(
            f"/api/v1/issued/{inv_ids[ctx.b.name]}/attachments/{att_ids[ctx.a.name]}/download"
        ),
        "issued attachment download with A's attachment id",
    )


@probe("payments")
async def _p_ar_payments(ctx: Ctx) -> None:
    inv_ids, pay_ids = {}, {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/issued", json=ISSUED_BODY)
        inv_ids[org.name] = inv.json()["id"]
        pay = await org.patch(
            f"/api/v1/issued/{inv.json()['id']}/payment",
            json={"amount_paid": "100.00", "paid_date": "2026-06-15"},
        )
        assert pay.status_code == 200, pay.text
        ledger = await org.get(f"/api/v1/issued/{inv.json()['id']}/payments")
        assert ledger.status_code == 200, ledger.text
        entries = ledger.json()
        assert entries, "seeding recorded no AR ledger entry"
        pay_ids[org.name] = {e["id"] for e in entries}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        ledger = await me.get(f"/api/v1/issued/{inv_ids[me.name]}/payments")
        seen = {e["id"] for e in ledger.json()}
        _assert_isolated("payments", pay_ids[me.name], pay_ids[other.name], seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/issued/{inv_ids[ctx.a.name]}/payments"),
        "AR ledger via A's invoice",
    )


@probe("supplier_payments")
async def _p_ap_payments(ctx: Ctx) -> None:
    # An AP payment may only be recorded on a scheduled invoice, so each org
    # drives the real submit → approve (second user, SoD) → schedule chain first.
    inv_ids, pay_ids = {}, {}
    for i, org in enumerate((ctx.a, ctx.b)):
        approver, _ = await org.member(f"ap-appr{i}@{'ab'[i]}-parity.io", role="admin")
        inv = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        inv_ids[org.name] = inv.json()["id"]
        sub = await org.post(f"/api/v1/invoices/{inv.json()['id']}/submit", json={"version": 1})
        assert sub.status_code == 200, sub.text
        appr = await org.client.post(
            f"/api/v1/invoices/{inv.json()['id']}/approve",
            headers=approver,
            json={"version": sub.json()["version"]},
        )
        assert appr.status_code == 200, appr.text
        sch = await org.post(
            f"/api/v1/invoices/{inv.json()['id']}/transition",
            json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
        )
        assert sch.status_code == 200, sch.text
        pay = await org.patch(
            f"/api/v1/invoices/{inv.json()['id']}/payment",
            json={"amount_paid": "40.00", "paid_date": "2026-06-15"},
        )
        assert pay.status_code == 200, pay.text
        ledger = await org.get(f"/api/v1/invoices/{inv.json()['id']}/payments")
        assert ledger.status_code == 200, ledger.text
        assert ledger.json(), "seeding recorded no AP ledger entry"
        pay_ids[org.name] = {e["id"] for e in ledger.json()}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        ledger = await me.get(f"/api/v1/invoices/{inv_ids[me.name]}/payments")
        seen = {e["id"] for e in ledger.json()}
        _assert_isolated("supplier_payments", pay_ids[me.name], pay_ids[other.name], seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/invoices/{inv_ids[ctx.a.name]}/payments"),
        "AP ledger via A's invoice",
    )


@probe("payment_runs")
async def _p_payment_runs(ctx: Ctx) -> None:
    run_ids = {}
    for i, org in enumerate((ctx.a, ctx.b)):
        approver, _ = await org.member(f"approver{i}@{'ab'[i]}-parity.io", role="admin")
        inv = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        iid = inv.json()["id"]
        sub = await org.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
        assert sub.status_code == 200, sub.text
        appr = await org.client.post(
            f"/api/v1/invoices/{iid}/approve",
            headers=approver,
            json={"version": sub.json()["version"]},
        )
        assert appr.status_code == 200, appr.text
        sch = await org.post(
            f"/api/v1/invoices/{iid}/transition",
            json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
        )
        assert sch.status_code == 200, sch.text
        run = await org.post("/api/v1/payment-runs", json={"invoice_ids": [iid]})
        assert run.status_code == 201, run.text
        run_ids[org.name] = run.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/payment-runs")
        assert listing.status_code == 200, listing.text
        seen = {r["id"] for r in listing.json()}
        _assert_isolated("payment_runs", {run_ids[me.name]}, {run_ids[other.name]}, seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/payment-runs/{run_ids[ctx.a.name]}"), "payment run GET by id"
    )


@probe("approval_steps")
async def _p_approval_steps(ctx: Ctx) -> None:
    inv_ids, step_ids = {}, {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        iid = inv.json()["id"]
        inv_ids[org.name] = iid
        sub = await org.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
        assert sub.status_code == 200, sub.text
        hist = await org.get(f"/api/v1/invoices/{iid}/approvals")
        assert hist.status_code == 200, hist.text
        steps = hist.json()["steps"]
        assert steps, "submit created no approval steps under the default policy"
        step_ids[org.name] = {s["id"] for s in steps}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        hist = await me.get(f"/api/v1/invoices/{inv_ids[me.name]}/approvals")
        seen = {s["id"] for s in hist.json()["steps"]}
        _assert_isolated("approval_steps", step_ids[me.name], step_ids[other.name], seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/invoices/{inv_ids[ctx.a.name]}/approvals"),
        "approval history via A's invoice",
    )


@probe("approval_policies")
async def _p_approval_policies(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post(
            "/api/v1/approval-policies", json={"name": "Overlap policy", "min_amount": "500"}
        )
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/approval-policies")
        assert listing.status_code == 200, listing.text
        seen = {p["id"] for p in listing.json()}
        _assert_isolated("approval_policies", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.patch(
            f"/api/v1/approval-policies/{ids[ctx.a.name]}", json={"name": "hijack", "version": 1}
        ),
        "approval policy PATCH by id",
    )


@probe("receipts")
async def _p_receipts(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post(
            "/api/v1/receipts", json={"amount": "1500.00", "received_on": "2026-02-05"}
        )
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/receipts")
        assert listing.status_code == 200, listing.text
        seen = {x["id"] for x in listing.json()}
        _assert_isolated("receipts", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(await ctx.b.get(f"/api/v1/receipts/{ids[ctx.a.name]}"), "receipt GET by id")


@probe("bank_statements", "bank_lines")
async def _p_bank_import(ctx: Ctx) -> None:
    csv = (
        "date,description,credit,debit\n"
        "2026-02-05,Payment from Overlap INV-1,1210.00,\n"
        "2026-02-10,Overlap payout,,250.00\n"
    )
    st_ids, line_ids = {}, {}
    for org in (ctx.a, ctx.b):
        up = await org.post(
            "/api/v1/reconciliation/import",
            files={"file": ("stmt.csv", io.BytesIO(csv.encode()), "text/csv")},
        )
        assert up.status_code == 201, up.text
        st_ids[org.name] = up.json()["statement_id"]
        lines = await org.get(f"/api/v1/reconciliation/statements/{st_ids[org.name]}/lines")
        assert lines.status_code == 200, lines.text
        assert lines.json(), "statement import produced no lines"
        line_ids[org.name] = {ln["id"] for ln in lines.json()}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/reconciliation/statements")
        assert listing.status_code == 200, listing.text
        seen = {s["id"] for s in listing.json()}
        _assert_isolated("bank_statements", {st_ids[me.name]}, {st_ids[other.name]}, seen)
        lines = await me.get(f"/api/v1/reconciliation/statements/{st_ids[me.name]}/lines")
        seen_lines = {ln["id"] for ln in lines.json()}
        _assert_isolated("bank_lines", line_ids[me.name], line_ids[other.name], seen_lines)
    _assert_404(
        await ctx.b.get(f"/api/v1/reconciliation/statements/{st_ids[ctx.a.name]}/lines"),
        "statement lines via A's statement",
    )
    _assert_404(
        await ctx.b.post(f"/api/v1/reconciliation/lines/{next(iter(line_ids[ctx.a.name]))}/ignore"),
        "bank line ignore by A's line id",
    )


@probe("reimbursement_batches")
async def _p_reimbursement_batches(ctx: Ctx) -> None:
    # The full HTTP chain to a batch needs approved expense reports with a second
    # approver + employee bank details; the READ path is what this probe proves,
    # so the rows are seeded directly (identical business values, distinct orgs)
    # and read through the real route. Never a hand-written verification select.
    from app.models.expense import ReimbursementBatch

    ids = {}
    for org in (ctx.a, ctx.b):
        row = ReimbursementBatch(
            org_id=org.org_id, reference="OVERLAP-BATCH", total_eur=Decimal("100.00")
        )
        ctx.db.add(row)
        await ctx.db.commit()
        ids[org.name] = row.id
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/reimbursements")
        assert listing.status_code == 200, listing.text
        seen = {b["id"] for b in listing.json()}
        _assert_isolated("reimbursement_batches", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/reimbursements/{ids[ctx.a.name]}"),
        "reimbursement batch GET by id",
    )


# --- Expenses -----------------------------------------------------------------


@probe("expense_reports", "expense_items")
async def _p_expense_reports(ctx: Ctx) -> None:
    rep_ids, item_ids = {}, {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/expenses", json=EXPENSE_BODY)
        assert r.status_code == 201, r.text
        rep_ids[org.name] = r.json()["id"]
        item_ids[org.name] = {i["id"] for i in r.json()["items"]}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/expenses")
        assert listing.status_code == 200, listing.text
        body = listing.json()
        rows = body["items"] if isinstance(body, dict) else body
        seen = {x["id"] for x in rows}
        _assert_isolated("expense_reports", {rep_ids[me.name]}, {rep_ids[other.name]}, seen)
        detail = await me.get(f"/api/v1/expenses/{rep_ids[me.name]}")
        seen_items = {i["id"] for i in detail.json()["items"]}
        _assert_isolated("expense_items", item_ids[me.name], item_ids[other.name], seen_items)
    _assert_404(
        await ctx.b.get(f"/api/v1/expenses/{rep_ids[ctx.a.name]}"), "expense report GET by id"
    )
    _assert_404(
        await ctx.b.patch(
            f"/api/v1/expenses/{rep_ids[ctx.a.name]}/items/{next(iter(item_ids[ctx.a.name]))}",
            json={"comment": "pwned"},
        ),
        "expense item PATCH via A's ids",
    )


@probe("expense_comments")
async def _p_expense_comments(ctx: Ctx) -> None:
    rep_ids, comment_ids = {}, {}
    for org in (ctx.a, ctx.b):
        rep = await org.post("/api/v1/expenses", json=EXPENSE_BODY)
        rep_ids[org.name] = rep.json()["id"]
        c = await org.post(
            f"/api/v1/expenses/{rep.json()['id']}/comments", json={"body": "overlap comment"}
        )
        assert c.status_code == 201, c.text
        comment_ids[org.name] = c.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/expenses/{rep_ids[me.name]}/comments")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated(
            "expense_comments", {comment_ids[me.name]}, {comment_ids[other.name]}, seen
        )
    _assert_404(
        await ctx.b.get(f"/api/v1/expenses/{rep_ids[ctx.a.name]}/comments"),
        "expense comments via A's report",
    )


@probe("expense_transactions")
async def _p_expense_transactions(ctx: Ctx) -> None:
    csv = "Date,Description,Debit,Credit,Balance\n2026-05-01,Overlap AWS,120.00,,4880.00\n"
    txn_ids = {}
    for org in (ctx.a, ctx.b):
        up = await org.post(
            "/api/v1/expenses/import/bank-statement",
            files={"file": ("stmt.csv", io.BytesIO(csv.encode()), "text/csv")},
        )
        assert up.status_code in (200, 201), up.text
        listing = await org.get("/api/v1/expenses/transactions")
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        rows = rows["items"] if isinstance(rows, dict) else rows
        assert rows, "statement import produced no expense transactions"
        txn_ids[org.name] = {t["id"] for t in rows}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/expenses/transactions")
        rows = listing.json()
        rows = rows["items"] if isinstance(rows, dict) else rows
        seen = {t["id"] for t in rows}
        _assert_isolated("expense_transactions", txn_ids[me.name], txn_ids[other.name], seen)
    _assert_404(
        await ctx.b.delete(f"/api/v1/expenses/transactions/{next(iter(txn_ids[ctx.a.name]))}"),
        "expense transaction DELETE by id",
    )


@probe("expense_policies")
async def _p_expense_policy(ctx: Ctx) -> None:
    # Singleton per org: everything identical except the marker value.
    for org, cap in ((ctx.a, "111"), (ctx.b, "222")):
        r = await org.put("/api/v1/expenses/policy", json={"max_item_amount": cap})
        assert r.status_code == 200, r.text
    for org, cap in ((ctx.a, "111"), (ctx.b, "222")):
        got = await org.get("/api/v1/expenses/policy")
        assert got.status_code == 200, got.text
        assert Decimal(got.json()["max_item_amount"]) == Decimal(cap), (
            "expense_policies: an org read back the OTHER org's policy value"
        )


@probe("expense_approval_policies")
async def _p_expense_approval_policies(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post(
            "/api/v1/expenses/approval-policies",
            json={"name": "Overlap chain", "approver_ids": [org.user_id]},
        )
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/expenses/approval-policies")
        assert listing.status_code == 200, listing.text
        seen = {p["id"] for p in listing.json()}
        _assert_isolated("expense_approval_policies", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.patch(
            f"/api/v1/expenses/approval-policies/{ids[ctx.a.name]}",
            json={"name": "hijack", "approver_ids": [ctx.b.user_id], "version": 1},
        ),
        "expense approval policy PATCH by id",
    )


@probe("expense_approval_steps")
async def _p_expense_approval_steps(ctx: Ctx) -> None:
    rep_ids, step_ids = {}, {}
    for i, org in enumerate((ctx.a, ctx.b)):
        approver_h, approver_id = await org.member(f"exp-appr{i}@{'ab'[i]}-parity.io", role="admin")
        pol = await org.post(
            "/api/v1/expenses/approval-policies",
            json={"name": "Overlap chain", "approver_ids": [approver_id]},
        )
        assert pol.status_code == 201, pol.text
        rep = await org.post("/api/v1/expenses", json=EXPENSE_BODY)
        rid = rep.json()["id"]
        rep_ids[org.name] = rid
        for it in rep.json()["items"]:
            await org.patch(
                f"/api/v1/expenses/{rid}/items/{it['id']}", json={"comment": "Client meeting"}
            )
            await org.post(
                f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
                files={"file": ("receipt.png", _PNG, "image/png")},
            )
        sub = await org.post(f"/api/v1/expenses/{rid}/submit")
        assert sub.status_code == 200, sub.text
        detail = await org.get(f"/api/v1/expenses/{rid}")
        steps = detail.json().get("approval_steps") or []
        assert steps, "submit created no expense approval steps under the policy"
        step_ids[org.name] = {s["id"] for s in steps}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        detail = await me.get(f"/api/v1/expenses/{rep_ids[me.name]}")
        seen = {s["id"] for s in detail.json().get("approval_steps") or []}
        _assert_isolated("expense_approval_steps", step_ids[me.name], step_ids[other.name], seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/expenses/{rep_ids[ctx.a.name]}"),
        "expense report detail via A's id",
    )


# --- AR periphery -------------------------------------------------------------


@probe("partners", "partner_documents")
async def _p_partners(ctx: Ctx) -> None:
    p_ids, doc_ids = {}, {}
    for org in (ctx.a, ctx.b):
        p = await org.post(
            "/api/v1/partners", json={"name": "Overlap Freight", "requires_contract": True}
        )
        assert p.status_code == 201, p.text
        p_ids[org.name] = p.json()["id"]
        d = await org.post(
            f"/api/v1/partners/{p.json()['id']}/documents",
            json={"kind": "contract", "title": "Overlap agreement"},
        )
        assert d.status_code == 201, d.text
        doc_ids[org.name] = d.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/partners")
        assert listing.status_code == 200, listing.text
        seen = {p["id"] for p in listing.json()}
        _assert_isolated("partners", {p_ids[me.name]}, {p_ids[other.name]}, seen)
        detail = await me.get(f"/api/v1/partners/{p_ids[me.name]}")
        seen_docs = {d["id"] for d in detail.json()["documents"]}
        _assert_isolated("partner_documents", {doc_ids[me.name]}, {doc_ids[other.name]}, seen_docs)
    _assert_404(await ctx.b.get(f"/api/v1/partners/{p_ids[ctx.a.name]}"), "partner GET by id")
    _assert_404(
        await ctx.b.post(
            f"/api/v1/partners/{p_ids[ctx.b.name]}/documents/{doc_ids[ctx.a.name]}/sign", json={}
        ),
        "partner document sign with A's doc id",
    )


@probe("customers", "customer_contacts")
async def _p_customers(ctx: Ctx) -> None:
    c_ids, contact_ids = {}, {}
    body = {
        "name": "Overlap Client SIA",
        "email": "ap@overlap-client.example",
        "contacts": [{"name": "Anna Ozola", "email": "anna@overlap-client.example"}],
    }
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/customers", json=body)
        assert r.status_code == 201, r.text
        c_ids[org.name] = r.json()["id"]
        contact_ids[org.name] = {c["id"] for c in r.json()["contacts"]}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/customers")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated("customers", {c_ids[me.name]}, {c_ids[other.name]}, seen)
        detail = await me.get(f"/api/v1/customers/{c_ids[me.name]}")
        seen_contacts = {c["id"] for c in detail.json()["contacts"]}
        _assert_isolated(
            "customer_contacts", contact_ids[me.name], contact_ids[other.name], seen_contacts
        )
    _assert_404(await ctx.b.get(f"/api/v1/customers/{c_ids[ctx.a.name]}"), "customer GET by id")


@probe("recurring_invoices")
async def _p_recurring(ctx: Ctx) -> None:
    ids = {}
    body = {
        "template": {
            "buyer_name": "Overlap Buyer SA",
            "lines": [
                {
                    "description": "Retainer",
                    "quantity": "1",
                    "unit_price": "500.00",
                    "vat_rate": "21",
                }
            ],
        },
        "frequency": "monthly",
        "interval": 1,
        "start_date": "2026-01-01",
        "title": "Overlap retainer",
    }
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/issued/recurring", json=body)
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/issued/recurring")
        assert listing.status_code == 200, listing.text
        seen = {s["id"] for s in listing.json()}
        _assert_isolated("recurring_invoices", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/issued/recurring/{ids[ctx.a.name]}"),
        "recurring schedule GET by id",
    )


@probe("email_messages")
async def _p_email_messages(ctx: Ctx) -> None:
    msg_ids = {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/issued", json=ISSUED_BODY)
        assert inv.status_code == 201, inv.text
        sent = await org.post(f"/api/v1/issued/{inv.json()['id']}/send", json={})
        assert sent.status_code == 200, sent.text
        listing = await org.get("/api/v1/issued/emails")
        assert listing.status_code == 200, listing.text
        assert listing.json(), "send recorded no outbound message"
        msg_ids[org.name] = {m["id"] for m in listing.json()}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/issued/emails")
        seen = {m["id"] for m in listing.json()}
        _assert_isolated("email_messages", msg_ids[me.name], msg_ids[other.name], seen)


@probe("issuer_profiles")
async def _p_issuer_registry(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/issuer/registry", json=ISSUER)
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/issuer/registry")
        assert listing.status_code == 200, listing.text
        seen = {p["id"] for p in listing.json()}
        _assert_isolated("issuer_profiles", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.put(f"/api/v1/issuer/registry/{ids[ctx.a.name]}", json=ISSUER),
        "issuer registry PUT by id",
    )


@probe("dunning_policies")
async def _p_dunning(ctx: Ctx) -> None:
    for org, days in ((ctx.a, 10), (ctx.b, 20)):
        r = await org.put(
            "/api/v1/dunning/policy",
            json={"levels": [{"level": 1, "days_overdue": days, "tone": "firm", "active": True}]},
        )
        assert r.status_code == 200, r.text
    for org, days in ((ctx.a, 10), (ctx.b, 20)):
        got = await org.get("/api/v1/dunning/policy")
        assert got.status_code == 200, got.text
        assert got.json()["levels"][0]["days_overdue"] == days, (
            "dunning_policies: an org read back the OTHER org's ladder"
        )


# --- Intake / capture ---------------------------------------------------------


@probe("extraction_runs")
async def _p_extraction_runs(ctx: Ctx) -> None:
    from app.services import jobs

    csv = (
        "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
        "Overlap Ltd,INV-OVERLAP-9,2026-06-01,Widgets,10,5.00,50.00,21\n"
    )
    run_ids = {}
    for org in (ctx.a, ctx.b):
        up = await org.post(
            "/api/v1/invoices/upload",
            files={"file": ("overlap.csv", io.BytesIO(csv.encode()), "text/csv")},
        )
        assert up.status_code == 202, up.text
        run_ids[org.name] = up.json()["extraction_run_id"]
    for _ in range(30):  # the worker is org-agnostic; drain both parses
        if await jobs.run_once(ctx.db, "parity-worker") is None:
            break
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        own = await me.get(f"/api/v1/invoices/upload/{run_ids[me.name]}")
        assert own.status_code == 200, own.text
        queue = await me.get("/api/v1/invoices/captures/review")
        assert queue.status_code == 200, queue.text
        seen = {i["extraction_run_id"] for i in queue.json()["items"]}
        _assert_isolated("extraction_runs", {run_ids[me.name]}, {run_ids[other.name]}, seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/invoices/upload/{run_ids[ctx.a.name]}"),
        "extraction run GET by id",
    )


@probe("extraction_fields")
async def _p_extraction_fields(ctx: Ctx) -> None:
    # Field rows hang off a capture run and are read ONLY through their scoped
    # parents: the capture-review queue item and GET /invoices/{id}/extraction.
    from app.services import jobs

    csv = (
        "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
        "Overlap Ltd,INV-OVERLAP-9,2026-06-01,Widgets,10,5.00,50.00,21\n"
    )
    run_ids = {}
    for org in (ctx.a, ctx.b):
        up = await org.post(
            "/api/v1/invoices/upload",
            files={"file": ("overlap.csv", io.BytesIO(csv.encode()), "text/csv")},
        )
        assert up.status_code == 202, up.text
        run_ids[org.name] = up.json()["extraction_run_id"]
    for _ in range(30):
        if await jobs.run_once(ctx.db, "parity-worker") is None:
            break
    # The field-review write path must refuse A's run id under B's context —
    # 404, and B's correction writes nothing into A's capture.
    _assert_404(
        await ctx.b.post(
            f"/api/v1/invoices/captures/{run_ids[ctx.a.name]}/review",
            json={"fields": [{"field": "vendor_name", "reviewed_value": "pwned"}]},
        ),
        "capture field review with A's run id",
    )


@probe("inbound_channel_health")
async def _p_inbound_channel_health(ctx: Ctx) -> None:
    """H-2: both orgs activate email intake and state a DIFFERENT expected
    delivery cadence for the SAME channel key. The channel key is identical by
    construction, so only tenancy can discriminate — a missing `org_id` filter
    would show one org the other's expectation, and the whole point of the value
    is that it decides when THAT workspace is told its intake has gone quiet.

    The discriminator is the VALUE, not a row id: this table's natural key is
    (org, channel) and its wire shape carries no id, so an id would be noise a
    client could not use.

    Deliberately a real probe rather than an EXEMPT row, in the same commit that
    creates the table."""
    from app.services import modules as modules_svc

    cadence = {ctx.a.name: 3, ctx.b.name: 21}
    for org in (ctx.a, ctx.b):
        await modules_svc.set_enabled(ctx.db, org.org_id, "email_intake", True)
        await ctx.db.commit()
        put = await org.put(
            "/api/v1/email/health/email", json={"expected_cadence_days": cadence[org.name]}
        )
        assert put.status_code == 200, put.text

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        seen = await me.get("/api/v1/email/health")
        assert seen.status_code == 200, seen.text
        row = next(c for c in seen.json() if c["channel"] == "email")
        assert row["expected_cadence_days"] == cadence[me.name], row
        assert row["expected_cadence_days"] != cadence[other.name], (
            "inbound_channel_health: one org is reading the other's stated cadence"
        )


@probe("capture_acknowledgements")
async def _p_capture_acknowledgements(ctx: Ctx) -> None:
    """H-1: both orgs upload an IDENTICAL unparseable file, so both get a failed
    capture with the same code, same filename and same content hash — only
    tenancy discriminates. Each org acknowledges its OWN failure and must see
    only its own worklist; acknowledging A's failure under B's context is an
    opaque 404, and must not silence it for A.

    Deliberately a real probe rather than an EXEMPT row, in the same commit that
    creates the table."""
    from app.services import jobs

    # Valid UTF-8 CSV that passes the security gate and then fails to PARSE
    # (no recognisable line-item columns) — a real failed capture, not a mock.
    bad = b"not,an,invoice\nnothing,here,at all\n"
    run_ids = {}
    for org in (ctx.a, ctx.b):
        up = await org.post(
            "/api/v1/invoices/upload",
            files={"file": ("unparseable.csv", io.BytesIO(bad), "text/csv")},
        )
        assert up.status_code == 202, up.text
        run_ids[org.name] = up.json()["extraction_run_id"]
    for _ in range(30):
        if await jobs.run_once(ctx.db, "parity-worker") is None:
            break

    for org in (ctx.a, ctx.b):
        wl = await org.get("/api/v1/invoices/captures/failures")
        assert wl.status_code == 200, wl.text
        assert any(i["ref_id"] == run_ids[org.name] for i in wl.json()["items"]), wl.text
        ack = await org.post(
            f"/api/v1/invoices/captures/failures/upload/{run_ids[org.name]}/acknowledge",
            json={"note": f"seen by {org.name}"},
        )
        assert ack.status_code == 200, ack.text

    # B cannot acknowledge A's failure...
    _assert_404(
        await ctx.b.post(
            f"/api/v1/invoices/captures/failures/upload/{run_ids[ctx.a.name]}/acknowledge",
            json={"note": "pwned"},
        ),
        "acknowledge A's failed capture as B",
    )
    # ...and each org's acknowledgement history is its own: A's worklist with
    # acknowledged rows included shows A's ack note and never B's.
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        seen = await me.get("/api/v1/invoices/captures/failures?include_acknowledged=true")
        assert seen.status_code == 200, seen.text
        notes = {i["acknowledgement_note"] for i in seen.json()["items"]}
        assert f"seen by {me.name}" in notes, notes
        assert f"seen by {other.name}" not in notes, notes
        _assert_isolated(
            "capture_acknowledgements",
            {run_ids[me.name]},
            {run_ids[other.name]},
            {i["ref_id"] for i in seen.json()["items"]},
        )


@probe("capture_field_memory")
async def _p_capture_field_memory(ctx: Ctx) -> None:
    """E1.5: both orgs correct the SAME field for an IDENTICALLY-spelled vendor
    with DIFFERENT corrected values — the overlap that forces the tenant
    filter to discriminate. Each org's NEXT capture from that vendor must
    surface `suggested_value` == its OWN correction, never the other org's —
    the hint has no list/by-id route of its own; it is read only through the
    already-scoped capture-fields response, so that is the real path probed."""
    from app.services import jobs

    csv1 = (
        "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
        "Overlap Fuels OU,INV-PARITY-MEM-1,2026-06-01,Diesel,10,1.50,15.00,21\n"
    )
    csv2 = (
        "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
        "Overlap Fuels OU,INV-PARITY-MEM-2,2026-06-02,Diesel,11,1.55,17.05,21\n"
    )
    corrected = {ctx.a.name: "Overlap Fuels OÜ (A)", ctx.b.name: "Overlap Fuels OÜ (B)"}
    for org in (ctx.a, ctx.b):
        up = await org.post(
            "/api/v1/invoices/upload",
            files={"file": ("mem1.csv", io.BytesIO(csv1.encode()), "text/csv")},
        )
        assert up.status_code == 202, up.text
        for _ in range(30):
            if await jobs.run_once(ctx.db, "parity-worker") is None:
                break
        r = await org.post(
            f"/api/v1/invoices/captures/{up.json()['extraction_run_id']}/review",
            json={"fields": [{"field": "vendor_name", "reviewed_value": corrected[org.name]}]},
        )
        assert r.status_code == 200, r.text

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        up2 = await me.post(
            "/api/v1/invoices/upload",
            files={"file": ("mem2.csv", io.BytesIO(csv2.encode()), "text/csv")},
        )
        assert up2.status_code == 202, up2.text
        for _ in range(30):
            if await jobs.run_once(ctx.db, "parity-worker") is None:
                break
        rows = (
            await me.get(f"/api/v1/invoices/captures/{up2.json()['extraction_run_id']}/fields")
        ).json()
        vendor_field = next(
            f for f in rows if f["field"] == "vendor_name" and f["line_index"] is None
        )
        assert vendor_field["suggested_value"] == corrected[me.name], (
            f"capture_field_memory: {me.name} did not see its own hint"
        )
        assert vendor_field["suggested_value"] != corrected[other.name], (
            f"capture_field_memory: TENANT LEAK — {me.name} saw {other.name}'s hint"
        )


@probe("email_intakes")
async def _p_email_intakes(ctx: Ctx) -> None:
    addresses = {}
    for org in (ctx.a, ctx.b):
        assert (
            await org.put("/api/v1/modules/email_intake", json={"enabled": True})
        ).status_code == 200
        r = await org.get("/api/v1/email/settings")
        assert r.status_code == 200, r.text
        addresses[org.name] = r.json()["address"]
        again = await org.get("/api/v1/email/settings")
        assert again.json()["address"] == addresses[org.name], "intake address not stable"
    assert addresses[ctx.a.name] != addresses[ctx.b.name], (
        "email_intakes: two orgs share one intake address — tenant resolution would collide"
    )


@probe("inbound_invoices")
async def _p_inbound_invoices(ctx: Ctx) -> None:
    import base64

    att = {
        "filename": "overlap.csv",
        "content_type": "text/csv",
        "content_base64": base64.b64encode(
            b"vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
            b"Overlap Ltd,INV-EMAIL-OVERLAP,2026-06-01,Widgets,1,5.00,5.00,21\n"
        ).decode(),
    }
    inbox_ids = {}
    for org in (ctx.a, ctx.b):
        assert (
            await org.put("/api/v1/modules/email_intake", json={"enabled": True})
        ).status_code == 200
        address = (await org.get("/api/v1/email/settings")).json()["address"]
        posted = await org.client.post(
            "/api/v1/email/inbound",
            json={
                "to": address,
                "from": "supplier@overlap.example",
                "subject": "Invoice",
                "attachments": [att],
            },
            headers={"X-Inbound-Secret": SECRET},
        )
        assert posted.status_code == 200, posted.text
        inbox = await org.get("/api/v1/email/inbox")
        assert inbox.status_code == 200, inbox.text
        assert inbox.json()["items"], "inbound webhook produced no inbox item"
        inbox_ids[org.name] = {i["id"] for i in inbox.json()["items"]}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        inbox = await me.get("/api/v1/email/inbox")
        seen = {i["id"] for i in inbox.json()["items"]}
        _assert_isolated("inbound_invoices", inbox_ids[me.name], inbox_ids[other.name], seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/email/inbox/{next(iter(inbox_ids[ctx.a.name]))}"),
        "inbound invoice GET by id",
    )


# --- Documents ----------------------------------------------------------------


@probe("documents", "document_versions")
async def _p_documents(ctx: Ctx) -> None:
    # A uploads the logo twice (a 2-entry version chain), B once — both the SAME
    # bytes, so even the content hash overlaps and only tenancy can separate
    # them. Version entries carry no row id, so the version-chain LENGTH is the
    # leak detector (a cross-tenant read would inflate it).
    for org, uploads in ((ctx.a, 2), (ctx.b, 1)):
        for _ in range(uploads):
            up = await org.post(
                "/api/v1/issuer/logo", files={"file": ("logo.png", _PNG, "image/png")}
            )
            assert up.status_code == 200, up.text
    doc_ids = {}
    for org in (ctx.a, ctx.b):
        docs = await org.get("/api/v1/documents")
        assert docs.status_code == 200, docs.text
        assert docs.json(), "logo upload registered no document"
        doc_ids[org.name] = {d["id"] for d in docs.json()}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        docs = await me.get("/api/v1/documents")
        seen = {d["id"] for d in docs.json()}
        _assert_isolated("documents", doc_ids[me.name], doc_ids[other.name], seen)
    for org, expected in ((ctx.a, 2), (ctx.b, 1)):
        vers = await org.get("/api/v1/issuer/logo/versions")
        assert vers.status_code == 200, vers.text
        assert len(vers.json()) == expected, (
            f"document_versions: {org.name} sees {len(vers.json())} versions, expected "
            f"{expected} — a version chain crossed tenants"
        )


@probe("invoice_comments")
async def _p_invoice_comments(ctx: Ctx) -> None:
    inv_ids, comment_ids = {}, {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        inv_ids[org.name] = inv.json()["id"]
        c = await org.post(
            f"/api/v1/invoices/{inv.json()['id']}/comments", json={"body": "overlap comment"}
        )
        assert c.status_code == 201, c.text
        comment_ids[org.name] = c.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/invoices/{inv_ids[me.name]}/comments")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated(
            "invoice_comments", {comment_ids[me.name]}, {comment_ids[other.name]}, seen
        )
    _assert_404(
        await ctx.b.get(f"/api/v1/invoices/{inv_ids[ctx.a.name]}/comments"),
        "invoice comments via A's invoice",
    )


@probe("invoice_attachments")
async def _p_invoice_attachments(ctx: Ctx) -> None:
    inv_ids, att_ids = {}, {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        inv_ids[org.name] = inv.json()["id"]
        up = await org.post(
            f"/api/v1/invoices/{inv.json()['id']}/attachments",
            files={"file": ("po.txt", io.BytesIO(b"purchase order 42"), "text/plain")},
        )
        assert up.status_code == 201, up.text
        att_ids[org.name] = up.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/invoices/{inv_ids[me.name]}/attachments")
        assert listing.status_code == 200, listing.text
        seen = {a["id"] for a in listing.json()}
        _assert_isolated("invoice_attachments", {att_ids[me.name]}, {att_ids[other.name]}, seen)
    _assert_404(
        await ctx.b.get(
            f"/api/v1/invoices/{inv_ids[ctx.b.name]}/attachments/{att_ids[ctx.a.name]}/download"
        ),
        "invoice attachment download with A's attachment id",
    )


# --- Identity & platform ------------------------------------------------------


@probe("users", "memberships")
async def _p_members(ctx: Ctx) -> None:
    # The roster is membership-driven (services/team.list_members -> memberships
    # .roster), so one probe proves both tables over the same real path. Emails
    # are globally unique; the OVERLAP is the display name and role.
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        roster = await me.get("/api/v1/team/members")
        assert roster.status_code == 200, roster.text
        seen_users = {m["id"] for m in roster.json()}
        _assert_isolated("users/memberships", {me.user_id}, {other.user_id}, seen_users)
    _assert_404(
        await ctx.b.patch(f"/api/v1/team/members/{ctx.a.user_id}", json={"role": "admin"}),
        "team member PATCH with A's user id",
    )


@probe("invitations")
async def _p_invitations(ctx: Ctx) -> None:
    inv_ids = {}
    for i, org in enumerate((ctx.a, ctx.b)):
        r = await org.post(
            "/api/v1/team/invites",
            json={"email": f"new-hire{i}@{'ab'[i]}-parity.io", "role": "user"},
        )
        assert r.status_code == 201, r.text
        inv_ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/team/invites")
        assert listing.status_code == 200, listing.text
        seen = {i["id"] for i in listing.json()}
        _assert_isolated("invitations", {inv_ids[me.name]}, {inv_ids[other.name]}, seen)
    # Revoke is idempotent (204 whether or not the id resolves in YOUR org) —
    # equally opaque as a 404: a cross-tenant id is indistinguishable from a
    # nonexistent one and, critically, deletes NOTHING across the boundary.
    cross = await ctx.b.delete(f"/api/v1/team/invites/{inv_ids[ctx.a.name]}")
    assert cross.status_code != 403, "cross-tenant revoke must not confirm the id exists"
    still = await ctx.a.get("/api/v1/team/invites")
    assert inv_ids[ctx.a.name] in {i["id"] for i in still.json()}, (
        "invitations: TENANT LEAK — B's revoke removed A's invitation"
    )


@probe("sessions")
async def _p_sessions(ctx: Ctx) -> None:
    sess_ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.get("/api/v1/auth/sessions")
        assert r.status_code == 200, r.text
        assert r.json(), "an authenticated owner has no session row"
        sess_ids[org.name] = {s["id"] for s in r.json()}
    assert not (sess_ids[ctx.a.name] & sess_ids[ctx.b.name]), "session ids overlap across orgs"
    _assert_404(
        await ctx.b.delete(f"/api/v1/auth/sessions/{next(iter(sess_ids[ctx.a.name]))}"),
        "session revoke with A's session id",
    )


@probe("org_modules")
async def _p_org_modules(ctx: Ctx) -> None:
    # Per-org module switch rows; the read is GET /modules. A enables `budget`,
    # B does not — each org must see exactly its own state.
    assert (await ctx.a.put("/api/v1/modules/budget", json={"enabled": True})).status_code == 200
    for org, expected in ((ctx.a, True), (ctx.b, False)):
        mods = await org.get("/api/v1/modules")
        assert mods.status_code == 200, mods.text
        state = {m["key"]: m["enabled"] for m in mods.json()}
        assert state.get("budget") is expected, (
            f"org_modules: {org.name} sees budget={state.get('budget')}, expected {expected} — "
            "a module toggle leaked across tenants"
        )


@probe("audit_events")
async def _p_audit_events(ctx: Ctx) -> None:
    vendor_ids = {}
    for org in (ctx.a, ctx.b):
        v = await org.post("/api/v1/vendors", json={"name": "Overlap Audit Vendor"})
        vendor_ids[org.name] = v.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        page = await me.get("/api/v1/audit", params={"page_size": 100})
        assert page.status_code == 200, page.text
        events = page.json()["items"] if isinstance(page.json(), dict) else page.json()
        targets = {e["target_id"] for e in events}
        assert vendor_ids[me.name] in targets, "own audit event missing from the org's trail"
        assert vendor_ids[other.name] not in targets, (
            "audit_events: TENANT LEAK — another org's audit event visible"
        )


@probe("jobs")
async def _p_jobs(ctx: Ctx) -> None:
    job_ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/jobs", json={"kind": "dunning.run"})
        assert r.status_code == 201, r.text
        job_ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/jobs")
        assert listing.status_code == 200, listing.text
        seen = {j["id"] for j in listing.json()}
        _assert_isolated("jobs", {job_ids[me.name]}, {job_ids[other.name]}, seen)
    _assert_404(await ctx.b.get(f"/api/v1/jobs/{job_ids[ctx.a.name]}"), "job GET by id")


@probe("webhook_endpoints", "webhook_deliveries")
async def _p_webhooks(ctx: Ctx) -> None:
    ep_ids, delivery_ids = {}, {}
    for org in (ctx.a, ctx.b):
        ep = await org.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/overlap-hook", "events": "*"},
        )
        assert ep.status_code == 201, ep.text
        ep_ids[org.name] = ep.json()["id"]
        ping = await org.post(f"/api/v1/webhooks/{ep.json()['id']}/ping")
        assert ping.status_code in (200, 202), ping.text
        dl = await org.get(f"/api/v1/webhooks/{ep.json()['id']}/deliveries")
        assert dl.status_code == 200, dl.text
        assert dl.json(), "ping queued no delivery"
        delivery_ids[org.name] = {d["id"] for d in dl.json()}
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/webhooks")
        assert listing.status_code == 200, listing.text
        seen = {e["id"] for e in listing.json()}
        _assert_isolated("webhook_endpoints", {ep_ids[me.name]}, {ep_ids[other.name]}, seen)
        dl = await me.get(f"/api/v1/webhooks/{ep_ids[me.name]}/deliveries")
        seen_d = {d["id"] for d in dl.json()}
        _assert_isolated(
            "webhook_deliveries", delivery_ids[me.name], delivery_ids[other.name], seen_d
        )
    _assert_404(
        await ctx.b.get(f"/api/v1/webhooks/{ep_ids[ctx.a.name]}/deliveries"),
        "webhook deliveries via A's endpoint",
    )


@probe("retention_policies", "legal_holds")
async def _p_retention(ctx: Ctx) -> None:
    hold_ids = {}
    for org, days in ((ctx.a, 30), (ctx.b, 60)):
        r = await org.put(
            "/api/v1/retention/policy", json={"category": "invoices", "retain_days": days}
        )
        assert r.status_code == 200, r.text
        h = await org.post("/api/v1/retention/holds", json={"reason": "Overlap litigation"})
        assert h.status_code == 201, h.text
        hold_ids[org.name] = h.json()["id"]
    for (me, other), days in zip(((ctx.a, ctx.b), (ctx.b, ctx.a)), (30, 60), strict=True):
        snap = await me.get("/api/v1/retention")
        assert snap.status_code == 200, snap.text
        by_cat = {c["key"]: c for c in snap.json()["categories"]}
        assert by_cat["invoices"]["retain_days"] == days, (
            "retention_policies: an org read back the OTHER org's retention window"
        )
        seen_holds = {h["id"] for h in snap.json()["holds"]}
        _assert_isolated("legal_holds", {hold_ids[me.name]}, {hold_ids[other.name]}, seen_holds)
    _assert_404(
        await ctx.b.post(f"/api/v1/retention/holds/{hold_ids[ctx.a.name]}/release"),
        "legal hold release by A's hold id",
    )


@probe("sso_connections")
async def _p_sso(ctx: Ctx) -> None:
    for org, slug in ((ctx.a, "alpha-parity"), (ctx.b, "beta-parity")):
        r = await org.put(
            "/api/v1/sso/connection",
            json={
                "slug": slug,
                "protocol": "oidc",
                "enabled": True,
                "issuer": "https://idp.overlap.example",
                "client_id": "overlap-client",
                "client_secret": "shhh",
                "default_role": "user",
            },
        )
        assert r.status_code == 200, r.text
    for org, slug in ((ctx.a, "alpha-parity"), (ctx.b, "beta-parity")):
        got = await org.get("/api/v1/sso/connection")
        assert got.status_code == 200, got.text
        assert got.json()["slug"] == slug, (
            "sso_connections: an org read back the OTHER org's connection"
        )


@probe("tax_codes")
async def _p_tax_codes(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post(
            "/api/v1/tax-codes", json={"code": "OVERLAP-STD", "name": "Overlap", "rate": "21"}
        )
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/tax-codes")
        assert listing.status_code == 200, listing.text
        seen = {t["id"] for t in listing.json()}
        _assert_isolated("tax_codes", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.patch(
            f"/api/v1/tax-codes/{ids[ctx.a.name]}", json={"active": False, "version": 1}
        ),
        "tax code PATCH by id",
    )


@probe("currencies")
async def _p_currencies(ctx: Ctx) -> None:
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.post("/api/v1/currencies", json={"code": "CAD", "name": "Canadian Dollar"})
        assert r.status_code == 201, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/currencies")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated("currencies", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.patch(
            f"/api/v1/currencies/{ids[ctx.a.name]}", json={"active": False, "version": 1}
        ),
        "currency PATCH by id",
    )


@probe("budget_targets")
async def _p_budget(ctx: Ctx) -> None:
    for org, limit in ((ctx.a, "250"), (ctx.b, "300")):
        assert (await org.put("/api/v1/modules/budget", json={"enabled": True})).status_code == 200
        r = await org.put(
            "/api/v1/budget/targets", json={"category": "travel", "monthly_limit": limit}
        )
        assert r.status_code == 200, r.text
    for org, limit in ((ctx.a, "250"), (ctx.b, "300")):
        listing = await org.get("/api/v1/budget/targets")
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        assert len(rows) == 1, f"budget_targets: expected only this org's target, got {rows}"
        assert Decimal(rows[0]["monthly_limit"]) == Decimal(limit), (
            "budget_targets: an org read back the OTHER org's limit"
        )


# --- Transport vertical (WO-76) -----------------------------------------------


@probe("vat_refund_claims", "vat_claim_lines")
async def _p_transport_claims(ctx: Ctx) -> None:
    """WO-76 — the first transport routes. Both orgs drive the SAME claim body
    over the real HTTP path. Module enablement is direct service setup
    (`transport` is deliberately in no billing plan yet, so the HTTP toggle
    would 402 — a commercial decision, docs/DECISIONS-NEEDED.md); the READ
    paths under proof stay HTTP. Claim lines are seeded directly with
    identical business values (the `reimbursement_batches` precedent — the
    route under proof is the read) and read back through the route."""
    from decimal import Decimal as D

    from app.models.transport.vat_claim import VatRefundClaimLine
    from app.services import modules as modules_svc

    claim_ids, line_ids = {}, {}
    for org in (ctx.a, ctx.b):
        await modules_svc.set_enabled(ctx.db, org.org_id, "transport", True)
        ent = await org.post("/api/v1/issuer/registry", json=ISSUER)
        assert ent.status_code == 201, ent.text
        r = await org.post(
            "/api/v1/transport/claims",
            json={
                "entity_id": ent.json()["id"],
                "refund_country": "LV",
                "ref_period": "2026-Q2",
            },
        )
        assert r.status_code == 200, r.text
        claim_ids[org.name] = r.json()["id"]
        row = VatRefundClaimLine(
            org_id=org.org_id,
            claim_id=claim_ids[org.name],
            invoice_ref="INV-OVERLAP-1",
            product_group="Diesel",
            goods_code="1",
            net_eur=D("100.00"),
            vat_eur=D("21.00"),
        )
        ctx.db.add(row)
        await ctx.db.commit()
        line_ids[org.name] = row.id
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/transport/claims")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated("vat_refund_claims", {claim_ids[me.name]}, {claim_ids[other.name]}, seen)
        lines = await me.get(f"/api/v1/transport/claims/{claim_ids[me.name]}/lines")
        assert lines.status_code == 200, lines.text
        seen_lines = {ln["id"] for ln in lines.json()}
        _assert_isolated("vat_claim_lines", {line_ids[me.name]}, {line_ids[other.name]}, seen_lines)
    _assert_404(
        await ctx.b.get(f"/api/v1/transport/claims/{claim_ids[ctx.a.name]}"),
        "transport claim GET by id",
    )
    _assert_404(
        await ctx.b.get(f"/api/v1/transport/claims/{claim_ids[ctx.a.name]}/lines"),
        "transport claim lines via A's claim",
    )


# --- Transport vertical (WO-77 admin/config surfaces) -------------------------


async def _transport_setup(ctx: Ctx) -> dict[str, str]:
    """Both orgs: transport enabled + one issuer entity each, created over the
    real HTTP registry route with the IDENTICAL body (the overlap discipline).
    Returns {org name: entity id}. Module enablement is direct service setup —
    `transport` is deliberately in no billing plan, so the HTTP toggle would
    402 (WO-76's recorded rationale); every READ path under proof stays HTTP."""
    from app.services import modules as modules_svc

    entities: dict[str, str] = {}
    for org in (ctx.a, ctx.b):
        await modules_svc.set_enabled(ctx.db, org.org_id, "transport", True)
        ent = await org.post("/api/v1/issuer/registry", json=ISSUER)
        assert ent.status_code == 201, ent.text
        entities[org.name] = ent.json()["id"]
    await ctx.db.commit()
    return entities


@probe("vat_receipt_waivers")
async def _p_transport_waivers(ctx: Ctx) -> None:
    """WO-77 — waivers set and read entirely over HTTP, on each org's own
    draft claim, with the SAME supplier code on both sides."""
    entities = await _transport_setup(ctx)
    ids, claim_ids = {}, {}
    for org in (ctx.a, ctx.b):
        claim = await org.post(
            "/api/v1/transport/claims",
            json={
                "entity_id": entities[org.name],
                "refund_country": "LV",
                "ref_period": "2026-Q2",
            },
        )
        assert claim.status_code == 200, claim.text
        claim_ids[org.name] = claim.json()["id"]
        r = await org.post(
            f"/api/v1/transport/claims/{claim_ids[org.name]}/waivers", json={"supplier": "Q8"}
        )
        assert r.status_code == 200, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/transport/claims/{claim_ids[me.name]}/waivers")
        assert listing.status_code == 200, listing.text
        seen = {w["id"] for w in listing.json()}
        _assert_isolated("vat_receipt_waivers", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.get(f"/api/v1/transport/claims/{claim_ids[ctx.a.name]}/waivers"),
        "transport waivers via A's claim",
    )


@probe("vat_checklist_rules")
async def _p_transport_checklist_rules(ctx: Ctx) -> None:
    """WO-77 — the rule rows are seeded over HTTP (identical `DEFAULT_RULES`
    keys in both orgs, so only tenancy discriminates) and read back through
    the admin route."""
    await _transport_setup(ctx)
    ids = {}
    for org in (ctx.a, ctx.b):
        seeded = await org.post("/api/v1/transport/checklist-rules/seed")
        assert seeded.status_code == 200, seeded.text
        ids[org.name] = {r["id"] for r in seeded.json()}
        assert ids[org.name], "the probe seeded no rules — the probe is broken"
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/transport/checklist-rules")
        assert listing.status_code == 200, listing.text
        seen = {r["id"] for r in listing.json()}
        _assert_isolated("vat_checklist_rules", ids[me.name], ids[other.name], seen)


@probe("vat_supplier_cadences")
async def _p_transport_cadences(ctx: Ctx) -> None:
    """WO-77 — the SAME supplier code and cadence value in both orgs."""
    await _transport_setup(ctx)
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.put("/api/v1/transport/cadences/Q8", json={"cadence": "monthly"})
        assert r.status_code == 200, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/transport/cadences")
        assert listing.status_code == 200, listing.text
        seen = {c["id"] for c in listing.json()}
        _assert_isolated("vat_supplier_cadences", {ids[me.name]}, {ids[other.name]}, seen)


@probe("vat_receipt_controls")
async def _p_transport_receipt_controls(ctx: Ctx) -> None:
    """WO-77 — the grid rows are seeded DIRECTLY with identical business
    values (the `reimbursement_batches` precedent: the control RUN is the
    close's stage, never a route — R60 — so the route under proof is the
    READ), then read + overridden through the real routes."""
    await _transport_setup(ctx)
    from app.models.transport.receipt_control import VatReceiptControl

    ids = {}
    for org in (ctx.a, ctx.b):
        row = VatReceiptControl(
            org_id=org.org_id,
            entity_id=str(uuid.uuid4()),
            supplier="Q8",
            period="2026-05",
            slot="M",
            country="",
            status="missing",
            txn_count=3,
            waived=False,
            note=None,
        )
        ctx.db.add(row)
        await ctx.db.commit()
        ids[org.name] = row.id
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        grid = await me.get("/api/v1/transport/receipt-controls?period=2026-05")
        assert grid.status_code == 200, grid.text
        seen = {r["id"] for r in grid.json()}
        _assert_isolated("vat_receipt_controls", {ids[me.name]}, {ids[other.name]}, seen)
    _assert_404(
        await ctx.b.post(
            f"/api/v1/transport/receipt-controls/{ids[ctx.a.name]}/override",
            json={"waived": True},
        ),
        "transport receipt-control override on A's row",
    )


@probe("vat_note_invoice_overrides")
async def _p_transport_note_overrides(ctx: Ctx) -> None:
    """WO-77 — each org registers an IDENTICALLY-named vendor+invoice and
    curates the SAME note key onto it; only tenancy discriminates."""
    entities = await _transport_setup(ctx)
    ids = {}
    for org in (ctx.a, ctx.b):
        inv = await org.post("/api/v1/invoices", json=INVOICE_BODY)
        assert inv.status_code == 201, inv.text
        r = await org.put(
            "/api/v1/transport/note-overrides",
            json={
                "entity_id": entities[org.name],
                "supplier": INVOICE_BODY["vendor_name"],
                "refund_country": "NL",
                "invoice_ref": "STMT-OVERLAP-1",
                "target_invoice_id": inv.json()["id"],
            },
        )
        assert r.status_code == 200, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/transport/note-overrides")
        assert listing.status_code == 200, listing.text
        seen = {o["id"] for o in listing.json()}
        _assert_isolated("vat_note_invoice_overrides", {ids[me.name]}, {ids[other.name]}, seen)


@probe("fuel_tieout_expectations")
async def _p_transport_tieout_expectations(ctx: Ctx) -> None:
    """WO-77 — identical typed figures in both orgs, set and read over HTTP."""
    entities = await _transport_setup(ctx)
    ids = {}
    for org in (ctx.a, ctx.b):
        r = await org.put(
            "/api/v1/transport/tie-out-expectations",
            json={
                "entity_id": entities[org.name],
                "supplier": "Q8",
                "period": "2026-05",
                "currency": "EUR",
                "expected_lines": 3,
                "expected_net_eur": "2000.00",
            },
        )
        assert r.status_code == 200, r.text
        ids[org.name] = r.json()["id"]
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get("/api/v1/transport/tie-out-expectations?period=2026-05")
        assert listing.status_code == 200, listing.text
        seen = {e["id"] for e in listing.json()}
        _assert_isolated("fuel_tieout_expectations", {ids[me.name]}, {ids[other.name]}, seen)
    # The by-natural-key delete is opaque across tenants too (§4.4).
    _assert_404(
        await ctx.b.delete(
            "/api/v1/transport/tie-out-expectations"
            f"?entity_id={entities[ctx.a.name]}&supplier=Q8&period=2026-05&currency=EUR"
        ),
        "transport tie-out delete on A's key",
    )


@probe("vat_customer_lifecycles", "vat_country_activations")
async def _p_transport_customer_lifecycle(ctx: Ctx) -> None:
    """WO-77 — the lifecycle ladder driven over HTTP in both orgs with the
    same country; each org's overview shows only its own rows, and A's entity
    id is an opaque 404 for B."""
    entities = await _transport_setup(ctx)
    life_ids, country_ids = {}, {}
    for org in (ctx.a, ctx.b):
        base = f"/api/v1/transport/customers/{entities[org.name]}"
        assert (await org.post(f"{base}/prospect")).status_code == 200
        requested = await org.post(f"{base}/countries/LV/request")
        assert requested.status_code == 200, requested.text
        body = requested.json()
        life_ids[org.name] = body["id"]
        country_ids[org.name] = {c["id"] for c in body["countries"]}
        assert country_ids[org.name], "the probe seeded no country row — the probe is broken"
    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        overview = await me.get(f"/api/v1/transport/customers/{entities[me.name]}/lifecycle")
        assert overview.status_code == 200, overview.text
        body = overview.json()
        _assert_isolated(
            "vat_customer_lifecycles",
            {life_ids[me.name]},
            {life_ids[other.name]},
            {body["id"]},
        )
        _assert_isolated(
            "vat_country_activations",
            country_ids[me.name],
            country_ids[other.name],
            {c["id"] for c in body["countries"]},
        )
    _assert_404(
        await ctx.b.get(f"/api/v1/transport/customers/{entities[ctx.a.name]}/lifecycle"),
        "transport lifecycle via A's entity",
    )
    _assert_404(
        await ctx.b.post(f"/api/v1/transport/customers/{entities[ctx.a.name]}/promote"),
        "transport promote via A's entity",
    )


# --- Transport vertical (WO-79 fuel-transaction read surface) -----------------


@probe("fuel_transactions")
async def _p_transport_fuel_transactions(ctx: Ctx) -> None:
    """WO-79 — `fuel_transactions` was an EXEMPT row from WO-50 until this
    order, for one reason its own exemption text stated: no route RETURNED one.
    `GET /transport/fuel-transactions` closes that, so the table now proves its
    isolation over the REAL HTTP read path like every other claim table.

    The rows are seeded through `fuel_ingest.ingest_transaction` (the only
    writer there is — the route surface is read-only by design, so there is no
    HTTP create to drive) with IDENTICAL business values in both orgs: same
    supplier, period, country, station, product, litres and amounts. Only
    tenancy discriminates, so a broken filter cannot pass by luck. Every
    assertion under proof is the HTTP READ."""
    from datetime import date
    from decimal import Decimal as D

    from app.services.transport import fuel_ingest

    entities = await _transport_setup(ctx)
    txn_ids = {}
    for org in (ctx.a, ctx.b):
        txn = await fuel_ingest.ingest_transaction(
            ctx.db,
            org.org_id,
            entity_id=entities[org.name],
            supplier="Q8",
            period="2026-05",
            line_seq=1,
            country="LV",
            vehicle_ref="TEST-CARD-0001",
            txn_date=date(2026, 5, 15),
            station="Overlap Station",
            product="DIESEL",
            qty=D("100.000"),
            currency="EUR",
            net_local=D("2000.00"),
            vat_local=D("420.00"),
            gross_local=D("2420.00"),
            net_eur=D("2000.00"),
            vat_eur=D("420.00"),
            invoice_ref="INV-OVERLAP-1",
        )
        txn_ids[org.name] = txn.id
    await ctx.db.commit()

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(
            f"/api/v1/transport/fuel-transactions?entity_id={entities[me.name]}&period=2026-05"
        )
        assert listing.status_code == 200, listing.text
        seen = {t["id"] for t in listing.json()["items"]}
        _assert_isolated("fuel_transactions", {txn_ids[me.name]}, {txn_ids[other.name]}, seen)
    # The only by-id surface this route exposes is the ENTITY filter — B naming
    # A's entity must be an opaque 404, never a 403 (§4.4).
    _assert_404(
        await ctx.b.get(
            f"/api/v1/transport/fuel-transactions?entity_id={entities[ctx.a.name]}&period=2026-05"
        ),
        "transport fuel transactions via A's entity",
    )


@probe("vat_supplier_contract_terms", "vat_overcharge_claims")
async def _p_transport_overcharge(ctx: Ctx) -> None:
    """WO-82 — the two G4.5 tables, both proven over the REAL HTTP routes and
    both seeded with IDENTICAL business values in each org: the same supplier
    code, the same country/product group, the same agreed €/L discount, the same
    period, the same litres and the same amounts. Only tenancy discriminates,
    so a broken filter cannot pass by luck.

    The contract TERM is created over `PUT /transport/contract-terms` and read
    over its GET. The claim-back is opened over `POST /transport/overcharges`
    (which needs a real detected breach, hence the fuel line seeded through
    `fuel_ingest.ingest_transaction` — the only writer there is) and read over
    its list. Both by-id surfaces are checked for the §4.4 opaque 404."""
    from datetime import date
    from decimal import Decimal as D

    from app.services.transport import fuel_ingest

    entities = await _transport_setup(ctx)
    term_ids, claim_ids = {}, {}
    for org in (ctx.a, ctx.b):
        term = await org.put(
            "/api/v1/transport/contract-terms",
            json={
                "supplier": "Q8",
                "country": "LV",
                "product_group": "Diesel",
                "expected_discount_eur_l": "0.20",
            },
        )
        assert term.status_code == 200, term.text
        term_ids[org.name] = term.json()["id"]

        await fuel_ingest.ingest_transaction(
            ctx.db,
            org.org_id,
            entity_id=entities[org.name],
            supplier="Q8",
            period="2026-05",
            line_seq=1,
            country="LV",
            vehicle_ref="TEST-CARD-0001",
            txn_date=date(2026, 5, 15),
            station="Overlap Station",
            product="DIESEL",
            qty=D("1000.000"),
            currency="EUR",
            net_local=D("1350.00"),
            vat_local=D("283.50"),
            gross_local=D("1633.50"),
            net_eur=D("1350.00"),
            vat_eur=D("283.50"),
            net_eur_eff=D("1300.00"),
        )
        await ctx.db.commit()

        claim = await org.post(
            "/api/v1/transport/overcharges", json={"supplier": "Q8", "period": "2026-05"}
        )
        assert claim.status_code == 200, claim.text
        claim_ids[org.name] = claim.json()["id"]

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        terms = await me.get("/api/v1/transport/contract-terms")
        assert terms.status_code == 200, terms.text
        _assert_isolated(
            "vat_supplier_contract_terms",
            {term_ids[me.name]},
            {term_ids[other.name]},
            {t["id"] for t in terms.json()},
        )
        claims = await me.get("/api/v1/transport/overcharges")
        assert claims.status_code == 200, claims.text
        _assert_isolated(
            "vat_overcharge_claims",
            {claim_ids[me.name]},
            {claim_ids[other.name]},
            {c["id"] for c in claims.json()},
        )

    _assert_404(
        await ctx.b.get(f"/api/v1/transport/overcharges/{claim_ids[ctx.a.name]}"),
        "transport overcharge claim-back fetch on A's row",
    )
    _assert_404(
        await ctx.b.post(
            f"/api/v1/transport/overcharges/{claim_ids[ctx.a.name]}/advance",
            json={"to_status": "packaged"},
        ),
        "transport overcharge claim-back advance on A's row",
    )


@probe("vat_off_invoice_rebates")
async def _p_transport_off_invoice_rebate(ctx: Ctx) -> None:
    """WO-84 — the G4.2 off-invoice rebate registry, proven over the REAL HTTP
    route and seeded with IDENTICAL business values in each org: the same fuel
    network, the same country and period, the same rebate document NUMBER, the
    same issuing party and the same amount. Only tenancy discriminates, so a
    missing `org_id` filter leaks visibly instead of passing by accident.

    Deliberately a real probe rather than an EXEMPT row, in the same commit that
    created the table."""
    from app.services import modules as modules_svc

    rebate_ids: dict[str, str] = {}
    for org in (ctx.a, ctx.b):
        await modules_svc.set_enabled(ctx.db, org.org_id, "transport", True)
        await ctx.db.commit()

        created = await org.post(
            "/api/v1/transport/rebates",
            json={
                "supplier": "Q8",
                "country": "LV",
                "period": "2026-05",
                "source_ref": "RBT-PARITY-1",
                "source_party": "Demo Rebate Partner",
                "rebate_date": "2026-06-05",
                "currency": "EUR",
                "amount_local": "50.00",
            },
        )
        assert created.status_code == 200, created.text
        rebate_ids[org.name] = created.json()["id"]

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listed = await me.get("/api/v1/transport/rebates")
        assert listed.status_code == 200, listed.text
        _assert_isolated(
            "vat_off_invoice_rebates",
            {rebate_ids[me.name]},
            {rebate_ids[other.name]},
            {r["id"] for r in listed.json()},
        )


@probe("vat_excise_rates")
async def _p_transport_excise_rates(ctx: Ctx) -> None:
    """WO-91 — the G4.6 diesel-excise rate registry, proven over the REAL HTTP
    routes. Deliberately a real probe rather than an EXEMPT row, in the same
    commit that creates the table.

    THE DISCRIMINATOR IS THE RATE, NOT A ROW ID, AND THAT IS DELIBERATE. This
    table's natural key is `(org, country)` and its wire shape carries no `id`
    (the resolved-rate view is keyed by country, so an id would be noise a
    client could not use). The overlap discipline is therefore applied to the
    VALUES: both orgs override the SAME state (`FR`) — same table, same country,
    same shape — at DIFFERENT rates, and B additionally overrides `IT` while A
    leaves it on the harvested default.

    A missing `org_id` filter is then visible three separate ways, in both
    directions: A would read B's FR rate, A's `IT` would flip from the EUR 30.00
    default to B's override, and `is_override` would be true for a state the
    reader never configured. Values that a broken filter cannot reproduce by
    luck are exactly what `_assert_isolated`'s id sets buy elsewhere.
    """
    from app.services import modules as modules_svc

    await _transport_setup(ctx)

    seeded = {ctx.a.name: {"FR": "22.5000"}, ctx.b.name: {"FR": "27.7500", "IT": "19.0000"}}
    for org in (ctx.a, ctx.b):
        await modules_svc.set_enabled(ctx.db, org.org_id, "transport", True)
        await ctx.db.commit()
        for country, rate in seeded[org.name].items():
            put = await org.put(
                "/api/v1/transport/excise/rates",
                json={"country": country, "rate_eur_per_1000l": rate},
            )
            assert put.status_code == 200, put.text

    for me in (ctx.a, ctx.b):
        listed = await me.get("/api/v1/transport/excise/rates")
        assert listed.status_code == 200, listed.text
        by_country = {r["country"]: r for r in listed.json()["rates"]}
        mine = seeded[me.name]
        for country, rate in mine.items():
            assert by_country[country]["rate_eur_per_1000l"] == rate, (
                f"vat_excise_rates: {me.name}'s own {country} rate is not what it typed"
            )
            assert by_country[country]["is_override"] is True
        for country in ("FR", "IT"):
            if country in mine:
                continue
            # Not configured by this org: it must read as the harvested default,
            # never as the OTHER org's typed override (the leak this catches).
            assert by_country[country]["is_override"] is False, (
                f"vat_excise_rates: TENANT LEAK — {me.name} sees an override it never typed "
                f"for {country}"
            )
            assert by_country[country]["rate_eur_per_1000l"] == "30.0000", (
                f"vat_excise_rates: TENANT LEAK — {me.name}'s {country} rate is not the default"
            )


# --------------------------------------------------------------------------- #
# The tests
# --------------------------------------------------------------------------- #


@probe("archived_invoices")
async def _archived_invoices(ctx: Ctx) -> None:
    """The platform archive — records kept after their recycle bin expired.

    A real probe rather than an EXEMPT row, in the same commit that created the
    table. This one earns the probe more than most: it holds the records clients
    believe they DELETED, and the owner's decision that a client's own company
    owner may read it turns its org filter from a backstop into a primary
    control read by every client owner.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.archived_invoice import ArchivedInvoice

    ids: dict[str, str] = {}
    for org in (ctx.a, ctx.b):
        row = ArchivedInvoice(
            org_id=org.org_id,
            original_invoice_id=str(uuid.uuid4()),
            invoice_number=f"ARC-PARITY-{org.name}",
            vendor_name="Fictional Fuels OU",
            currency="EUR",
            archived_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=365),
        )
        ctx.db.add(row)
        await ctx.db.commit()
        ids[org.name] = row.id

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listed = await me.get("/api/v1/archive")
        assert listed.status_code == 200, listed.text
        seen = {i["id"] for i in listed.json()["items"]}
        assert ids[other.name] not in seen, (
            f"TENANT LEAK: {me.name} saw {other.name}'s archived invoice in the list"
        )
        fetched = await me.get(f"/api/v1/archive/{ids[other.name]}")
        assert fetched.status_code == 404, (
            f"TENANT LEAK: {me.name} fetched {other.name}'s archived invoice "
            f"({fetched.status_code})"
        )
        doc = await me.get(f"/api/v1/archive/{ids[other.name]}/document")
        assert doc.status_code == 404, (
            f"TENANT LEAK: {me.name} reached {other.name}'s archived document ({doc.status_code})"
        )


@probe("project_cost_entries")
async def _project_cost_entries(ctx: Ctx) -> None:
    """Manual cost lines on a project (project-profitability phase 1). The org
    filter here guards a competitor-sensitive figure: what a rival tenant pays
    its people for a job is exactly the number a leak would hand over."""
    from decimal import Decimal

    ids: dict[str, dict] = {}
    for org in (ctx.a, ctx.b):
        proj = await org.post(
            "/api/v1/masters/projects",
            json={"code": f"PAR-{org.name}", "name": f"Parity {org.name}"},
        )
        assert proj.status_code in (200, 201), proj.text
        project_id = proj.json()["id"]
        entry = await org.post(
            f"/api/v1/masters/projects/{project_id}/cost-entries",
            json={"label": f"Wages {org.name}", "category": "wages", "amount": "100.00"},
        )
        assert entry.status_code == 201, entry.text
        ids[org.name] = {"project": project_id, "entry": entry.json()["id"]}

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        # The other tenant's project is opaque — so are everything under it.
        listing = await me.get(
            f"/api/v1/masters/projects/{ids[other.name]['project']}/cost-entries"
        )
        assert listing.status_code == 404, (
            f"TENANT LEAK: {me.name} listed {other.name}'s cost entries ({listing.status_code})"
        )
        # And MY project's listing never contains THEIR entry.
        mine = await me.get(f"/api/v1/masters/projects/{ids[me.name]['project']}/cost-entries")
        assert mine.status_code == 200, mine.text
        assert ids[other.name]["entry"] not in {e["id"] for e in mine.json()}, (
            f"TENANT LEAK: {me.name} saw {other.name}'s cost entry"
        )
        # Cross-tenant delete: opaque 404, and the row survives.
        gone = await me.delete(
            f"/api/v1/masters/projects/{ids[other.name]['project']}"
            f"/cost-entries/{ids[other.name]['entry']}"
        )
        assert gone.status_code == 404, (
            f"TENANT LEAK: {me.name} deleted {other.name}'s cost entry ({gone.status_code})"
        )
        assert Decimal(
            (await other.get(f"/api/v1/masters/projects/{ids[other.name]['project']}/pnl")).json()[
                "manual_costs"
            ]
        ) == Decimal("100.00"), "a cross-tenant delete attempt must not touch the row"


@probe("project_documents")
async def _project_documents(ctx: Ctx) -> None:
    """Contract files attached to a project. The bytes are a signed commercial
    contract — the single most sensitive document a tenant stores."""
    ids: dict[str, dict] = {}
    for org in (ctx.a, ctx.b):
        proj = await org.post(
            "/api/v1/masters/projects",
            json={"code": f"DOC-{org.name}", "name": f"Docs {org.name}"},
        )
        assert proj.status_code in (200, 201), proj.text
        project_id = proj.json()["id"]
        up = await org.post(
            f"/api/v1/masters/projects/{project_id}/documents",
            files={"file": (f"contract-{org.name}.pdf", b"%PDF-1.4 parity", "application/pdf")},
        )
        assert up.status_code == 201, up.text
        ids[org.name] = {"project": project_id, "doc": up.json()["id"]}

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/masters/projects/{ids[other.name]['project']}/documents")
        assert listing.status_code == 404, (
            f"TENANT LEAK: {me.name} listed {other.name}'s project documents"
        )
        dl = await me.get(
            f"/api/v1/masters/projects/{ids[other.name]['project']}"
            f"/documents/{ids[other.name]['doc']}/download"
        )
        assert dl.status_code == 404, (
            f"TENANT LEAK: {me.name} downloaded {other.name}'s contract ({dl.status_code})"
        )


@probe("invoice_project_splits")
async def _invoice_project_splits(ctx: Ctx) -> None:
    """Percentage cost allocation (project-profitability phase 2). Shipped a run
    late — the partition guard caught the table probeless in the full
    regression, which is the guard doing its job; the probe now proves the
    allocation surface end to end. What a rival tenant allocates to which jobs
    is commercial structure a leak would hand over."""
    ids: dict[str, dict] = {}
    for org in (ctx.a, ctx.b):
        proj = await org.post(
            "/api/v1/masters/projects",
            json={"code": f"ALC-{org.name}", "name": f"Alloc {org.name}"},
        )
        assert proj.status_code in (200, 201), proj.text
        inv = await org.post(
            "/api/v1/invoices",
            json={
                "vendor_name": f"Supplier {org.name} OU",
                "invoice_number": f"ALC-INV-{org.name}",
                "issue_date": "2026-08-01",
                "currency": "EUR",
                "line_items": [
                    {
                        "description": "Work",
                        "quantity": "1",
                        "unit_price": "100.00",
                        "amount": "100.00",
                        "tax_rate": "0",
                    }
                ],
            },
        )
        assert inv.status_code == 201, inv.text
        alloc = await org.put(
            f"/api/v1/invoices/{inv.json()['id']}/allocation",
            json={"splits": [{"project_id": proj.json()["id"], "percent": "100"}]},
        )
        assert alloc.status_code == 200, alloc.text
        ids[org.name] = {"project": proj.json()["id"], "invoice": inv.json()["id"]}

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        # Writing an allocation on the OTHER tenant's invoice: opaque 404.
        w = await me.put(
            f"/api/v1/invoices/{ids[other.name]['invoice']}/allocation",
            json={"splits": [{"project_id": ids[me.name]["project"], "percent": "100"}]},
        )
        assert w.status_code == 404, (
            f"TENANT LEAK: {me.name} wrote an allocation on {other.name}'s invoice "
            f"({w.status_code})"
        )
        # Splitting MY invoice to THEIR project: opaque 404 — a split row must
        # never bridge two tenants.
        x = await me.put(
            f"/api/v1/invoices/{ids[me.name]['invoice']}/allocation",
            json={"splits": [{"project_id": ids[other.name]["project"], "percent": "100"}]},
        )
        assert x.status_code == 404, (
            f"TENANT LEAK: {me.name} split an invoice onto {other.name}'s project ({x.status_code})"
        )
        # And THEIR split never leaks into MY project's P&L.
        pnl = await me.get(f"/api/v1/masters/projects/{ids[me.name]['project']}/pnl")
        assert pnl.status_code == 200, pnl.text
        assert pnl.json()["invoice_costs"] == "100.00", (
            f"TENANT LEAK: {me.name}'s P&L moved after {other.name}'s writes"
        )


@probe("project_offers")
async def _project_offers(ctx: Ctx) -> None:
    """Offers/estimates (lifecycle phase 4). An offer is a PRICE a tenant quoted
    — the single most competitively sensitive number they hold before a
    contract exists."""
    ids: dict[str, dict] = {}
    for org in (ctx.a, ctx.b):
        proj = await org.post(
            "/api/v1/masters/projects",
            json={"code": f"OFR-{org.name}", "name": f"Offers {org.name}"},
        )
        assert proj.status_code in (200, 201), proj.text
        offer = await org.post(
            f"/api/v1/masters/projects/{proj.json()['id']}/offers",
            json={
                "title": f"Quote {org.name}",
                "lines": [{"description": "Work", "amount": "500.00"}],
            },
        )
        assert offer.status_code == 201, offer.text
        ids[org.name] = {"project": proj.json()["id"], "offer": offer.json()["id"]}

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        listing = await me.get(f"/api/v1/masters/projects/{ids[other.name]['project']}/offers")
        assert listing.status_code == 404, (
            f"TENANT LEAK: {me.name} listed {other.name}'s offers ({listing.status_code})"
        )
        hijack = await me.post(
            f"/api/v1/masters/projects/{ids[other.name]['project']}"
            f"/offers/{ids[other.name]['offer']}/transition",
            json={"status": "accepted"},
        )
        assert hijack.status_code == 404, (
            f"TENANT LEAK: {me.name} transitioned {other.name}'s offer ({hijack.status_code})"
        )


@probe("invoicing_plan_rows")
async def _invoicing_plan_rows(ctx: Ctx) -> None:
    """The contracted invoicing schedule (lifecycle phase 4) — what a tenant's
    customer agreed to pay and when. Cross-tenant reads and writes are opaque."""
    ids: dict[str, str] = {}
    for org in (ctx.a, ctx.b):
        proj = await org.post(
            "/api/v1/masters/projects",
            json={"code": f"PLN-{org.name}", "name": f"Plan {org.name}"},
        )
        assert proj.status_code in (200, 201), proj.text
        put = await org.put(
            f"/api/v1/masters/projects/{proj.json()['id']}/invoicing-plan",
            json=[{"label": f"Advance {org.name}", "amount": "1000.00"}],
        )
        assert put.status_code == 200, put.text
        ids[org.name] = proj.json()["id"]

    for me, other in ((ctx.a, ctx.b), (ctx.b, ctx.a)):
        read = await me.get(f"/api/v1/masters/projects/{ids[other.name]}/invoicing-plan")
        assert read.status_code == 404, (
            f"TENANT LEAK: {me.name} read {other.name}'s invoicing plan ({read.status_code})"
        )
        wipe = await me.put(f"/api/v1/masters/projects/{ids[other.name]}/invoicing-plan", json=[])
        assert wipe.status_code == 404, (
            f"TENANT LEAK: {me.name} rewrote {other.name}'s invoicing plan ({wipe.status_code})"
        )
        mine = await me.get(f"/api/v1/masters/projects/{ids[me.name]}/invoicing-plan")
        assert mine.json()["contracted_total"] == "1000.00", (
            f"TENANT LEAK: {me.name}'s plan moved after {other.name}'s writes"
        )


ALL_TABLES = sorted(m.__tablename__ for m in TENANT_MODELS)


def test_exemption_list_has_no_stale_entries():
    """PROBES ∪ EXEMPT must equal the tenant-model registry EXACTLY, and the two
    sets must be disjoint — a new tenant table with neither a probe nor a
    reasoned exemption fails by name, and a stale exemption (table dropped or
    since given a probe) fails too. Every exemption carries a reason."""
    tables = set(ALL_TABLES)
    probed = set(PROBES)
    exempt = set(EXEMPT)
    assert probed.isdisjoint(exempt), f"both probed and exempt: {sorted(probed & exempt)}"
    unclassified = tables - probed - exempt
    assert not unclassified, (
        f"tenant tables with neither a parity probe nor a reasoned exemption: "
        f"{sorted(unclassified)}"
    )
    stale = (probed | exempt) - tables
    assert not stale, f"probe/exemption entries naming a non-tenant table: {sorted(stale)}"
    for table, reason in EXEMPT.items():
        assert reason and len(reason) > 20, f"exemption for {table} carries no real reason"


@pytest.mark.asyncio
@pytest.mark.parametrize("table", sorted(PROBES))
async def test_every_scoped_table_isolates_via_the_real_query_path(table, ctx):
    await PROBES[table](ctx)


@pytest.mark.asyncio
async def test_cross_tenant_fetch_by_id_returns_404_not_403(ctx):
    """Invariant §4.4 across the three core object kinds: a cross-tenant id and a
    nonexistent id are indistinguishable — both an opaque 404, never a 403."""
    inv = await ctx.a.post("/api/v1/invoices", json=INVOICE_BODY)
    issued = await ctx.a.post("/api/v1/issued", json=ISSUED_BODY)
    rep = await ctx.a.post("/api/v1/expenses", json=EXPENSE_BODY)
    for path in (
        f"/api/v1/invoices/{inv.json()['id']}",
        f"/api/v1/issued/{issued.json()['id']}",
        f"/api/v1/expenses/{rep.json()['id']}",
    ):
        _assert_404(await ctx.b.get(path), f"cross-tenant {path}")
    ghost = await ctx.b.get(f"/api/v1/invoices/{uuid.uuid4()}")
    real = await ctx.b.get(f"/api/v1/invoices/{inv.json()['id']}")
    assert ghost.status_code == real.status_code == 404, (
        "a cross-tenant id must be indistinguishable from a nonexistent one"
    )


@pytest.mark.asyncio
async def test_tenant_context_does_not_leak_between_requests():
    """The guard's lifecycle: `TenantScopeMiddleware` resets the tenant
    ContextVar at BOTH ends of every request, so a handler that sets (and even
    'forgets' to reset) the org can never leak it into the next request or back
    into the caller's context. Proven over the real middleware class wrapping a
    deliberately leaky inner app."""
    from app.core import tenant

    observed: list[str | None] = []

    async def leaky_app(scope, receive, send):
        observed.append(tenant.get_current_org())
        tenant.set_current_org("leaked-org")  # a buggy handler that never resets
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    wrapped = tenant.TenantScopeMiddleware(leaky_app)
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://parity") as c:
        await c.get("/one")
        await c.get("/two")

    assert observed == [None, None], f"tenant context leaked between requests: {observed}"
    assert tenant.get_current_org() is None, "tenant context leaked out of the request cycle"


@pytest.mark.asyncio
async def test_parity_check_catches_a_deliberately_unscoped_query(ctx, monkeypatch):
    """The self-test: with the vendors service's org filter dropped AND the ORM
    guard neutralised (both app-layer defences off, as a real regression would
    be), the vendors probe MUST fail. A checker that cannot fail proves
    nothing."""
    from sqlalchemy import select as sa_select

    from app.core import tenant
    from app.models.vendor import Vendor
    from app.services import vendors as vendor_service

    async def unscoped_list(db, org_id):  # the bug under test: no org filter
        return list((await db.scalars(sa_select(Vendor))).all())

    monkeypatch.setattr(vendor_service, "list_vendors", unscoped_list)
    monkeypatch.setattr(tenant, "TENANT_MODELS", ())  # defence layer 2 off

    with pytest.raises(AssertionError, match="TENANT LEAK"):
        await PROBES["vendors"](ctx)
