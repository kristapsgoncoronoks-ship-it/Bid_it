"""Config-driven generic 'form-login + download' portal adapter (http_form) +
inbound e-invoice intake — both fully exercised with a FAKE HTTP transport / in-memory
queue (no network; egress is disabled in this env).

What's covered:
  • http_form logs in (form POST), enumerates statements (link-regex), downloads each
    file against a FAKE session, and ENQUEUES them into the intake pipeline.
  • OAuth2 client-credentials auth mode acquires a bearer token first.
  • url_template enumeration over ids + the download-window placeholders.
  • a wrong credential / failed login is handled: the run is recorded 'failed', the
    governed supplier's circuit-breaker is driven, and nothing raises out of the worker.
  • the per-supplier rate-limiter still gates a fetch job (re-using the slice-1 cap).
  • credentials round-trip sealed/opened and never appear in the run log.
  • inbound e-invoice: a UBL XML is parsed via extract.parse_einvoice into a correct
    review draft and enqueued; a malformed / oversized / non-invoice doc is rejected.
"""
import importlib
import json

import pytest


# ---- a FAKE requests.Session ---------------------------------------------------
class FakeResponse:
    def __init__(self, status=200, text="", content=b"", url=""):
        self.status_code = status
        self.text = text
        self.content = content
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Routes request()/get()/post() to a {(METHOD, url-substring): FakeResponse|callable}
    map. Records every call (incl. data/auth/headers) so a test can assert the login body
    was sent and NO secret was logged elsewhere."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for (m, frag), resp in self.routes.items():
            if m == method.upper() and frag in url:
                return resp(self, url, kw) if callable(resp) else resp
        return FakeResponse(404, "not found", b"", url)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


@pytest.fixture()
def ps(tmp_path, monkeypatch):
    import portal_scraper
    importlib.reload(portal_scraper)
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    return portal_scraper


