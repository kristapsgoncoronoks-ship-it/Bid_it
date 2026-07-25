"""Opt-in data validation: AI (automated) and human review, both off by default.

Plus the WO-7 rule-registry integrity checks: one engine in
app/services/validation.py, every rule declaring `block | advise` and its own
tolerance, no rule implemented twice, and a stable code set.
"""

import pytest


def _payload(number="V-1", unit="100.00", qty="1", tax="0", status="pending"):
    return {
        "vendor_name": "Acme",
        "invoice_number": number,
        "issue_date": "2026-03-01",
        "currency": "EUR",
        "status": status,
        "line_items": [
            {
                "description": "svc",
                "category": "c",
                "quantity": qty,
                "unit_price": unit,
                "tax_rate": tax,
            },
        ],
    }


async def _set_settings(auth_client, ai=None, human=None):
    body = {}
    if ai is not None:
        body["ai_validation_enabled"] = ai
    if human is not None:
        body["human_validation_enabled"] = human
    return await auth_client.put("/api/v1/settings/validation", json=body)


@pytest.mark.asyncio
async def test_defaults_off_status_none(auth_client):
    s = (await auth_client.get("/api/v1/settings/validation")).json()
    assert s == {"ai_validation_enabled": False, "human_validation_enabled": False}

    inv = (await auth_client.post("/api/v1/invoices", json=_payload())).json()
    assert inv["validation_status"] == "none"


@pytest.mark.asyncio
async def test_ai_only_passes_clean_invoice(auth_client):
    await _set_settings(auth_client, ai=True)
    inv = (await auth_client.post("/api/v1/invoices", json=_payload())).json()
    assert inv["validation_status"] == "passed"
    assert inv["validation_findings"] == []


@pytest.mark.asyncio
async def test_ai_flags_duplicate_and_line_math(auth_client):
    await _set_settings(auth_client, ai=True)
    await auth_client.post("/api/v1/invoices", json=_payload("DUP-1"))
    # same vendor + number → duplicate (error); qty×unit mismatch → warning
    bad = {
        "vendor_name": "Acme",
        "invoice_number": "DUP-1",
        "issue_date": "2026-03-01",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "3",
                "unit_price": "100.00",
                "amount": "250.00",
                "tax_rate": "0",
            },
        ],
    }
    inv = (await auth_client.post("/api/v1/invoices", json=bad)).json()
    assert inv["validation_status"] == "flagged"
    codes = {f["code"] for f in inv["validation_findings"]}
    assert "duplicate" in codes
    assert "line_math" in codes


@pytest.mark.asyncio
async def test_human_review_gate_and_approve(auth_client):
    await _set_settings(auth_client, human=True)
    inv = (await auth_client.post("/api/v1/invoices", json=_payload("H-1"))).json()
    assert inv["validation_status"] == "pending"

    # shows up in the pending queue
    queue = (await auth_client.get("/api/v1/invoices?validation_status=pending")).json()
    assert queue["total"] == 1

    approved = (
        await auth_client.post(
            f"/api/v1/invoices/{inv['id']}/validate",
            json={"action": "approve", "note": "looks good"},
        )
    ).json()
    assert approved["validation_status"] == "approved"
    assert approved["validated_by"] == "owner@acme.io"


@pytest.mark.asyncio
async def test_both_on_ai_findings_plus_human_gate(auth_client):
    await _set_settings(auth_client, ai=True, human=True)
    # future date → warning, but human still gates it at pending
    p = _payload("B-1")
    p["issue_date"] = "2999-01-01"
    inv = (await auth_client.post("/api/v1/invoices", json=p)).json()
    assert inv["validation_status"] == "pending"
    assert any(f["code"] == "future_date" for f in inv["validation_findings"])

    rejected = (
        await auth_client.post(f"/api/v1/invoices/{inv['id']}/validate", json={"action": "reject"})
    ).json()
    assert rejected["validation_status"] == "rejected"


