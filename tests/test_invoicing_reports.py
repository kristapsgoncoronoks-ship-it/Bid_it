"""
INVOICING REPORTS (invoicing_reports.py) — Phase 5 of the sales-invoicing module: the
READ-ONLY reporting suite over the invoicing data. These tests repoint invoicing.DB at a
temp file (so the live invoicing.db is never touched) and exercise:

  - the shared period helper (month / quarter / year windows; bad input);
  - the VAT-output (PVN) report: ties out per-rate (net + VAT), a credit note REDUCES the
    period's output VAT, reverse-charge + 0% rows are SEPARATE and carry no output VAT,
    and sum(per-rate VAT) == total output VAT;
  - period filtering: a document outside the window is excluded (by issue date);
  - the revenue report: by month / customer / service sums correctly, credit notes
    subtracted;
  - the customer statement: running balance = invoices − credit notes − payments; the
    closing balance is opening + Σdebits − Σcredits;
  - the AR aging report: buckets by days-past-due, total == sum of outstanding;
  - the Excel + PDF exports produce non-empty valid files;
  - i18n EN-default + LV catalog entries exist for the new labels.
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import invoicing            # noqa: E402
import invoicing_reports as ivr  # noqa: E402
import money                # noqa: E402


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


def _customer(inv, name="Bauer GmbH", **kw):
    base = dict(country="LV", vat_number="LV111111111", address="Hauptstr 2, Riga",
                email="cust@example.com")
    base.update(kw)
    c, _ = inv.add_customer(name, **base)
    return c


def _issue_invoice(inv, cust, lines, issue_date, reverse_charge=False):
    """Create+issue an invoice with the given lines (list of (desc, qty, price, rate))."""
    draft, _ = inv.create_draft(customer_id=cust["id"], reverse_charge=reverse_charge)
    for desc, qty, price, rate in lines:
        inv.add_line(draft["id"], description=desc, quantity=qty,
                     unit_price_net=price, vat_rate=rate)
    issued, err = inv.issue(draft["id"], issued_by="pytest", issue_date=issue_date)
    assert err == "", err
    return issued


def _issue_credit(inv, original_id, issue_date, mode="full", lines=None, reason="x"):
    cn, err = inv.create_credit_note(original_id, mode=mode, lines=lines, reason=reason)
    assert err == "", err
    issued, err = inv.issue(cn["id"], issued_by="pytest", issue_date=issue_date)
    assert err == "", err
    return issued


# ====================================================================== period helper
def test_period_window_month_quarter_year():
    assert ivr.period_window("month", 2026, 3) == ("2026-03-01", "2026-03-31", "2026-03")
    assert ivr.period_window("quarter", 2026, 2) == ("2026-04-01", "2026-06-30", "2026-Q2")
    assert ivr.period_window("year", 2026) == ("2026-01-01", "2026-12-31", "2026")
    # December month end and Q4 end
    assert ivr.period_window("month", 2026, 12)[1] == "2026-12-31"
    assert ivr.period_window("quarter", 2026, 4) == ("2026-10-01", "2026-12-31", "2026-Q4")


def test_period_window_rejects_bad_input():
    for args in [("month", 2026, 13), ("quarter", 2026, 5), ("bogus", 2026, 1),
                 ("year", "notayear")]:
        with pytest.raises(ValueError):
            ivr.period_window(*args)


# ====================================================================== VAT output (PVN)
def test_vat_output_ties_out_per_rate_and_total(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # 21% line (net 100, vat 21), 12% line (net 50, vat 6), 5% line (net 20, vat 1)
    _issue_invoice(inv, c, [("Std", 1, 100, 0.21), ("Reduced", 1, 50, 0.12),
                            ("Books", 1, 20, 0.05)], "2026-03-10")
    rep = ivr.vat_output_report("2026-03-01", "2026-03-31", label="2026-03")
    by = {r["key"]: r for r in rep["rows"]}
    assert by[0.21]["net"] == 100.0 and by[0.21]["vat"] == 21.0
    assert by[0.12]["net"] == 50.0 and by[0.12]["vat"] == 6.0
    assert by[0.05]["net"] == 20.0 and by[0.05]["vat"] == 1.0
    assert rep["net_total"] == 170.0
    assert rep["vat_total"] == 28.0
    # the tie-out: sum of per-rate VAT == total output VAT
    assert money.fsum([r["vat"] for r in rep["rows"]]) == rep["vat_total"]


def test_vat_output_credit_note_reduces_period_output_vat(inv):
    _set_issuer(inv)
    c = _customer(inv)
    orig = _issue_invoice(inv, c, [("Std", 2, 100, 0.21)], "2026-03-10")  # net 200, vat 42
    before = ivr.vat_output_report("2026-03-01", "2026-03-31")
    assert before["rows"][0]["vat"] == 42.0 and before["vat_total"] == 42.0
    # a partial credit of net 100 (vat 21) issued in the SAME period
    _issue_credit(inv, orig["id"], "2026-03-20", mode="partial",
                  lines=[{"line_no": 1, "quantity": 1, "unit_price_net": 100}])
    after = ivr.vat_output_report("2026-03-01", "2026-03-31")
    by = {r["key"]: r for r in after["rows"]}
    # net 200 − 100 = 100; vat 42 − 21 = 21
    assert by[0.21]["net"] == 100.0 and by[0.21]["vat"] == 21.0
    assert after["vat_total"] == 21.0
    assert after["credits_vat"] == 21.0 and after["invoices_vat"] == 42.0


def test_vat_output_reverse_charge_and_zero_separate_no_vat(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # reverse-charge invoice: 0% VAT but reportable net
    _issue_invoice(inv, c, [("Freight", 1, 500, 0.21)], "2026-03-05", reverse_charge=True)
    # a 0%/exempt domestic supply
    c2 = _customer(inv, name="Zero Co", country="LV", vat_number="LV222222222")
    _issue_invoice(inv, c2, [("Exempt svc", 1, 80, 0.0)], "2026-03-06")
    rep = ivr.vat_output_report("2026-03-01", "2026-03-31")
    by = {r["key"]: r for r in rep["rows"]}
    assert "reverse_charge" in by and by["reverse_charge"]["net"] == 500.0
    assert by["reverse_charge"]["vat"] == 0.0
    assert by["reverse_charge"]["kind"] == "reverse_charge"
    assert "zero" in by and by["zero"]["net"] == 80.0 and by["zero"]["vat"] == 0.0
    # total output VAT is zero (both lines carry no output VAT) but net is reportable
    assert rep["vat_total"] == 0.0
    assert rep["net_total"] == 580.0


def test_vat_output_period_filter_excludes_out_of_window(inv):
    _set_issuer(inv)
    c = _customer(inv)
    _issue_invoice(inv, c, [("In window", 1, 100, 0.21)], "2026-03-15")
    _issue_invoice(inv, c, [("Out of window", 1, 999, 0.21)], "2026-04-15")
    rep = ivr.vat_output_report("2026-03-01", "2026-03-31")
    assert rep["net_total"] == 100.0 and rep["vat_total"] == 21.0


def test_vat_output_excludes_drafts(inv):
    _set_issuer(inv)
    c = _customer(inv)
    draft, _ = inv.create_draft(customer_id=c["id"], reverse_charge=False)
    inv.add_line(draft["id"], description="Draft", quantity=1, unit_price_net=100,
                 vat_rate=0.21)
    # a draft has no issue date and must never appear in a period report
    rep = ivr.vat_output_report("2026-01-01", "2026-12-31")
    assert rep["net_total"] == 0.0 and rep["rows"] == []


# ====================================================================== revenue report
def test_revenue_by_month_customer_service_with_credit(inv):
    _set_issuer(inv)
    a = _customer(inv, name="Alpha")
    b = _customer(inv, name="Beta")
    _issue_invoice(inv, a, [("Transport", 1, 100, 0.21)], "2026-01-10")
    _issue_invoice(inv, b, [("Consulting", 1, 200, 0.21)], "2026-02-10")
    orig = _issue_invoice(inv, a, [("Transport", 1, 100, 0.21)], "2026-02-12")
    # credit one of Alpha's invoices fully in Feb (net -100)
    _issue_credit(inv, orig["id"], "2026-02-20", mode="full")
    rep = ivr.revenue_report("2026-01-01", "2026-12-31", label="2026")
    # net = 100 (Jan Alpha) + 200 (Feb Beta) + 100 (Feb Alpha) − 100 (Feb credit) = 300
    assert rep["net_total"] == 300.0
    months = {m["month"]: m for m in rep["by_month"]}
    assert months["2026-01"]["net"] == 100.0
    assert months["2026-02"]["net"] == 200.0   # 200 + 100 − 100
    custs = {c["customer"]: c for c in rep["by_customer"]}
    assert custs["Alpha"]["net"] == 100.0       # 100 + 100 − 100
    assert custs["Beta"]["net"] == 200.0
    svcs = {s["service"]: s for s in rep["by_service"]}
    assert svcs["Transport"]["net"] == 100.0    # 100 + 100 − 100
    assert svcs["Consulting"]["net"] == 200.0
    assert rep["invoice_count"] == 3 and rep["credit_count"] == 1


# ====================================================================== customer statement
def test_customer_statement_running_and_closing_balance(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # invoice 1: gross 121 (net 100 + vat 21)
    i1 = _issue_invoice(inv, c, [("Svc", 1, 100, 0.21)], "2026-03-01")
    # a payment of 50 against it
    inv.record_payment(i1["id"], 50.0, "2026-03-05", method="transfer")
    # invoice 2: gross 242
    _issue_invoice(inv, c, [("Svc", 2, 100, 0.21)], "2026-03-10")
    # a full credit of invoice 1 (gross 121)
    _issue_credit(inv, i1["id"], "2026-03-15", mode="full")
    stm = ivr.customer_statement(c["id"], "2026-03-01", "2026-03-31", label="2026-03")
    # closing = opening(0) + debits(121+242) − credits(50 payment + 121 credit note)
    assert stm["opening_balance"] == 0.0
    assert stm["closing_balance"] == money.f2(121 + 242 - 50 - 121)
    # the running balance of the last line equals the closing balance
    assert stm["lines"][-1]["balance"] == stm["closing_balance"]
    # ledger contains the two invoices, one payment, one credit note
    types = sorted(ln["type"] for ln in stm["lines"])
    assert types == ["credit_note", "invoice", "invoice", "payment"]
    # debits − credits identity
    debits = money.fsum([ln["debit"] for ln in stm["lines"]])
    credits = money.fsum([ln["credit"] for ln in stm["lines"]])
    assert money.f2(stm["opening_balance"] + debits - credits) == stm["closing_balance"]


def test_customer_statement_opening_balance_from_prior_period(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # an invoice BEFORE the window contributes to the opening balance
    _issue_invoice(inv, c, [("Old", 1, 100, 0.21)], "2026-01-10")     # gross 121
    _issue_invoice(inv, c, [("New", 1, 100, 0.21)], "2026-03-10")     # gross 121
    stm = ivr.customer_statement(c["id"], "2026-03-01", "2026-03-31")
    assert stm["opening_balance"] == 121.0
    assert stm["closing_balance"] == 242.0


# ====================================================================== AR aging
def test_ar_aging_buckets_and_total_ties_to_outstanding(inv):
    _set_issuer(inv)   # issuer default payment terms = 14 days
    c = _customer(inv)
    # as_of 2026-04-15 with 14-day terms: issued 2026-03-25 -> due 2026-04-08 = 7 days
    # past due (bucket 1-30); issued 2026-01-01 -> due 2026-01-15 = 90 days past (60+).
    _issue_invoice(inv, c, [("A", 1, 100, 0.0)], "2026-03-25")   # 1-30 bucket
    _issue_invoice(inv, c, [("B", 1, 200, 0.0)], "2026-01-01")   # 60+ bucket
    rep = ivr.ar_aging_report(today="2026-04-15", label="x")
    row = rep["rows"][0]
    assert row["customer"] == "Bauer GmbH"
    assert row["b1_30"] == 100.0
    assert row["b60p"] == 200.0
    assert row["total"] == 300.0
    assert row["overdue"] == 300.0       # both are past due
    assert rep["total_outstanding"] == 300.0
    # grand total == sum of the bucket amounts
    b = rep["buckets"]
    assert money.fsum([b["current"], b["1-30"], b["31-60"], b["60+"]]) == 300.0


def test_ar_aging_does_not_fork_ar_total(inv):
    """The aging total must equal invoicing.accounts_receivable's total_outstanding."""
    _set_issuer(inv)
    c = _customer(inv)
    _issue_invoice(inv, c, [("A", 1, 100, 0.0)], "2026-03-25")
    _issue_invoice(inv, c, [("B", 1, 200, 0.0)], "2026-01-01")
    ar = inv.accounts_receivable(today="2026-04-15")
    rep = ivr.ar_aging_report(today="2026-04-15")
    assert rep["total_outstanding"] == ar["total_outstanding"]


