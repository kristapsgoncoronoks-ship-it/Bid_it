"""
INVOICING (invoicing.py) — Phase 1 of the NEW sales-invoicing module: a client issues
legally-compliant sales invoices to ITS OWN customers. These tests repoint invoicing.DB at
a temp file (so the live invoicing.db is never touched) and exercise:

  - VAT math: per-line + per-rate breakdown + totals, ROUND_HALF_UP, a multi-rate invoice;
  - reverse charge: 0% VAT + the mandatory note for a cross-border EU B2B customer;
  - gap-free numbering: sequential, no gaps, and a concurrency/locking test (two threads
    issuing at once never collide or skip a number);
  - issue immutability: editing an ISSUED invoice (add/remove line, fields) is refused;
  - the PDF / text carries EVERY Art. 226 mandatory field; a DRAFT shows the DRAFT label;
  - customer CRUD; the landing page renders + lists; the web POST issue flow;
  - tenant scoping (a tenant can't see another tenant's customers/invoices).
"""
import concurrent.futures
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
    base = dict(name="Acme Logistics OU", address="Tartu mnt 1, Tallinn, Estonia",
                vat_number="EE100000000", reg_no="12345678", iban="EE001234567890",
                bank="LHV", series="INV", number_format="{series}-{year}-{seq:06d}",
                payment_terms_days="14")
    base.update(over)
    inv.set_issuer(base)


# ====================================================================== VAT math
def test_compute_line_half_up_rounding(inv):
    # 1 * 10.005 * (rate handled separately): use a known half-up trap on net
    net, vat, rate = inv.compute_line(quantity=3, unit_price_net=2.675, vat_rate=0.21)
    # 3 * 2.675 = 8.025 -> half-up -> 8.03 (banker's would give 8.02)
    assert net == 8.03, net
    assert vat == money.f2(8.03 * 0.21), vat
    assert rate == 0.21


def test_multi_rate_totals_and_breakdown(inv):
    tot = inv.compute_totals([
        {"quantity": 2, "unit_price_net": 10, "vat_rate": 0.21},   # net 20, vat 4.20
        {"quantity": 1, "unit_price_net": 100, "vat_rate": 0.09},  # net 100, vat 9.00
        {"quantity": 5, "unit_price_net": 4, "vat_rate": 0.21},    # net 20, vat 4.20
    ])
    assert tot["net_total"] == 140.0, tot
    assert tot["vat_total"] == money.f2(4.20 + 9.00 + 4.20), tot
    assert tot["gross_total"] == money.f2(140.0 + 17.40), tot
    by = {round(b["rate"], 4): b for b in tot["by_rate"]}
    # two rate buckets, the 21% bucket aggregates both 21% lines
    assert set(by) == {0.21, 0.09}
    assert by[0.21]["net"] == 40.0 and by[0.21]["vat"] == 8.40
    assert by[0.09]["net"] == 100.0 and by[0.09]["vat"] == 9.0


def test_totals_persist_on_invoice_header(inv):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE111111111",
                            address="Hauptstr 2, Berlin")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="Transport", quantity=2,
                 unit_price_net=10, vat_rate=0.21)
    inv.add_line(draft["id"], description="Surcharge", quantity=1,
                 unit_price_net=100, vat_rate=0.09)
    h = inv.get_invoice(draft["id"])
    assert h["net_total"] == 120.0
    assert h["vat_total"] == money.f2(4.20 + 9.00)
    assert h["gross_total"] == money.f2(133.20)


# ================================================================ reverse charge
def test_reverse_charge_zeroes_vat_and_emits_note(inv):
    _set_issuer(inv)  # EE issuer
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE111111111",
                            address="Hauptstr 2, Berlin")
    # auto-derive: cross-border (EE->DE) B2B (both have VAT no) -> reverse charge default
    draft, _ = inv.create_draft(customer_id=c["id"])
    assert draft["reverse_charge"] is True
    inv.add_line(draft["id"], description="Freight", quantity=1,
                 unit_price_net=1000, vat_rate=0.21)
    h = inv.get_invoice(draft["id"])
    assert h["vat_total"] == 0.0
    assert h["net_total"] == 1000.0 and h["gross_total"] == 1000.0
    txt = inv.invoice_text(draft["id"])
    assert "Reverse charge" in txt


def test_reverse_charge_not_derived_for_domestic(inv):
    _set_issuer(inv)  # EE issuer
    c, _ = inv.add_customer("Eesti Klient OU", country="EE", vat_number="EE222222222",
                            address="Narva mnt 5, Tallinn")
    draft, _ = inv.create_draft(customer_id=c["id"])
    assert draft["reverse_charge"] is False   # same country -> normal VAT


