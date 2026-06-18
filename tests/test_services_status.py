"""Services control center: status engine, the generic /admin set_service toggle, and the
SSO auto-provision hardening (blank allowlist must never auto-create)."""
import re

import services_status as svc


def _csrf(client):
    body = client.get("/admin").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_services_have_required_shape():
    rows = svc.services()
    assert rows, "expected a non-empty service list"
    for s in rows:
        for k in ("key", "title", "what", "toggleable", "on", "status", "reason"):
            assert k in s, f"{k} missing from {s.get('key')}"
        assert s["status"] in ("active", "off", "needs_setup", "info")


def test_toggleable_settings_allowlist():
    # only genuine on/off settings are flippable by the generic toggle
    assert "ai_vision_capture_enabled" in svc.TOGGLEABLE_SETTINGS
    assert "sso_enabled" in svc.TOGGLEABLE_SETTINGS
    # a non-toggle status line is NOT in the allowlist
    assert "backup_interval_hours" not in svc.TOGGLEABLE_SETTINGS
    assert None not in svc.TOGGLEABLE_SETTINGS


def test_set_service_toggles_and_rejects_unknown(client):
    import auth
    tok = _csrf(client)
    # turn ON a real service
    r = client.post("/admin", data={"_csrf": tok, "__act": "set_service",
                                    "service": "ai_vision_capture_enabled", "state": "on"})
    assert r.status_code == 200
    assert (auth.get_setting("ai_vision_capture_enabled", "off") or "").lower() == "on"
    # turn it OFF
    client.post("/admin", data={"_csrf": tok, "__act": "set_service",
                                "service": "ai_vision_capture_enabled", "state": "off"})
    assert (auth.get_setting("ai_vision_capture_enabled", "off") or "").lower() == "off"
    # an unknown / non-allowlisted key is ignored, never written
    client.post("/admin", data={"_csrf": tok, "__act": "set_service",
                                "service": "evil_setting", "state": "on"})
    assert auth.get_setting("evil_setting", None) is None


def test_control_center_renders_visually(client):
    body = client.get("/admin").get_data(as_text=True)
    assert "Services — switch on / off" in body
    # a switch button exists
    assert 'value="set_service"' in body


def test_sso_auto_provision_requires_domain_allowlist():
    import sso, auth
    auth.set_setting("sso_auto_provision", "on")
    # blank allowlist -> NEVER auto-create (closes the open self-provisioning hole)
    auth.set_setting("sso_allowed_domains", "")
    assert sso.auto_provision_allowed("anyone@gmail.com") is False
    # explicit allowlist -> only matching domains auto-create
    auth.set_setting("sso_allowed_domains", "mycompany.com")
    assert sso.auto_provision_allowed("staff@mycompany.com") is True
    assert sso.auto_provision_allowed("outsider@gmail.com") is False
    # auto-provision off -> never, even with a matching domain
    auth.set_setting("sso_auto_provision", "off")
    assert sso.auto_provision_allowed("staff@mycompany.com") is False
