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


def test_login_required_redirect():
    import app as A
    c = A.app.test_client()  # not logged in
    r = c.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")
