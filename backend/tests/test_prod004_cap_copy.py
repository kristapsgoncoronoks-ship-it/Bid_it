"""PROD-004 (audit 2026-09-05) — the cap refusal names a door the customer can open.

Both 402s said "ask a platform operator to raise the limit". A workspace's
members cannot reach a platform operator; the person who can act is the
workspace owner, and the door is Plan & billing. The SPA now warns before the
cap and shows that path (`frontend/e2e/fe-product-set.spec.ts`); the refusal
itself says the same thing.
"""

from __future__ import annotations

import pytest

from tests.test_access import _inv, _make_platform_op


@pytest.mark.asyncio
async def test_the_402s_point_at_plan_and_billing_not_a_platform_operator(auth_client, db_session):
    await _make_platform_op(db_session)
    await auth_client.put(
        "/api/v1/access/matrix/trial",
        json={"monthly_invoice_limit": 1, "monthly_upload_limit": 1},
    )
    assert (await auth_client.post("/api/v1/invoices", json=_inv(1))).status_code == 201
    blocked = await auth_client.post("/api/v1/invoices", json=_inv(2))
    assert blocked.status_code == 402
    detail = blocked.json()["detail"]
    assert "platform operator" not in detail.lower()
    assert "Plan & billing" in detail and "owner" in detail.lower()
