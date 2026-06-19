"""
Tests for per-user OPT-IN email two-factor (email OTP) on the local login.

Hard invariants exercised:
  * DEFAULT-OFF: master switch off => twofa_enabled is False and login completes in ONE
    step (byte-identical to before);
  * issue + verify happy path; wrong code increments attempts; 5 wrong invalidates;
    expiry rejects; single-use (verify twice fails); 60s resend cooldown;
  * constant-time compare is used (secrets.compare_digest);
  * the plaintext code is NEVER written to the app log;
  * the LOGIN route redirects an opted-in user to /login/verify and only completes after
    a correct code (real test client + a FAKE SMTP transport capturing the code);
  * the /account opt-in test-code gate (cannot enable without entering the emailed code);
  * the admin break-glass reset clears the flag + any pending code.
"""
import re
import time

import pytest

import auth
import twofa


# ---------------------------------------------------------------- fake transport
class FakeTransport:
    """A notify-shaped transport (.send(to, subject, html, text)) that captures the body
    so a test can read the 6-digit code out of the email instead of the DB."""
    def __init__(self):
        self.sent = []

    def send(self, to, subject, html, text):
        self.sent.append({"to": to, "subject": subject, "html": html, "text": text})

    def last_code(self):
        m = re.search(r"\b(\d{6})\b", self.sent[-1]["text"])
        return m.group(1) if m else None


@pytest.fixture
def user(admin_session):
    """A throwaway local user with an email, used as the 2FA subject."""
    uname = "twofa_user"
    auth.add_user(uname, "Pw!twofa123", role="processor")
    auth.set_user_email(uname, "twofa_user@example.com")
    twofa.clear(uname)
    auth.set_twofa(uname, False)
    yield {"user": uname, "pw": "Pw!twofa123", "email": "twofa_user@example.com"}
    twofa.clear(uname)


# ---------------------------------------------------------------- enablement (default off)
def test_master_off_means_not_enabled(user):
    # No master switch + opted in still => no challenge.
    auth.set_setting("twofa_email_enabled", "0")
    auth.set_twofa(user["user"], True)
    assert twofa.enabled() is False
    assert twofa.twofa_enabled(user["user"]) is False
    assert auth.twofa_enabled(user["user"]) is False


def test_enabled_requires_master_optin_and_email(user):
    auth.set_setting("twofa_email_enabled", "1")
    assert twofa.enabled() is True
    # opted out => no challenge
    assert twofa.twofa_enabled(user["user"]) is False
    auth.set_twofa(user["user"], True)
    assert twofa.twofa_enabled(user["user"]) is True
    # no email => no challenge (can't deliver)
    auth.set_user_email(user["user"], "")
    assert twofa.twofa_enabled(user["user"]) is False


# ---------------------------------------------------------------- OTP lifecycle
def test_issue_verify_happy_path(user):
    code = twofa.issue(user["user"])
    assert code and code.isdigit() and len(code) == 6
    res = twofa.verify(user["user"], code)
    assert res["ok"] is True


def test_code_stored_hashed_not_plaintext(user):
    code = twofa.issue(user["user"])
    con = twofa.connect()
    row = con.execute("SELECT code_hash, salt FROM login_otp WHERE username=?",
                      (user["user"],)).fetchone()
    con.close()
    assert row["code_hash"] and code not in row["code_hash"]


def test_wrong_code_increments_attempts(user):
    twofa.issue(user["user"])
    assert twofa.attempts_left(user["user"]) == twofa.MAX_ATTEMPTS
    res = twofa.verify(user["user"], "000000")
    # (vanishingly unlikely the real code is 000000; if it were, this asserts ok which
    #  would be a different but still-correct branch — guard against it.)
    if res["ok"]:
        pytest.skip("issued code was 000000")
    assert res["reason"] == "bad_code"
    assert twofa.attempts_left(user["user"]) == twofa.MAX_ATTEMPTS - 1


def test_five_wrong_invalidates_code(user):
    code = twofa.issue(user["user"])
    for _ in range(twofa.MAX_ATTEMPTS):
        twofa.verify(user["user"], "999999" if code != "999999" else "111111")
    # the code is now dead — even the CORRECT code no longer works.
    res = twofa.verify(user["user"], code)
    assert res["ok"] is False
    assert res["reason"] in ("no_code", "locked")


def test_expiry_rejects(user, monkeypatch):
    code = twofa.issue(user["user"])
    # fast-forward past the TTL
    real = time.time()
    monkeypatch.setattr(twofa.time, "time", lambda: real + twofa.OTP_TTL_SEC + 1)
    res = twofa.verify(user["user"], code)
    assert res["ok"] is False and res["reason"] == "expired"