def test_reverse_charge_not_derived_for_b2c(inv):
    _set_issuer(inv)
    c, _ = inv.add_customer("Privat Hans", country="DE", vat_number="",
                            address="Hauptstr 2, Berlin")  # no VAT no = B2C
    draft, _ = inv.create_draft(customer_id=c["id"])
    assert draft["reverse_charge"] is False


# ================================================================ gap-free numbering
def test_sequential_numbering_no_gaps(inv):
    nums = []
    for _ in range(5):
        n, seq = inv.next_number("INV", 2026)
        nums.append((n, seq))
    seqs = [s for _, s in nums]
    assert seqs == [1, 2, 3, 4, 5], seqs
    assert nums[0][0] == "INV-2026-000001"
    assert nums[4][0] == "INV-2026-000005"


def test_numbering_independent_per_series_and_year(inv):
    assert inv.next_number("INV", 2026)[1] == 1
    assert inv.next_number("INV", 2026)[1] == 2
    assert inv.next_number("INV", 2027)[1] == 1   # different year resets
    assert inv.next_number("CN", 2026)[1] == 1     # different series resets


def test_concurrent_numbering_never_collides_or_skips(inv):
    """Two threads hammering next_number must produce a contiguous, unique set."""
    # warm the schema once (single-threaded) so the WAL/migration setup isn't racing
    inv.connect().close()
    N = 40

    def grab():
        return inv.next_number("INV", 2026)[1]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        seqs = list(ex.map(lambda _i: grab(), range(N)))
    assert len(set(seqs)) == N, f"collision: {sorted(seqs)}"
    assert sorted(seqs) == list(range(1, N + 1)), "gap or skip in the sequence"


# ================================================================ issue + immutability
def _ready_draft(inv, reverse_charge=False):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE111111111",
                            address="Hauptstr 2, Berlin", payment_terms_days=30)
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=reverse_charge)
    inv.add_line(draft["id"], description="Transport service", quantity=2,
                 unit="h", unit_price_net=50, vat_rate=0.21)
    return draft, c


def test_issue_assigns_number_and_snapshots(inv):
    draft, c = _ready_draft(inv)
    issued, err = inv.issue(draft["id"], issued_by="pytest", issue_date="2026-03-10")
    assert err == "", err
    assert issued["status"] == "issued"
    assert issued["number"] == "INV-2026-000001"
    assert issued["issue_date"] == "2026-03-10"
    assert issued["due_date"] == "2026-04-09"   # +30 days (customer terms override)
    assert issued["issuer_snapshot"] and issued["customer_snapshot"]
    # mutating the customer book afterwards must NOT change the filed invoice
    inv.update_customer(c["id"], name="RENAMED LATER")
    txt = inv.invoice_text(issued["id"])
    assert "Bauer GmbH" in txt and "RENAMED LATER" not in txt


def test_issue_refused_when_incomplete(inv):
    # no issuer profile set -> refuse
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE1",
                            address="Hauptstr 2")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="x", quantity=1, unit_price_net=1, vat_rate=0.21)
    out, err = inv.issue(draft["id"])
    assert out is None and "issuer profile" in err

    _set_issuer(inv)
    # no lines -> refuse
    d2, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    out, err = inv.issue(d2["id"])
    assert out is None and "at least one line" in err


def test_issued_invoice_is_immutable(inv):
    draft, _ = _ready_draft(inv)
    issued, err = inv.issue(draft["id"], issued_by="pytest")
    assert err == ""
    out, err = inv.add_line(issued["id"], description="late", quantity=1,
                            unit_price_net=5, vat_rate=0.21)
    assert out is None and "immutable" in err
    lines = inv.get_lines(issued["id"])
    out, err = inv.remove_line(issued["id"], lines[0]["id"])
    assert out is None and "immutable" in err
    out, err = inv.set_invoice_fields(issued["id"], notes="hack")
    assert out is None and "immutable" in err


def test_double_issue_refused(inv):
    draft, _ = _ready_draft(inv)
    inv.issue(draft["id"])
    out, err = inv.issue(draft["id"])
    assert out is None and "already issued" in err


