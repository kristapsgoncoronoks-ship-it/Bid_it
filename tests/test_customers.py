"""Tests for VAT-refund customer onboarding: activation gating, documents, and
the fee model (% of refunded VAT floored at a per-declaration minimum)."""
import importlib

import pytest


@pytest.fixture()
def cd(tmp_path, monkeypatch):
    import customer_db
    importlib.reload(customer_db)
    monkeypatch.setattr(customer_db, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_db, "DOCDIR", str(tmp_path / "docs"))
    return customer_db


def test_new_customer_is_pending_with_incomplete_checklist(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    assert cd.is_active("ACME") is False
    con = cd.connect()
    items, ready = cd.activation_checklist(con, "ACME")
    con.close()
    assert ready is False
    assert [lbl for lbl, _ in items] == ["Trade registry extract",
                                         "Bank account (IBAN) on file", "Signed contract"]


def test_activation_requires_docs_and_bank(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    # cannot activate yet
    _i, ready = cd.activation_checklist(con, "ACME")
    assert ready is False
    con.execute("INSERT INTO customer_bank_accounts (customer,iban,bank,currency,purpose) "
                "VALUES ('ACME','LV80BANK0001','MyBank','EUR','refund payout')")
    con.commit()
    cd.add_document(con, "ACME", "trade_registry", "reg.pdf", b"REG")
    cd.add_document(con, "ACME", "signed_contract", "contract.pdf", b"CONTRACT")
    _i, ready = cd.activation_checklist(con, "ACME")
    assert ready is True
    cd.set_activation(con, "ACME", True)
    con.close()
    assert cd.is_active("ACME") is True


def test_fee_priority_percent_then_minimum(cd):
    # 15% of 1000 = 150 (above the 50 minimum) -> percent
    assert cd.compute_fee(1000, 15, 50) == (150.0, "percent")
    # 15% of 100 = 15 (below 50) -> minimum charged
    assert cd.compute_fee(100, 15, 50) == (50.0, "minimum")
    # nothing refunded -> minimum still applies
    assert cd.compute_fee(0, 15, 50) == (50.0, "minimum")
    # no fee configured
    assert cd.compute_fee(1000, 0, 0) == (0.0, "percent")


def test_untracked_customer_not_gated(cd):
    assert cd.is_active("NOT-A-CUSTOMER") is None


def test_per_country_fee_override(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    cd.set_fee(con, "ACME", 15, 50)                       # default
    cd.set_country_fee(con, "ACME", "Belgium", 10, 100)   # Belgium override
    con.close()
    assert cd.fee_for("ACME", "Belgium") == (10.0, 100.0)  # override
    assert cd.fee_for("ACME", "Poland") == (15.0, 50.0)    # default
    assert cd.fee_for("ACME") == (15.0, 50.0)              # no country -> default


def test_per_country_activation_flow(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    # no country started -> not gated
    assert cd.country_active("ACME", "Belgium") is None
    cd.request_country(con, "ACME", "Belgium")
    assert cd.country_active("ACME", "Belgium") is False        # requested, not active
    _i, ready = cd.country_doc_checklist(con, "ACME", "Belgium")
    assert ready is False                                       # POA not received
    cd.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"POA")
    _i, ready = cd.country_doc_checklist(con, "ACME", "Belgium")
    assert ready is True
    cd.activate_country(con, "ACME", "Belgium", True)
    con.close()
    assert cd.country_active("ACME", "Belgium") is True
    # country docs are isolated from the customer-level onboarding checklist
    con = cd.connect()
    _i, cust_ready = cd.activation_checklist(con, "ACME")
    con.close()
    assert cust_ready is False


def test_country_gate_blocks_until_activated(tmp_path, monkeypatch):
    import customer_db
    import vat_refund
    monkeypatch.setattr(customer_db, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_db, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_db, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])
    customer_db.add_customer("ACME", "Acme SIA", "LV")
    con = customer_db.connect()
    customer_db.set_activation(con, "ACME", True)          # customer active
    customer_db.request_country(con, "ACME", "Belgium")    # country requested, NOT active
    con.close()
    vc = vat_refund.connect()
    vc.execute("CREATE TABLE transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    vc.execute("INSERT INTO transactions VALUES ('ACME','Belgium','2026-04',1000)"); vc.commit()
    ok, msg = vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    assert ok is False and "country 'Belgium' is not activated" in msg
    # receive POA + activate -> submission now passes the country gate
    con = customer_db.connect()
    customer_db.add_country_document(con, "ACME", "Belgium", "power_of_attorney", "poa.pdf", b"POA")
    customer_db.activate_country(con, "ACME", "Belgium", True)
    con.close()
    ok, msg = vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    assert ok, msg
    vc.close()


def test_fee_frozen_on_submission(tmp_path, monkeypatch):
    import customer_db
    import vat_refund
    monkeypatch.setattr(customer_db, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(customer_db, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "stream_invoices", lambda *a, **k: [])  # skip lock/doc checks
    # active customer, Belgium override 10% / min 100
    customer_db.add_customer("ACME", "Acme SIA", "LV")
    con = customer_db.connect()
    customer_db.set_country_fee(con, "ACME", "Belgium", 10, 100)
    customer_db.set_activation(con, "ACME", True)
    con.close()
    vc = vat_refund.connect()
    vc.execute("CREATE TABLE transactions (entity TEXT, country TEXT, period TEXT, vat_eur REAL)")
    vc.execute("INSERT INTO transactions VALUES ('ACME','Belgium','2026-04',2000)")
    vc.commit()
    ok, msg = vat_refund.set_status(vc, "ACME", "Belgium", "2026-Q2", "submitted")
    assert ok, msg
    snap = vc.execute("SELECT fee_eur, fee_pct, vat_eur FROM vat_applications "
                      "WHERE entity='ACME'").fetchone()
    assert snap["fee_eur"] == 200.0 and snap["fee_pct"] == 10.0   # 10% of 2000
    vc.close()
    # changing the fee afterwards must NOT change the frozen claim
    con = customer_db.connect()
    customer_db.set_country_fee(con, "ACME", "Belgium", 99, 9999)
    con.close()
    rows, _summ = vat_refund.recovery_report("2026")
    frozen = next(r for r in rows if r["entity"] == "ACME")
    assert frozen["fee_eur"] == 200.0          # locked, not re-priced at 99%


def test_set_status_blocks_pending_customer(tmp_path, monkeypatch):
    import customer_db
    import vat_refund
    monkeypatch.setattr(customer_db, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_db, "_SCHEMA_READY", set())
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat.db"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    customer_db.add_customer("BLK", "Blocked UAB", "LT")   # pending
    con = vat_refund.connect()
    ok, msg = vat_refund.set_status(con, "BLK", "Belgium", "2026-Q2", "submitted")
    con.close()
    assert ok is False and "not activated" in msg


def test_customers_page_and_recovery_fee_render(client):
    h = client.get("/customers").get_data(as_text=True)
    assert "Onboard a new VAT-refund customer" in h
    assert "Activation checklist" in h
    rec = client.get("/recovery").get_data(as_text=True)
    assert "Our fee" in rec and "Fee basis" in rec
