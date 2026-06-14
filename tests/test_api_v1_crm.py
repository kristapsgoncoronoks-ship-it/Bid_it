"""
Security + contract tests for the BASIC CRM-sync surface on /api/v1.

This is a WRITE API to customer master data (it feeds VAT claims), so the tests
are security/audit-critical:
  * READ (list/detail) needs an api:crm key; WRITE (create/patch) needs the
    separate api:crm.write scope. A read-only api:crm key MUST NOT write (403).
  * no key -> 401; wrong scope -> 403 (enforced by the shared _api_v1_guard,
    which checks scope regardless of HTTP method).
  * create lands REAL field values (not the "INPUT: ..." placeholders that
    add_customer seeds), duplicate code -> 409, missing required -> 400.
  * a write is AUDIT-ATTRIBUTED to api:<key-label> in customers.db's audit_log.
  * the customer_master.update_customer writer enforces the editable allowlist,
    rejects empty values, and fails closed for an unknown customer.

Keys are issued against the throwaway test security.db (conftest restores it).
Customers are written to the real customers.db with unique TEST-* codes and
cleaned up after each test, so demo data is not clobbered.
"""
import uuid

import pytest

import app as A
import api_keys
import audit
import customer_master as CD


def _client():
    """A fresh, NOT-logged-in client (token path only, no session cookie)."""
    return A.app.test_client()


def _issue(scopes, label="crm-test"):
    kid, token = api_keys.issue(label, scopes, "pytest_admin")
    return kid, token


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _new_code():
    """A unique, throwaway customer code that will not collide with demo data."""
    return "TEST" + uuid.uuid4().hex[:8].upper()


@pytest.fixture()
def cleanup_codes():
    """Track created customer codes and delete them (and their audit rows) after."""
    codes = []
    yield codes
    con = CD.connect()
    try:
        for code in codes:
            con.execute("DELETE FROM customers WHERE code=?", (code,))
            con.execute("DELETE FROM audit_log WHERE tbl='customers' AND rowkey=?", (code,))
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------- READ: list/detail