# ================================================================ Art. 226 PDF fields
def test_pdf_text_carries_all_mandatory_fields(inv):
    draft, _ = _ready_draft(inv)
    # add a second rate so the per-rate breakdown has >1 row
    inv.add_line(draft["id"], description="Materials", quantity=1,
                 unit_price_net=100, vat_rate=0.09)
    issued, err = inv.issue(draft["id"], issued_by="pytest", issue_date="2026-03-10")
    assert err == ""
    txt = inv.invoice_text(issued["id"])
    # (2) sequential number; (1) issue date
    assert "INV-2026-000001" in txt
    assert "2026-03-10" in txt
    # (3) supplier full name + address + VAT number
    assert "Acme Logistics OU" in txt and "EE100000000" in txt
    assert "Tartu mnt 1" in txt
    # (4) customer name + address + VAT number
    assert "Bauer GmbH" in txt and "DE111111111" in txt
    # (6)/(8) per-line description + (9) per-rate
    assert "Transport service" in txt and "Materials" in txt
    assert "21%" in txt and "9%" in txt
    # (10) per-rate VAT breakdown header + amounts
    assert "VAT breakdown" in txt
    # totals + currency
    assert "Total net" in txt and "Total VAT" in txt and "Grand total" in txt
    assert "EUR" in txt
    # payment terms / due date + IBAN
    assert issued["due_date"] in txt
    assert "EE001234567890" in txt
    # the issued PDF must NOT carry the draft label
    assert "DRAFT" not in txt

    # and the actual PDF bytes render
    pdf = inv.invoice_pdf(issued["id"])
    assert pdf and pdf[:5] == b"%PDF-"


def test_draft_pdf_shows_draft_label(inv):
    draft, _ = _ready_draft(inv)
    txt = inv.invoice_text(draft["id"])
    assert "DRAFT" in txt and "not a valid invoice" in txt
    pdf = inv.invoice_pdf(draft["id"])
    assert pdf and pdf[:5] == b"%PDF-"


def test_reverse_charge_invoice_pdf_has_note_and_zero_vat(inv):
    draft, _ = _ready_draft(inv, reverse_charge=True)
    issued, err = inv.issue(draft["id"], issued_by="pytest")
    assert err == ""
    txt = inv.invoice_text(issued["id"])
    assert "Reverse charge" in txt
    assert issued["vat_total"] == 0.0


# ================================================================ customer CRUD
def test_customer_crud(inv):
    c, err = inv.add_customer("Klient AB", country="se", vat_number="SE999",
                              email="a@b.com", payment_terms_days="21")
    assert err == "" and c["country"] == "SE"   # uppercased
    assert c["payment_terms_days"] == 21
    got = inv.get_customer(c["id"])
    assert got["name"] == "Klient AB"
    inv.update_customer(c["id"], name="Klient AB (renamed)", active=False)
    got = inv.get_customer(c["id"])
    assert got["name"] == "Klient AB (renamed)" and got["active"] is False
    # blank name refused
    bad, err = inv.add_customer("   ")
    assert bad is None and "name is required" in err
    assert len(inv.list_customers()) == 1


# ================================================================ tenancy scoping
def test_tenant_scoping(inv, monkeypatch):
    import auth
    import tenancy
    # turn the multitenant switch ON for this test
    monkeypatch.setattr(tenancy, "multitenant_enabled", lambda: True)
    try:
        tenancy.set_tenant("t1")
        c1, _ = inv.add_customer("Tenant1 Co", address="x")
        d1, _ = inv.create_draft(customer_id=c1["id"])
        tenancy.set_tenant("t2")
        c2, _ = inv.add_customer("Tenant2 Co", address="y")
        # t2 sees only its own customer/invoices
        names2 = [c["name"] for c in inv.list_customers()]
        assert names2 == ["Tenant2 Co"]
        assert inv.get_customer(c1["id"]) is None     # cannot read t1's customer
        assert inv.get_invoice(d1["id"]) is None        # cannot read t1's invoice
        # back to t1
        tenancy.set_tenant("t1")
        assert [c["name"] for c in inv.list_customers()] == ["Tenant1 Co"]
    finally:
        tenancy.reset_tenant()


def test_tenant_numbering_isolated(inv, monkeypatch):
    import tenancy
    monkeypatch.setattr(tenancy, "multitenant_enabled", lambda: True)
    try:
        tenancy.set_tenant("t1")
        assert inv.next_number("INV", 2026)[1] == 1
        assert inv.next_number("INV", 2026)[1] == 2
        tenancy.set_tenant("t2")
        assert inv.next_number("INV", 2026)[1] == 1   # t2's own counter
    finally:
        tenancy.reset_tenant()


