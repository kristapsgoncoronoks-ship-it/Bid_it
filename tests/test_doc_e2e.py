"""WO6 — end-to-end integration over the SHIPPED sample prepared forms.

This drives the WHOLE Document Management flow against the REAL committed templates in
templates_samples/ (poa_generic.docx, engagement_contract.docx), proving they are valid
OOXML, that WO2's run-merge fills a placeholder Word split across formatting runs, and that
the WO4 cross-module effect (a received PoA satisfies the country activation checklist
WITHOUT auto-activating) holds when driven through the real lifecycle:

  * upload poa_generic.docx as a template -> create a power_of_attorney request bound to it
    -> generate -> the draft auto-vaults (generated_sha256 set, status 'generated') and the
    generated docx XML has NO leftover {{...}} (the SPLIT {{company_name}} placeholder filled).
  * advance generated -> sent_for_signature -> signed -> received (with a signed original)
    -> country_doc_checklist / country_ready_to_activate now True, country_active still
    False (only activate_country flips it).
  * engagement_contract.docx fills every fee/bank/supplier-account field with no leftover.

Each sample ships at least one deliberately split placeholder ({{ / company_name / }} in
three separate <w:r><w:t> runs); reading the filled XML back is what proves the run-merge
works end-to-end through the genuine form.
"""
import importlib
import os
import re
import zipfile
import io

import pytest

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "templates_samples")
REFUND_COUNTRY = "Belgium"        # has a TAX_AUTHORITY entry, so {{tax_authority}} is non-empty


def _cm(tmp_path, monkeypatch):
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "customers.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "cdocs"))
    return customer_master


def _seed_customer(cm):
    """Seed a customer with EVERYTHING the two sample forms need: identity + signatory,
    a refund country requested, a fee, a refund-payout bank account, a supplier account."""
    cm.add_customer("ACME", "Acme Transport SIA", "LV")
    con = cm.connect()
    con.execute("""UPDATE customers SET reg_number='LV40003123456',
                   vat_number='LV40003123456', legal_address='Brivibas iela 1, Riga, LV-1010',
                   nace_code='49.41', signatory_name='Janis Berzins',
                   signatory_title='Member of the Board' WHERE code='ACME'""")
    con.commit()
    cm.set_fee(con, "ACME", 12, 250)
    con.execute("""INSERT INTO customer_bank_accounts (customer, iban, swift, bank, currency, purpose)
                   VALUES ('ACME','LV80BANK0000435195001','HABALV22','Swedbank AS','EUR','refund payout')""")
    con.execute("""INSERT INTO customer_supplier_accounts (customer, supplier, account_no)
                   VALUES ('ACME','BP','BP-DE-99887766')""")
    con.commit()
    cm.set_payout_route(con, "ACME", "us")
    cm.request_country(con, "ACME", REFUND_COUNTRY)
    return con


def _upload_sample(cm, con, filename, kind):
    with open(os.path.join(SAMPLES, filename), "rb") as fh:
        body = fh.read()
    # sanity: the shipped sample really IS a valid OOXML zip with a split placeholder
    z = zipfile.ZipFile(io.BytesIO(body))
    xml = z.read("word/document.xml").decode("utf-8")
    assert "<w:document" in xml and "<w:body>" in xml
    assert '<w:t xml:space="preserve">{{</w:t>' in xml, \
        f"{filename} must ship a deliberately split placeholder for WO2's run-merge"
    return cm.add_template(con, filename.rsplit(".", 1)[0], kind, filename, body)


def _xml_of_vault_doc(cm, con, doc_id):
    """Read back the bytes of a vaulted customer_documents row and return its document.xml."""
    row = con.execute("SELECT stored_path FROM customer_documents WHERE id=?",
                      (doc_id,)).fetchone()
    with open(row["stored_path"], "rb") as fh:
        data = fh.read()
    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")