@pytest.mark.asyncio
async def test_settings_owner_only(client):
    # a fresh org owner can change settings; verify PUT works for owner
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Z",
            "name": "z",
            "email": "z@zed.io",
            "password": "supersecret",
        },
    )
    tok = r.json()["token"]["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    upd = await client.put(
        "/api/v1/settings/validation", json={"ai_validation_enabled": True}, headers=h
    )
    assert upd.status_code == 200
    assert upd.json()["ai_validation_enabled"] is True


# --------------------------------------------------------------------------- #
# WO-7 — the one rule registry (integrity + stability)
# --------------------------------------------------------------------------- #


def test_rule_registry_has_no_duplicate_codes():
    from app.services import validation

    codes = [r.code for r in validation.RULES]
    assert len(codes) == len(set(codes)), f"duplicate rule codes: {sorted(codes)}"
    assert all(c and c.strip() for c in codes), "every rule must have a non-empty code"


def test_block_rules_have_zero_tolerance():
    from decimal import Decimal

    from app.services import validation

    # Zero tolerance on every blocking rule is deliberate (see the module
    # docstring): a submitted invoice enters the approval chain and locks on
    # approval — drift there is a control failure. No exception is justified.
    for r in validation.RULES:
        if r.policy == "block":
            assert r.tolerance == Decimal("0"), f"block rule {r.code} must have zero tolerance"


def test_rule_codes_snapshot():
    """The sorted code list is a stable contract — an accidental rename or a
    silently dropped rule must be a visible diff here."""
    from app.services import validation

    assert sorted(r.code for r in validation.RULES) == [
        "due_before_issue",
        "duplicate",
        "duplicate_cross_supplier",
        "future_date",
        "fx_deviation",
        "line_math",
        "missing_number",
        "no_lines",
        "non_positive_total",
        "old_date",
        "recon_line_amount",
        "recon_subtotal",
        "recon_tax",
        "recon_tax_rate_range",
        "recon_total",
        "subtotal_mismatch",
        "tax_mismatch",
        "total_mismatch",
        "unknown_currency",
    ]


def test_every_rule_declares_policy_and_scope():
    from app.services import validation

    for r in validation.RULES:
        assert r.policy in ("block", "advise")
        assert r.scope in ("header", "line", "document")


@pytest.mark.asyncio
async def test_advisory_findings_shape_unchanged(auth_client):
    """Persisted findings keep the exact {severity, code, message, field} shape
    the SPA renders — the refactor must not change it."""
    await _set_settings(auth_client, ai=True)
    p = _payload("SHAPE-1")
    p["issue_date"] = "2999-01-01"  # future date → at least one finding
    inv = (await auth_client.post("/api/v1/invoices", json=p)).json()
    assert inv["validation_findings"], "expected at least one advisory finding"
    for f in inv["validation_findings"]:
        assert set(f.keys()) == {"severity", "code", "message", "field"}
        assert f["severity"] in ("info", "warning", "error")
        assert f["code"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ai", "human", "expected_status"),
    [
        (False, False, "none"),  # both off: invoice behaves exactly as before
        (True, False, "flagged"),  # AI only: an error finding auto-resolves to flagged
        (False, True, "pending"),  # human only: routed to the review gate
        (True, True, "pending"),  # both: AI findings assist, human still gates
    ],
)
async def test_toggle_matrix(auth_client, ai, human, expected_status):
    await _set_settings(auth_client, ai=ai, human=human)
    # A same-vendor duplicate number gives the AI validator an error finding,
    # so the AI-only combination lands on `flagged` (not `passed`).
    await auth_client.post("/api/v1/invoices", json=_payload("TM-1"))
    inv = (await auth_client.post("/api/v1/invoices", json=_payload("TM-1"))).json()
    assert inv["validation_status"] == expected_status


def test_no_rule_is_implemented_twice():
    """The route module must not contain a reconciliation implementation any
    more — the service registry is the only engine (source scan is cheap and
    catches a reintroduced helper)."""
    from pathlib import Path

    import app.api.routes.invoice_review as review_route

    src = Path(review_route.__file__).read_text(encoding="utf-8")
    assert "_reconcile" not in src, "reconciliation logic must live in services/validation.py"
    assert "Subtotal mismatch" not in src, "reconcile message strings belong to the service"
    assert "out of range 0–100" not in src, "tax-range check belongs to the service"