# ================================================================ web routes
def test_landing_page_renders_and_lists(inv, client):
    draft, _ = _ready_draft(inv)
    inv.issue(draft["id"], issued_by="pytest_admin", issue_date="2026-03-10")
    body = client.get("/invoicing").get_data(as_text=True)
    assert "Invoices" in body
    assert "INV-2026-000001" in body
    assert "Bauer GmbH" in body


def test_landing_page_escapes_xss(inv, client):
    _set_issuer(inv)
    inv.add_customer('<script>alert(1)</script>', address="z")
    inv.create_draft(customer_id=None)
    body = client.get("/invoicing/customers").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_web_issue_flow(inv, client):
    draft, _ = _ready_draft(inv)
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get(f"/invoicing/compose/{draft['id']}").get_data(as_text=True)).group(1)
    r = client.post("/invoicing/issue",
                    data={"_csrf": tok, "invoice_id": draft["id"]})
    assert r.status_code == 302   # redirect to the issued invoice
    issued = inv.get_invoice(draft["id"])
    assert issued["status"] == "issued" and issued["number"]


def test_web_pdf_download(inv, client):
    draft, _ = _ready_draft(inv)
    inv.issue(draft["id"], issued_by="pytest_admin")
    r = client.get(f"/invoicing/pdf/{draft['id']}")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.get_data()[:5] == b"%PDF-"


def test_web_add_line(inv, client):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE1",
                            address="Hauptstr 2")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get(f"/invoicing/compose/{draft['id']}").get_data(as_text=True)).group(1)
    r = client.post("/invoicing/line/add", data={
        "_csrf": tok, "invoice_id": draft["id"], "description": "Consulting",
        "quantity": "2", "unit_price_net": "100", "vat_rate": "21"})
    assert r.status_code == 302
    lines = inv.get_lines(draft["id"])
    assert len(lines) == 1 and lines[0]["description"] == "Consulting"
    assert lines[0]["line_net"] == 200.0 and lines[0]["vat_rate"] == 0.21


# ===================================================================================
# PHASE 2 — EN-16931 / PEPPOL BIS Billing 3.0 e-invoice (XML) + hybrid PDF (Factur-X)
# + the Latvia refinements (rate presets / simplified <=€150 / VAT-in-EUR / retention).
# ===================================================================================
import xml.etree.ElementTree as ET  # noqa: E402

import safexml  # noqa: E402
import extract  # noqa: E402

_UBL = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"


def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _findall_local(root, name):
    return [e for e in root.iter() if _local(e.tag) == name]


def _multi_rate_issued(inv, **over):
    """A ready, ISSUED two-rate invoice (21% + 9%) for the e-invoice tests."""
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE111111111",
                            address="Hauptstr 2, Berlin")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False, **over)
    inv.add_line(draft["id"], description="Transport service", quantity=2, unit="h",
                 unit_price_net=50, vat_rate=0.21)        # net 100, vat 21
    inv.add_line(draft["id"], description="Materials", quantity=1,
                 unit_price_net=100, vat_rate=0.09)        # net 100, vat 9
    issued, err = inv.issue(draft["id"], issued_by="pytest", issue_date="2026-03-10")
    assert err == "", err
    return issued, c


