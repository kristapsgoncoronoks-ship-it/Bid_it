"""Refund-country MINIMUM gate (Dir. 2008/9/EC Art. 17), enforced in national currency
with an admin override. A sub-year claim must reach €400 (or the fixed national amount:
SEK 4 000, DKK 3 000, …); a full-year claim €50 (SEK 500, DKK 400, …). Below the
minimum, set_status_code(code="2") HARD-BLOCKS submission (the invoices would otherwise
be filed, rejected, and locked out of the annual mop-up). An admin may override per
claim, recorded in status_note. Euro countries and Poland fall back to the EUR base on
vat_eur."""
import importlib

import pytest


def _modules(tmp_path, monkeypatch, country):
    """Reload + isolate the claim/customer/supplier DBs into tmp_path, with one invoice
    (BP/INV1) in the claim stream and its document attached — like test_claim_status,
    but parameterised on the REFUND COUNTRY so we can exercise SEK/DKK/PLN/EUR streams."""
    import customer_master, supplier_master, vat_refund
    importlib.reload(customer_master); importlib.reload(supplier_master)
    importlib.reload(vat_refund)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "s.db"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [("BP", "INV1")])
    monkeypatch.setattr(vat_refund, "docs_index", lambda con: {("Acme SIA", "BP", "INV1")})
    return customer_master, vat_refund


def _complete_checklist(cm, country):
    con = cm.connect()
    if not con.execute("SELECT 1 FROM customers WHERE code='ACME'").fetchone():
        con.close(); cm.add_customer("ACME", "Acme SIA", "LV"); con = cm.connect()
    con.execute("""UPDATE customers SET reg_number='LV123', vat_number='LV456',
                   legal_address='Riga 1', nace_code='49.41' WHERE code='ACME'"""); con.commit()
    cm.add_document(con, "ACME", "signed_contract", "c.pdf", b"C")
    cm.add_document(con, "ACME", "trade_registry", "t.pdf", b"T")
    con.execute("INSERT INTO customer_bank_accounts (customer, iban) VALUES ('ACME','LV99TESTIBAN')")
    con.commit()
    cm.add_country_document(con, "ACME", country, "power_of_attorney", "poa.pdf", b"P")
    con.close()