def test_poa_generic_e2e_through_real_form(tmp_path, monkeypatch):
    cm = _cm(tmp_path, monkeypatch)
    con = _seed_customer(cm)
    tid = _upload_sample(cm, con, "poa_generic.docx", "power_of_attorney")

    # before: country has no PoA -> not ready, not active
    _items, ready = cm.country_doc_checklist(con, "ACME", REFUND_COUNTRY)
    assert ready is False
    assert cm.country_ready_to_activate(con, "ACME", REFUND_COUNTRY) is False

    rid = cm.create_document_request(con, "ACME", "power_of_attorney", tid,
                                     country=REFUND_COUNTRY)
    filled, out_name, ext = cm.generate_request_document(con, rid)
    assert filled and out_name

    # the draft auto-vaulted with a recorded hash and status 'generated'
    r = cm.get_document_request(con, rid)
    assert r["status"] == "generated"
    assert r["generated_sha256"] and r["generated_doc_id"]

    # soffice is broken in this sandbox -> .docx fallback; if a real PDF came back just
    # assert no error + a vaulted doc, otherwise read the docx XML and prove no leftovers.
    if ext == "docx":
        xml = _xml_of_vault_doc(cm, con, r["generated_doc_id"])
        leftovers = re.findall(r"\{\{\w+\}\}", xml)
        assert leftovers == [], f"unfilled placeholders survived the run-merge: {leftovers}"
        # the SPLIT {{company_name}} was filled with the seeded value
        assert "Acme Transport SIA" in xml
        assert "{{" not in xml and "}}" not in xml
        # a per-country field (tax authority for Belgium) was merged too
        assert "Federale Overheidsdienst" in xml
    else:
        assert ext == "pdf"

    # advance generated -> sent_for_signature -> signed
    for s in ("sent_for_signature", "signed"):
        ok, msg = cm.advance_document_request(con, rid, s)
        assert ok, msg
    # received WITH the signed original -> vaults under kind 'power_of_attorney' for the country
    ok, msg = cm.advance_document_request(con, rid, "received",
                                          signed_file=b"%PDF-1.4 WET-SIGNED POA bytes",
                                          signed_filename="signed_poa.pdf")
    assert ok, msg
    r = cm.get_document_request(con, rid)
    assert r["status"] == "received" and r["signed_doc_id"]

    # CROSS-MODULE EFFECT: the received PoA satisfies the country checklist...
    _items, ready = cm.country_doc_checklist(con, "ACME", REFUND_COUNTRY)
    assert ready is True
    assert cm.country_ready_to_activate(con, "ACME", REFUND_COUNTRY) is True
    # ...but NOT auto-activated — the country stays inactive until an admin clicks activate.
    assert cm.country_active("ACME", REFUND_COUNTRY) is False
    cm.activate_country(con, "ACME", REFUND_COUNTRY, True)
    assert cm.country_active("ACME", REFUND_COUNTRY) is True
    con.close()


def test_engagement_contract_e2e_fills_all_fields(tmp_path, monkeypatch):
    cm = _cm(tmp_path, monkeypatch)
    con = _seed_customer(cm)
    tid = _upload_sample(cm, con, "engagement_contract.docx", "contract")

    rid = cm.create_document_request(con, "ACME", "contract", tid)
    filled, out_name, ext = cm.generate_request_document(con, rid)
    assert filled and out_name
    r = cm.get_document_request(con, rid)
    assert r["status"] == "generated" and r["generated_sha256"] and r["generated_doc_id"]

    if ext == "docx":
        xml = _xml_of_vault_doc(cm, con, r["generated_doc_id"])
        leftovers = re.findall(r"\{\{\w+\}\}", xml)
        assert leftovers == [], f"unfilled placeholders survived: {leftovers}"
        assert "{{" not in xml and "}}" not in xml
        # the seeded fee / bank / supplier-account data was merged in
        assert "Acme Transport SIA" in xml
        assert "12%" in xml                         # fee_pct_fmt
        assert "LV80BANK0000435195001" in xml       # bank_iban
        assert "Swedbank AS" in xml                 # bank_name
        assert "BP: BP-DE-99887766" in xml          # supplier_accounts line
    else:
        assert ext == "pdf"
    con.close()