def test_einvoice_xml_mandatory_business_terms(inv):
    issued, _ = _multi_rate_issued(inv)
    xml = inv.einvoice_xml(issued["id"])
    assert isinstance(xml, bytes)
    root = safexml.fromstring(xml)        # parses back as well-formed XML
    assert _local(root.tag) == "Invoice"

    def one(name):
        els = _findall_local(root, name)
        assert els, f"missing {name}"
        return els[0].text

    # CustomizationID = PEPPOL BIS Billing 3.0 + ProfileID
    assert one("CustomizationID") == inv.PEPPOL_CUSTOMIZATION_ID
    assert one("ProfileID") == inv.PEPPOL_PROFILE_ID
    # BT-1 number, BT-2 date, BT-3 type 380, BT-5 currency
    assert one("ID") == "INV-2026-000001"
    assert one("IssueDate") == "2026-03-10"
    assert one("InvoiceTypeCode") == "380"
    assert one("DocumentCurrencyCode") == "EUR"
    # both VAT ids (supplier BT-31 + customer BT-48) present
    company_ids = [e.text for e in _findall_local(root, "CompanyID")]
    assert "EE100000000" in company_ids and "DE111111111" in company_ids
    # IBAN payment means (BG-16): the PayeeFinancialAccount carries the IBAN as its ID
    fa = _findall_local(root, "PayeeFinancialAccount")
    assert fa, "missing PayeeFinancialAccount"
    fa_iban = [e.text for e in fa[0] if _local(e.tag) == "ID"]
    assert "EE001234567890" in fa_iban

    # one InvoiceLine per line
    lines = _findall_local(root, "InvoiceLine")
    assert len(lines) == 2

    # per-rate TaxSubtotal summing to the header TaxTotal
    tax_total = _findall_local(root, "TaxTotal")[0]
    header_tax = float([e for e in tax_total
                        if _local(e.tag) == "TaxAmount"][0].text)
    subs = _findall_local(tax_total, "TaxSubtotal")
    assert len(subs) == 2     # 21% and 9% buckets
    sub_sum = 0.0
    base_sum = 0.0
    for s in subs:
        ta = float([e for e in s if _local(e.tag) == "TaxAmount"][0].text)
        ba = float([e for e in s if _local(e.tag) == "TaxableAmount"][0].text)
        sub_sum += ta
        base_sum += ba
    assert round(sub_sum, 2) == round(header_tax, 2) == 30.0   # 21 + 9
    assert round(base_sum, 2) == 200.0

    # LegalMonetaryTotal payable == gross
    lmt = _findall_local(root, "LegalMonetaryTotal")[0]
    payable = float([e for e in lmt if _local(e.tag) == "PayableAmount"][0].text)
    assert payable == issued["gross_total"] == 230.0


def test_einvoice_refused_for_draft(inv):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE1", address="y")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="x", quantity=1, unit_price_net=10, vat_rate=0.21)
    with pytest.raises(ValueError):
        inv.einvoice_xml(draft["id"])
    with pytest.raises(ValueError):
        inv.invoice_pdf_hybrid(draft["id"])


def test_einvoice_reverse_charge_category_ae(inv):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE111111111",
                            address="Hauptstr 2, Berlin")
    draft, _ = inv.create_draft(customer_id=c["id"])   # auto reverse charge
    assert draft["reverse_charge"] is True
    inv.add_line(draft["id"], description="Freight", quantity=1,
                 unit_price_net=1000, vat_rate=0.21)
    issued, err = inv.issue(draft["id"], issued_by="pytest")
    assert err == ""
    root = safexml.fromstring(inv.einvoice_xml(issued["id"]))
    # every TaxCategory/ClassifiedTaxCategory ID is 'AE', percent 0
    cats = _findall_local(root, "TaxCategory") + _findall_local(root, "ClassifiedTaxCategory")
    assert cats
    for cat in cats:
        cid = [e for e in cat if _local(e.tag) == "ID"][0].text
        pct = [e for e in cat if _local(e.tag) == "Percent"][0].text
        assert cid == "AE", cid
        assert float(pct) == 0.0
    # mandatory reverse-charge note + exemption reason present
    notes = " ".join(e.text or "" for e in _findall_local(root, "Note"))
    reasons = " ".join(e.text or "" for e in _findall_local(root, "TaxExemptionReason"))
    assert "Reverse charge" in notes
    assert "Reverse charge" in reasons


def test_hybrid_pdf_round_trips_through_our_own_reader(inv):
    issued, _ = _multi_rate_issued(inv)
    hybrid = inv.invoice_pdf_hybrid(issued["id"])
    assert hybrid[:5] == b"%PDF-"
    # OUR OWN reader pulls the embedded factur-x.xml back out ...
    emb = extract._pdf_embedded_xml(hybrid)
    assert emb is not None, "embedded XML not found by our reader"
    # ... and parse_einvoice turns it into a correct draft.
    drafted = extract.parse_einvoice(emb)
    assert drafted["supplier"] == "Acme Logistics OU"
    assert drafted["statement_ref"] == "INV-2026-000001"
    # the net total ties out (reader groups lines by country -> one bucket of net 200)
    net = sum(float(l["net"]) for l in drafted["lines"])
    assert round(net, 2) == 200.0


def test_hybrid_degrades_to_plain_pdf_without_pikepdf(inv, monkeypatch):
    issued, _ = _multi_rate_issued(inv)
    # Simulate pikepdf being unavailable: make `import pikepdf` raise inside the module.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "pikepdf":
            raise ImportError("simulated missing pikepdf")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    data = inv.invoice_pdf_hybrid(issued["id"])
    assert data[:5] == b"%PDF-"
    # degraded -> a PLAIN PDF, so our reader finds NO embedded XML
    monkeypatch.undo()
    assert extract._pdf_embedded_xml(data) is None