def _seed(vr, country, currency, periods):
    """Register supplier BP in `country` + invoice INV1, and seed one transaction per
    (period, vat_local, vat_eur) tuple. `periods` are MONTH periods (e.g. '2026-01') —
    _stream_vat aggregates the claim over the quarter's MONTHS, so a quarter claim is
    seeded by putting its VAT in one of its months. The transaction `note` is 'INV1' so
    the line resolves to that invoice."""
    import supplier_master
    sc = supplier_master.connect()
    sc.execute("INSERT INTO suppliers (code, legal_name) VALUES ('BP','B2Mobility GmbH')")
    sc.execute("""INSERT INTO supplier_vat_registrations (supplier, country, vat_number, source)
                  VALUES ('BP',?,'BE0123456789','registry')""", (country,))
    sc.execute("""INSERT INTO supplier_invoices (supplier, country, invoice_no, invoice_date)
                  VALUES ('BP',?,'INV1','2026-01-10')""", (country,))
    sc.commit(); sc.close()
    # the claim-level invoice_documents row (claim checklist: document attached)
    vc = vr.connect()
    vc.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename, sha256)
                  VALUES ('Acme SIA','BP','INV1','i.pdf','abc123')""")
    vc.commit(); vc.close()
    import sqlite3
    ac = sqlite3.connect(vr.ANALYTICS_DB)
    ac.execute("""CREATE TABLE IF NOT EXISTS transactions (
        entity TEXT, supplier TEXT, country TEXT, period TEXT, product_group TEXT,
        note TEXT, qty REAL, currency TEXT, net_local REAL, vat_local REAL,
        net_eur REAL, vat_eur REAL)""")
    for period, vat_local, vat_eur in periods:
        ac.execute("""INSERT INTO transactions
            (entity, supplier, country, period, product_group, note, qty, currency,
             net_local, vat_local, net_eur, vat_eur)
            VALUES ('Acme SIA','BP',?,?,'Diesel','INV1',500,?,?,?,?,?)""",
            (country, period, currency, vat_local * 4.762, vat_local,
             vat_eur * 4.762, vat_eur))
    ac.commit(); ac.close()


def _build(tmp_path, monkeypatch, country, currency, periods):
    cm, vr = _modules(tmp_path, monkeypatch, country)
    cm.add_customer("ACME", "Acme SIA", "LV")
    _complete_checklist(cm, country)
    _seed(vr, country, currency, periods)
    return cm, vr


def _status(con, vr, country, period):
    r = con.execute("""SELECT status, status_code, status_note FROM vat_applications
                       WHERE entity='Acme SIA' AND refund_country=? AND ref_period=?""",
                    (country, period)).fetchone()
    return r


# --------------------------------------------------------------- min_for selector
def test_min_for_returns_currency_amount_basis():
    import vat_config as VC
    # Sweden: fixed SEK in law -> local basis
    assert VC.min_for("Sweden", is_annual=False) == ("SEK", 4000, "local")
    assert VC.min_for("Sweden", is_annual=True) == ("SEK", 500, "local")
    # Denmark: fixed DKK
    assert VC.min_for("Denmark", is_annual=False) == ("DKK", 3000, "local")
    assert VC.min_for("Denmark", is_annual=True) == ("DKK", 400, "local")
    # Poland: NOT in the table -> EUR base on vat_eur
    assert VC.min_for("Poland", is_annual=False) == ("EUR", VC.MIN_QUARTER, "eur")
    assert VC.min_for("Poland", is_annual=True) == ("EUR", VC.MIN_ANNUAL, "eur")
    # Belgium (euro country): EUR base
    assert VC.min_for("Belgium", is_annual=False) == ("EUR", VC.MIN_QUARTER, "eur")
    assert VC.min_for("Belgium", is_annual=True) == ("EUR", VC.MIN_ANNUAL, "eur")


# --------------------------------------------------------------- Sweden (local SEK)
def test_sweden_below_quarterly_min_blocks_submit(tmp_path, monkeypatch):
    # SEK 3 120 < SEK 4 000 quarterly minimum -> BLOCKED, claim stays unsubmitted
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2026-01", 3120, 300)])
    con = vr.connect()
    below, why = vr.below_minimum(con, "Acme SIA", "Sweden", "2026-Q1")
    assert below and "SEK" in why and "4,000" in why
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2")
    assert not ok and msg.startswith("BLOCKED") and "SEK" in msg
    # nothing was written: no application row / no locked invoices
    assert _status(con, vr, "Sweden", "2026-Q1") is None
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 0
    con.close()


def test_sweden_at_or_above_quarterly_min_submits(tmp_path, monkeypatch):
    # SEK 4 000 == minimum (>=, not strictly above) -> submits and locks the invoice
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2026-01", 4000, 380)])
    con = vr.connect()
    below, _ = vr.below_minimum(con, "Acme SIA", "Sweden", "2026-Q1")
    assert not below
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2")
    assert ok, msg
    assert _status(con, vr, "Sweden", "2026-Q1")["status_code"] == "2"
    assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1
    con.close()


def test_submit_freezes_vat_local_on_claim_row(tmp_path, monkeypatch):
    """set_status freezes BOTH vat_eur AND vat_local onto vat_applications at first
    submission, on the locked claim_set basis. Before this fix vat_local stayed 0/null,
    so _stream_vat's FROZEN branch returned 0 for a locked SE/DK claim and below_minimum
    (local-currency basis) would misread. Assert the row carries the SEK base and that
    the frozen branch and below_minimum agree with the pre-lock aggregate."""
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2026-01", 4200, 400)])
    con = vr.connect()
    # pre-lock aggregate (what the verdict path reads before submission)
    pre_ve, pre_vl, _ = vr._stream_vat(con, "Acme SIA", "Sweden", "2026-Q1")
    assert pre_vl == 4200 and pre_ve == 400
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2")
    assert ok, msg
    # the frozen row carries the national-currency VAT (NOT 0/null) on the claim_set basis
    row = con.execute("""SELECT vat_eur, vat_local, status FROM vat_applications
                         WHERE entity='Acme SIA' AND refund_country='Sweden'
                         AND ref_period='2026-Q1'""").fetchone()
    assert row["vat_local"] == 4200 and row["vat_eur"] == 400
    # the now-LOCKED claim reads the frozen branch of _stream_vat -> same figures, not 0
    fz_ve, fz_vl, _ = vr._stream_vat(con, "Acme SIA", "Sweden", "2026-Q1")
    assert fz_vl == 4200 and fz_ve == 400
    assert fz_vl == pre_vl and fz_ve == pre_ve
    # below_minimum on the LOCKED claim reads the frozen vat_local: 4200 >= 4000 -> not below
    below, _ = vr.below_minimum(con, "Acme SIA", "Sweden", "2026-Q1")
    assert not below
    con.close()


def test_frozen_vat_local_below_min_reads_correctly(tmp_path, monkeypatch):
    """A claim FROZEN below the SEK 4,000 quarterly minimum (submitted via admin override)
    is correctly read as 'below' by below_minimum off the frozen vat_local — the frozen-
    branch path agrees with the pre-lock aggregate path."""
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2026-01", 3120, 300)])
    con = vr.connect()
    # pre-lock: below the SEK 4,000 quarterly minimum
    assert vr.below_minimum(con, "Acme SIA", "Sweden", "2026-Q1")[0]
    # admin override submits it anyway, freezing SEK 3 120 onto the row
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2",
                                 override_threshold=True)
    assert ok, msg
    row = con.execute("""SELECT vat_local FROM vat_applications WHERE entity='Acme SIA'
                         AND refund_country='Sweden' AND ref_period='2026-Q1'""").fetchone()
    assert row["vat_local"] == 3120          # frozen, not 0/null
    # the LOCKED claim still reads 'below' off the frozen vat_local (not 0)
    below, why = vr.below_minimum(con, "Acme SIA", "Sweden", "2026-Q1")
    assert below and "SEK" in why
    con.close()


def test_sweden_annual_uses_annual_min(tmp_path, monkeypatch):
    # A -YEAR period uses the SEK 500 annual minimum (NOT the SEK 4 000 quarterly one).
    # Use a PAST year (2025-YEAR has ended) so the minimum gate — not the period-end
    # gate — is what blocks. SEK 450 < 500 -> the threshold blocks at submit.
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2025-01", 450, 40)])
    con = vr.connect()
    below, why = vr.below_minimum(con, "Acme SIA", "Sweden", "2025-YEAR")
    assert below and "500" in why and "annual" in why
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2025-YEAR", "2")
    assert not ok and "SEK" in msg and "annual" in msg     # the MINIMUM block, not period-end
    con.close()


def test_sweden_annual_above_min_submits(tmp_path, monkeypatch):
    # SEK 600 >= the SEK 500 annual minimum -> the threshold does not block; the closed
    # 2025-YEAR period submits.
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2025-01", 600, 55)])
    con = vr.connect()
    assert not vr.below_minimum(con, "Acme SIA", "Sweden", "2025-YEAR")[0]
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2025-YEAR", "2")
    assert ok, msg
    con.close()


# --------------------------------------------------------------- EUR country (Belgium)
def test_belgium_below_eur_min_blocks_then_above_submits(tmp_path, monkeypatch):
    # Belgium has NO national amount -> EUR base on vat_eur. €380 < €400 -> blocked.
    cm, vr = _build(tmp_path, monkeypatch, "Belgium", "EUR",
                    [("2026-01", 380, 380)])
    con = vr.connect()
    below, why = vr.below_minimum(con, "Acme SIA", "Belgium", "2026-Q1")
    assert below and "EUR" in why and "400" in why
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert not ok and msg.startswith("BLOCKED")
    con.close()


def test_belgium_at_eur_min_submits(tmp_path, monkeypatch):
    cm, vr = _build(tmp_path, monkeypatch, "Belgium", "EUR",
                    [("2026-01", 400, 400)])
    con = vr.connect()
    assert not vr.below_minimum(con, "Acme SIA", "Belgium", "2026-Q1")[0]
    ok, msg = vr.set_status_code(con, "Acme SIA", "Belgium", "2026-Q1", "2")
    assert ok, msg
    con.close()


# --------------------------------------------------------------- Poland (EUR fallback)
def test_poland_falls_back_to_eur_base(tmp_path, monkeypatch):
    """Poland is NOT in NATIONAL_MINIMUMS, so the gate compares vat_eur to the €400 base
    — NOT a PLN fixed amount. Here vat_local (PLN) is large (1 600) but vat_eur is €380,
    so the claim is BELOW the EUR minimum and blocked (the PLN figure is irrelevant)."""
    cm, vr = _build(tmp_path, monkeypatch, "Poland", "PLN",
                    [("2026-01", 1600, 380)])
    con = vr.connect()
    below, why = vr.below_minimum(con, "Acme SIA", "Poland", "2026-Q1")
    assert below and "EUR" in why          # EUR basis, not PLN
    ok, msg = vr.set_status_code(con, "Acme SIA", "Poland", "2026-Q1", "2")
    assert not ok and msg.startswith("BLOCKED")
    con.close()


# --------------------------------------------------------------- admin override
def test_override_submits_below_minimum_and_records_note(tmp_path, monkeypatch):
    import audit
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2026-01", 3120, 300)])
    con = vr.connect()
    audit.set_actor(con, "alice")
    try:
        # without override -> blocked
        ok, _ = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2")
        assert not ok
        # with override -> submits, and the override is recorded in status_note
        ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2",
                                     override_threshold=True)
        assert ok, msg
        row = _status(con, vr, "Sweden", "2026-Q1")
        assert row["status_code"] == "2"
        assert "overridden by alice" in (row["status_note"] or "")
        assert con.execute("SELECT COUNT(*) FROM vat_claimed_invoices").fetchone()[0] == 1
    finally:
        audit.reset_actor(con)
    con.close()


def test_override_appends_to_existing_note(tmp_path, monkeypatch):
    cm, vr = _build(tmp_path, monkeypatch, "Sweden", "SEK",
                    [("2026-01", 3120, 300)])
    con = vr.connect()
    ok, msg = vr.set_status_code(con, "Acme SIA", "Sweden", "2026-Q1", "2",
                                 note="filed early per client request",
                                 override_threshold=True)
    assert ok, msg
    note = _status(con, vr, "Sweden", "2026-Q1")["status_note"]
    assert "filed early per client request" in note and "overridden by" in note
    con.close()


# --------------------------------------------------------------- route-level admin gate
def _spy_submit(monkeypatch):
    """Replace vat_refund.set_status_code with a spy that records the override_threshold
    it was called with, so we can assert the /vat route's admin-only gating without
    seeding a full claim. Returns a dict the test reads after the POST."""
    import vat_refund as VR
    seen = {}
    def spy(con, ent, ctry, per, code, note=None, deadline=None, override_threshold=False):
        seen["override"] = override_threshold
        seen["code"] = code
        return False, "BLOCKED — below the SEK 4,000 quarterly minimum"
    monkeypatch.setattr(VR, "set_status_code", spy)
    return seen


def test_route_admin_can_override(client, monkeypatch):
    """An admin's "override min" checkbox reaches set_status_code as
    override_threshold=True."""
    seen = _spy_submit(monkeypatch)
    body = client.get("/vat?year=2026").get_data(as_text=True)
    import re
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    r = client.post("/vat?year=2026", data={
        "_csrf": tok, "entity": "Acme SIA", "country": "Sweden",
        "ref_period": "2026-Q1", "status": "2", "override_threshold": "1"})
    assert r.status_code == 200
    assert seen.get("override") is True and seen.get("code") == "2"


def test_route_processor_cannot_override(admin_session, monkeypatch):
    """A processor's override_threshold=1 form field is DROPPED at the route, so
    set_status_code is called with override_threshold=False (the gate still blocks)."""
    import app as A, auth, re
    # a processor account that may operate the VAT module's submit action
    try:
        auth.add_user("vatproc_min", "Pw!23456", role="processor")
    except Exception:
        pass
    # the whole VAT module is admin-only -> a processor is 403 on /vat. Assert that:
    # the override can never reach the engine because the route itself is forbidden.
    c = A.app.test_client()
    assert c.post("/login", data={"username": "vatproc_min",
                                  "password": "Pw!23456"}).status_code == 302
    seen = _spy_submit(monkeypatch)
    # acquire a CSRF token from a page the processor CAN see (/queue renders a form), so
    # the POST passes the CSRF gate and reaches the ADMIN_ONLY check rather than 400ing.
    body = c.get("/queue").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    r = c.post("/vat?year=2026", data={
        "_csrf": tok,
        "entity": "Acme SIA", "country": "Sweden",
        "ref_period": "2026-Q1", "status": "2", "override_threshold": "1"})
    assert r.status_code == 403            # VAT module is admin-only
    assert "override" not in seen          # set_status_code never reached
