"""Security-hardening tests: response headers, the served JS asset, and login lockout."""
import app as A


def test_security_headers_present(client):
    h = client.get("/").headers
    assert "Content-Security-Policy" in h
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert h.get("Referrer-Policy") == "no-referrer"


def test_app_js_served_without_login():
    c = A.app.test_client()                      # not logged in
    r = c.get("/app.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("Content-Type", "")
    assert b"sortTable" in r.get_data()


def test_upload_size_capped():
    assert A.app.config["MAX_CONTENT_LENGTH"] == 25 * 1024 * 1024


def test_authenticated_pages_not_cached(client):
    assert client.get("/").headers.get("Cache-Control") == "no-store"
    # the static asset keeps its own cache policy
    assert "max-age" in A.app.test_client().get("/app.js").headers.get("Cache-Control", "")


def test_doc_unknown_id_is_404(client):
    r = client.get("/doc/999999")
    assert r.status_code == 404
    assert "No such document" in r.get_data(as_text=True)


def test_csrf_wrong_token_rejected(client):
    r = client.post("/data", data={"_dbk": "x", "_csrf": "not-the-token"})
    assert r.status_code == 400
    assert "CSRF" in r.get_data(as_text=True)


def test_ip_throttle(admin_session):
    import auth
    c = A.app.test_client()
    for i in range(25):                          # spray many usernames from one IP
        c.post("/login", data={"username": f"u{i}", "password": "X"},
               environ_base={"REMOTE_ADDR": "9.9.9.9"})
    assert auth.is_locked_ip("9.9.9.9")
    r = c.post("/login", data={"username": admin_session["user"], "password": admin_session["pw"]},
               environ_base={"REMOTE_ADDR": "9.9.9.9"})
    assert "Temporarily locked" in r.get_data(as_text=True)


def test_login_lockout(admin_session):
    import auth
    auth.add_user("lock_test", "Pw!23456", role="processor")
    c = A.app.test_client()
    for _ in range(8):                           # 8 wrong attempts
        c.post("/login", data={"username": "lock_test", "password": "WRONG"})
    assert auth.is_locked("lock_test")
    # now even the correct password is refused while locked
    r = c.post("/login", data={"username": "lock_test", "password": "Pw!23456"})
    assert "Temporarily locked" in r.get_data(as_text=True)
