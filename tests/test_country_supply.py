"""Per-country VAT & entity-of-supply summary on the review screen (read-only roll-up)."""
import app


def _draft():
    return {"backend": "e-invoice", "confidence": "high", "supplier": "W.A.G. a.s.",
            "files": [{"name": "x.pdf"}],
            "lines": [
                {"invoice_no": "INV1", "country": "Germany", "net": 593.55, "vat": 112.77,
                 "supplier_name": "W.A.G. Deutschland GmbH", "supplier_vat": "DE811"},
                {"invoice_no": "INV1", "country": "Poland", "net": 488.30, "vat": 112.31,
                 "supplier_name": "W.A.G. Polska", "supplier_vat": "PL527"},
                {"invoice_no": "INV1", "country": "Germany", "net": 405.00, "vat": 85.05,
                 "supplier_name": "W.A.G. Deutschland GmbH", "supplier_vat": "DE811"},
            ]}


def test_groups_and_sums_by_country():
    html = app._country_supply_summary_html(_draft())
    assert html
    assert "998.55" in html and "197.82" in html   # Germany net/vat combined
    assert "Poland" in html and "488.30" in html
    assert "1,486.85" in html                       # all-countries net total


def test_shows_entity_of_supply_per_country():
    html = app._country_supply_summary_html(_draft())
    assert "W.A.G. Deutschland GmbH" in html and "DE811" in html
    assert "W.A.G. Polska" in html and "PL527" in html


def test_hidden_when_no_country():
    d = {"lines": [{"invoice_no": "X", "net": 10, "vat": 2}]}   # no country
    assert app._country_supply_summary_html(d) == ""
    assert app._country_supply_summary_html({"lines": []}) == ""


def test_falls_back_to_header_supplier_label():
    d = {"lines": [{"country": "Czechia", "net": 100, "vat": 21}]}  # no per-line entity
    html = app._country_supply_summary_html(d)
    assert "Czechia" in html and "header supplier" in html
