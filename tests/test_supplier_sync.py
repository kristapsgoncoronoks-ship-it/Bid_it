"""
AUTO SUPPLIER-MASTER MAINTENANCE from captured invoices (supplier_sync.py).

Load-bearing invariants asserted here:
  * plan() diffs correctly — a NEW supplier; a SAFE-field (legal name / address) change; a
    bank/IBAN or VAT change classified HIGH-RISK (never safe).
  * apply() NEW — creates a PROVISIONAL supplier with full legal details + per-country VAT
    registration + the IBAN, ONLY when verified / high-confidence; refuses when not verified.
  * apply() EXISTING — a SAFE address change auto-applies (audited); a changed IBAN does NOT
    touch supplier_bank_accounts and instead creates a pending supplier_change_requests row;
    a changed VAT number likewise becomes pending and does NOT overwrite the registration.
  * the admin approve flow applies a pending change; reject discards it (touches nothing).
  * the whole thing NEVER raises and NEVER blocks confirm on failure.

Per-test DB isolation comes from the conftest FFS_DATA_DIR redirect (the suppliers.db
copy is per-test). vision_capture's new capture fields are exercised in
test_vision_capture_legal_entity.
"""
import re

import vision_capture
import supplier_master as SM
import supplier_sync as SS


# ───────────────────────────────────────────────── seeded master (per-test suppliers.db)
def _seed():
    con = SM.connect()
    SM.seed(con)
    con.close()


def _existing(code):
    return SS.load_existing(code)


# ───────────────────────────────────────────────────────────────────────── plan()
def test_plan_new_supplier():
    p = SS.plan({"legal_name": "Foo Energy GmbH", "country": "Germany",
                 "vat": "DE123456789", "iban": "DE89 3704 0044 0532 0130 00"}, None)
    assert p["new"] is True
    assert p["code"] is None
    assert p["safe_updates"] == {}
    assert p["high_risk_changes"] == {}


def test_apply_existing_learns_per_country_entity():
    # 'Learn the entity from invoices': a captured Belgian seller for a multi-country group
    # (Eurowag, home CZ) SEEDS the Belgium per-country entity + VAT so it lands on the VAT
    # claim — and does NOT overwrite the group PRIMARY legal_name, nor queue a spurious VAT
    # change from the per-country VAT.
    _seed()
    cap = {"legal_name": "W.A.G. payment solutions BE BVBA", "country": "Belgium",
           "vat": "BE0648861506"}
    res = SS.apply(cap, "EUROWAG", actor="t", verified=True, invoice_ref="BE1")
    assert res.get("entity_country") == "Belgium"
    assert res.get("pending") == []                       # no bogus VAT 'change' queued
    assert "legal_name" not in (res.get("updated") or [])  # group primary not churned
    # get_issuer for Belgium now returns the BELGIAN entity + VAT (was the Czech fallback)
    name, vat, _ = SM.get_issuer("EUROWAG", "Belgium")
    assert name == "W.A.G. payment solutions BE BVBA"
    assert vat == "BE0648861506"
    # the group PRIMARY legal_name is unchanged
    con = SM.connect()
    prim = con.execute("SELECT legal_name FROM suppliers WHERE code='EUROWAG'").fetchone()[0]
    con.close()
    assert prim == "W.A.G. Issuing Services, a.s."


def test_plan_safe_field_change():
    _seed()
    ex = _existing("TFC")
    assert ex is not None
    cap = {"legal_name": "TFC by Moya — Belgium S.A.", "address": "New HQ, Antwerp",
           "vat": ex.get("vat"), "iban": (ex.get("ibans") or [None])[0]}
    p = SS.plan(cap, ex)
    assert p["new"] is False and p["code"] == "TFC"
    assert "legal_name" in p["safe_updates"]
    assert "address" in p["safe_updates"]
    # the VAT/IBAN matched the stored ones -> no high-risk change
    assert p["high_risk_changes"] == {}


def test_plan_classifies_bank_and_vat_high_risk():
    _seed()
    ex = _existing("E100")
    assert ex is not None
    cap = {"legal_name": ex.get("legal_name"),       # unchanged safe field
           "vat": "BE9999999999",                    # CHANGED VAT
           "iban": "LT000000000000000000"}           # IBAN not on file
    p = SS.plan(cap, ex)
    assert p["safe_updates"] == {}                   # nothing safe changed
    assert "vat" in p["high_risk_changes"]
    assert "iban" in p["high_risk_changes"]
    # high-risk fields are NEVER misclassified as safe
    assert "vat" not in p["safe_updates"]
    assert "iban" not in p["safe_updates"]


