"""
INVOICING COMPOSER OVERHAUL — the presentation/interaction layer over the existing
(unchanged) legal/VAT/numbering engine. These tests cover the THREE additions:

  1. the BATCH line-save endpoint /invoicing/lines/save — one POST replaces ALL of a
     draft's lines and (optionally) issues; the SERVER recomputes the authoritative
     totals (the client preview is never trusted for a stored figure);
  2. the INLINE new-customer flow on /invoicing/create — one action creates the bill-to
     customer AND the draft, landing in the editor;
  3. the NO-JS fallback — the legacy per-line add/remove forms + the issue form still work.

Plus: every preserved feature still functions through the batch path (line + document
discount, reverse charge, simplified, currency), the new pages render real HTML (no
escaped block markup), and the LV/EN i18n of the new labels.

The fixtures repoint invoicing.DB at a temp file (the live invoicing.db is never touched).
"""
import os
import re
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import invoicing  # noqa: E402
import money      # noqa: E402


@pytest.fixture()
def inv(tmp_path, monkeypatch):
    """Repoint invoicing.DB at a temp file + reset the schema-ready cache."""
    monkeypatch.setattr(invoicing, "DB", str(tmp_path / "invoicing.db"), raising=True)
    monkeypatch.setattr(invoicing, "_SCHEMA_READY", set(), raising=True)
    return invoicing


def _set_issuer(inv, **over):
    # A LATVIAN issuer so a Latvian bill-to customer is a DOMESTIC supply (VAT charged, not
    # reverse-charged). The cross-border reverse-charge case has its own test below.
    base = dict(name="Acme Latvija SIA", address="Brivibas 100, Riga, Latvia",
                vat_number="LV40003000000", reg_no="40003000000", iban="LV80BANK0000000000000",
                bank="Swedbank", series="INV", number_format="{series}-{year}-{seq:06d}",
                payment_terms_days="14")
    base.update(over)
    inv.set_issuer(base)


def _csrf(client, path):
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def _domestic_customer(inv):
    c, _ = inv.add_customer("Latvija SIA", country="LV", vat_number="LV40000000000",
                            address="Brivibas 1, Riga")
    return c


# ============================================================ replace_lines (server math)
def test_replace_lines_replaces_set_and_server_recomputes_totals(inv):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    # seed one line, then REPLACE with a different two-line set
    inv.add_line(draft["id"], description="old", quantity=1, unit_price_net=9, vat_rate=0.21)
    out, err = inv.replace_lines(draft["id"], [
        {"description": "Service A", "quantity": 2, "unit_price_net": 100, "vat_rate": 0.21},
        {"description": "Service B", "quantity": 1, "unit_price_net": 50, "vat_rate": 0.12},
    ])
    assert not err, err
    lines = inv.get_lines(draft["id"])
    assert [l["description"] for l in lines] == ["Service A", "Service B"]
    # SERVER-computed figures (never trusting any client math):
    assert out["net_total"] == money.f2(250.0)
    assert out["vat_total"] == money.f2(200 * 0.21 + 50 * 0.12)
    assert out["gross_total"] == money.f2(out["net_total"] + out["vat_total"])


def test_replace_lines_drops_empty_rows(inv):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    out, err = inv.replace_lines(draft["id"], [
        {"description": "Real", "quantity": 1, "unit_price_net": 10, "vat_rate": 0.21},
        {"description": "", "quantity": 0, "unit_price_net": 0, "vat_rate": 0.21},
    ])
    assert not err
    assert len(inv.get_lines(draft["id"])) == 1


def test_replace_lines_refused_on_issued(inv):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    inv.add_line(draft["id"], description="x", quantity=1, unit_price_net=10, vat_rate=0.21)
    inv.issue(draft["id"], issued_by="t")
    out, err = inv.replace_lines(draft["id"], [
        {"description": "tamper", "quantity": 9, "unit_price_net": 9, "vat_rate": 0.21}])
    assert out is None and err            # immutable
    # the issued invoice's lines are untouched
    assert [l["description"] for l in inv.get_lines(draft["id"])] == ["x"]


