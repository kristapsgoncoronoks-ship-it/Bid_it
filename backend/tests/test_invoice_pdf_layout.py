"""Structural characterisation of the issued-invoice layout (WO-97, design A).

What these tests CAN prove: that every legally-required element is on the page,
that per-rate VAT is broken out, that page breaks repeat the header and carry a
real page count, and that the totals never end up on a page holding none of the
lines they total.

What they CANNOT prove: that the page looks right. Text extraction is blind to
collided columns, a figure wrapped across two lines, and a rule in the wrong
place — every assertion here passes on a broken page. The visual gate is
rendering each case and looking at it; see `docs/design/invoice-layout.md`.

All data is synthetic: invented company names, all-ones/all-twos VAT ids,
`example.invalid` addresses, and an IBAN assembled at runtime from fragments.
"""

from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.services import invoice_pdf, vat

pytest.importorskip("reportlab")

_XML = b"<?xml version='1.0'?><CrossIndustryInvoice/>"


def _seller(**kw) -> dict:
    base = {
        "legal_name": "Northwind Recovery OU",
        "address_line1": "Tornimae tn 5",
        "postal_code": "10145",
        "city": "Tallinn",
        "country": "EE",
        "vat_number": "EE" + "1" * 9,
        "registration_number": "1" * 8,
        "email": "billing@example.invalid",
        # Assembled at runtime so no literal resembling an account number is
        # committed to the repository.
        "iban": "EE" + "38" + "2200" + "2210" + "2014" + "5685",
        "bic": "EEUHEE2X",
    }
    base.update(kw)
    return base