# ====================================================================== Excel + PDF exports
def test_excel_exports_are_valid_nonempty_xlsx(inv):
    import io
    from openpyxl import load_workbook
    _set_issuer(inv)
    c = _customer(inv)
    i1 = _issue_invoice(inv, c, [("Svc", 1, 100, 0.21)], "2026-03-10")
    inv.record_payment(i1["id"], 30.0, "2026-03-12")
    vat = ivr.vat_output_workbook(ivr.vat_output_report("2026-03-01", "2026-03-31"))
    rev = ivr.revenue_workbook(ivr.revenue_report("2026-03-01", "2026-03-31"))
    stm = ivr.customer_statement_workbook(
        ivr.customer_statement(c["id"], "2026-03-01", "2026-03-31"))
    ar = ivr.ar_aging_workbook(ivr.ar_aging_report(today="2026-04-15"))
    for data in (vat, rev, stm, ar):
        assert isinstance(data, (bytes, bytearray)) and len(data) > 0
        # the PK zip magic of a real .xlsx
        assert data[:2] == b"PK"
        wb = load_workbook(io.BytesIO(data))   # parses without error
        assert wb.worksheets


def test_pdf_exports_are_valid_nonempty(inv):
    _set_issuer(inv)
    c = _customer(inv)
    _issue_invoice(inv, c, [("Svc", 1, 100, 0.21)], "2026-03-10")
    for data in (
        ivr.vat_output_pdf(ivr.vat_output_report("2026-03-01", "2026-03-31"), lang="en"),
        ivr.revenue_pdf(ivr.revenue_report("2026-03-01", "2026-03-31"), lang="en"),
        ivr.customer_statement_pdf(
            ivr.customer_statement(c["id"], "2026-03-01", "2026-03-31"), lang="lv"),
        ivr.ar_aging_pdf(ivr.ar_aging_report(today="2026-04-15"), lang="en"),
    ):
        assert data is not None and len(data) > 0
        assert data[:5] == b"%PDF-"


