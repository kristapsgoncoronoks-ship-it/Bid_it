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


def test_login_lockout(admin_session):
    import auth
    auth.add_user("lock_test", "Pw!23456", role="processor")
    c = A.app.test_client()
    for _ in range(8):                           # 8 wrong attempts
        c.post("/login", data={"username": "lock_test", "password": "WRONG"})
    assert auth.is_locked("lock_test")
    # now even the correct password is refused while locked
    r = c.post("/login", data={"username": "lock_test", "password": "Pw!23456"})
    assert "temporarily locked" in r.get_data(as_text=True)