def test_plan_classifies_company_reg_as_identity_anchor():
    # registration number is a STABLE IDENTITY anchor (like VAT) — a change is HIGH-RISK
    # (admin confirms), never an auto-applied SAFE update.
    _seed()
    ex = _existing("E100")
    assert ex is not None
    cap = {"legal_name": ex.get("legal_name"), "reg_no": "NEW-REG-12345"}
    p = SS.plan(cap, ex)
    assert "company_reg" in p["high_risk_changes"], "reg number change must be high-risk"
    assert "company_reg" not in p["safe_updates"]


def test_plan_empty_capture_never_blanks():
    _seed()
    ex = _existing("TFC")
    # a capture that read no legal name / address must not propose blanking the stored values
    p = SS.plan({"legal_name": None, "address": ""}, ex)
    assert p["safe_updates"] == {}
    assert p["high_risk_changes"] == {}


# ───────────────────────────────────────────────────────────────────── apply() NEW
def _captured_new():
    return {"legal_name": "Aral Tankstelle GmbH", "reg_no": "HRB 12345",
            "address": "Wittener Str. 45, 44789 Bochum", "country": "Germany",
            "vat": "DE811128135", "iban": "DE89370400440532013000", "bank": "Commerzbank"}


def test_apply_new_creates_provisional_when_verified():
    _seed()
    res = SS.apply(_captured_new(), None, actor="tester", verified=True,
                   invoice_ref="INV-1")
    assert "created" in res
    code = res["created"]
    con = SM.connect()
    row = con.execute("SELECT * FROM suppliers WHERE code=?", (code,)).fetchone()
    assert row is not None
    assert row["status"] == "provisional"
    assert row["legal_name"] == "Aral Tankstelle GmbH"
    assert row["company_reg"] == "HRB 12345"
    assert (row["home_country"] or "").upper() == "DE"
    # per-country VAT registration created with the captured VAT + entity name (source capture)
    vr = con.execute("SELECT vat_number, entity_name, source FROM supplier_vat_registrations "
                     "WHERE supplier=? AND country='DE'", (code,)).fetchone()
    assert vr is not None and vr["vat_number"] == "DE811128135"
    assert vr["entity_name"] == "Aral Tankstelle GmbH"
    # the captured IBAN landed in supplier_bank_accounts (a NEW supplier may carry it)
    ba = con.execute("SELECT iban FROM supplier_bank_accounts WHERE supplier=?",
                     (code,)).fetchone()
    assert ba is not None and SS.norm_iban(ba["iban"]) == "DE89370400440532013000"
    con.close()


def test_apply_new_high_confidence_also_creates():
    _seed()
    res = SS.apply(_captured_new(), None, actor="tester", verified=False,
                   confidence="high", invoice_ref="INV-2")
    assert "created" in res


def test_apply_new_refused_when_not_verified():
    _seed()
    res = SS.apply(_captured_new(), None, actor="tester", verified=False,
                   confidence="low", invoice_ref="INV-3")
    assert "created" not in res
    assert "skipped" in res
    # nothing was created
    con = SM.connect()
    n = con.execute("SELECT COUNT(*) c FROM suppliers WHERE legal_name=?",
                    ("Aral Tankstelle GmbH",)).fetchone()["c"]
    con.close()
    assert n == 0


# ─────────────────────────────────────────────────────────────── apply() EXISTING
def test_apply_existing_safe_address_autoapplies():
    _seed()
    cap = {"legal_name": SM.connect().execute(
                "SELECT legal_name FROM suppliers WHERE code='TFC'").fetchone()["legal_name"],
           "address": "Brand-new registered address, Brussels"}
    res = SS.apply(cap, "TFC", actor="tester", verified=True, invoice_ref="INV-A")
    assert "address" in res["updated"]
    assert res["pending"] == []
    con = SM.connect()
    addr = con.execute("SELECT address FROM suppliers WHERE code='TFC'").fetchone()["address"]
    con.close()
    assert addr == "Brand-new registered address, Brussels"


