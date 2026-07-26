"""AI policy (WO-10, invariant §4.19 / ADR-0027): with every AI setting at its
default, the system runs END TO END with ZERO external calls.

There is deliberately no model wired today — extraction is the deterministic
chain (structured e-invoice → embedded XML → PDF text → OCR → CSV/JSON parsers),
`ai_enrich` is a no-op seam, and any future AI must be opt-in, default-off,
advisory and DLP-gated (ADR-0027). This test is the executable form of that
promise: the network is blocked AT THE SOCKET LAYER and a representative
capture flow — upload → queue → worker parse → review → confirm into an
invoice — must complete anyway. If a future change wires a model into the
default path, this test fails before a customer document ever leaves the box.
"""

from __future__ import annotations

import io
import socket

import pytest

CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Globex Ltd,INV-AI-POLICY-1,2026-06-01,Widgets,10,5.00,50.00,21\n"
)


@pytest.fixture
def _no_network(monkeypatch):
    """Block outbound networking at the socket layer. The in-process ASGI test
    transport and the in-memory SQLite/aiosqlite database never open sockets, so
    ANY socket construction during the flow is an external call by definition."""

    def _blocked(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(
            "EXTERNAL NETWORK CALL ATTEMPTED with AI/settings at defaults — "
            "invariant §4.19 requires the default configuration to make zero "
            "external calls (see ADR-0027)."
        )

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.mark.asyncio
async def test_defaults_make_zero_external_calls(auth_client, db_session, _no_network):
    from app.services import jobs

    # Upload (202 + run id) — the parse is queued, not inline.
    up = await auth_client.post(
        "/api/v1/invoices/upload",
        files={"file": ("invoice.csv", io.BytesIO(CSV.encode()), "text/csv")},
    )
    assert up.status_code == 202, up.text
    run_id = up.json()["extraction_run_id"]

    # The worker parses it — deterministically, offline.
    for _ in range(30):
        if await jobs.run_once(db_session, "ai-policy-worker") is None:
            break

    res = await auth_client.get(f"/api/v1/invoices/upload/{run_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "parsed", body
    parsed = body["draft"]
    assert parsed["method"] == "csv", (
        f"expected the deterministic CSV parser, got {parsed['method']!r}"
    )
    draft = parsed["draft"]
    assert draft["invoice_number"] == "INV-AI-POLICY-1"

    # The capture sits in the human-review queue (advisory — a human confirms).
    queue = await auth_client.get("/api/v1/invoices/captures/review")
    assert queue.status_code == 200, queue.text
    assert run_id in {i["extraction_run_id"] for i in queue.json()["items"]}

    # Confirm: the reviewed draft becomes the invoice — still zero external calls.
    confirm = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": draft["vendor_name"],
            "invoice_number": draft["invoice_number"],
            "issue_date": draft["issue_date"],
            "currency": draft.get("currency") or "EUR",
            "extraction_run_id": run_id,
            "line_items": [
                {
                    "description": li["description"],
                    "category": li.get("category") or "general",
                    "quantity": str(li["quantity"]),
                    "unit_price": str(li["unit_price"]),
                    "tax_rate": str(li["tax_rate"]),
                }
                for li in draft["line_items"]
            ],
        },
    )
    assert confirm.status_code == 201, confirm.text
    assert confirm.json()["invoice_number"] == "INV-AI-POLICY-1"


@pytest.mark.asyncio
async def test_default_settings_carry_no_ai_backend(auth_client):
    """Belt to the socket braces: the org's validation settings default to the
    deterministic pipeline — AI validation is opt-in and OFF on a fresh org."""
    r = await auth_client.get("/api/v1/settings/validation")
    assert r.status_code == 200, r.text
    assert r.json()["ai_validation_enabled"] is False, (
        "a fresh org must not have AI validation on by default (§4.19)"
    )
