"""
REGRESSION — RAW-HTML-AS-TEXT in the invoicing module pages (app.py).

ROOT CAUSE this guards against: ``markupsafe.Markup`` is a ``str`` subclass whose
``__radd__``/``__add__`` ESCAPE the other operand. In an HTML-string concatenation
chain that builds a page, a bare ``... + esc(x)`` (esc = markupsafe.escape, returns a
Markup) makes Python call ``Markup.__radd__``, which escapes the WHOLE accumulated HTML
to its left (``<div>`` -> ``&lt;div&gt;``) and contaminates everything appended after.
The user then SEES literal ``<div class="card">...`` markup as text on the page.

The fix wraps the Markup operand in ``str(...)`` (or uses an f-string) at every such
``+ esc(...)`` site so the surrounding HTML is never escaped — while the VALUE is still
correctly escaped as plain text.

These tests log in as admin, repoint the invoicing module at a throwaway DB, seed
minimal data, GET every invoicing page, and assert the rendered body contains NO
escaped BLOCK markup (``&lt;div``, ``&lt;p``, ``&lt;a``, ``&lt;span``, ``&lt;form``) —
which would mean an HTML fragment leaked through the escaper. They FAIL before the fix
and PASS after.
"""
import os
import re
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import invoicing  # noqa: E402

# Escaped block-level markup that must NEVER appear in a rendered body — each indicates
# an HTML fragment that was escaped to text instead of being emitted as real markup.
_ESCAPED_MARKERS = ("&lt;div", "&lt;p>", "&lt;p ", "&lt;a ", "&lt;a>",
                    "&lt;span", "&lt;form", "&lt;h2", "&lt;label", "&lt;button")


def _assert_clean_html(body, where):
    leaked = [m for m in _ESCAPED_MARKERS if m in body]
    assert not leaked, (
        f"{where}: rendered body contains ESCAPED block markup {leaked} — an HTML "
        f"fragment was escaped to visible text (the Markup __radd__ concatenation bug).")


@pytest.fixture()
def seeded(tmp_path, monkeypatch, client):
    """A logged-in admin client whose invoicing module is repointed at a fresh temp DB
    with a complete issuer, a customer, an issued invoice, a draft, and a recurring
    template — enough that every page has real content to render."""
    monkeypatch.setattr(invoicing, "DB", str(tmp_path / "invoicing.db"), raising=True)
    monkeypatch.setattr(invoicing, "_SCHEMA_READY", set(), raising=True)

    invoicing.set_issuer(dict(
        name="Acme Logistics OU", address="Tartu mnt 1, Tallinn, Estonia",
        vat_number="EE100000000", reg_no="12345678", iban="EE001234567890",
        bank="LHV", series="INV", number_format="{series}-{year}-{seq:06d}",
        payment_terms_days="14"))

    cust, err = invoicing.add_customer(
        name="Beta Transport OU", address="Parnu mnt 5, Tallinn, Estonia",
        vat_number="EE200000000", country="EE", email="ap@beta.ee")
    assert not err, err

    # An ISSUED invoice (gets a number, drives revenue/VAT/statement/aging reports).
    issued, derr = invoicing.create_draft(customer_id=cust["id"])
    assert not derr, derr
    _, lerr = invoicing.add_line(issued["id"], description="Diesel card November",
                                 quantity=100, unit="L", unit_price_net=1.50, vat_rate=21)
    assert not lerr, lerr
    inv2, ierr = invoicing.issue(issued["id"], issued_by="tester")
    assert not ierr, ierr

    # A DRAFT (exercises /invoicing/compose/<id> with a live draft).
    draft, d2err = invoicing.create_draft(customer_id=cust["id"])
    assert not d2err, d2err
    _, l2err = invoicing.add_line(draft["id"], description="Toll December",
                                  quantity=1, unit="ea", unit_price_net=42.00, vat_rate=21)
    assert not l2err, l2err

    # A recurring TEMPLATE (exercises /invoicing/recurring?edit=<id>).
    tpl, terr = invoicing.create_recurring(
        customer_id=cust["id"], name="Monthly fuel-card billing",
        lines=[dict(description="Monthly card fee", quantity=1, unit="ea",
                    unit_price_net=10.0, vat_rate=21)])
    assert not terr, terr

    return {"client": client, "customer": cust, "issued": inv2,
            "draft": draft, "template": tpl}


def _get(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    return r.get_data(as_text=True)


def test_invoicing_pages_render_real_html_not_escaped(seeded):
    """Every invoicing-module page renders real HTML (no escaped block markup)."""
    c = seeded["client"]
    cid = seeded["customer"]["id"]
    tid = seeded["template"]["id"]
    did = seeded["draft"]["id"]

    paths = [
        "/invoicing",
        "/invoicing/recurring",
        f"/invoicing/recurring?edit={tid}",
        "/invoicing/customers",
        "/invoicing/issuer",
        "/invoicing/compose",
        f"/invoicing/compose/{did}",
        "/invoicing/reports",
        "/invoicing/reports/vat",
        "/invoicing/reports/revenue",
        f"/invoicing/reports/statement?customer_id={cid}",
        "/invoicing/reports/aging",
    ]
    for p in paths:
        _assert_clean_html(_get(c, p), p)


def test_recurring_page_renders_real_cards(seeded):
    """The page that originally showed the literal ``<div class="card"><h2>Recurring
    invoices``... renders those as REAL <div class="card"> elements now."""
    c = seeded["client"]
    body = _get(c, "/invoicing/recurring")
    # The help + scheduler cards must be real markup.
    real_divs = len(re.findall(r"<div\b", body))
    assert real_divs > 0, "no real <div elements in the recurring page"
    assert "&lt;div" not in body, "recurring page still escapes <div> to text"
    # The card headings render as real text inside real <h2>, not escaped.
    assert "Recurring invoices" in body
    assert "Scheduler" in body
    assert "&lt;h2" not in body and "&lt;p" not in body


def test_aging_export_buttons_are_real_links(seeded):
    """The aging report's Excel/PDF export buttons were built with a bare ``+ esc(...)``
    — they must now be real <a class="btn"> anchors, not escaped text."""
    c = seeded["client"]
    body = _get(c, "/invoicing/reports/aging")
    assert '<a class="btn" href="/invoicing/reports/aging.xlsx">' in body
    assert '<a class="btn" href="/invoicing/reports/aging.pdf">' in body
    assert "&lt;a " not in body