# ====================================================================== i18n
def test_i18n_en_default_and_lv_catalog():
    import i18n
    # EN default returns the key text verbatim
    assert i18n.t("Output VAT", "en") == "Output VAT"
    assert i18n.t("Statement of account", "en") == "Statement of account"
    # LV catalog carries the requested accounting terms
    assert i18n.t("Output VAT", "lv") == "Aprēķinātais PVN"
    assert i18n.t("Statement of account", "lv") == "Norēķinu izraksts"
    assert i18n.t("Invoicing reports", "lv") == "Rēķinu pārskati"
    assert i18n.t("Accounts receivable aging", "lv") == "Debitoru parādu novecošana"


# ====================================================================== web routes
def test_web_reports_pages_render(inv, client):
    _set_issuer(inv)
    c = _customer(inv)
    _issue_invoice(inv, c, [("Svc", 1, 100, 0.21)], "2026-03-10")
    assert client.get("/invoicing/reports").status_code == 200
    vat = client.get("/invoicing/reports/vat?kind=month&year=2026&sub=3")
    assert vat.status_code == 200
    assert "Output VAT" in vat.get_data(as_text=True)
    rev = client.get("/invoicing/reports/revenue?kind=year&year=2026")
    assert rev.status_code == 200 and "revenue" in rev.get_data(as_text=True).lower()
    ar = client.get("/invoicing/reports/aging")
    assert ar.status_code == 200 and "aging" in ar.get_data(as_text=True).lower()
    stm = client.get(f"/invoicing/reports/statement?customer_id={c['id']}"
                     "&start=2026-01-01&end=2026-12-31")
    assert stm.status_code == 200 and "Statement of account" in stm.get_data(as_text=True)


def test_web_report_excel_and_pdf_downloads(inv, client):
    _set_issuer(inv)
    c = _customer(inv)
    _issue_invoice(inv, c, [("Svc", 1, 100, 0.21)], "2026-03-10")
    x = client.get("/invoicing/reports/vat.xlsx?kind=month&year=2026&sub=3")
    assert x.status_code == 200
    assert x.headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert x.get_data()[:2] == b"PK"
    p = client.get("/invoicing/reports/aging.pdf")
    assert p.status_code == 200 and p.headers["Content-Type"] == "application/pdf"
    assert p.get_data()[:5] == b"%PDF-"
