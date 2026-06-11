"""Tests for the admin error log: storage helpers, the global handler, and the
Admin-panel view / clear action."""
import importlib

import pytest


@pytest.fixture()
def temp_auth(tmp_path, monkeypatch):
    import auth
    importlib.reload(auth)
    monkeypatch.setattr(auth, "DB", str(tmp_path / "security_test.db"))
    return auth


def test_log_recent_clear_roundtrip(temp_auth):
    temp_auth.log_error("unit", "ValueError", "bad thing", "trace...", "tester")
    rows = temp_auth.recent_errors()
    assert len(rows) == 1
    assert rows[0]["context"] == "unit" and rows[0]["etype"] == "ValueError"
    assert rows[0]["username"] == "tester"
    temp_auth.clear_errors()
    assert temp_auth.recent_errors() == []


def test_error_log_is_capped(temp_auth, monkeypatch):
    # the table must not grow without bound
    monkeypatch.setattr(temp_auth, "ERROR_LOG_KEEP", 25)
    for i in range(80):
        temp_auth.log_error("cap", "E", f"m{i}")
    assert len(temp_auth.recent_errors(10000)) <= 26


def test_log_error_never_raises(temp_auth, monkeypatch):
    # even if the DB blows up, logging must be a no-op, not an exception
    monkeypatch.setattr(temp_auth, "connect", lambda: (_ for _ in ()).throw(RuntimeError()))
    temp_auth.log_error("x", "Y", "z")  # must not raise


def test_admin_shows_and_clears_errors(client):
    import re
    import auth
    auth.clear_errors()
    auth.log_error("import batch", "ZeroDivisionError", "division by zero",
                   "Traceback (most recent call last): ...", "alice")
    html = client.get("/admin").get_data(as_text=True)
    assert "<h2>Error log</h2>" in html
    assert "import batch" in html and "ZeroDivisionError" in html
    assert "<details>" in html  # traceback collapsed
    tok = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
    r = client.post("/admin", data={"_csrf": tok, "__act": "clear_errors"})
    assert "Error log cleared" in r.get_data(as_text=True)
    assert "No errors logged." in client.get("/admin").get_data(as_text=True)


def test_global_handler_logs_unhandled(client, monkeypatch):
    import app as A
    import auth
    auth.clear_errors()
    # force an unhandled error inside the dashboard view
    monkeypatch.setattr(A, "q_periods",
                        lambda con: (_ for _ in ()).throw(RuntimeError("boom-test-xyz")))
    old = A.app.config.get("PROPAGATE_EXCEPTIONS")
    A.app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        r = client.get("/")
        assert r.status_code == 500
        assert "logged" in r.get_data(as_text=True)
    finally:
        A.app.config["PROPAGATE_EXCEPTIONS"] = old
    assert any("boom-test-xyz" in (e["message"] or "") for e in auth.recent_errors())
    auth.clear_errors()
