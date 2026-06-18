"""PER-COUNTRY ACTUAL SUPPLIER ENTITY — the issuing/supplying legal entity name on the
legal VAT claim is per (supplier, country), not one name for the whole supplier.

Today suppliers.legal_name is ONE name; the per-country VAT NUMBER already varies. This
slice adds supplier_vat_registrations.entity_name so e.g. EUROWAG -> "W.A.G. Deutschland
GmbH" in DE while another country falls back to the supplier default legal_name.

What this proves:
  * get_issuer returns the per-country entity_name when set, the supplier legal_name when
    not (per country), and is byte-identical to before when no entity_name is set anywhere.
  * set_vat_registration upserts entity_name; a 'capture' source does NOT overwrite a
    'manual' one but SEEDS an empty slot; a VAT-only write preserves an existing name.
  * the /extract/confirm capture-seed populates entity_name for the countries whose draft
    lines carried a line-specific supplier_name, and does NOT clobber a pre-set manual one.
  * a vat_refund.invoice_lines build shows the per-country entity as the claim issuer.

DB isolation is per-test (conftest redirects FFS_DATA_DIR to a fresh copy of the demo DBs).
"""
import json
import os
import re

import pytest

import supplier_master as SM


def _csrf(client, path="/extract"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def _seed_country(con, supplier, country, vat=None, source="manual", entity=None):
    con.execute(
        "INSERT OR REPLACE INTO supplier_vat_registrations "
        "(supplier, country, vat_number, source, entity_name, tenant_id) "
        "VALUES (?,?,?,?,?, 'default')",
        (supplier, country, vat, source, entity))
    con.commit()


# ── get_issuer precedence ──────────────────────────────────────────────────────────

def test_get_issuer_falls_back_to_legal_name_when_no_entity():
    # Q8 ships with per-country rows but NO entity_name -> the supplier legal_name shows.
    name, vat, src = SM.get_issuer("Q8", "Germany")
    con = SM.connect()
    legal = con.execute("SELECT legal_name FROM suppliers WHERE code='Q8'").fetchone()[0]
    con.close()
    assert name == legal
    assert legal != ""  # the demo supplier has a legal name


def test_get_issuer_uses_per_country_entity_name_when_set():
    con = SM.connect()
    _seed_country(con, "Q8", "Germany", entity="W.A.G. Deutschland GmbH")
    con.close()
    de_name, _, _ = SM.get_issuer("Q8", "Germany")
    fr_name, _, _ = SM.get_issuer("Q8", "France")   # France has no entity_name set
    con = SM.connect()
    legal = con.execute("SELECT legal_name FROM suppliers WHERE code='Q8'").fetchone()[0]
    con.close()
    assert de_name == "W.A.G. Deutschland GmbH"     # per-country wins
    assert fr_name == legal                          # other country falls back


def test_get_issuer_byte_identical_when_no_entity_anywhere():
    # With NO entity_name set on any row, every country returns exactly today's name.
    con = SM.connect()
    rows = con.execute(
        "SELECT supplier, country FROM supplier_vat_registrations").fetchall()
    for r in rows:
        name, _, _ = SM.get_issuer(r["supplier"], r["country"], con=con)
        legal = con.execute("SELECT legal_name FROM suppliers WHERE code=?",
                             (r["supplier"],)).fetchone()
        expected = legal[0] if legal else r["supplier"]
        assert name == expected
    con.close()


def test_get_issuer_blank_entity_name_falls_back():
    con = SM.connect()
    _seed_country(con, "Q8", "Spain", entity="   ")   # whitespace -> treated as unset
    con.close()
    name, _, _ = SM.get_issuer("Q8", "Spain")
    con = SM.connect()
    legal = con.execute("SELECT legal_name FROM suppliers WHERE code='Q8'").fetchone()[0]
    con.close()
    assert name == legal


# ── the setter (precedence manual > capture) ───────────────────────────────────────

def test_setter_upserts_entity_name():
    SM.set_vat_registration("Q8", "France", None, source="manual",
                            entity_name="W.A.G. France SAS")
    name, _, _ = SM.get_issuer("Q8", "France")
    assert name == "W.A.G. France SAS"


def test_capture_does_not_overwrite_manual_entity():
    SM.set_vat_registration("Q8", "Germany", None, source="manual",
                            entity_name="W.A.G. Deutschland GmbH")
    # a capture write must NOT clobber the admin/manual name OR downgrade the source
    SM.set_vat_registration("Q8", "Germany", None, source="capture",
                            entity_name="Some Captured GmbH")
    name, _, _ = SM.get_issuer("Q8", "Germany")
    assert name == "W.A.G. Deutschland GmbH"
    con = SM.connect()
    row = con.execute("SELECT source, entity_name FROM supplier_vat_registrations "
                      "WHERE supplier='Q8' AND country='Germany'").fetchone()
    con.close()
    assert row["entity_name"] == "W.A.G. Deutschland GmbH"
    assert row["source"] == "manual"      # source not downgraded to 'capture'


def test_capture_seeds_empty_slot():
    # Austria ships with a row but NO entity_name -> a capture write SEEDS it.
    SM.set_vat_registration("Q8", "Austria", None, source="capture",
                            entity_name="W.A.G. Austria GmbH")
    name, _, _ = SM.get_issuer("Q8", "Austria")
    assert name == "W.A.G. Austria GmbH"


def test_vat_only_write_preserves_existing_entity():
    SM.set_vat_registration("Q8", "Germany", None, source="manual",
                            entity_name="W.A.G. Deutschland GmbH")
    # a later VAT-number-only write (entity_name omitted) must keep the entity name
    SM.set_vat_registration("Q8", "Germany", "DE811234567", source="manual")
    name, vat, _ = SM.get_issuer("Q8", "Germany")
    assert name == "W.A.G. Deutschland GmbH"
    assert vat == "DE811234567"


# ── the /extract/confirm capture-seed ──────────────────────────────────────────────

def _draft(supplier, lines):
    return {"supplier": supplier, "period": "2026-05", "statement_date": "2026-05-31",
            "lines": lines}


def _stash_draft(token, draft):
    import app as A
    d = os.path.join(A.WORKDIR, ".extract_tmp")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, token + ".draft.json"), "w") as f:
        json.dump(draft, f)