# ----------------------------------------------------- Latvia refinements
def test_vat_rate_presets_constant(inv):
    assert inv.LV_VAT_RATE_PRESETS == (0.21, 0.12, 0.05, 0.0)


def test_simplified_invoice_relaxes_customer_detail(inv):
    _set_issuer(inv)
    # a customer with NO address — normally refused, but allowed when simplified + small.
    c, _ = inv.add_customer("Walk-in", country="LV")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="Coffee", quantity=1,
                 unit_price_net=10, vat_rate=0.21)        # gross 12.10 <= 150
    # without the flag: refused (missing address)
    assert "address" in inv.validate_for_issue(draft["id"])
    inv.set_invoice_fields(draft["id"], simplified=True)
    assert inv.validate_for_issue(draft["id"]) == ""
    issued, err = inv.issue(draft["id"], issued_by="pytest")
    assert err == "" and issued["simplified"] is True
    assert "Simplified invoice" in inv.invoice_text(issued["id"])
    # and the e-invoice carries the simplified note
    root = safexml.fromstring(inv.einvoice_xml(issued["id"]))
    notes = " ".join(e.text or "" for e in _findall_local(root, "Note"))
    assert "Simplified invoice" in notes


def test_simplified_refused_when_over_ceiling(inv):
    _set_issuer(inv)
    c, _ = inv.add_customer("Walk-in", country="LV")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="Big", quantity=1,
                 unit_price_net=500, vat_rate=0.21)        # gross 605 > 150
    inv.set_invoice_fields(draft["id"], simplified=True)
    err = inv.validate_for_issue(draft["id"])
    assert "150" in err and "simplified" in err.lower()


def test_vat_in_eur_for_foreign_currency(inv, monkeypatch):
    # Force "no cached ECB rate" so the test is deterministic regardless of the box's
    # ecb_rates.db — the user must then supply the rate, which is exactly what we assert.
    import ecb_rates
    monkeypatch.setattr(ecb_rates, "rate_for", lambda ccy, on_date=None: (None, None))
    _set_issuer(inv)
    c, _ = inv.add_customer("UK Co", country="GB", vat_number="GB123",
                            address="London")
    draft, _ = inv.create_draft(customer_id=c["id"], currency="GBP",
                                reverse_charge=False)
    inv.add_line(draft["id"], description="Service", quantity=1,
                 unit_price_net=100, vat_rate=0.21)        # vat 21 GBP
    # no FX rate yet -> issue refused (cannot state VAT in EUR)
    err = inv.validate_for_issue(draft["id"])
    assert "EUR" in err and "FX" in err
    # supply a rate: 0.85 GBP per 1 EUR -> EUR VAT = 21 / 0.85 = 24.71
    inv.set_invoice_fields(draft["id"], fx_rate=0.85)
    assert inv.validate_for_issue(draft["id"]) == ""
    issued, e = inv.issue(draft["id"], issued_by="pytest")
    assert e == ""
    eur_vat, rate, source = inv.vat_total_eur(issued)
    assert rate == 0.85 and source == "manual"
    assert eur_vat == 24.71
    # the PDF text states the EUR VAT, and the XML carries BT-6 + a EUR TaxAmount
    txt = inv.invoice_text(issued["id"])
    assert "Total VAT (EUR)" in txt and "24.71" in txt
    root = safexml.fromstring(inv.einvoice_xml(issued["id"]))
    assert any(e.text == "EUR" for e in _findall_local(root, "TaxCurrencyCode"))
    eur_amounts = [e.text for e in _findall_local(root, "TaxAmount")
                   if e.get("currencyID") == "EUR"]
    assert "24.71" in eur_amounts


def test_retention_marker_stamped_at_issue(inv):
    issued, _ = _multi_rate_issued(inv)
    assert issued["retain_until"] == "2031-03-10"   # issue 2026-03-10 + 5y


def test_web_einvoice_and_hybrid_downloads(inv, client):
    issued, _ = _multi_rate_issued(inv)
    r = client.get(f"/invoicing/einvoice/{issued['id']}.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["Content-Type"]
    assert b"CustomizationID" in r.get_data()
    r2 = client.get(f"/invoicing/hybrid/{issued['id']}")
    assert r2.status_code == 200
    assert r2.headers["Content-Type"] == "application/pdf"
    assert r2.get_data()[:5] == b"%PDF-"


def test_web_einvoice_refused_for_draft(inv, client):
    _set_issuer(inv)
    c, _ = inv.add_customer("Bauer GmbH", country="DE", vat_number="DE1", address="y")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="x", quantity=1, unit_price_net=10, vat_rate=0.21)
    r = client.get(f"/invoicing/einvoice/{draft['id']}.xml")
    assert r.status_code == 400


