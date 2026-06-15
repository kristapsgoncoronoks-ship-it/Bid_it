"""
Re-sync of supplier_invoices.gross_total on statement re-registration.

invoice_control.register_statement auto-syncs VAT-bearing statement lines into the
supplier_invoices registry. Originally that was insert-once: a CORRECTED statement
re-registered with a different net/vat left the existing gross_total STALE (drift).

The fix: an existing auto-synced row (notes start with "auto-synced from statement")
is UPDATED to the current line; a manually-curated row (any other note) is preserved.
These tests pin both halves plus the brand-new insert.
"""
import importlib

import pytest

import invoice_control


@pytest.fixture()
def suppliers_db(tmp_path, monkeypatch):
    """Point supplier_master at a throwaway suppliers.db with a fresh schema."""
    import supplier_master
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    # register_statement resolves a customer via customer_master; point it at a temp DB
    # so it never touches the demo customers.db (we pass customer= explicitly anyway).
    import customer_master
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    customer_master._SCHEMA_READY.clear()
    return supplier_master


def _gross(con, supplier, inv_no):
    r = con.execute("""SELECT gross_total, notes, country, period, currency
                       FROM supplier_invoices WHERE supplier=? AND invoice_no=?""",
                    (supplier, inv_no)).fetchone()
    return r


def test_resync_corrects_stale_gross_total(suppliers_db):
    """A corrected statement line re-syncs gross_total (and the re-synced fields)."""
    sm = suppliers_db
    # initial registration: inv X net=100 vat=8 -> gross 108
    n = invoice_control.register_statement(
        "DKV", "S-RESYNC", "2026-05", "2026-05-31",
        [("X", "2026-05-31", "Germany", "EUR", 100.0, 8.0)], customer="OUR ENTITY")
    assert n == 1
    con = sm.connect()
    row = _gross(con, "DKV", "X")
    con.close()
    assert row["gross_total"] == 108.0
    assert row["notes"].startswith("auto-synced from statement")

    # re-register the SAME invoice with a CORRECTION: net=120 vat=9 -> gross 129,
    # and a changed country/currency, under a new statement ref.
    n2 = invoice_control.register_statement(
        "DKV", "S-RESYNC-V2", "2026-06", "2026-06-30",
        [("X", "2026-06-30", "France", "USD", 120.0, 9.0)], customer="OUR ENTITY")
    assert n2 == 1, "re-sync should count toward the synced return value"
    con = sm.connect()
    row = _gross(con, "DKV", "X")
    # exactly one row still (PK supplier+invoice_no, UPDATE not duplicate INSERT)
    cnt = con.execute("SELECT COUNT(*) FROM supplier_invoices WHERE invoice_no='X'").fetchone()[0]
    con.close()
    assert cnt == 1
    assert row["gross_total"] == 129.0, "gross_total drifted — not re-synced (the bug)"
    assert row["country"] == "France"
    assert row["period"] == "2026-06"
    assert row["currency"] == "USD"
    assert row["notes"] == "auto-synced from statement S-RESYNC-V2"


def test_manual_row_is_preserved(suppliers_db):
    """A human-curated supplier_invoices row is NOT overwritten by a re-register."""
    sm = suppliers_db
    con = sm.connect()
    con.execute("""INSERT INTO supplier_invoices
                   (supplier, country, invoice_no, invoice_date, period,
                    currency, gross_total, notes) VALUES (?,?,?,?,?,?,?,?)""",
                ("DKV", "Germany", "Y", "2026-05-31", "2026-05", "EUR", 500.0,
                 "manual entry"))
    con.commit(); con.close()

    # register a statement line for Y with a DIFFERENT gross — must not touch the row.
    n = invoice_control.register_statement(
        "DKV", "S-MANUAL", "2026-05", "2026-05-31",
        [("Y", "2026-05-31", "France", "USD", 1000.0, 200.0)], customer="OUR ENTITY")
    assert n == 0, "a manual row must not count as synced (left untouched)"
    con = sm.connect()
    row = _gross(con, "DKV", "Y")
    con.close()
    assert row["gross_total"] == 500.0, "manual row was clobbered"
    assert row["notes"] == "manual entry"
    assert row["country"] == "Germany"
    assert row["currency"] == "EUR"


def test_new_insert_counts(suppliers_db):
    """A brand-new VAT-bearing line is inserted and counted in synced."""
    sm = suppliers_db
    n = invoice_control.register_statement(
        "DKV", "S-NEW", "2026-05", "2026-05-31",
        [("Z", "2026-05-31", "Belgium", "EUR", 1000.0, 210.0),
         ("Z0", "2026-05-31", "Belgium", "EUR", 1000.0, 0.0)],  # vat=0 -> not synced
        customer="OUR ENTITY")
    assert n == 1, "only the VAT-bearing line is auto-synced"
    con = sm.connect()
    row = _gross(con, "DKV", "Z")
    none_row = _gross(con, "DKV", "Z0")
    con.close()
    assert row is not None and row["gross_total"] == 1210.0
    assert none_row is None, "a vat=0 line must not be auto-synced"