def test_replace_lines_preserves_line_and_document_discount(inv):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    inv.set_document_discount(draft["id"], discount_kind="percent", discount_value=10)
    out, err = inv.replace_lines(draft["id"], [
        {"description": "A", "quantity": 1, "unit_price_net": 100, "vat_rate": 0.21,
         "discount_kind": "amount", "discount_value": 20},
    ])
    assert not err
    ln = inv.get_lines(draft["id"])[0]
    assert ln["discount_amount"] == money.f2(20.0)        # line discount kept
    assert ln["line_net"] == money.f2(80.0)               # 100 - 20
    # document discount (10% of 80 = 8) still applied to the header total
    assert out["net_total"] == money.f2(72.0)
    assert out["disc_amount"] == money.f2(8.0)


def test_replace_lines_reverse_charge_zeroes_vat(inv):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE111111111",
                            address="Hauptstr 2")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=True)
    out, err = inv.replace_lines(draft["id"], [
        {"description": "Service", "quantity": 2, "unit_price_net": 100, "vat_rate": 0.21}])
    assert not err
    assert out["vat_total"] == 0.0 and out["net_total"] == money.f2(200.0)
    assert inv.get_lines(draft["id"])[0]["vat_rate"] == 0.0


# ============================================================ web: batch save endpoint
def test_web_batch_save_replaces_lines(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    tok = _csrf(client, f"/invoicing/compose/{draft['id']}")
    import json
    payload = json.dumps([
        {"description": "Consulting", "quantity": 3, "unit_price_net": 100, "vat_rate": "21"},
        {"description": "Parts", "quantity": 1, "unit_price_net": 40, "vat_rate": "12"},
    ])
    r = client.post("/invoicing/lines/save",
                    data={"_csrf": tok, "invoice_id": draft["id"],
                          "action": "save", "lines": payload})
    assert r.status_code == 302
    lines = inv.get_lines(draft["id"])
    assert [l["description"] for l in lines] == ["Consulting", "Parts"]
    out = inv.get_invoice(draft["id"])
    # SERVER recomputed (rate '21'/'12' parsed to fractions server-side):
    assert out["net_total"] == money.f2(340.0)
    assert out["vat_total"] == money.f2(300 * 0.21 + 40 * 0.12)


def test_web_batch_save_then_issue_same_invoice(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    tok = _csrf(client, f"/invoicing/compose/{draft['id']}")
    import json
    payload = json.dumps([
        {"description": "Service", "quantity": 2, "unit_price_net": 50, "vat_rate": "21"}])
    r = client.post("/invoicing/lines/save",
                    data={"_csrf": tok, "invoice_id": draft["id"],
                          "action": "issue", "lines": payload})
    assert r.status_code == 302
    issued = inv.get_invoice(draft["id"])
    assert issued["status"] == "issued" and issued["number"]   # number assigned by server
    assert issued["net_total"] == money.f2(100.0)
    assert issued["vat_total"] == money.f2(21.0)


def test_web_batch_save_server_ignores_client_totals(inv, client):
    """The endpoint takes ONLY the line inputs — it cannot be made to store a client-asserted
    total. We send a wildly-wrong fake total field; the server still computes the truth."""
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    tok = _csrf(client, f"/invoicing/compose/{draft['id']}")
    import json
    payload = json.dumps([
        {"description": "X", "quantity": 1, "unit_price_net": 10, "vat_rate": "21"}])
    client.post("/invoicing/lines/save",
                data={"_csrf": tok, "invoice_id": draft["id"], "action": "save",
                      "lines": payload, "net_total": "999999", "gross_total": "999999"})
    out = inv.get_invoice(draft["id"])
    assert out["net_total"] == money.f2(10.0)
    assert out["gross_total"] == money.f2(12.1)


def test_web_batch_save_requires_csrf(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    r = client.post("/invoicing/lines/save",
                    data={"invoice_id": draft["id"], "action": "save", "lines": "[]"})
    assert r.status_code == 400        # CSRF rejected


# ============================================================ web: inline new customer
def test_web_inline_new_customer_creates_customer_and_draft(inv, client):
    _set_issuer(inv)
    assert inv.list_customers() == []
    tok = _csrf(client, "/invoicing/compose")
    r = client.post("/invoicing/create",
                    data={"_csrf": tok, "doc_type": "invoice", "customer_mode": "new",
                          "new_name": "Inline Co SIA", "new_vat_number": "LV12345678901",
                          "new_country": "LV", "new_address": "Riga 1",
                          "new_email": "ap@inline.lv", "currency": "EUR"})
    assert r.status_code == 302
    custs = inv.list_customers()
    assert [c["name"] for c in custs] == ["Inline Co SIA"]
    # a draft was created against the new customer, and we land in its editor
    loc = r.headers["Location"]
    iid = int(loc.rstrip("/").split("/")[-1])
    draft = inv.get_invoice(iid)
    assert draft and draft["customer_id"] == custs[0]["id"]


def test_web_inline_new_customer_blank_name_errors(inv, client):
    _set_issuer(inv)
    tok = _csrf(client, "/invoicing/compose")
    r = client.post("/invoicing/create",
                    data={"_csrf": tok, "doc_type": "invoice", "customer_mode": "new",
                          "new_name": "  ", "currency": "EUR"})
    # not a redirect — an error page is rendered, and no customer/draft was created
    assert r.status_code == 200
    assert inv.list_customers() == []


def test_web_existing_customer_still_works(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    tok = _csrf(client, "/invoicing/compose")
    r = client.post("/invoicing/create",
                    data={"_csrf": tok, "doc_type": "invoice", "customer_mode": "existing",
                          "customer_id": c["id"], "currency": "EUR"})
    assert r.status_code == 302
    iid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    assert inv.get_invoice(iid)["customer_id"] == c["id"]


# ============================================================ no-JS fallback preserved
def test_nojs_per_line_add_and_remove_still_work(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    tok = _csrf(client, f"/invoicing/compose/{draft['id']}")
    # add via the legacy per-line form (the <noscript> path)
    r = client.post("/invoicing/line/add", data={
        "_csrf": tok, "invoice_id": draft["id"], "description": "NoJS line",
        "quantity": "1", "unit_price_net": "10", "vat_rate": "21"})
    assert r.status_code == 302
    lines = inv.get_lines(draft["id"])
    assert len(lines) == 1
    # remove via the legacy per-line form
    r2 = client.post("/invoicing/line/remove", data={
        "_csrf": tok, "invoice_id": draft["id"], "line_id": lines[0]["id"]})
    assert r2.status_code == 302
    assert inv.get_lines(draft["id"]) == []


def test_compose_page_carries_nojs_and_editor_markup(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    body = client.get(f"/invoicing/compose/{draft['id']}").get_data(as_text=True)
    # the JS editor mount point is present
    assert "data-ivc-editor" in body
    # the no-JS legacy forms are present inside <noscript>
    assert "<noscript>" in body
    assert 'action="/invoicing/line/add"' in body


# ============================================================ real HTML (no escaped markup)
_ESCAPED = ("&lt;div", "&lt;p>", "&lt;p ", "&lt;a ", "&lt;span", "&lt;form",
            "&lt;h2", "&lt;label", "&lt;button", "&lt;details", "&lt;noscript")


def test_compose_pages_render_real_html(inv, client):
    _set_issuer(inv)
    c = _domestic_customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"])
    inv.add_line(draft["id"], description="L", quantity=1, unit_price_net=10, vat_rate=0.21)
    for p in ("/invoicing/compose", f"/invoicing/compose/{draft['id']}"):
        body = client.get(p).get_data(as_text=True)
        leaked = [m for m in _ESCAPED if m in body]
        assert not leaked, f"{p}: escaped block markup {leaked}"


# ============================================================ i18n (EN default + LV)
def test_new_labels_translate_lv():
    import i18n
    for s in ("Line items", "Save draft", "More options", "+ New customer",
              "New customer", "Custom…", "FX rate"):
        assert i18n.t(s, lang="en") == s                 # EN default = source
        assert i18n.has(s, "lv"), f"missing LV: {s}"
        assert i18n.t(s, lang="lv") != s                 # LV translated
