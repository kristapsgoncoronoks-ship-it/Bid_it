"""L2 summaries preserve line rounding and distinguish missing evidence."""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice
from app.schemas.invoice import LineItemIn
from app.services import received_lines
from tests.test_n1_line_amounts import draft


def summary(lines, subtotal, tax, total):
    return received_lines.vat_breakdown(
        SimpleNamespace(
            line_items=lines,
            subtotal=Decimal(subtotal),
            tax_amount=Decimal(tax),
            total=Decimal(total),
        )
    )


def lines(*pairs):
    return received_lines.build(
        [
            LineItemIn(description="Synthetic service", amount=Decimal(net), tax_rate=Decimal(rate))
            for net, rate in pairs
        ]
    )[0]


def test_rates_sorted_and_repeated_rates_grouped_without_source_substitution():
    items = lines(("100", "21"), ("50", "0"), ("25", "9"), ("10", "21"))
    items[0].captured_tax_amount = Decimal("999")
    result = summary(items, "185", "25.35", "210.35")
    assert result["complete"] and result["matches_header"]
    assert [
        (g["tax_rate"], g["net"], g["tax"], g["gross"], g["line_count"]) for g in result["rates"]
    ] == [
        (Decimal("0"), Decimal("50"), Decimal("0"), Decimal("50"), 1),
        (Decimal("9"), Decimal("25"), Decimal("2.25"), Decimal("27.25"), 1),
        (Decimal("21"), Decimal("110"), Decimal("23.10"), Decimal("133.10"), 2),
    ]


def test_sum_saved_rounding_instead_of_tax_on_grouped_base():
    result = summary(lines(("0.03", "21"), ("0.03", "21")), "0.06", "0.02", "0.08")
    assert result["rates"][0]["tax"] == Decimal("0.02")
    assert result["matches_header"] is True  # grouped-base rounding would give 0.01


@pytest.mark.parametrize("field", ["tax_amount", "gross_amount"])
def test_missing_snapshot_does_not_become_zero_or_recompute(field):
    items = lines(("100", "21"), ("50", "21"), ("10", "9"))
    setattr(items[0], field, None)
    result = summary(items, "160", "32.4", "192.4")
    assert result["complete"] is False and result["matches_header"] is None
    assert result["rates"][1]["tax" if field == "tax_amount" else "gross"] is None
    assert result["rates"][0]["tax"] == Decimal("0.90")


def test_empty_lines_are_incomplete_even_for_zero_header():
    assert summary([], "0", "0", "0") == {"rates": [], "complete": False, "matches_header": None}


@pytest.mark.parametrize(
    "totals", [("101", "21", "121"), ("100", "22", "121"), ("100", "21", "122")]
)
def test_compare_each_header_component(totals):
    assert summary(lines(("100", "21")), *totals)["matches_header"] is False


async def test_detail_review_and_guarded_edit_show_same_saved_breakdown(auth_client, db_session):
    body = draft()
    body["currency"] = "USD"
    body["fx_rate"] = "1.10"
    body["line_items"] += [{"description": "Zero rate service", "amount": "50", "tax_rate": "0"}]
    response = await auth_client.post("/api/v1/invoices", json=body)
    assert response.status_code == 201
    saved = response.json()
    url = f"/api/v1/invoices/{saved['id']}"
    breakdown = saved["vat_breakdown"]
    assert breakdown["matches_header"] is True
    assert [r["tax_rate"] for r in breakdown["rates"]] == ["0.00", "21.00"]
    assert Decimal(breakdown["rates"][1]["tax"]) == Decimal("21")
    review = (await auth_client.get(url + "/review")).json()
    assert review["vat_breakdown"] == breakdown
    updated = await auth_client.put(
        url + "/lines",
        json={
            "version": review["version"],
            "lines": [{**line, "tax_rate": "9"} for line in review["lines"]],
        },
    )
    assert updated.status_code == 200
    current = updated.json()
    assert len(current["vat_breakdown"]["rates"]) == 1
    assert Decimal(current["vat_breakdown"]["rates"][0]["tax"]) == Decimal("13.50")
    assert (await auth_client.get(url)).json()["vat_breakdown"] == current["vat_breakdown"]
    stale = await auth_client.put(
        url + "/lines", json={"version": review["version"], "lines": review["lines"]}
    )
    assert stale.status_code == 409
    assert (await auth_client.get(url)).json()["vat_breakdown"] == current["vat_breakdown"]
    invoice = await db_session.scalar(select(Invoice).where(Invoice.id == saved["id"]))
    await db_session.refresh(invoice, attribute_names=["line_items"])
    invoice.line_items[0].tax_amount = None
    await db_session.commit()
    missing = (await auth_client.get(url)).json()["vat_breakdown"]
    assert missing["complete"] is False and missing["matches_header"] is None
    assert missing["rates"][0]["tax"] is None
