"""
Register-failure reconcile (D4 split-brain): invoice_control.unregistered_vaulted_documents().

On statement confirm the source PDFs are vaulted IN-REQUEST (invoice_documents in
vat_claims.db) but the registry write is ENQUEUED (kind='register' -> suppliers.db).
If that register job fails / is held / is lost, the documents are vaulted but the
invoices are NEVER registered. This read-only sweep surfaces those orphans.

The orphan signal MUST be checked against `statement_invoices` (the COMPLETE registry,
written for every line incl. vat=0), NOT `supplier_invoices` (only the vat>0 subset is
auto-synced there) — otherwise every legitimately-vaulted vat=0 invoice false-positives.
"""
import pytest

import invoice_control


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Point supplier_master (statement_invoices) and vat_refund (invoice_documents)
    at throwaway DBs with fresh schemas."""
    import supplier_master, vat_refund
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "suppliers.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "vat_claims.db"))
    # point the legacy-migration source at a non-existent tmp DB so
    # _migrate_from_analytics is a no-op (otherwise a fresh vat_claims.db seeds
    # invoice_documents from the real demo fuel_history.db). Same pattern as the
    # other vat_refund tests (e.g. test_claim_db / test_document_vault).
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "fh.db"))
    vat_refund._SCHEMA_READY.clear()
    return supplier_master, vat_refund


def _register_line(sm, supplier, invoice_no, vat):
    """Write ONE statement_invoices row directly (the complete registry)."""
    con = sm.connect()
    con.execute("INSERT OR REPLACE INTO statement_invoices VALUES (?,?,?,?,?,?,?,?,?)",
                (supplier, "S-1", invoice_no, "2026-05-31", "Belgium", "EUR",
                 100.0, vat, 100.0 + vat))
    con.commit(); con.close()


def _vault_doc(vr, entity, supplier, invoice_ref, filename="orig.pdf"):
    """Write ONE invoice_documents row directly (a vaulted source PDF)."""
    con = vr.connect()
    # sha256 keyed off filename so two distinct files for the SAME invoice are two
    # rows (the UNIQUE constraint is on entity,supplier,invoice_ref,sha256).
    con.execute("""INSERT INTO invoice_documents
                   (entity, supplier, invoice_ref, filename, stored_path, sha256, size)
                   VALUES (?,?,?,?,?,?,?)""",
                (entity, supplier, invoice_ref, filename,
                 f"/vault/{filename}", f"{invoice_ref}-{filename}-hash", 10))
    con.commit(); con.close()


def test_registered_doc_not_flagged(stores):
    """A vaulted doc whose (supplier, invoice_ref) IS in statement_invoices -> NOT flagged."""
    sm, vr = stores
    _register_line(sm, "DKV", "BE001", vat=210.0)
    _vault_doc(vr, "OUR ENTITY", "DKV", "BE001")
    assert invoice_control.unregistered_vaulted_documents() == []


def test_orphan_doc_flagged(stores):
    """A vaulted doc with NO statement_invoices row -> flagged (the orphan)."""
    sm, vr = stores
    _vault_doc(vr, "OUR ENTITY", "DKV", "BE999")
    out = invoice_control.unregistered_vaulted_documents()
    assert len(out) == 1
    o = out[0]
    assert o["supplier"] == "DKV"
    assert o["invoice_ref"] == "BE999"
    assert o["entity"] == "OUR ENTITY"
    assert o["n_docs"] == 1


def test_vat_zero_registered_in_statement_invoices_not_flagged(stores):
    """A vat=0 invoice registered in statement_invoices (but NOT in supplier_invoices)
    -> NOT flagged. Proves the basis is statement_invoices, not supplier_invoices."""
    sm, vr = stores
    _register_line(sm, "DKV", "BE000", vat=0.0)   # vat=0 -> never auto-synced to supplier_invoices
    _vault_doc(vr, "OUR ENTITY", "DKV", "BE000")
    # sanity: supplier_invoices has NO row for this invoice
    con = sm.connect()
    n = con.execute("SELECT COUNT(*) FROM supplier_invoices WHERE invoice_no='BE000'").fetchone()[0]
    con.close()
    assert n == 0, "fixture invalid: vat=0 line should not be in supplier_invoices"
    # but it IS in statement_invoices, so the vaulted doc is NOT an orphan
    assert invoice_control.unregistered_vaulted_documents() == []


def test_multiple_docs_same_invoice_count(stores):
    """Two vaulted docs for the same orphan (supplier, invoice_ref) collapse to one
    row with n_docs=2."""
    sm, vr = stores
    _vault_doc(vr, "OUR ENTITY", "DKV", "BE777", filename="a.pdf")
    _vault_doc(vr, "OUR ENTITY", "DKV", "BE777", filename="b.pdf")
    out = invoice_control.unregistered_vaulted_documents()
    assert len(out) == 1
    assert out[0]["invoice_ref"] == "BE777"
    assert out[0]["n_docs"] == 2


def test_empty_stores_returns_empty(stores):
    """Empty stores -> [] (never raises)."""
    assert invoice_control.unregistered_vaulted_documents() == []
