"""
VISION CAPTURE — the FULL LEGAL ENTITY supplier header (registration number + IBAN/bank,
in addition to name/vat/address/country) read into the capture document and mapped onto the
review draft so the auto supplier-master maintenance (supplier_sync) has the real entity.
"""
import vision_capture as VC


def _raw():
    return {
        "header": {
            "supplier": {"name": "Aral Tankstelle GmbH", "vat_number": "DE811128135",
                         "registration_number": "HRB 12345",
                         "address": "Wittener Str. 45, 44789 Bochum", "country": "Germany",
                         "iban": "DE89 3704 0044 0532 0130 00", "bank_name": "Commerzbank"},
            "customer": {"name": "Acme", "vat_number": None, "account_or_card_no": None},
            "invoice": {"number": "INV-9", "issue_date": "2026-05-31", "due_date": None,
                        "currency": "EUR", "exchange_rate": None},
        },
        "lines": [{"date": "2026-05-12", "product": "Diesel", "quantity": 100.0,
                   "net": 150.0, "vat": 28.5, "country": "Germany"}],
        "totals": {"net_total": 150.0, "vat_total": 28.5, "gross_total": 178.5},
    }


def test_parse_capture_reads_reg_no_and_iban():
    cap = VC.parse_capture(_raw())
    sup = cap["header"]["supplier"]
    assert sup["registration_number"] == "HRB 12345"
    assert sup["iban"] == "DE89 3704 0044 0532 0130 00"
    assert sup["bank_name"] == "Commerzbank"
    assert sup["name"] == "Aral Tankstelle GmbH"
    assert sup["vat_number"] == "DE811128135"


def test_to_draft_maps_legal_entity_fields():
    draft = VC.to_draft(VC.parse_capture(_raw()))
    assert draft["supplier"] == "Aral Tankstelle GmbH"
    assert draft["supplier_vat"] == "DE811128135"
    assert draft["supplier_reg_no"] == "HRB 12345"
    assert draft["supplier_address"] == "Wittener Str. 45, 44789 Bochum"
    assert draft["supplier_country"] == "Germany"
    assert draft["supplier_iban"] == "DE89 3704 0044 0532 0130 00"
    assert draft["supplier_bank"] == "Commerzbank"


def test_strict_null_when_not_printed():
    raw = _raw()
    raw["header"]["supplier"]["registration_number"] = None
    raw["header"]["supplier"]["iban"] = None
    draft = VC.to_draft(VC.parse_capture(raw))
    assert draft["supplier_reg_no"] is None
    assert draft["supplier_iban"] is None
    # never invents a value
    assert draft["supplier_bank"] == "Commerzbank"
