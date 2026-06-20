"""
INVOICING — PHASE 6: RECURRING INVOICES (invoicing.py).

A recurring template auto-generates invoices on a schedule (e.g. monthly fuel-card
billing). These tests repoint invoicing.DB at a temp file (so the live invoicing.db is
never touched) and exercise:

  - the schedule math (advance_next_run) for weekly / monthly / quarterly / yearly, and
    the MONTH-END-SAFE case (a Jan-31 monthly recurring lands Feb-28/29, never skips);
  - generate_due composes a DRAFT invoice with the template's lines + correct totals and
    links it back to the template, then advances next_run by exactly one period;
  - auto_issue=True yields an ISSUED invoice with a gap-free number;
  - idempotency: running generate_due twice the same day yields ONE invoice per template;
  - end_date / max_occurrences deactivate (pause) the template;
  - a paused template is skipped;
  - the scheduler is OFF by default (the tick generates nothing);
  - the web management page renders + the manual "generate due now" action works;
  - i18n EN-default + LV catalog entries exist for the new labels.
"""
import datetime
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


def _customer(inv, **over):
    # DOMESTIC customer (same country as the issuer, EE) so an ordinary 21% VAT applies —
    # a cross-border EU B2B customer would auto-derive reverse charge (0% VAT), which is a
    # different scenario tested elsewhere in the invoicing suite.
    base = dict(name="Beta Transport OU", address="Parnu mnt 5, Tallinn, Estonia",
                vat_number="EE200000000", country="EE", email="ap@beta.ee")
    base.update(over)
    c, err = inv.add_customer(**base)
    assert not err, err
    return c


def _tmpl(inv, cust, **over):
    base = dict(customer_id=cust["id"],
                lines=[{"description": "Monthly fuel-card billing", "quantity": 1,
                        "unit": "mo", "unit_price_net": 100, "vat_rate": 0.21}],
                frequency="monthly", start_date="2026-01-15")
    base.update(over)
    t, err = inv.create_recurring(**base)
    assert not err, err
    return t


# ====================================================================== schedule math
def test_advance_monthly(inv):
    t = {"frequency": "monthly", "interval_n": 1}
    assert inv.advance_next_run(t, "2026-01-15") == datetime.date(2026, 2, 15)


def test_advance_month_end_safe_jan31(inv):
    # a Jan-31 monthly recurring must land on Feb-28 (2026 is not a leap year) — never skip
    t = {"frequency": "monthly", "interval_n": 1}
    assert inv.advance_next_run(t, "2026-01-31") == datetime.date(2026, 2, 28)
    # and from that Feb-28 the next is Mar-28 (we do NOT snap back to the 31st)
    assert inv.advance_next_run(t, "2026-02-28") == datetime.date(2026, 3, 28)


def test_advance_month_end_safe_leap(inv):
    # 2028 IS a leap year -> Jan-31 monthly lands Feb-29
    t = {"frequency": "monthly", "interval_n": 1}
    assert inv.advance_next_run(t, "2028-01-31") == datetime.date(2028, 2, 29)


def test_advance_weekly_quarterly_yearly(inv):
    assert inv.advance_next_run({"frequency": "weekly", "interval_n": 1},
                                "2026-01-15") == datetime.date(2026, 1, 22)
    assert inv.advance_next_run({"frequency": "weekly", "interval_n": 2},
                                "2026-01-15") == datetime.date(2026, 1, 29)
    assert inv.advance_next_run({"frequency": "quarterly", "interval_n": 1},
                                "2026-01-31") == datetime.date(2026, 4, 30)
    assert inv.advance_next_run({"frequency": "yearly", "interval_n": 1},
                                "2026-02-29" if False else "2026-01-15") == datetime.date(2027, 1, 15)


def test_advance_interval_n_months(inv):
    # every 2 months from Jan-31 -> Mar-31
    assert inv.advance_next_run({"frequency": "monthly", "interval_n": 2},
                                "2026-01-31") == datetime.date(2026, 3, 31)


# ====================================================================== due / generation
def test_create_seeds_next_run_from_start(inv):
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    assert t["next_run"] == "2026-01-15"
    assert t["status"] == "active"
    assert t["occurrences_done"] == 0


def test_due_templates_respects_next_run(inv):
    c = _customer(inv)
    _tmpl(inv, c, start_date="2026-02-01")
    assert inv.due_templates("2026-01-31") == []           # not due yet
    assert len(inv.due_templates("2026-02-01")) == 1       # due on/after next_run
    assert len(inv.due_templates("2026-03-15")) == 1


