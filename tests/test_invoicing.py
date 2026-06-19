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