def test_list_with_crm_scope(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Acme Read OU", "LT")
    _, token = _issue(["api:crm"])
    r = _client().get("/api/v1/customers", headers=_hdr(token))
    assert r.status_code == 200 and r.is_json
    rows = r.get_json()["customers"]
    mine = next((x for x in rows if x["code"] == code), None)
    assert mine is not None
    assert set(mine.keys()) == {"code", "company_name", "country", "status",
                                "active", "countries_active"}
    assert mine["active"] is False           # freshly added -> 'pending'
    assert isinstance(mine["countries_active"], list)


def test_detail_with_crm_scope(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Acme Detail OU", "LV")
    _, token = _issue(["api:crm"])
    r = _client().get(f"/api/v1/customers/{code}", headers=_hdr(token))
    assert r.status_code == 200 and r.is_json
    body = r.get_json()
    assert body["code"] == code
    assert body["company_name"] == "Acme Detail OU"
    assert body["is_active"] is False
    assert isinstance(body["activation_checklist"], list)
    assert isinstance(body["countries"], list)


def test_detail_unknown_is_404(admin_session):
    _, token = _issue(["api:crm"])
    r = _client().get("/api/v1/customers/NOPENOPE", headers=_hdr(token))
    assert r.status_code == 404


def test_read_without_key_is_401(admin_session):
    assert _client().get("/api/v1/customers").status_code == 401
    assert _client().get("/api/v1/customers/X").status_code == 401


def test_read_with_wrong_scope_is_403(admin_session):
    # a key scoped only for benchmark must not reach the CRM read surface
    _, token = _issue(["api:benchmark"])
    assert _client().get("/api/v1/customers", headers=_hdr(token)).status_code == 403
    assert _client().get("/api/v1/customers/X", headers=_hdr(token)).status_code == 403


# ---------------------------------------------------------------- WRITE: create

def test_create_lands_real_fields_not_placeholders(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    _, token = _issue(["api:crm.write"])
    payload = {"code": code, "company_name": "Created OU", "country": "EE",
               "reg_number": "REG-123", "vat_number": "EE999999999",
               "legal_address": "1 Main St, Tallinn", "home_portal": "https://emta.ee",
               "phone": "+372 555 1234", "email": "ops@created.ee"}
    r = _client().post("/api/v1/customers", json=payload, headers=_hdr(token))
    assert r.status_code == 201 and r.is_json
    body = r.get_json()
    assert body["code"] == code

    # the persisted row carries the REAL values, with NO "INPUT: ..." placeholders
    con = CD.connect()
    try:
        row = con.execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row["reg_number"] == "REG-123"
    assert row["vat_number"] == "EE999999999"
    assert row["legal_address"] == "1 Main St, Tallinn"
    assert row["home_portal"] == "https://emta.ee"
    assert row["phone"] == "+372 555 1234"
    assert row["email"] == "ops@created.ee"
    for field in ("reg_number", "vat_number", "legal_address", "home_portal"):
        assert not str(row[field]).startswith("INPUT:")


def test_create_minimal_required_only(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    _, token = _issue(["api:crm.write"])
    r = _client().post("/api/v1/customers",
                       json={"code": code, "company_name": "Bare OU", "country": "LT"},
                       headers=_hdr(token))
    assert r.status_code == 201


def test_create_duplicate_is_409(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Existing OU", "LT")
    _, token = _issue(["api:crm.write"])
    r = _client().post("/api/v1/customers",
                       json={"code": code, "company_name": "Dup OU", "country": "LT"},
                       headers=_hdr(token))
    assert r.status_code == 409


def test_create_missing_required_is_400(admin_session):
    _, token = _issue(["api:crm.write"])
    # missing country
    r = _client().post("/api/v1/customers",
                       json={"code": _new_code(), "company_name": "No Country OU"},
                       headers=_hdr(token))
    assert r.status_code == 400
    # empty code
    r = _client().post("/api/v1/customers",
                       json={"code": "  ", "company_name": "X", "country": "LT"},
                       headers=_hdr(token))
    assert r.status_code == 400


def test_read_only_key_cannot_create(admin_session):
    # an api:crm (read) key must NOT be able to call the write endpoint
    _, token = _issue(["api:crm"])
    r = _client().post("/api/v1/customers",
                       json={"code": _new_code(), "company_name": "Nope OU", "country": "LT"},
                       headers=_hdr(token))
    assert r.status_code == 403


def test_create_without_key_is_401(admin_session):
    r = _client().post("/api/v1/customers",
                       json={"code": _new_code(), "company_name": "X", "country": "LT"})
    assert r.status_code == 401


# ---------------------------------------------------------------- WRITE: patch

def test_patch_updates_fields(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Before OU", "LT")
    _, token = _issue(["api:crm.write"])
    r = _client().patch(f"/api/v1/customers/{code}",
                        json={"company_name": "After OU", "email": "new@after.lt"},
                        headers=_hdr(token))
    assert r.status_code == 200
    body = r.get_json()
    assert body["company_name"] == "After OU"
    assert body["email"] == "new@after.lt"

    con = CD.connect()
    try:
        row = con.execute("SELECT company_name, email FROM customers WHERE code=?",
                          (code,)).fetchone()
    finally:
        con.close()
    assert row["company_name"] == "After OU"
    assert row["email"] == "new@after.lt"


def test_patch_unknown_code_is_404(admin_session):
    _, token = _issue(["api:crm.write"])
    r = _client().patch("/api/v1/customers/NOSUCHCODE",
                        json={"company_name": "X"}, headers=_hdr(token))
    assert r.status_code == 404


def test_patch_empty_value_is_400(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Keep OU", "LT")
    _, token = _issue(["api:crm.write"])
    r = _client().patch(f"/api/v1/customers/{code}",
                        json={"company_name": "   "}, headers=_hdr(token))
    assert r.status_code == 400


def test_read_only_key_cannot_patch(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Locked OU", "LT")
    _, token = _issue(["api:crm"])
    r = _client().patch(f"/api/v1/customers/{code}",
                        json={"company_name": "Hacked OU"}, headers=_hdr(token))
    assert r.status_code == 403
    # the value is unchanged
    con = CD.connect()
    try:
        row = con.execute("SELECT company_name FROM customers WHERE code=?", (code,)).fetchone()
    finally:
        con.close()
    assert row["company_name"] == "Locked OU"


# ---------------------------------------------------------------- audit attribution

def test_write_is_attributed_to_api_key(admin_session, cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    _, token = _issue(["api:crm.write"], label="ext-crm-connector")
    # create then patch under the api:crm.write key
    _client().post("/api/v1/customers",
                   json={"code": code, "company_name": "Audited OU", "country": "LT"},
                   headers=_hdr(token))
    _client().patch(f"/api/v1/customers/{code}",
                    json={"company_name": "Audited 2 OU"}, headers=_hdr(token))

    con = CD.connect()
    try:
        rows = audit.history(con, table="customers", key_like=code)
    finally:
        con.close()
    actors = {r["changed_by"] for r in rows if r["action"] in ("INSERT", "UPDATE")}
    assert "api:ext-crm-connector" in actors, actors
    # nothing was attributed to a blank/system actor
    assert "system" not in actors


# ---------------------------------------------------------------- update_customer unit

def test_update_customer_allowlist_ignores_unknown_keys(cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Unit OU", "LT")
    # 'status' is NOT editable -> silently ignored; 'email' (allowed) is written
    ok, _ = CD.update_customer(code, status="active", email="unit@x.lt")
    assert ok is True
    con = CD.connect()
    try:
        row = con.execute("SELECT status, email FROM customers WHERE code=?", (code,)).fetchone()
    finally:
        con.close()
    assert row["email"] == "unit@x.lt"
    assert row["status"] == "pending"   # the non-editable column was NOT touched


def test_update_customer_rejects_empty_value(cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "Empty OU", "LT")
    ok, msg = CD.update_customer(code, company_name="   ")
    assert ok is False and "empty" in msg.lower()


def test_update_customer_unknown_customer(admin_session):
    ok, msg = CD.update_customer("DOESNOTEXIST", email="x@y.lt")
    assert ok is False and "not found" in msg.lower()


def test_update_customer_only_unknown_keys_is_noop_error(cleanup_codes):
    code = _new_code()
    cleanup_codes.append(code)
    CD.add_customer(code, "NoEdit OU", "LT")
    ok, msg = CD.update_customer(code, status="active", fee_pct=5)
    assert ok is False and "no editable fields" in msg.lower()