def test_apply_existing_safe_not_applied_when_unverified():
    _seed()
    before = SM.connect().execute(
        "SELECT address FROM suppliers WHERE code='TFC'").fetchone()["address"]
    cap = {"address": "Should not be applied"}
    res = SS.apply(cap, "TFC", actor="tester", verified=False, confidence="low")
    assert res["updated"] == []
    after = SM.connect().execute(
        "SELECT address FROM suppliers WHERE code='TFC'").fetchone()["address"]
    assert after == before


def test_apply_existing_iban_change_is_pending_not_applied():
    _seed()
    ex = _existing("E100")
    stored_ibans_before = set(SS.norm_iban(x) for x in (ex.get("ibans") or []))
    cap = {"iban": "LT999999999999999999"}     # not on file
    res = SS.apply(cap, "E100", actor="tester", verified=True, invoice_ref="INV-IB")
    assert "iban" in res["pending"]
    assert res["updated"] == []
    con = SM.connect()
    # the stored bank accounts are UNCHANGED — the new IBAN was NOT inserted
    after = set(SS.norm_iban(r["iban"]) for r in con.execute(
        "SELECT iban FROM supplier_bank_accounts WHERE supplier='E100'"))
    assert after == stored_ibans_before
    assert SS.norm_iban("LT999999999999999999") not in after
    # a pending change request row exists
    cr = con.execute("SELECT * FROM supplier_change_requests "
                     "WHERE supplier='E100' AND field='iban' AND status='pending'").fetchone()
    con.close()
    assert cr is not None
    assert cr["source"] == "capture"
    assert cr["invoice_ref"] == "INV-IB"


def test_apply_existing_vat_change_is_pending_not_overwritten():
    _seed()
    ex = _existing("E100")
    old_vat = ex.get("vat")
    cap = {"vat": "BE0000000000"}              # changed VAT
    res = SS.apply(cap, "E100", actor="tester", verified=True, invoice_ref="INV-VAT")
    assert "vat" in res["pending"]
    con = SM.connect()
    # the home-country VAT registration is UNCHANGED
    home = con.execute("SELECT home_country FROM suppliers WHERE code='E100'").fetchone()["home_country"]
    vr = con.execute("SELECT vat_number FROM supplier_vat_registrations "
                     "WHERE supplier='E100' AND country=?", (home,)).fetchone()
    # home_country for E100 is 'PL' (no registration there) — assert no capture clobbered the
    # known BE registration either
    be = con.execute("SELECT vat_number FROM supplier_vat_registrations "
                     "WHERE supplier='E100' AND country='Belgium'").fetchone()
    cr = con.execute("SELECT * FROM supplier_change_requests "
                     "WHERE supplier='E100' AND field='vat' AND status='pending'").fetchone()
    con.close()
    assert cr is not None
    assert be is not None and be["vat_number"] == "BE0676647155"   # untouched


# ─────────────────────────────────────────────────────── admin approve / reject flow
def test_approve_change_applies_iban():
    _seed()
    SS.apply({"iban": "LT123412341234123412"}, "E100", actor="t", verified=True,
             invoice_ref="INV-X")
    pend = SS.pending_changes("pending")
    assert pend and pend[0]["field"] == "iban"
    rid = pend[0]["id"]
    res = SS.approve_change(rid, actor="admin")
    assert res.get("applied") == "iban"
    con = SM.connect()
    ibans = set(SS.norm_iban(r["iban"]) for r in con.execute(
        "SELECT iban FROM supplier_bank_accounts WHERE supplier='E100'"))
    status = con.execute("SELECT status FROM supplier_change_requests WHERE id=?",
                         (rid,)).fetchone()["status"]
    con.close()
    assert SS.norm_iban("LT123412341234123412") in ibans   # now applied
    assert status == "approved"


def test_approve_change_applies_vat():
    _seed()
    SS.apply({"vat": "BE1111111111"}, "TFC", actor="t", verified=True, invoice_ref="INV-Y")
    pend = [c for c in SS.pending_changes("pending") if c["field"] == "vat"]
    assert pend
    rid = pend[0]["id"]
    res = SS.approve_change(rid, actor="admin")
    assert res.get("applied") == "vat"
    con = SM.connect()
    home = con.execute("SELECT home_country FROM suppliers WHERE code='TFC'").fetchone()["home_country"]
    vr = con.execute("SELECT vat_number, source FROM supplier_vat_registrations "
                     "WHERE supplier='TFC' AND country=?", (home,)).fetchone()
    con.close()
    assert vr is not None and SS.norm_vat(vr["vat_number"]) == "BE1111111111"
    assert vr["source"] == "manual"     # an admin approval makes it manual (wins over capture)


