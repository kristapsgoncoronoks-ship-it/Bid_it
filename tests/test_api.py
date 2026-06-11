"""Tests for the JSON API endpoints: shape, auth, and capability gating."""
import app as A

LIST_ENDPOINTS = ["/api/periods", "/api/benchmark", "/api/compare",
                  "/api/headtohead", "/api/entities", "/api/vat"]
DICT_ENDPOINTS = ["/api/pricing", "/api/recovery"]


def test_api_returns_json(client):
    for e in LIST_ENDPOINTS:
        r = client.get(e)
        assert r.status_code == 200 and r.is_json, e
        assert isinstance(r.get_json(), list), e
    for e in DICT_ENDPOINTS:
        r = client.get(e)
        assert r.status_code == 200 and r.is_json, e
        assert isinstance(r.get_json(), dict), e


def test_api_periods_and_recovery_payload(client):
    assert "2026-05" in client.get("/api/periods").get_json()
    rec = client.get("/api/recovery").get_json()
    assert set(rec.keys()) == {"claims", "summary"}


def test_api_compare_respects_filter(client):
    data = client.get("/api/compare?period=2026-05&supplier=Q8&supplier=BP").get_json()
    assert data and {r["supplier"] for r in data} <= {"Q8", "BP"}


def test_api_requires_login(admin_session):
    c = A.app.test_client()           # not logged in
    r = c.get("/api/periods")
    assert r.status_code == 302 and "/login" in r.headers.get("Location", "")


def test_api_capability_gating(admin_session):
    import re
    import auth
    auth.add_user("api_proc", "Pw!23456", role="processor")
    # admin revokes 'pricing' from the processor role
    ca = A.app.test_client()
    ca.post("/login", data={"username": admin_session["user"], "password": admin_session["pw"]})
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    ca.get("/admin").get_data(as_text=True)).group(1)
    ca.post("/admin", data={"_csrf": tok, "__act": "perms", "perm_data_import": "on",
                            "perm_invoice_control": "on", "perm_vat_claims": "on",
                            "perm_documents": "on", "perm_exports": "on"})
    try:
        cp = A.app.test_client()
        cp.post("/login", data={"username": "api_proc", "password": "Pw!23456"})
        assert cp.get("/api/pricing").status_code == 403   # gated
        assert cp.get("/api/compare").status_code == 200    # ungated view query
    finally:
        auth.set_permission("processor", "pricing", True)