def test_single_use(user):
    code = twofa.issue(user["user"])
    assert twofa.verify(user["user"], code)["ok"] is True
    # a second verify of the same code fails — the row was deleted on success.
    res = twofa.verify(user["user"], code)
    assert res["ok"] is False and res["reason"] == "no_code"


def test_resend_cooldown(user):
    code1 = twofa.issue(user["user"])
    assert code1 is not None
    # within the cooldown, a resend returns None (caller shows "please wait") and the
    # original code still stands.
    assert twofa.issue(user["user"]) is None
    assert twofa.verify(user["user"], code1)["ok"] is True


def test_no_pending_code(user):
    twofa.clear(user["user"])
    res = twofa.verify(user["user"], "123456")
    assert res["ok"] is False and res["reason"] == "no_code"


# ---------------------------------------------------------------- constant-time + no log
def test_constant_time_compare_used(monkeypatch, user):
    called = {"n": 0}
    real = twofa.secrets.compare_digest

    def spy(a, b):
        called["n"] += 1
        return real(a, b)

    monkeypatch.setattr(twofa.secrets, "compare_digest", spy)
    code = twofa.issue(user["user"])
    twofa.verify(user["user"], code)
    assert called["n"] >= 1, "verify must use secrets.compare_digest (constant-time)"


