"""The 'Captured supplier — legal entity' panel must show the entity that ISSUED THIS
invoice — country-aware for a multi-entity supplier group (Eurowag issues through a
different legal entity per country), not the group's primary legal name.

Regression: a Eurowag Belgium invoice was showing the Czech primary entity
("W.A.G. Issuing Services, a.s.", CZ) instead of the Belgian issuer."""
import re
import app


def _text(draft):
    with app.app.test_request_context("/extract"):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", app._captured_entity_html(draft)))


def test_eurowag_be_invoice_shows_belgian_issuer():
    # multi-entity supplier: the BE invoice must show the Belgian issuing entity + BE VAT,
    # NOT the group's Czech primary entity.
    draft = {"supplier": "EUROWAG",
             "lines": [{"invoice_no": "BE1234567890", "country": "Belgium",
                        "net": 22071.55, "vat": 4635.03}]}
    t = _text(draft)
    assert "W.A.G. payment solutions BE BVBA" in t       # the Belgian issuer
    assert "BE0648861506" in t                            # its Belgian VAT
    assert "W.A.G. Issuing Services" not in t             # NOT the Czech primary entity
    assert "Issuing legal entity for Belgium" in t        # labelled as per-country


def test_e100_single_entity_shows_master_legal_name():
    # single-entity supplier: show the canonical legal name + the BE-matching registered VAT.
    draft = {"supplier": "E100", "supplier_vat": "BE0676647155",
             "lines": [{"invoice_no": "BE95489/5413791 #27", "country": "Belgium",
                        "net": 12297.12, "vat": 2582.39}]}
    t = _text(draft)
    assert "E100 International Trade sp. z o.o." in t
    assert "BE0676647155" in t
    assert "BE954895413791" not in t.replace(" ", "")     # no stray buyer/data leak


def test_diff_flag_fires_on_mismatched_vat():
    # if the capture read a VAT that is NOT the registered one for the country, flag it.
    draft = {"supplier": "E100", "supplier_vat": "LV43603043473",   # the buyer's id (a mis-read)
             "lines": [{"invoice_no": "X #1", "country": "Belgium",
                        "net": 100.0, "vat": 21.0}]}
    t = _text(draft)
    assert "Invoice differs" in t
    assert "LV43603043473" in t and "BE0676647155" in t


def test_no_false_flag_when_vat_matches():
    draft = {"supplier": "E100", "supplier_vat": "BE0676647155",
             "lines": [{"invoice_no": "X #1", "country": "Belgium",
                        "net": 100.0, "vat": 21.0}]}
    assert "Invoice differs" not in _text(draft)
