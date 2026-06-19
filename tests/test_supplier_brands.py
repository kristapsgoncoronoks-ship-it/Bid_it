"""
BRAND→LEGAL-ENTITY aliases: the supplier LEGAL ENTITY is canonical; brands read off
invoices are explicit links to it so intake recognises them.

Covers:
  * supplier_master add/remove/list brands; normalized code_for_brand match
    (case/punctuation/spacing-insensitive); all_brand_map.
  * app._resolve_supplier_code consults the alias map (a taught brand resolves to the
    linked legal-entity code; an unknown brand keeps the old None/fuzzy behavior).
  * an explicit brand alias BEATS fuzzy group/legal containment.
  * tenant scoping (a brand stamped under one tenant isn't seen under another).
  * the review render leads with the LEGAL ENTITY when a brand resolves.

Per-test DB isolation comes from the conftest FFS_DATA_DIR redirect (suppliers.db is a
per-test copy of the demo DB, which carries DKV/BP/EUROWAG/TFC/etc.).
"""
import re

import app
import supplier_master as SM
import tenancy


def _csrf(client, path="/suppliers"):
    body = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


# ───────────────────────────────────────── supplier_master brand API
def test_add_list_remove_brand():
    assert SM.add_brand("DKV", "DKV Mobility Card", actor="tester") is True
    assert SM.add_brand("DKV", "Shell Roaming", actor="tester") is True
    assert SM.brands_for("DKV") == ["DKV Mobility Card", "Shell Roaming"]
    # idempotent on the NORMALIZED key: re-adding the same brand with different casing/
    # spacing does not create a second row (it refreshes the display text in place).
    assert SM.add_brand("DKV", "DKV  Mobility   CARD", actor="tester") is True
    assert len(SM.brands_for("DKV")) == 2
    assert SM.code_for_brand("dkv mobility card") == "DKV"
    assert SM.remove_brand("DKV", "Shell Roaming") is True
    assert len(SM.brands_for("DKV")) == 1
    # removing an unlinked brand reports False
    assert SM.remove_brand("DKV", "Not Linked") is False


def test_add_brand_rejects_blanks():
    assert SM.add_brand("", "Shell") is False
    assert SM.add_brand("DKV", "") is False
    assert SM.add_brand("DKV", "   ") is False


def test_code_for_brand_is_normalized():
    SM.add_brand("MOEVE", "Cepsa", actor="tester")
    # case / punctuation / spacing all normalize to the same key
    assert SM.code_for_brand("Cepsa") == "MOEVE"
    assert SM.code_for_brand("CEPSA") == "MOEVE"
    assert SM.code_for_brand("  cepsa.  ") == "MOEVE"
    assert SM.code_for_brand("c e p s a") is None     # interior spacing differs -> no match
    assert SM.code_for_brand("Unlinked Brand") is None
    assert SM.code_for_brand("") is None


def test_all_brand_map():
    SM.add_brand("DKV", "Shell", actor="tester")
    SM.add_brand("MOEVE", "Moeve Pro", actor="tester")
    m = SM.all_brand_map()
    assert m.get("shell") == "DKV"
    assert m.get("moeve pro") == "MOEVE"


# ───────────────────────────────────────── app._resolve_supplier_code consults aliases
def test_resolve_uses_brand_alias():
    # "Shell" is not any supplier's name -> would be None without an alias
    assert app._resolve_supplier_code("Shell") is None
    SM.add_brand("DKV", "Shell", actor="tester")
    assert app._resolve_supplier_code("Shell") == "DKV"
    assert app._resolve_supplier_code("SHELL") == "DKV"       # normalized
    assert app._resolve_supplier_code("shell.") == "DKV"
    # an unknown brand still resolves to None
    assert app._resolve_supplier_code("Some Brand-New Fuel Card") is None


def test_brand_alias_beats_fuzzy_containment():
    # "Moya" is fuzzily contained in TFC's group_name ("Moya Energy") today.
    assert app._resolve_supplier_code("Moya") == "TFC"
    # An EXPLICIT brand link to a DIFFERENT supplier must WIN over the fuzzy match.
    SM.add_brand("BP", "Moya", actor="tester")
    assert app._resolve_supplier_code("Moya") == "BP"


def test_resolve_vat_still_wins_over_brand():
    # VAT is the strongest tier and must beat a brand alias pointing elsewhere.
    # MOEVE has VAT ESA25009192 in the demo DB.
    SM.add_brand("DKV", "Moeve", actor="tester")
    assert app._resolve_supplier_code("Moeve", "ESA25009192") == "MOEVE"


# ───────────────────────────────────────── tenant scoping
def test_brand_tenant_scoped(monkeypatch):
    # Bind a non-default tenant, add a brand, then confirm the default tenant can't see it.
    monkeypatch.setattr(tenancy, "queue_tenant", lambda: "tenant_b")
    assert SM.add_brand("DKV", "TenantBOnly", actor="tester") is True

    def _scope_for(tid):
        return (" AND tenant_id=?", [tid])

    # reading as tenant_b sees it
    monkeypatch.setattr(tenancy, "scope_clause", lambda column="tenant_id": _scope_for("tenant_b"))
    assert SM.code_for_brand("TenantBOnly") == "DKV"
    # reading as default does NOT
    monkeypatch.setattr(tenancy, "scope_clause", lambda column="tenant_id": _scope_for("default"))
    assert SM.code_for_brand("TenantBOnly") is None
    assert "TenantBOnly" not in SM.brands_for("DKV")


# ───────────────────────────────────────── review render leads with the legal entity
def test_review_renders_legal_entity_for_resolved_brand():
    SM.add_brand("DKV", "Shell", actor="tester")
    draft = {"supplier": "Shell", "supplier_vat": "",
             "statement_ref": "INV1", "statement_date": "2026-05-31"}
    with app.app.test_request_context("/extract"):
        html = app._read_first_notice(draft, "2026-05")
    # the resolved CODE is prefilled for confirm
    assert draft["supplier"] == "DKV"
    # the LEGAL ENTITY is the prominent label; the brand is secondary context
    assert "DKV Euro Service GmbH" in html
    assert "read from invoice as" in html
    assert "Shell" in html


def test_review_unrecognised_brand_guides_to_link():
    draft = {"supplier": "Circle K", "supplier_vat": "",
             "statement_ref": "INV2", "statement_date": "2026-05-31"}
    with app.app.test_request_context("/extract"):
        html = app._read_first_notice(draft, "2026-05")
    # left unmatched, and the banner tells the user to link it as a brand
    assert "UNMATCHED" in html
    assert "link it as a brand" in html.lower() or "brand" in html.lower()
    assert "/suppliers" in html


# ───────────────────────────────────────── /suppliers POST brand management (web)
def test_suppliers_post_add_and_remove_brand(client):
    r = client.post("/suppliers", data={"_csrf": _csrf(client), "__act": "add_brand",
                                        "code": "DKV", "brand": "Shell"})
    assert r.status_code == 200
    assert "Linked brand" in r.get_data(as_text=True)
    assert SM.brands_for("DKV") == ["Shell"]
    # the brand chip renders on the page (GET)
    page = client.get("/suppliers").get_data(as_text=True)
    assert "Shell" in page
    r2 = client.post("/suppliers", data={"_csrf": _csrf(client), "__act": "remove_brand",
                                         "code": "DKV", "brand": "Shell"})
    assert "Removed brand" in r2.get_data(as_text=True)
    assert SM.brands_for("DKV") == []