@pytest.fixture()
def iq(tmp_path, monkeypatch):
    import waiting_room
    importlib.reload(waiting_room)
    monkeypatch.setattr(waiting_room, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(waiting_room, "INBOX", str(tmp_path / "inbox"))
    waiting_room._SCHEMA_READY.clear()
    return waiting_room


def _wire_session(ps, monkeypatch, routes):
    fake = FakeSession(routes)
    monkeypatch.setattr(ps, "_http_session", lambda: fake)
    return fake


# ---------------------------------------------------------------- form login + links
def test_http_form_login_enumerate_download_enqueues(ps, iq, monkeypatch):
    # the adapter enqueues onto waiting_room.enqueue — route it at the reloaded iq.
    monkeypatch.setitem(__import__("sys").modules, "waiting_room", iq)

    listing = ('<a href="/dl/jan.csv">Jan</a> <a href="/dl/feb.csv">Feb</a>')
    routes = {
        ("POST", "/login"): FakeResponse(200, "Welcome back", b"", "https://x/home"),
        ("GET", "/statements"): FakeResponse(200, listing),
        ("GET", "/dl/jan.csv"): FakeResponse(200, "", b"country,net\nDE,1.4\n"),
        ("GET", "/dl/feb.csv"): FakeResponse(200, "", b"country,net\nDE,1.5\n"),
    }
    fake = _wire_session(ps, monkeypatch, routes)

    ps.set_config("ACME", "http_form", base_url="https://x", config={
        "auth_mode": "form", "login_url": "/login",
        "username_field": "email", "password_field": "pass",
        "login_success": {"text_contains": "Welcome"},
        "list_url": "/statements",
        "link_regex": r'href="(/dl/[^"]+\.csv)"',
        "format": "csv",
    })
    ps.set_credentials("ACME", "ENT1", "user@x.com", "s3cr3t-pw")

    res = ps.scrape("ACME", "ENT1")
    assert res == {"supplier": "ACME", "entity": "ENT1", "fetched": 2, "loaded": 2}

    # the login carried the configured field names + the decrypted credential
    login = next(c for c in fake.calls if "/login" in c["url"])
    assert login["data"]["email"] == "user@x.com"
    assert login["data"]["pass"] == "s3cr3t-pw"

    # both statements were enqueued into the intake pipeline as extract jobs
    assert iq.counts()["queued"] == 2
    con = iq.connect()
    rows = con.execute("SELECT filename, backend, kind FROM intake_jobs ORDER BY id").fetchall()
    con.close()
    assert [r["filename"] for r in rows] == ["jan.csv", "feb.csv"]
    assert all(r["backend"] == "ACME" and r["kind"] == "extract" for r in rows)

    # the run is recorded ok and the secret NEVER appears in the run message
    rcon = ps.connect()
    run = rcon.execute("SELECT status, message FROM portal_runs ORDER BY id DESC LIMIT 1").fetchone()
    rcon.close()
    assert run["status"] == "ok"
    assert "s3cr3t-pw" not in (run["message"] or "")


def test_http_form_oauth2_and_url_template(ps, iq, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "waiting_room", iq)
    routes = {
        ("POST", "/oauth/token"): FakeResponse(200, json.dumps({"access_token": "TOK123"})),
        ("GET", "/api/stmt/2026-01"): FakeResponse(200, "", b"<Invoice/>"),
    }
    fake = _wire_session(ps, monkeypatch, routes)
    ps.set_config("OAUTHCO", "http_form", base_url="https://y", config={
        "auth_mode": "oauth2_client_credentials",
        "token_url": "/oauth/token",
        "url_template": "/api/stmt/{id}",
        "ids": ["2026-01"],
        "format": "xml",
    })
    ps.set_credentials("OAUTHCO", "E", "client-id", "client-secret")
    res = ps.scrape("OAUTHCO", "E")
    assert res["fetched"] == 1 and res["loaded"] == 1
    # the download carried the bearer token from the token endpoint
    dl = next(c for c in fake.calls if "/api/stmt/" in c["url"])
    assert dl["headers"]["Authorization"] == "Bearer TOK123"
    con = iq.connect()
    fn = con.execute("SELECT filename FROM intake_jobs").fetchone()["filename"]
    con.close()
    assert fn.endswith(".xml")


def test_http_form_failed_login_records_failed_and_drives_breaker(ps, iq, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "waiting_room", iq)
    routes = {("POST", "/login"): FakeResponse(200, "Login failed: bad password", b"", "https://x/login")}
    _wire_session(ps, monkeypatch, routes)
    ps.set_config("BADCO", "http_form", base_url="https://x", config={
        "auth_mode": "form", "login_url": "/login",
        "login_success": {"text_absent": "Login failed"},
        "url_template": "/dl/x.csv", "format": "csv",
    })
    ps.set_credentials("BADCO", "E", "u", "wrong-pw")

    # scrape RAISES (recorded first) — but the worker path must NOT let it escape.
    with pytest.raises(RuntimeError):
        ps.scrape("BADCO", "E")
    rcon = ps.connect()
    run = rcon.execute("SELECT status, message FROM portal_runs ORDER BY id DESC LIMIT 1").fetchone()
    rcon.close()
    assert run["status"] == "failed"
    assert "wrong-pw" not in (run["message"] or "")     # secret never logged
    assert "Login failed" in (run["message"] or "")     # the failure reason is named
    assert iq.counts().get("queued", 0) == 0            # nothing enqueued

    # the worker path: _do_fetch drives the breaker and _fail_or_retry never raises out.
    iq.set_supplier_limit("BADCO", max_concurrent=5, min_interval_s=0, breaker_threshold=1)

    import portal_scraper
    real = ps.scrape

    def via_real(supplier, entity, date_from=None, date_to=None):
        return real(supplier, entity, date_from, date_to)
    monkeypatch.setattr(portal_scraper, "scrape", via_real, raising=False)
    monkeypatch.setattr(iq, "BACKOFF_BASE", 0)
    monkeypatch.setattr(iq, "BACKOFF_MAX", 0)
    monkeypatch.setitem(__import__("sys").modules, "portal_scraper", ps)

    jid, _ = iq.enqueue_fetch("BADCO", "E")
    out = iq.process_one()                              # MUST NOT raise
    assert out == (jid, "retry")
    # one failure recorded; threshold 1 -> breaker open for the governed supplier
    bs = iq.breaker_state("BADCO")
    assert bs["consec_failures"] >= 1 and bs["open"] is True


def test_http_form_rate_limiter_gates_fetch(ps, iq):
    """A governed supplier with max_concurrent=1 only lets one fetch be in-flight."""
    iq.set_supplier_limit("ACME", max_concurrent=1, min_interval_s=0)
    f1, _ = iq.enqueue_fetch("acme", "E1")
    f2, _ = iq.enqueue_fetch("acme", "E2")
    con = iq.connect(); r1 = iq._claim(con); con.close()
    assert r1["id"] == f1
    con = iq.connect(); r2 = iq._claim(con); con.close()
    assert r2 is None                                   # at cap
    iq.complete(f1)
    con = iq.connect(); r3 = iq._claim(con); con.close()
    assert r3["id"] == f2


# ---------------------------------------------------------------- inbound e-invoice
UBL = b"""<?xml version="1.0"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>INV-INBOUND-1</cbc:ID><cbc:IssueDate>2026-05-31</cbc:IssueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyLegalEntity>
   <cbc:RegistrationName>DKV Euro Service GmbH</cbc:RegistrationName></cac:PartyLegalEntity>
 </cac:Party></cac:AccountingSupplierParty>
 <cac:InvoiceLine><cbc:LineExtensionAmount>500.00</cbc:LineExtensionAmount>
   <cac:Delivery><cac:DeliveryLocation><cac:Address><cac:Country>
     <cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country></cac:Address>
   </cac:DeliveryLocation></cac:Delivery></cac:InvoiceLine>
</Invoice>"""

BILLION_LAUGHS = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
                  b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
                  b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">'
                  b'<ID>&lol2;</ID></Invoice>')