def _confirm_form(client, supplier, lines, token):
    n = len(lines)
    form = {"_csrf": _csrf(client), "token": token, "nlines": str(n),
            "supplier": supplier, "period": "2026-05", "stmt_ref": "ENT-TEST-1",
            "stmt_date": "2026-05-31"}
    for i, ln in enumerate(lines):
        form[f"inv_{i}"] = ln["invoice_no"]
        form[f"date_{i}"] = ln.get("date", "2026-05-31")
        form[f"ctry_{i}"] = ln.get("country", "")
        form[f"ccy_{i}"] = ln.get("currency", "EUR")
        form[f"net_{i}"] = str(ln.get("net", 100))
        form[f"vat_{i}"] = str(ln.get("vat", 19))
    return form


def test_confirm_seed_populates_per_country_entity(client):
    token = "enttok_seed"
    lines = [
        {"invoice_no": "DE-001", "country": "Germany", "net": 100, "vat": 19,
         "supplier_name": "W.A.G. Deutschland GmbH", "supplier_vat": "DE811234567",
         "supplier_is_line_specific": True},
        {"invoice_no": "PL-001", "country": "Poland", "net": 200, "vat": 46,
         "supplier_name": "W.A.G. Polska sp. z o.o.", "supplier_vat": "PL5252445795",
         "supplier_is_line_specific": True},
        # a non-line-specific line must NOT seed an entity
        {"invoice_no": "BE-001", "country": "Belgium", "net": 50, "vat": 10.5,
         "supplier_name": "Header Supplier NV", "supplier_is_line_specific": False},
    ]
    _stash_draft(token, _draft("Q8", lines))
    r = client.post("/extract/confirm", data=_confirm_form(client, "Q8", lines, token))
    assert r.status_code == 200

    con = SM.connect()
    de = con.execute("SELECT entity_name, source, vat_number FROM supplier_vat_registrations "
                     "WHERE supplier='Q8' AND country='Germany'").fetchone()
    pl = con.execute("SELECT entity_name, source, vat_number FROM supplier_vat_registrations "
                     "WHERE supplier='Q8' AND country='Poland'").fetchone()
    be = con.execute("SELECT entity_name FROM supplier_vat_registrations "
                     "WHERE supplier='Q8' AND country='Belgium'").fetchone()
    con.close()
    assert de["entity_name"] == "W.A.G. Deutschland GmbH"
    assert de["source"] == "capture"
    assert de["vat_number"] == "DE811234567"
    assert pl["entity_name"] == "W.A.G. Polska sp. z o.o."
    # Belgium's line was not line-specific -> no entity seeded (stays NULL or absent)
    assert be is None or not (be["entity_name"] or "").strip()


def test_confirm_seed_does_not_clobber_manual_entity(client):
    # Pre-set a MANUAL entity for Germany; a confirmed capture must NOT overwrite it.
    SM.set_vat_registration("Q8", "Germany", "DE-ADMIN-VAT", source="manual",
                            entity_name="Admin Set GmbH")
    token = "enttok_noclobber"
    lines = [
        {"invoice_no": "DE-002", "country": "Germany", "net": 100, "vat": 19,
         "supplier_name": "Captured Other GmbH", "supplier_vat": "DE999999999",
         "supplier_is_line_specific": True},
    ]
    _stash_draft(token, _draft("Q8", lines))
    r = client.post("/extract/confirm", data=_confirm_form(client, "Q8", lines, token))
    assert r.status_code == 200

    con = SM.connect()
    de = con.execute("SELECT entity_name, source, vat_number FROM supplier_vat_registrations "
                     "WHERE supplier='Q8' AND country='Germany'").fetchone()
    con.close()
    assert de["entity_name"] == "Admin Set GmbH"     # manual wins
    assert de["source"] == "manual"
    assert de["vat_number"] == "DE-ADMIN-VAT"         # admin VAT untouched


# ── the per-country entity surfaces on the claim ───────────────────────────────────

def test_invoice_lines_issuer_is_per_country_entity():
    import vat_refund as VR
    # Find a real (entity, country, period) from the demo claim matrix, set the supplier's
    # per-country entity_name, and prove invoice_lines emits it as the line `issuer`.
    con = VR.connect()
    matrix = VR.claim_matrix(con, "2026", with_portal=False)
    # Pick a claim that actually yields invoice lines (has a supplier we can name).
    target = supplier = None
    for m in matrix:
        if m["period"].endswith("YEAR"):
            continue
        lines = VR.invoice_lines(con, m["entity"], m["country"], m["period"])
        if lines:
            target, supplier = m, lines[0]["supplier"]
            break
    con.close()
    assert target is not None, "demo claim matrix yields at least one invoice line"

    scon = SM.connect()
    _seed_country(scon, supplier, target["country"], entity="LOCAL ENTITY FOR CLAIM SA")
    scon.close()

    con = VR.connect()
    lines = VR.invoice_lines(con, target["entity"], target["country"], target["period"])
    con.close()
    issuers = {l["issuer"] for l in lines if l["supplier"] == supplier}
    assert "LOCAL ENTITY FOR CLAIM SA" in issuers
