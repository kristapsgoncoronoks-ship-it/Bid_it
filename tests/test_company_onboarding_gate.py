"""COMPANY ONBOARDING GATE: the service is unusable until the operating company's own legal
entity (issuer profile: legal name + address + VAT number) is registered. ON by default; an
admin is routed to complete it, then the gate lifts."""
import pytest

import auth
import invoicing


@pytest.fixture(autouse=True)
def _tmp_invoicing_db(tmp_path, monkeypatch):
    """Isolate the issuer registry (invoicing.db) per test so a company added in one test
    never leaks into another (the gate reads the registry)."""
    monkeypatch.setattr(invoicing, "DB", str(tmp_path / "invoicing.db"))
    monkeypatch.setattr(invoicing, "_SCHEMA_READY", set())


def test_gate_redirects_admin_to_issuer_then_lifts(client):
    auth.set_setting("require_company_profile", "1")     # gate ON (conftest defaults it off)
    assert not invoicing.issuer_complete()               # conftest left the issuer blank

    # a normal page is blocked -> the admin is sent to register the company
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and "/invoicing/issuer" in r.headers["Location"]

    # the issuer profile page itself stays reachable (exempt)
    assert client.get("/invoicing/issuer").status_code == 200

    # register the company -> the gate lifts (same session)
    invoicing.set_issuer(dict(name="My Co OU", address="Tallinn, Estonia",
                              vat_number="EE123456789"))
    assert client.get("/", follow_redirects=False).status_code == 200


def test_gate_off_by_setting_allows_use_without_issuer(client):
    auth.set_setting("require_company_profile", "0")
    assert not invoicing.issuer_complete()
    assert client.get("/", follow_redirects=False).status_code == 200


def _csrf(client, path):
    import re
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_registry_add_company_lifts_gate_and_lists(client):
    import auth, invoicing
    auth.set_setting("require_company_profile", "1")
    # add a company via the registry page (reachable while gated)
    r = client.post("/invoicing/companies/save",
                    data={"_csrf": _csrf(client, "/invoicing/issuer"),
                          "label": "Alpha OU", "name": "Alpha OU",
                          "address": "Tallinn, EE", "vat_number": "EE100", "series": "ALPHA"})
    assert r.status_code == 200
    assert any(c["name"] == "Alpha OU" for c in invoicing.list_issuers())
    # the gate has lifted -> a normal page renders
    assert client.get("/", follow_redirects=False).status_code == 200
    # the company appears on the issuer page with its series
    body = client.get("/invoicing/issuer").get_data(as_text=True)
    assert "Alpha OU" in body and "ALPHA" in body