# ===================================================================================
# LATVIAN / UNICODE RENDERING — the invoice PDF must render Latvian diacritics, NEVER
# the legacy latin-1 `?`. PRIMARY = HTML/wkhtmltopdf; FALLBACK = DejaVu-embedded PDF.
# ===================================================================================
# Latvian text exercising the full diacritic set: ā š ž ē ī ū ķ ļ ņ ģ č.
_LV_ISSUER = "Auroras māja SIA"
_LV_CUSTOMER = "SIA Žagariņš"
_LV_LINE = "Degvielas karšu apkalpošana"
_LV_DIACRITICS = "āšžēīūķļņģč"


def _lv_issued(inv):
    """A ready, ISSUED invoice whose issuer/customer/line carry Latvian diacritics."""
    inv.set_issuer(dict(name=_LV_ISSUER, address="Brīvības iela 1, Rīga, Latvija",
                        vat_number="LV40003000000", reg_no="40003000000",
                        iban="LV80BANK0000435195001", bank="Swedbank",
                        series="INV", number_format="{series}-{year}-{seq:06d}",
                        payment_terms_days="14"))
    c, _ = inv.add_customer(_LV_CUSTOMER, country="LV", vat_number="LV40103000000",
                            address="Ķengaraga iela 5, Rīga")
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description=_LV_LINE, quantity=2, unit="gab",
                 unit_price_net=50, vat_rate=0.21)
    issued, err = inv.issue(draft["id"], issued_by="pytest", issue_date="2026-03-10")
    assert err == "", err
    return issued


def test_invoice_html_carries_latvian_and_is_escaped(inv):
    issued = _lv_issued(inv)
    html = inv.invoice_html(issued["id"])
    # the designed template renders the Latvian text natively (UTF-8 source)
    for word in (_LV_ISSUER, _LV_CUSTOMER, _LV_LINE, "Brīvības", "Ķengaraga", "Rēķins"):
        assert word in html, word
    # no `?` substitution of Latvian (the OLD failure mode is gone)
    assert "?" not in html.replace("?>", "")   # ignore an XML/doctype '?>' if any
    # XSS / DB values are markupsafe-escaped
    inv2 = inv
    c, _ = inv2.add_customer("<script>x</script>", address="z")
    d, _ = inv2.create_draft(customer_id=c["id"])
    body = inv2.invoice_html(d["id"])
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body


def test_invoice_pdf_renders_latvian_via_wkhtmltopdf(inv, tmp_path):
    """PRIMARY path: when wkhtmltopdf is present the PDF is produced AND the Latvian text
    is recoverable from it (proving the glyphs are the real letters, not `?`)."""
    if not inv._wkhtmltopdf_available():
        pytest.skip("wkhtmltopdf not installed on this host")
    issued = _lv_issued(inv)
    pdf = inv._render_pdf_wkhtmltopdf(inv.invoice_html(issued["id"]))
    assert pdf and pdf[:5] == b"%PDF-"
    out = tmp_path / "inv_wk.pdf"
    out.write_bytes(pdf)
    assert out.stat().st_size > 0
    # recover the text and assert the Latvian came through verbatim, zero `?`. pypdf
    # reconstructs words from positioned glyphs so inter-word whitespace may differ
    # (tabs/newlines); normalise it, then assert each diacritic-bearing token survived.
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(out))
    raw_text = "\n".join(p.extract_text() for p in reader.pages)
    norm = " ".join(raw_text.split())
    for token in ("māja", "Žagariņš", "apkalpošana", "Brīvības", "Rīga",
                  "Ķengaraga", "Rēķins"):
        assert token in norm, f"{token!r} not recovered from the PDF text"
    assert "?" not in raw_text, "Latvian collapsed to '?' — the old latin-1 bug is back"


