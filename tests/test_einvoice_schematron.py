"""
EN 16931 / PEPPOL BIS Billing 3.0 SCHEMATRON validation (einvoice_validate.py).

These tests run OFFLINE against the VENDORED official schematrons (schematron/*.sch) + the
ISO XSLT2 skeleton, using the pip-installed `saxonche` (a REAL XSLT 2.0 engine). They are
the HEADLINE PROOF that the sales-invoicing UBL is submission-grade:

  - a complete DOMESTIC 21% issued invoice passes the OFFICIAL CEN + PEPPOL schematrons with
    ZERO errors (the error count is asserted == 0 and printed);
  - a REVERSE-CHARGE invoice and a CREDIT NOTE (381, a UBL CreditNote) also pass with 0 errors;
  - an invoice missing the buyer country FAILS the expected address rule (BR-11), proving the
    validator is really running the rules (not a false pass);
  - the validator NEVER raises and reports `available: False` cleanly when saxonche is absent;
  - the structured cac:PostalAddress (CityName / PostalZone / Country) is present.

The saxonche-dependent tests are skip-guarded so the suite still passes on a CI runner that
lacks the wheel; the fail-soft test runs WITHOUT saxonche.
"""
import os
import sys

import pytest

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import invoicing          # noqa: E402
import einvoice_validate  # noqa: E402


@pytest.fixture()
def inv(tmp_path, monkeypatch):
    """Repoint invoicing.DB at a temp file + reset the schema-ready cache + a LV issuer."""
    monkeypatch.setattr(invoicing, "DB", str(tmp_path / "invoicing.db"), raising=True)
    monkeypatch.setattr(invoicing, "_SCHEMA_READY", set(), raising=True)
    invoicing.set_issuer(dict(
        name="Demo Latvia SIA", address="Brivibas iela 1",
        vat_number="LV40003012345", reg_no="40003012345",
        iban="LV80BANK0000435195001", bank="Swedbank",
        city="Riga", postal_code="LV-1010", country_code="LV",
        series="INV", credit_series="KR",
        number_format="{series}-{year}-{seq:06d}", payment_terms_days="14"))
    return invoicing


def _domestic_invoice(inv):
    cust, err = inv.add_customer(
        "Klients SIA", vat_number="LV40103012346", reg_no="40103012346",
        address="Liela iela 5", city="Daugavpils", postal_code="LV-5400",
        country_code="LV", email="ar@klients.lv")
    assert not err, err
    draft, err = inv.create_draft(cust["id"], currency="EUR", reverse_charge=False)
    assert not err, err
    inv.add_line(draft["id"], description="Consulting", quantity=10, unit="h",
                 unit_price_net=100, vat_rate=0.21)
    inv.issue(draft["id"], issued_by="tester")
    return draft["id"], cust


def _reverse_charge_invoice(inv):
    cust, err = inv.add_customer(
        "Kunde GmbH", vat_number="DE123456789", reg_no="HRB1",
        address="Hauptstr 2", city="Berlin", postal_code="10115", country_code="DE")
    assert not err, err
    draft, err = inv.create_draft(cust["id"], currency="EUR", reverse_charge=True)
    assert not err, err
    inv.add_line(draft["id"], description="Cross-border service", quantity=5, unit="h",
                 unit_price_net=200, vat_rate=0.0)
    inv.issue(draft["id"], issued_by="tester")
    return draft["id"]


def _err_summary(res):
    return [(e["schematron"], e["rule"], e["text"][:80]) for e in res["errors"]]


# ============================================================ structured address (always)
def test_structured_postal_address_present(inv):
    """The structured cac:PostalAddress (CityName / PostalZone / Country) is emitted for
    BOTH parties — no saxonche needed (a pure XML-shape assertion)."""
    iid, _ = _domestic_invoice(inv)
    xml = invoicing.einvoice_xml(iid).decode("utf-8")
    assert "<cbc:CityName>Riga</cbc:CityName>" in xml
    assert "<cbc:CityName>Daugavpils</cbc:CityName>" in xml
    assert "<cbc:PostalZone>LV-1010</cbc:PostalZone>" in xml
    assert "<cbc:PostalZone>LV-5400</cbc:PostalZone>" in xml
    assert "<cbc:IdentificationCode>LV</cbc:IdentificationCode>" in xml
    # PEPPOL electronic address (EndpointID) + a buyer reference must be present too.
    assert 'schemeID="9930"' in xml
    assert "<cbc:EndpointID" in xml
    assert "<cbc:BuyerReference>" in xml