def test_generate_due_creates_draft_with_lines_and_totals(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15",
              lines=[{"description": "Fuel card", "quantity": 2,
                      "unit_price_net": 50, "vat_rate": 0.21}])
    res = inv.generate_due("2026-01-20")
    assert len(res) == 1
    tid, iid, status = res[0]
    assert tid == t["id"] and iid and status == "draft"
    got = inv.get_invoice(iid)
    assert got["status"] == "draft"
    assert got["from_template_id"] == t["id"]
    # net = 2*50 = 100, vat = 21 -> gross 121
    assert money.f2(got["net_total"]) == 100.0
    assert money.f2(got["vat_total"]) == 21.0
    assert money.f2(got["gross_total"]) == 121.0
    lines = inv.get_lines(iid)
    assert len(lines) == 1 and lines[0]["description"] == "Fuel card"


def test_generate_advances_next_run_by_one_month(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-31")
    inv.generate_due("2026-02-01")
    t2 = inv.get_recurring(t["id"])
    # month-end safe: Jan-31 -> Feb-28
    assert t2["next_run"] == "2026-02-28"
    assert t2["occurrences_done"] == 1


def test_auto_issue_yields_numbered_issued_invoice(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15", auto_issue=True)
    res = inv.generate_due("2026-01-20")
    tid, iid, status = res[0]
    assert status == "issued"
    got = inv.get_invoice(iid)
    assert got["status"] == "issued"
    assert got["number"]                         # a gap-free number was assigned
    assert re.match(r"INV-2026-\d{6}", got["number"]), got["number"]


def test_idempotent_same_day(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    first = inv.generate_due("2026-01-20")
    assert first[0][1]                           # made an invoice
    # running again the SAME day must NOT generate a second invoice for this template
    again = inv.generate_due("2026-01-20")
    # the template's next_run is now 2026-02-15, so it isn't even due on 2026-01-20
    assert again == []
    assert len(inv.invoices_from_template(t["id"])) == 1


def test_idempotent_guard_when_still_due(inv):
    # force the cursor to stay on the same period and re-run: the run-log unique guard
    # must prevent a double generation for the same (template, period).
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    inv.generate_due("2026-01-20")
    # rewind next_run back to the original period (simulating a double tick before advance)
    inv.update_recurring(t["id"], next_run="2026-01-15")
    res = inv.generate_due("2026-01-20")
    assert res == [(t["id"], None, "skipped")]
    assert len(inv.invoices_from_template(t["id"])) == 1


def test_max_occurrences_deactivates(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15", max_occurrences=1)
    inv.generate_due("2026-01-20")
    t2 = inv.get_recurring(t["id"])
    assert t2["occurrences_done"] == 1
    assert t2["status"] == "paused"              # exhausted -> deactivated
    # no longer due
    assert inv.due_templates("2026-06-01") == []


def test_end_date_deactivates(inv):
    _set_issuer(inv)
    c = _customer(inv)
    # a monthly template ending mid-Feb: after the Jan run, next_run (Feb-15) is past end.
    t = _tmpl(inv, c, start_date="2026-01-15", end_date="2026-02-01")
    inv.generate_due("2026-01-20")
    t2 = inv.get_recurring(t["id"])
    assert t2["status"] == "paused"


def test_paused_template_skipped(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    inv.pause_recurring(t["id"])
    assert inv.due_templates("2026-01-20") == []
    assert inv.generate_due("2026-01-20") == []
    assert inv.invoices_from_template(t["id"]) == []
    # resume -> now generates
    inv.resume_recurring(t["id"])
    res = inv.generate_due("2026-01-20")
    assert res and res[0][1]


def test_generated_invoice_links_back(inv):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    res = inv.generate_due("2026-01-20")
    iid = res[0][1]
    fromtmpl = inv.invoices_from_template(t["id"])
    assert [x["id"] for x in fromtmpl] == [iid]
    runs = inv.recurring_runs_for(t["id"])
    assert len(runs) == 1
    assert runs[0]["invoice_id"] == iid
    assert runs[0]["period"] == "2026-01-15"


def test_never_raise_on_one_bad_template(inv):
    # a template with no customer / a customer that issue can't validate still must not
    # break generation of the others. We make one good and one with a missing customer.
    _set_issuer(inv)
    c = _customer(inv)
    good = _tmpl(inv, c, start_date="2026-01-15")
    bad, err = inv.create_recurring(customer_id=999999,
                                    lines=[{"description": "x", "quantity": 1,
                                            "unit_price_net": 10, "vat_rate": 0.21}],
                                    frequency="monthly", start_date="2026-01-15",
                                    auto_issue=True)
    assert not err
    res = inv.generate_due("2026-01-20")
    by_t = {r[0]: r for r in res}
    # the good template generated; the bad one is recorded but did not raise
    assert by_t[good["id"]][1]
    assert bad["id"] in by_t


# ====================================================================== scheduler (opt-in)
def test_scheduler_off_by_default_generates_nothing(inv, monkeypatch):
    import app as A
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    # the setting is OFF by default (conftest clears runtime settings) -> tick is inert
    assert A.invoicing_recurring_on() is False
    assert A._invoicing_recurring_tick() is False
    assert inv.invoices_from_template(t["id"]) == []


def test_scheduler_on_generates_and_records_run_date(inv, monkeypatch):
    import app as A
    import auth
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date=datetime.date.today().isoformat())
    auth.set_setting("invoicing_recurring_enabled", "1")
    auth.set_setting("invoicing_recurring_last_run", "")
    assert A._invoicing_recurring_tick() is True
    assert len(inv.invoices_from_template(t["id"])) == 1
    # last-run date is stamped -> a second tick the same day is a no-op
    assert A._invoicing_recurring_tick() is False
    assert len(inv.invoices_from_template(t["id"])) == 1


# ====================================================================== web routes
def test_web_recurring_page_and_manual_generate(inv, client):
    _set_issuer(inv)
    c = _customer(inv)
    # create a template via the web form
    page = client.get("/invoicing/recurring").get_data(as_text=True)
    assert "Recurring invoices" in page
    tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    r = client.post("/invoicing/recurring/save", data={
        "_csrf": tok, "customer_id": c["id"], "currency": "EUR",
        "frequency": "monthly", "interval_n": "1",
        "start_date": "2026-01-15", "auto_issue": "",
        "description": "Fuel card", "quantity": "1", "unit": "mo",
        "unit_price_net": "100", "vat_rate": "21"})
    assert r.status_code == 200
    tmpls = inv.list_recurring()
    assert len(tmpls) == 1
    tid = tmpls[0]["id"]
    # manual "generate due now"
    page2 = client.get("/invoicing/recurring").get_data(as_text=True)
    tok2 = re.search(r'name="_csrf" value="([^"]+)"', page2).group(1)
    g = client.post("/invoicing/recurring/generate", data={"_csrf": tok2})
    assert g.status_code == 200
    assert len(inv.invoices_from_template(tid)) == 1


def test_web_pause_resume_delete(inv, client):
    _set_issuer(inv)
    c = _customer(inv)
    t = _tmpl(inv, c, start_date="2026-01-15")
    page = client.get(f"/invoicing/recurring?edit={t['id']}").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    # pause
    client.post("/invoicing/recurring/action",
                data={"_csrf": tok, "id": t["id"], "op": "pause"})
    assert inv.get_recurring(t["id"])["status"] == "paused"
    # resume
    client.post("/invoicing/recurring/action",
                data={"_csrf": tok, "id": t["id"], "op": "resume"})
    assert inv.get_recurring(t["id"])["status"] == "active"
    # toggle the scheduler setting
    import auth
    client.post("/invoicing/recurring/action",
                data={"_csrf": tok, "op": "set_scheduler", "enabled": "1"})
    assert auth.get_setting("invoicing_recurring_enabled") == "1"
    # delete
    client.post("/invoicing/recurring/action",
                data={"_csrf": tok, "id": t["id"], "op": "delete"})
    assert inv.get_recurring(t["id"]) is None


# ====================================================================== i18n
def test_i18n_en_default_and_lv_catalog():
    import i18n
    # EN default returns the key text verbatim
    assert i18n.t("Recurring invoices", "en") == "Recurring invoices"
    assert i18n.t("Generate due now", "en") == "Generate due now"
    assert i18n.t("Auto-issue", "en") == "Auto-issue"
    # LV catalog carries the new labels
    assert i18n.t("Recurring invoices", "lv") == "Periodiskie rēķini"
    assert i18n.t("Generate due now", "lv") == "Izveidot pienākušos tagad"
    assert i18n.t("monthly", "lv") == "ikmēneša"
    assert i18n.t("weekly", "lv") == "iknedēļas"
    assert i18n.t("quarterly", "lv") == "ceturkšņa"
    assert i18n.t("yearly", "lv") == "gada"