def test_invoice_pdf_fallback_embeds_unicode_font_and_glyphs(inv, tmp_path):
    """FALLBACK path (wkhtmltopdf absent): the dependency-free PDF embeds a Unicode TTF and
    draws the Latvian letters as REAL glyphs (Type0/Identity-H) — never the latin-1 `?`."""
    issued = _lv_issued(inv)
    # force the fallback by pretending wkhtmltopdf is not on the host
    pdf = inv._invoice_pdf_fallback(issued["id"])
    assert pdf and pdf[:5] == b"%PDF-"
    out = tmp_path / "inv_fb.pdf"
    out.write_bytes(pdf)
    assert out.stat().st_size > 0
    raw = pdf
    # embedded Unicode TrueType font + composite Type0/Identity-H encoding
    assert b"FontFile2" in raw and b"Identity-H" in raw and b"CIDFontType2" in raw
    # every Latvian diacritic resolves to a NON-zero glyph that differs from the `?` glyph,
    # and that glyph id is what gets DRAWN in the content stream (hex, Identity-H).
    font_path = inv._find_fallback_font()
    assert font_path, "no Unicode TTF on the host for the fallback"
    data = open(font_path, "rb").read()
    tables = inv._ttf_tables(data)
    cmap = inv._ttf_cmap_unicode(data, tables)
    q_gid = cmap.get(ord("?"))
    # (a) EVERY Latvian diacritic resolves to a real, non-`?` glyph in the font cmap.
    for ch in _LV_DIACRITICS:
        gid = cmap.get(ord(ch), 0)
        assert gid > 0, f"{ch!r} has no glyph (would render blank/notdef)"
        assert gid != q_gid, f"{ch!r} maps to the '?' glyph"
    # (b) the diacritics PRESENT in this invoice's text are actually DRAWN as those glyphs
    #     (hex glyph runs in the content stream) — never substituted by `?`.
    drawn = "".join(sorted(set(inv.invoice_text(issued["id"])) & set(_LV_DIACRITICS)))
    assert drawn, "the test invoice should contain some Latvian diacritics"
    for ch in drawn:
        gid = cmap[ord(ch)]
        assert ("%04X" % gid).encode("latin-1") in raw, f"{ch!r} glyph not drawn"


def test_invoice_pdf_uses_fallback_when_wkhtmltopdf_absent(inv, monkeypatch, tmp_path):
    """invoice_pdf() degrades to the Latvian-capable fallback when wkhtmltopdf is missing —
    it does NOT regress to the broken latin-1 path. Verified end-to-end via invoice_pdf()."""
    issued = _lv_issued(inv)
    # make wkhtmltopdf appear absent
    monkeypatch.setattr(inv.shutil, "which", lambda b: None)
    pdf = inv.invoice_pdf(issued["id"])
    assert pdf and pdf[:5] == b"%PDF-"
    out = tmp_path / "inv_forced_fb.pdf"
    out.write_bytes(pdf)
    assert out.stat().st_size > 0
    # it is the Unicode-font fallback (not the wkhtmltopdf output, not latin-1 text_to_pdf)
    assert b"Identity-H" in pdf and b"FontFile2" in pdf


def test_invoice_pdf_top_level_produces_pdf(inv):
    """invoice_pdf() (whichever path the host supports) yields a valid PDF for a Latvian
    invoice — both for an issued invoice and a draft (draft watermark path)."""
    issued = _lv_issued(inv)
    assert inv.invoice_pdf(issued["id"])[:5] == b"%PDF-"
    # a fresh draft with Latvian text -> still a valid PDF (DRAFT banner/watermark)
    c, _ = inv.add_customer("SIA Liepāja", address="Kūrmājas prospekts 1, Liepāja")
    d, _ = inv.create_draft(customer_id=c["id"])
    inv.add_line(d["id"], description="Apkalpošana", quantity=1, unit_price_net=10,
                 vat_rate=0.21)
    pdf = inv.invoice_pdf(d["id"])
    assert pdf and pdf[:5] == b"%PDF-"
    html = inv.invoice_html(d["id"])
    assert "DRAFT" in html and "not a valid invoice" in html


def test_hybrid_round_trips_on_fallback_pdf(inv, monkeypatch):
    """The Factur-X hybrid embed must keep round-tripping when the BASE PDF is the Unicode
    fallback (wkhtmltopdf absent) — not just the wkhtmltopdf output."""
    issued = _lv_issued(inv)
    monkeypatch.setattr(inv.shutil, "which", lambda b: None)   # force fallback base PDF
    hybrid = inv.invoice_pdf_hybrid(issued["id"])
    assert hybrid[:5] == b"%PDF-"
    emb = extract._pdf_embedded_xml(hybrid)
    assert emb is not None, "embedded XML lost when embedding into the fallback PDF"
    drafted = extract.parse_einvoice(emb)
    assert drafted["supplier"] == _LV_ISSUER
    assert drafted["statement_ref"] == "INV-2026-000001"
