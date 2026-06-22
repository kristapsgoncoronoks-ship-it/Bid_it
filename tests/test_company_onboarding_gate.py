"""COMPANY ONBOARDING GATE: the service is unusable until the operating company's own legal
entity (issuer profile: legal name + address + VAT number) is registered. ON by default; an
admin is routed to complete it, then the gate lifts."""
import auth
import invoicing


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