def test_code_never_logged(user, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    code = twofa.issue(user["user"])
    transport = FakeTransport()
    assert twofa.send_code(user["user"], code, transport=transport) is True
    twofa.verify(user["user"], "000000")
    # the plaintext code must not appear anywhere in captured log records.
    for rec in caplog.records:
        assert code not in rec.getMessage(), "plaintext OTP leaked into the log"


# ---------------------------------------------------------------- send_code
def test_send_code_uses_transport_and_targets_user_email(user):
    code = twofa.issue(user["user"])
    t = FakeTransport()
    assert twofa.send_code(user["user"], code, transport=t) is True
    assert t.sent[-1]["to"] == user["email"]
    assert t.last_code() == code
    assert "Fleet Fuel" in t.sent[-1]["subject"]


def test_send_code_no_email_returns_false(user):
    auth.set_user_email(user["user"], "")
    assert twofa.send_code(user["user"], "123456", transport=FakeTransport()) is False


# ---------------------------------------------------------------- login flow (web)
def _login_client():
    import app as A
    return A.app.test_client()


def test_login_one_step_when_master_off(admin_session, monkeypatch):
    # master OFF => login completes in one step, byte-identical to today.
    auth.set_setting("twofa_email_enabled", "0")
    import app as A
    sent = []
    monkeypatch.setattr("twofa.send_code", lambda *a, **k: sent.append(1) or True)
    c = A.app.test_client()
    r = c.post("/login", data={"username": admin_session["user"],
                               "password": admin_session["pw"]})
    assert r.status_code == 302 and "/login/verify" not in r.headers.get("Location", "")
    assert c.get("/").status_code == 200
    assert not sent, "no code should be sent when 2FA is off"


def test_login_redirects_optin_user_to_verify_and_completes(user, monkeypatch):
    auth.set_setting("twofa_email_enabled", "1")
    auth.set_twofa(user["user"], True)
    transport = FakeTransport()
    # capture the emailed code by injecting our transport into send_code.
    monkeypatch.setattr("twofa.send_code",
                        lambda uname, code, transport=None: transport_send(transport, uname, code))

    captured = {}

    def transport_send(_t, uname, code):
        # mimic twofa.send_code but with our fake transport so we capture the code
        captured["code"] = code
        transport.send(auth.user_email(uname), "Your Fleet Fuel sign-in code",
                       f"<p>{code}</p>", code)
        return True

    import app as A
    c = A.app.test_client()
    r = c.post("/login", data={"username": user["user"], "password": user["pw"]})
    # opted-in user goes to the verify page, NOT logged in yet.
    assert r.status_code == 302 and r.headers["Location"].endswith("/login/verify")
    with c.session_transaction() as s:
        assert s.get("pending_2fa_user") == user["user"]
        assert not s.get("user"), "must NOT be authenticated before the code is entered"
    # home is not reachable yet
    assert c.get("/", follow_redirects=False).status_code == 302
    # GET the verify page, grab CSRF, submit the WRONG code first
    body = c.get("/login/verify").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    wrong = "000000" if captured["code"] != "000000" else "111111"
    r = c.post("/login/verify", data={"_csrf": tok, "code": wrong})
    assert b"Incorrect or expired code" in r.data
    with c.session_transaction() as s:
        assert not s.get("user")
    # now the CORRECT code completes the login
    r = c.post("/login/verify", data={"_csrf": tok, "code": captured["code"]})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    with c.session_transaction() as s:
        assert s.get("user") == user["user"]
    assert c.get("/").status_code == 200


def test_verify_page_redirects_without_pending(admin_session):
    import app as A
    c = A.app.test_client()
    r = c.get("/login/verify")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_login_fails_closed_when_send_fails(user, monkeypatch):
    auth.set_setting("twofa_email_enabled", "1")
    auth.set_twofa(user["user"], True)
    monkeypatch.setattr("twofa.send_code", lambda *a, **k: False)
    import app as A
    c = A.app.test_client()
    r = c.post("/login", data={"username": user["user"], "password": user["pw"]},
               follow_redirects=False)
    # no redirect to /login/verify; the login page is re-rendered with an error and the
    # user is NOT authenticated.
    assert r.status_code == 200
    assert b"could not send your sign-in code" in r.data
    with c.session_transaction() as s:
        assert not s.get("user") and not s.get("pending_2fa_user")


# ---------------------------------------------------------------- /account opt-in gate
def test_account_optin_requires_test_code(user, monkeypatch):
    auth.set_setting("twofa_email_enabled", "1")
    captured = {}
    monkeypatch.setattr(
        "twofa.send_code",
        lambda uname, code, transport=None: (captured.__setitem__("code", code) or True))
    # log in (2FA still OFF for this user, so one-step login)
    import app as A
    c = A.app.test_client()
    r = c.post("/login", data={"username": user["user"], "password": user["pw"]})
    assert r.status_code == 302
    # GET /account, grab CSRF
    body = c.get("/account").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    # begin opt-in: emails a test code; the flag is NOT yet set
    r = c.post("/account", data={"_csrf": tok, "__act": "twofa_begin",
                                 "email": user["email"]})
    assert b"test code was sent" in r.data
    assert bool((auth.get_user(user["user"]) or {}).get("twofa_email")) is False
    # a WRONG test code does not enable it
    r = c.post("/account", data={"_csrf": tok, "__act": "twofa_confirm",
                                 "code": "000000" if captured["code"] != "000000" else "111111"})
    assert bool((auth.get_user(user["user"]) or {}).get("twofa_email")) is False
    # the CORRECT test code enables it
    r = c.post("/account", data={"_csrf": tok, "__act": "twofa_confirm",
                                 "code": captured["code"]})
    assert b"now ON" in r.data
    assert bool((auth.get_user(user["user"]) or {}).get("twofa_email")) is True


def test_account_turn_off(user):
    auth.set_setting("twofa_email_enabled", "1")
    auth.set_twofa(user["user"], True)
    # this user has 2FA on, so logging in needs a code — log in via the admin client trick:
    # set the flag off through the route as the user themselves. We must first reach a
    # logged-in session; simplest is to log in then off through the model is not the web
    # path, so drive the route directly with a session.
    import app as A
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["user"] = user["user"]
        s["role"] = "processor"
    body = c.get("/account").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    r = c.post("/account", data={"_csrf": tok, "__act": "twofa_off"})
    assert b"now OFF" in r.data
    assert bool((auth.get_user(user["user"]) or {}).get("twofa_email")) is False


# ---------------------------------------------------------------- admin break-glass
def test_admin_reset_clears_flag_and_pending(user, client):
    auth.set_setting("twofa_email_enabled", "1")
    auth.set_twofa(user["user"], True)
    twofa.issue(user["user"])           # a pending code exists
    # `client` is a logged-in admin; grab a CSRF token from the admin page.
    body = client.get("/admin").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    r = client.post("/admin", data={"_csrf": tok, "__act": "twofa_reset",
                                    "username": user["user"]})
    assert r.status_code == 200
    assert bool((auth.get_user(user["user"]) or {}).get("twofa_email")) is False
    assert twofa.attempts_left(user["user"]) == 0   # pending code cleared


def test_admin_master_toggle(client):
    body = client.get("/admin").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    r = client.post("/admin", data={"_csrf": tok, "__act": "set_twofa_master",
                                    "twofa_email_enabled": "on"})
    assert r.status_code == 200
    assert twofa.enabled() is True
    # turning it back off
    r = client.post("/admin", data={"_csrf": tok, "__act": "set_twofa_master"})
    assert twofa.enabled() is False


# ---------------------------------------------------------------- endpoint coverage
def test_endpoint_coverage_clean():
    import app as A
    assert A._assert_endpoint_coverage() == set()