# ============================================================ fail-soft (no saxonche)
def test_validate_fail_soft_without_saxonche(inv, monkeypatch):
    """If saxonche cannot be imported the validator reports available=False, ok=None — never
    raises, never a false pass. We force the import to fail and clear the compiled cache."""
    monkeypatch.setattr(einvoice_validate, "_compiled", None, raising=False)
    monkeypatch.setattr(einvoice_validate, "_processor", None, raising=False)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def fake_import(name, *a, **k):
        if name == "saxonche" or name.startswith("saxonche."):
            raise ImportError("saxonche blocked for the test")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    iid, _ = _domestic_invoice(inv)
    res = invoicing.validate_einvoice(iid)
    assert res["available"] is False
    assert res["ok"] is None
    assert res["errors"] == [] and res["warnings"] == []
    assert "saxonche" in res["message"].lower()


# ============================================================ HEADLINE: official schematrons
def test_domestic_21pct_passes_official_schematrons(inv):
    """HEADLINE PROOF: a complete domestic-21% issued invoice passes the OFFICIAL CEN +
    PEPPOL schematrons with ZERO errors."""
    pytest.importorskip("saxonche")
    iid, _ = _domestic_invoice(inv)
    res = invoicing.validate_einvoice(iid)
    assert res["available"] is True, res
    print("DOMESTIC 21%% — errors:", len(res["errors"]), "warnings:", len(res["warnings"]))
    assert res["ok"] is True, _err_summary(res)
    assert len(res["errors"]) == 0, _err_summary(res)


def test_reverse_charge_passes_official_schematrons(inv):
    """A reverse-charge (AE) cross-border invoice passes with 0 errors."""
    pytest.importorskip("saxonche")
    iid = _reverse_charge_invoice(inv)
    res = invoicing.validate_einvoice(iid)
    assert res["available"] is True, res
    print("REVERSE CHARGE — errors:", len(res["errors"]), "warnings:", len(res["warnings"]))
    assert res["ok"] is True, _err_summary(res)
    assert len(res["errors"]) == 0, _err_summary(res)


def test_credit_note_381_passes_official_schematrons(inv):
    """A CREDIT NOTE (381, a UBL CreditNote document) passes with 0 errors."""
    pytest.importorskip("saxonche")
    orig_id, _ = _domestic_invoice(inv)
    cn, err = invoicing.create_credit_note(orig_id, created_by="tester")
    assert not err, err
    invoicing.issue(cn["id"], issued_by="tester")
    xml = invoicing.einvoice_xml(cn["id"]).decode("utf-8")
    # It MUST be a UBL CreditNote with CreditNoteTypeCode 381 (NOT an Invoice with code 381).
    assert "<CreditNote" in xml
    assert "<cbc:CreditNoteTypeCode>381</cbc:CreditNoteTypeCode>" in xml
    assert "<cac:CreditNoteLine>" in xml
    res = invoicing.validate_einvoice(cn["id"])
    assert res["available"] is True, res
    print("CREDIT NOTE 381 — errors:", len(res["errors"]), "warnings:", len(res["warnings"]))
    assert res["ok"] is True, _err_summary(res)
    assert len(res["errors"]) == 0, _err_summary(res)


def test_missing_country_fails_address_rule(inv):
    """NEGATIVE: an invoice whose buyer has NO country (and no derivable VAT prefix) FAILS
    the expected address rule (BR-11, buyer country code) — proving the rules really run."""
    pytest.importorskip("saxonche")
    cust, err = inv.add_customer("NoCountry SIA", vat_number="999999",
                                 reg_no="x", address="X street")
    assert not err, err
    draft, err = inv.create_draft(cust["id"], currency="EUR", reverse_charge=False)
    assert not err, err
    inv.add_line(draft["id"], description="Item", quantity=1, unit="pcs",
                 unit_price_net=50, vat_rate=0.21)
    inv.issue(draft["id"], issued_by="tester")
    res = invoicing.validate_einvoice(draft["id"])
    assert res["available"] is True
    assert res["ok"] is False
    rules = {e["rule"] for e in res["errors"]}
    assert "BR-11" in rules, _err_summary(res)


def test_validate_draft_returns_buildable_message(inv):
    """validate_einvoice on a DRAFT (no legal number) reports a clear message, not a pass."""
    cust, _ = inv.add_customer("X SIA", vat_number="LV40103012346", country_code="LV",
                               city="Riga", address="A 1")
    draft, _ = inv.create_draft(cust["id"], currency="EUR")
    inv.add_line(draft["id"], description="Item", quantity=1, unit_price_net=10, vat_rate=0.21)
    res = invoicing.validate_einvoice(draft["id"])
    assert res["ok"] is None
    assert res["errors"] == []