def test_reject_change_discards_and_touches_nothing():
    _seed()
    ex_before = set(SS.norm_iban(x) for x in (_existing("E100").get("ibans") or []))
    SS.apply({"iban": "LT555555555555555555"}, "E100", actor="t", verified=True)
    rid = SS.pending_changes("pending")[0]["id"]
    res = SS.reject_change(rid, actor="admin")
    assert res.get("rejected") == rid
    con = SM.connect()
    ibans = set(SS.norm_iban(r["iban"]) for r in con.execute(
        "SELECT iban FROM supplier_bank_accounts WHERE supplier='E100'"))
    status = con.execute("SELECT status FROM supplier_change_requests WHERE id=?",
                         (rid,)).fetchone()["status"]
    con.close()
    assert ibans == ex_before                 # bank accounts untouched
    assert SS.norm_iban("LT555555555555555555") not in ibans
    assert status == "rejected"


def test_approve_unknown_id_is_skipped():
    _seed()
    res = SS.approve_change(999999, actor="admin")
    assert "skipped" in res


# ─────────────────────────────────────────────────────────── never raises / blocks
def test_apply_never_raises_on_garbage():
    # no seed, garbage inputs — must return a dict, never raise
    res = SS.apply({"legal_name": None, "vat": None}, "DOES_NOT_EXIST", actor="x",
                   verified=True)
    assert isinstance(res, dict)


def test_plan_never_raises_on_none():
    assert isinstance(SS.plan({}, None), dict)
    assert isinstance(SS.plan(None, None), dict)


def test_pending_count_zero_when_clean():
    _seed()
    assert SS.pending_count() == 0


# ─────────────────────────────────────────────── admin web route (/supplier-changes)
def _csrf(client, path="/supplier-changes"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_web_supplier_changes_admin_approve(client):
    _seed()
    SS.apply({"iban": "LT777777777777777777"}, "TFC", actor="t", verified=True,
             invoice_ref="INV-W")
    rid = SS.pending_changes("pending")[0]["id"]
    body = client.get("/supplier-changes").get_data(as_text=True)
    assert "Pending supplier changes" in body
    assert "TFC" in body
    tok = _csrf(client)
    r = client.post("/supplier-changes",
                    data={"_csrf": tok, "__act": "approve", "id": str(rid)})
    assert r.status_code == 200
    con = SM.connect()
    ibans = set(SS.norm_iban(x["iban"]) for x in con.execute(
        "SELECT iban FROM supplier_bank_accounts WHERE supplier='TFC'"))
    con.close()
    assert SS.norm_iban("LT777777777777777777") in ibans


def test_web_supplier_changes_admin_reject(client):
    _seed()
    SS.apply({"vat": "BE2222222222"}, "TFC", actor="t", verified=True)
    rid = [c for c in SS.pending_changes("pending") if c["field"] == "vat"][0]["id"]
    tok = _csrf(client)
    r = client.post("/supplier-changes",
                    data={"_csrf": tok, "__act": "reject", "id": str(rid)})
    assert r.status_code == 200
    # the change is discarded; no pending VAT change remains
    assert not [c for c in SS.pending_changes("pending") if c["field"] == "vat"]


def test_web_supplier_changes_csrf_required(client):
    _seed()
    SS.apply({"iban": "LT888888888888888888"}, "TFC", actor="t", verified=True)
    rid = SS.pending_changes("pending")[0]["id"]
    # missing CSRF -> 400, change stays pending
    r = client.post("/supplier-changes", data={"__act": "approve", "id": str(rid)})
    assert r.status_code == 400
    assert SS.pending_changes("pending")


# ──────────────────────────────────────────────── normalisation helpers
def test_norm_iban_and_vat():
    assert SS.norm_iban("de89 3704-0044") == "DE8937040044"
    assert SS.norm_iban(None) == ""
    assert SS.norm_vat("be 0676.647155") == "BE0676647155"