@pytest.fixture()
def ie(iq, monkeypatch):
    import inbound_einvoice
    importlib.reload(inbound_einvoice)
    # inbound_einvoice imports waiting_room lazily inside intake_einvoice -> route it.
    monkeypatch.setitem(__import__("sys").modules, "waiting_room", iq)
    return inbound_einvoice


def test_inbound_einvoice_enqueues_and_parses(ie, iq):
    jid, st = ie.intake_einvoice(UBL, "inbound.xml", user="auditor")
    assert st == "queued"
    job = iq.get_job(jid)
    assert job["backend"] == "einvoice-inbound" and job["kind"] == "extract"

    # the worker's standard EXTRACT path parses it via extract.parse_einvoice.
    out = iq.process_one()
    assert out == (jid, "ready")
    draft = iq.get_stored_draft(jid)
    assert draft["backend"] == "e-invoice"
    assert draft["statement_ref"] == "INV-INBOUND-1"
    assert draft["supplier"] == "DKV Euro Service GmbH"
    assert draft["lines"][0]["country"] == "DE" and draft["lines"][0]["net"] == 500.0


def test_inbound_einvoice_rejects_non_invoice(ie):
    with pytest.raises(ie.InboundRejected):
        ie.intake_einvoice(b"just some text, not xml", "note.txt")


def test_inbound_einvoice_rejects_empty(ie):
    with pytest.raises(ie.InboundRejected):
        ie.intake_einvoice(b"", "empty.xml")


def test_inbound_einvoice_rejects_oversized(ie, monkeypatch):
    monkeypatch.setattr(ie, "MAX_INBOUND_BYTES", 10)
    with pytest.raises(ie.InboundRejected):
        ie.intake_einvoice(UBL, "big.xml")


def test_inbound_einvoice_rejects_entity_bomb(ie):
    # defusedxml refuses entity expansion -> the probe rejects it safely (no DoS).
    with pytest.raises(ie.InboundRejected):
        ie.intake_einvoice(BILLION_LAUGHS, "bomb.xml")