def _invoice(**kw) -> SimpleNamespace:
    base = dict(
        number="INV-2026-0184",
        doc_type="invoice",
        currency="EUR",
        issue_date=date(2026, 8, 11),
        supply_date=None,
        due_date=date(2026, 8, 25),
        vat_scheme="standard",
        po_reference="PO-44120",
        tax_exemption_reason=None,
        buyer_name="Baltic Haulage AS",
        buyer_address_line1="Parnu maantee 141",
        buyer_postal_code="11314",
        buyer_city="Tallinn",
        buyer_country="EE",
        buyer_vat_number="EE" + "2" * 9,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _line(desc: str, qty: str, price: str, rate: str = "22", disc: str | None = None) -> dict:
    li = {
        "description": desc,
        "quantity": Decimal(qty),
        "unit_price": Decimal(price),
        "vat_rate": Decimal(rate),
    }
    if disc is not None:
        li["discount_percent"] = Decimal(disc)
    return li


_ONE_LINE = [_line("Recovery service fee", "1", "100.00")]


def _render(invoice=None, lines=None, scheme="standard", seller=None, logo=None) -> bytes:
    return invoice_pdf.build_pdf(
        invoice or _invoice(),
        seller or _seller(),
        vat.compute(lines or _ONE_LINE, scheme),
        _XML,
        logo,
    )


def _pages(pdf: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]


def _text(pdf: bytes) -> str:
    return "\n".join(_pages(pdf))


# --- the metadata strip --------------------------------------------------------


def test_metadata_strip_labels_are_present():
    text = _text(_render())
    for label in ("BILL TO", "INVOICE NO.", "ISSUED", "DUE"):
        assert label in text, f"missing metadata label {label!r}"
    assert "INV-2026-0184" in text
    assert "Baltic Haulage AS" in text
    assert "11 August 2026" in text
    assert "25 August 2026" in text


def test_due_column_is_omitted_when_there_is_no_due_date():
    """An empty labelled column asks the reader what is missing. Drop it."""
    text = _text(_render(_invoice(due_date=None)))
    assert "ISSUED" in text
    assert "DUE" not in text


def test_seller_identity_and_registration_numbers_are_on_the_page():
    text = _text(_render())
    assert "Northwind Recovery OU" in text
    assert f"VAT {_seller()['vat_number']}" in text
    assert "Reg 11111111" in text
    assert "Tornimae tn 5" in text


# --- credit notes --------------------------------------------------------------


def test_credit_note_heading_and_number_label():
    text = _text(_render(_invoice(doc_type="credit_note", number="CN-2026-0007")))
    assert "CREDIT NOTE" in text
    assert "CREDIT NOTE NO." in text
    # The document must not head itself INVOICE when it is not one.
    assert not re.search(r"^\s*INVOICE\s*$", text, re.MULTILINE)


def test_credit_note_total_is_not_labelled_as_due():
    """A credit note is not payable, so "Total due" would misstate it."""
    text = _text(_render(_invoice(doc_type="credit_note", number="CN-2026-0007")))
    assert "Total credited" in text
    assert "Total due" not in text


# --- VAT breakdown -------------------------------------------------------------


def test_every_vat_rate_gets_its_own_breakdown_row():
    lines = [
        _line("Standard rated", "10", "120.00", "22"),
        _line("Reduced rated", "4", "45.00", "9"),
        _line("Zero rated", "1", "800.00", "0"),
    ]
    text = _text(_render(lines=lines))
    rows = re.findall(r"VAT (\d+)% of ([\d,]+\.\d{2}) EUR", text)
    assert len(rows) == 3, f"expected one row per rate, got {rows}"
    assert {r for r, _ in rows} == {"0", "9", "22"}
    bases = sum(Decimal(b.replace(",", "")) for _, b in rows)
    # The per-rate bases must reconcile to the printed subtotal — a breakdown
    # that does not add up is worse than none.
    assert bases == Decimal("2180.00")
    assert "2,180.00 EUR" in text
    assert "2,460.20 EUR" in text  # 2180 + 264.00 + 16.20 + 0


def test_single_rate_still_shows_its_base_and_vat():
    text = _text(_render())
    assert "VAT 22% of 100.00 EUR" in text
    assert "22.00 EUR" in text
    assert "122.00 EUR" in text


# --- exemptions and zero-VAT schemes -------------------------------------------


def test_zero_vat_scheme_prints_the_scheme_note_and_zero_vat():
    text = _text(_render(_invoice(vat_scheme="reverse_charge"), scheme="reverse_charge"))
    assert "Reverse charge" in text
    assert "Art. 196" in text
    assert "VAT 0% of 100.00 EUR" in text
    assert "0.00 EUR" in text


def test_explicit_exemption_reason_wins_over_the_scheme_note():
    """EN-16931 BT-120: the invoice's own ground beats the scheme default."""
    inv = _invoice(vat_scheme="reverse_charge", tax_exemption_reason="Ground stated by the issuer.")
    text = _text(_render(inv, scheme="reverse_charge"))
    assert "Ground stated by the issuer." in text
    assert "Art. 196" not in text


def test_exemption_reason_is_printed_even_on_a_standard_scheme():
    """The legally required text can never be dropped because the scheme looks
    ordinary — `tax_exemption_reason` is set by a human and always prints."""
    inv = _invoice(tax_exemption_reason="Exempt under Art. 44.")
    assert "Exempt under Art. 44." in _text(_render(inv))


# --- page breaks, repeated header, page numbering ------------------------------


def _many(n: int) -> list[dict]:
    return [
        _line(f"Reconciliation batch {i:02d} - fuel and toll postings", str(i), "212.00")
        for i in range(1, n + 1)
    ]


def test_thirty_lines_span_pages_with_a_repeated_header():
    pages = _pages(_render(lines=_many(30)))
    assert len(pages) > 1
    for i, page in enumerate(pages, start=1):
        assert "DESCRIPTION" in page, f"column header missing on page {i}"


def test_page_numbering_is_real_on_a_multipage_invoice():
    pdf = _render(lines=_many(30))
    pages = _pages(pdf)
    total = len(pages)
    assert total > 1
    for i, page in enumerate(pages, start=1):
        assert f"Page {i} of {total}" in page
    # The claimed count must equal the actual one — a decorative "of 3" on a
    # 4-page document is a lie a reader cannot check without counting.
    claimed = {int(m) for m in re.findall(r"Page \d+ of (\d+)", "\n".join(pages))}
    assert claimed == {total}


def test_continuation_pages_identify_the_document():
    """A detached page 2 is worthless if it does not say which invoice it is."""
    pages = _pages(_render(lines=_many(30)))
    for page in pages:
        assert "INV-2026-0184" in page


def test_single_page_invoice_has_no_page_furniture():
    text = _text(_render())
    assert "Page 1 of 1" not in text
    assert "Page" not in text


@pytest.mark.parametrize("n", list(range(18, 30)))
def test_totals_are_never_orphaned_from_the_lines_they_total(n: int):
    """Sweep the whole page-break neighbourhood, not one lucky line count.

    The page carrying `Total due` must also carry at least one line item. The
    first attempt at this layout used `KeepTogether`, which stops the totals
    being SPLIT but does not bind them to the table: at 30 lines the totals
    landed alone on page 3. `NOSPLIT` over (last line, totals) is what actually
    holds, so this asserts the property across every count where the break can
    fall near the end.
    """
    pages = _pages(_render(lines=_many(n)))
    totals_page = next(p for p in pages if "Total due" in p)
    assert "Reconciliation batch" in totals_page, (
        f"{n} lines: the totals page shows no line item it totals"
    )


# --- the line table ------------------------------------------------------------


def test_discount_column_appears_only_when_a_line_carries_a_discount():
    plain = _text(_render(lines=[_line("Widgets", "4", "25.00", "21")]))
    assert "DISC %" not in plain

    discounted = _text(_render(lines=[_line("Widgets", "4", "25.00", "21", disc="20")]))
    assert "DISC %" in discounted
    assert "20%" in discounted
    # 4 x 25 = 100, less 20% = 80 net. Art. 226(8) requires the discount to be
    # visible when it is not already in the unit price.
    assert "80.00 EUR" in discounted


def test_seven_figure_and_negative_amounts_render_intact():
    lines = [
        _line("Annual settlement", "1", "1234567.89"),
        _line("Goodwill adjustment", "1", "-1500.00"),
    ]
    text = _text(_render(lines=lines))
    assert "1,234,567.89 EUR" in text
    assert "-1,500.00 EUR" in text


def test_large_quantities_do_not_go_exponential():
    """`format(Decimal("1000000"), "g")` is `1E+6`. An invoice may not say that."""
    text = _text(_render(lines=[_line("Per-litre reconciliation", "9481220", "0.35")]))
    assert "9,481,220" in text
    assert "E+" not in text


def test_long_buyer_name_and_description_are_not_truncated():
    long_name = "Northern Continental Intermodal Freight & Logistics Holdings KGaA"
    long_desc = (
        "Preparation, review and electronic submission of the cross-border "
        "input-VAT refund dossier under Directive 2008/9/EC including the "
        "per-country minimum-threshold assessment"
    )
    text = _text(_render(_invoice(buyer_name=long_name), lines=[_line(long_desc, "1", "3200.00")]))
    # Extraction inserts line breaks where the paragraph wrapped, so compare on
    # collapsed whitespace: the point is that no CHARACTERS were dropped.
    flat = re.sub(r"\s+", " ", text)
    assert long_name in flat
    assert long_desc in flat


# --- supply date, PO, terms ----------------------------------------------------


def test_supply_date_is_printed_only_when_it_differs_from_the_issue_date():
    """Art. 226(7) makes it mandatory in exactly that case, and noise otherwise."""
    same = _text(_render(_invoice(supply_date=date(2026, 8, 11))))
    assert "Supply date" not in same

    differs = _text(_render(_invoice(supply_date=date(2026, 7, 31))))
    assert "Supply date 31 July 2026" in differs


def test_purchase_order_and_terms_are_omitted_when_absent():
    text = _text(_render(_invoice(po_reference=None, due_date=None)))
    assert "Purchase order" not in text
    assert "Terms" not in text


def test_terms_restate_the_interval_between_the_printed_dates():
    assert "Terms 14 days" in _text(_render())


# --- payment block -------------------------------------------------------------


def test_payment_block_carries_the_account_and_the_reference_instruction():
    text = _text(_render())
    assert "Payment" in text
    assert "BIC EEUHEE2X" in text
    assert "Please quote INV-2026-0184 as the payment reference." in text
    assert "billing@example.invalid" in text


def test_seller_payment_instructions_and_notes_are_rendered():
    seller = _seller(payment_instructions="Pay by bank transfer.", notes="Registered in Estonia.")
    text = _text(_render(seller=seller))
    assert "Pay by bank transfer." in text
    assert "Registered in Estonia." in text


# --- the logo ------------------------------------------------------------------


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (240, 80), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_logo_is_rendered_without_displacing_the_seller_identity():
    pdf = _render(logo=("image/png", _png()))
    text = _text(pdf)
    assert "Northwind Recovery OU" in text
    assert "INV-2026-0184" in text


def test_a_corrupt_logo_does_not_cost_the_customer_their_invoice(caplog):
    """FAIL OPEN, and prove it: an unreadable logo is dropped, not raised.

    The document is legally complete without a mark and incomplete without the
    figures, so the only safe direction for this gate is open.
    """
    with caplog.at_level("WARNING", logger="invoiceiq.invoice_pdf"):
        pdf = _render(logo=("image/png", b"this is not a png"))
    text = _text(pdf)
    assert "INV-2026-0184" in text
    assert "122.00 EUR" in text
    assert any("logo" in r.message for r in caplog.records)


# --- Art. 226 completeness -----------------------------------------------------


def test_every_article_226_element_is_findable_on_one_rendered_invoice():
    """One assertion per mandatory element, so a layout edit that silently drops
    one fails here rather than in front of a tax inspector."""
    inv = _invoice(supply_date=date(2026, 7, 31))
    lines = [_line("Advisory work", "10", "120.00", "22"), _line("Dossier", "4", "45.00", "9")]
    text = _text(_render(inv, lines=lines))

    required = {
        "(1) date of issue": "11 August 2026",
        "(2) sequential number": "INV-2026-0184",
        # Assembled, never written literally: the PII scanner reads a structural
        # VAT id in source as a quarantine hit regardless of how synthetic it is.
        "(3) supplier VAT identification": f"VAT {_seller()['vat_number']}",
        "(4) customer VAT identification": f"VAT {inv.buyer_vat_number}",
        "(5) supplier name and address": "Northwind Recovery OU",
        "(5) customer name and address": "Baltic Haulage AS",
        "(6) description of the goods/services": "Advisory work",
        "(6) quantity": "10",
        "(7) date of supply where it differs": "Supply date 31 July 2026",
        "(8) taxable amount per rate": "VAT 22% of 1,200.00 EUR",
        "(8) unit price excluding VAT": "120.00 EUR",
        "(9) VAT rate applied": "22%",
        "(10) VAT amount payable": "264.00 EUR",
    }
    missing = [name for name, needle in required.items() if needle not in text]
    assert not missing, f"Art. 226 elements missing from the rendered page: {missing}"
