"""Admin-managed provider API keys (appsecrets): sealed round-trip, env precedence,
and the /admin set_api_keys action."""
import os
import re

import appsecrets


def _clean(name="ANTHROPIC_API_KEY"):
    appsecrets.clear_secret(name)
    os.environ.pop(name, None)


def _csrf(client):
    """GET /admin to seed session['_csrf'] and return the token for a POST."""
    body = client.get("/admin").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_set_seal_and_status_masks(admin_session):
    _clean()
    assert appsecrets.status("ANTHROPIC_API_KEY") == ("none", "")
    appsecrets.set_secret("ANTHROPIC_API_KEY", "sk-ant-api03-SECRET-wxyz9999")
    # applied to the live environment immediately
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-api03-SECRET-wxyz9999"
    src, hint = appsecrets.status("ANTHROPIC_API_KEY")
    assert src == "stored"
    assert hint == "…9999"            # masked tail only — never the whole key
    _clean()


def test_load_into_environ_decrypts_and_env_wins(admin_session):
    _clean()
    appsecrets.set_secret("ANTHROPIC_API_KEY", "sk-ant-STORED")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    assert appsecrets.load_into_environ() >= 1
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-STORED"
    # a real env var (systemd/shell) must take precedence over the stored copy
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-FROM-ENV"
    appsecrets.load_into_environ()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-FROM-ENV"
    _clean()


def test_unmanaged_name_rejected(admin_session):
    try:
        appsecrets.set_secret("EVIL_VAR", "x")
        assert False, "should reject an unmanaged secret name"
    except ValueError:
        pass


def test_admin_set_api_keys_action(client):
    _clean()
    tok = _csrf(client)
    r = client.post("/admin", data={"_csrf": tok, "__act": "set_api_keys",
                                    "key_ANTHROPIC_API_KEY": "sk-ant-api03-VIAFORM-1234"})
    assert r.status_code == 200
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-api03-VIAFORM-1234"
    assert appsecrets.is_stored("ANTHROPIC_API_KEY")
    # the masked status renders on the page, the raw key never does
    body = client.get("/admin").get_data(as_text=True)
    assert "…1234" in body
    assert "VIAFORM" not in body
    # clearing removes the stored secret
    r = client.post("/admin", data={"_csrf": tok, "__act": "set_api_keys",
                                    "clear_ANTHROPIC_API_KEY": "on"})
    assert r.status_code == 200
    assert not appsecrets.is_stored("ANTHROPIC_API_KEY")
    _clean()


def test_blank_field_leaves_existing_unchanged(client):
    _clean()
    appsecrets.set_secret("ANTHROPIC_API_KEY", "sk-ant-KEEPME")
    tok = _csrf(client)
    client.post("/admin", data={"_csrf": tok, "__act": "set_api_keys",
                                "key_ANTHROPIC_API_KEY": ""})
    assert appsecrets.is_stored("ANTHROPIC_API_KEY")           # not wiped by a blank submit
    _clean()
