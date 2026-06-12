"""Document mining: extract VAT numbers from vaulted docs and propose fills for the
INPUT gaps in master data."""
import importlib

import pytest


@pytest.fixture()
def dm(tmp_path, monkeypatch):
    import vat_refund, supplier_master, customer_master, doc_mining
    for m in (supplier_master, customer_master, vat_refund, doc_mining):
        importlib.reload(m)
    docdir = tmp_path / "documents"; docdir.mkdir()
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "fh.db"))
    monkeypatch.setattr(vat_refund, "DOCDIR", str(docdir))
    monkeypatch.setattr(supplier_master, "DB", str(tmp_path / "sup.db"))
    supplier_master._SCHEMA_READY.clear()
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))

    # a vaulted XML invoice carrying a Swedish VAT number
    xml = b"<Invoice><CompanyID>SE502044770101</CompanyID><ID>INV1</ID></Invoice>"
    (docdir / "dkv.xml").write_bytes(xml)
    con = vat_refund.connect()
    con.execute("""INSERT INTO invoice_documents (entity, supplier, invoice_ref, filename,
                   stored_path, sha256, size) VALUES ('JUPITER','DKV','INV1','dkv.xml',?, 'x', ?)""",
                (str(docdir / "dkv.xml"), len(xml)))
    con.commit(); con.close()
    return vat_refund, supplier_master, customer_master, doc_mining


def test_proposes_missing_supplier_vat(dm):
    _vr, SM, _cm, DM = dm
    # a registration row exists for DKV/Sweden but the number is INPUT
    con = SM.connect()
    con.execute("INSERT INTO supplier_vat_registrations (supplier,country,vat_number,source)"
                " VALUES ('DKV','Sweden','INPUT','to capture')")
    con.commit(); con.close()

    props = DM.proposals()
    sup = [p for p in props if p["kind"] == "supplier"]
    assert any(p["supplier"] == "DKV" and p["country"] == "Sweden"
               and p["value"] == "SE502044770101" for p in sup)


def test_apply_supplier_vat_fills_master(dm):
    _vr, SM, _cm, DM = dm
    DM.apply_supplier_vat("DKV", "Sweden", "SE502044770101")
    regs = {(r["supplier"], r["country"]): r["vat_number"] for r in SM.vat_registrations()}
    assert regs[("DKV", "Sweden")] == "SE502044770101"


def test_proposes_customer_vat(dm):
    _vr, _sm, CM, DM = dm
    CM.add_customer("JUPITER", "Jupiter Plus AS", "Sweden")   # vat defaults to INPUT
    props = DM.proposals()
    cust = [p for p in props if p["kind"] == "customer"]
    assert any(p["value"] == "SE502044770101" for p in cust)


def test_no_proposal_when_field_already_set(dm):
    _vr, SM, _cm, DM = dm
    con = SM.connect()
    con.execute("INSERT INTO supplier_vat_registrations (supplier,country,vat_number,source)"
                " VALUES ('DKV','Sweden','SE999999999999','already known')")
    con.commit(); con.close()
    assert all(not (p["kind"] == "supplier" and p["country"] == "Sweden")
               for p in DM.proposals())


def test_vat_extraction_validates_shape():
    import doc_mining as DM
    found = DM.extract_vat_numbers("good SE502044770101 BE0676647155 bad SE12 noise")
    assert found == {"SE502044770101", "BE0676647155"}
