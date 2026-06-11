"""
Web-layer smoke tests: every GET page serves 200 when logged in, and the CSRF
guard rejects tokenless POSTs while accepting tokened ones.
"""
import re


def _get_routes():
    import app as A
    routes = []
    for rule in A.app.url_map.iter_rules():
        p = str(rule.rule)
        if "GET" not in rule.methods or "<" in p:
            continue
        if rule.endpoint in ("static", "logout"):
            continue
        routes.append(p)
    return sorted(set(routes))


def test_all_get_pages_200(client):
    failures = []
    for path in _get_routes():
        r = client.get(path)
        # /setup self-redirects to /login once an admin exists
        if r.status_code == 200:
            continue
        if path == "/setup" and r.status_code == 302:
            continue
        failures.append((path, r.status_code))
    assert not failures, f"non-200 routes: {failures}"


def test_csrf_rejects_tokenless_post(client):
    r = client.post("/data", data={"_dbk": "x"})
    assert r.status_code == 400, f"expected 400 (CSRF), got {r.status_code}"


def test_csrf_token_present_in_forms(client):
    # A representative POST form must carry the hidden _csrf field.
    html = client.get("/data").get_data(as_text=True)
    assert 'name="_csrf"' in html, "data page form missing CSRF token"


def test_compare_multi_supplier_filter(client):
    import re
    html = client.get("/compare?period=ALL&supplier=Q8&supplier=BP").get_data(as_text=True)
    shown = set(re.findall(r"<td>(Q8|BP|TFC|E100|MOEVE|DKV)</td>", html))
    assert shown <= {"Q8", "BP"}, f"filter leaked other suppliers: {shown}"
    assert shown, "expected Q8/BP rows"


def test_compare_has_multiselect_and_totals(client):
    html = client.get("/compare").get_data(as_text=True)
    assert 'name="supplier" multiple' in html
    assert 'name="station" multiple' in html
    assert "TOTAL (" in html


def test_export_compare_returns_xlsx(client):
    r = client.get("/export/compare?period=ALL&supplier=Q8")
    assert r.status_code == 200
    assert r.get_data()[:2] == b"PK"  # xlsx is a zip


def test_processor_blocked_from_admin(admin_session):
    import app as A
    import auth
    auth.add_user("proc_test", "Pw!23456", role="processor")
    c = A.app.test_client()
    c.post("/login", data={"username": "proc_test", "password": "Pw!23456"})
    # no Admin nav link, and /admin is forbidden
    assert "/admin" not in c.get("/").get_data(as_text=True)
    assert c.get("/admin").status_code == 403
    # but normal operations are allowed by default
    assert c.get("/data").status_code == 200
    assert c.get("/pricing").status_code == 200


def test_admin_can_revoke_processor_capability(admin_session):
    import re
    import app as A
    import auth
    auth.add_user("proc2", "Pw!23456", role="processor")
    ca = A.app.test_client()
    ca.post("/login", data={"username": admin_session["user"], "password": admin_session["pw"]})
    adm = ca.get("/admin").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', adm).group(1)
    # save perms with pricing unchecked -> revoked
    ca.post("/admin", data={"_csrf": tok, "__act": "perms", "perm_data_import": "on",
                            "perm_invoice_control": "on", "perm_vat_claims": "on",
                            "perm_documents": "on", "perm_exports": "on"})
    cp = A.app.test_client()
    cp.post("/login", data={"username": "proc2", "password": "Pw!23456"})
    assert cp.get("/pricing").status_code == 403
    assert cp.get("/data").status_code == 200
    # restore so other tests/state are unaffected
    auth.set_permission("processor", "pricing", True)


def test_savings_page_renders_chart(client):
    html = client.get("/savings").get_data(as_text=True)
    assert "<svg" in html
    assert "Avoidable overpay" in client.get("/").get_data(as_text=True)


def test_transactions_drilldown(client):
    r = client.get("/transactions?period=ALL&supplier=Q8")
    assert r.status_code == 200
    assert "Transactions —" in r.get_data(as_text=True)


def test_export_stations_xlsx(client):
    r = client.get("/export/stations")
    assert r.status_code == 200 and r.get_data()[:2] == b"PK"


def test_login_required_redirect():
    import app as A
    c = A.app.test_client()  # not logged in
    r = c.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")
