"""
AI VERIFY — INDEPENDENT SECOND OPINION (a different backend/model than CAPTURE).

The VERIFY step may be pointed at a DIFFERENT model (and optionally provider) than the
capture step via two OPTIONAL settings:
    * `ai_verify_backend` — unset => the shared capture/review backend (byte-identical).
    * `ai_verify_model`   — unset => that backend's default model (byte-identical).

Load-bearing invariants asserted here:
  * with `ai_verify_model` set, verify()'s outbound request carries THAT model (not the
    capture default), while capture stays on the capture model;
  * with `ai_verify_backend` set to the OTHER provider (key present), verify routes to that
    provider while capture stays on the capture backend;
  * with both unset, the resolved provider+model are EXACTLY today's (byte-identical);
  * status() reports the VERIFY backend+model (which may differ from capture);
  * test_connection() probes the verify model;
  * the API key is never leaked into any returned/displayed string.

No real key or network is used — the vision backend + auth.get_setting are monkeypatched.
"""
import pytest

import ai_verify
import vision_capture as VC


# --------------------------------------------------------------- setting harness
def _settings(monkeypatch, **vals):
    """Patch auth.get_setting so it returns the named overrides; everything else -> default.
    Keys not in `vals` return the caller's default (the 2nd arg)."""
    import auth
    def _get(k, d=None):
        if k in vals:
            return vals[k]
        return d
    monkeypatch.setattr(auth, "get_setting", _get)


DRAFT = {"supplier": "DKV", "statement_ref": "S-1", "currency": "EUR",
         "lines": [{"invoice_no": "B1", "net": 100.0, "vat": 21.0}]}


def _patch_render(monkeypatch, n=1):
    monkeypatch.setattr(ai_verify, "_render_pages", lambda b, c: [b"PNG"] * n)


# =============================================================== model override
def test_verify_model_override_used_in_request(monkeypatch):
    """With `ai_verify_model` set, verify()'s outbound request uses THAT model — not the
    backend/capture default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_model="claude-sonnet-4-6")
    _patch_render(monkeypatch)
    sent = {}

    def _call(prompt, data_str, images, model=None):
        sent["model"] = model
        return {"verdict": "confirmed", "fields": [], "notes": ""}

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _call)
    out = ai_verify.verify(b"%PDF fake", DRAFT)
    assert sent["model"] == "claude-sonnet-4-6"          # the override, not the default
    assert out["model"] == "claude-sonnet-4-6"           # surfaced on the verdict
    assert out["provider"] == "claude"


def test_capture_keeps_capture_model_when_verify_overridden(monkeypatch):
    """Capture stays on the CAPTURE/backend default model even when verify is overridden to a
    different model — the override is verify-only."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_model="claude-sonnet-4-6")
    # vision_capture.model_name resolves the CAPTURE model (the backend default).
    assert VC.model_name("claude") == "claude-opus-4-8"
    # but the verify model is the override
    assert ai_verify._verify_model("claude") == "claude-sonnet-4-6"


# =============================================================== backend override
def test_verify_backend_override_routes_to_other_provider(monkeypatch):
    """With `ai_verify_backend` set to the OTHER provider (key present), verify routes to that
    provider while capture stays on the capture backend."""
    # capture/review backend = claude; verify overridden to openai (both keys present)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_backend="openai")
    _patch_render(monkeypatch)

    routed = {"claude": 0, "openai": 0}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: routed.__setitem__("claude", routed["claude"] + 1)
                        or {"verdict": "confirmed", "fields": [], "notes": ""})
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai",
                        lambda *a, **k: routed.__setitem__("openai", routed["openai"] + 1)
                        or {"verdict": "confirmed", "fields": [], "notes": ""})

    out = ai_verify.verify(b"%PDF fake", DRAFT)
    assert out["provider"] == "openai"                   # verify routed to the override
    assert routed == {"claude": 0, "openai": 1}
    # capture's provider is still the shared (claude) backend
    assert VC._provider() == "claude"
    assert ai_verify._verify_provider() == "openai"


def test_verify_backend_override_uses_other_backend_default_model(monkeypatch):
    """When only the backend is overridden (no model override), verify uses the OVERRIDDEN
    backend's default model — e.g. openai -> gpt-4o, not claude's default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_backend="openai")
    _patch_render(monkeypatch)
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai",
                        lambda p, d, images, model=None: sent.__setitem__("model", model)
                        or {"verdict": "confirmed", "fields": [], "notes": ""})
    ai_verify.verify(b"%PDF fake", DRAFT)
    assert sent["model"] == "gpt-4o"                     # the openai default, not claude's


def test_verify_backend_and_model_override_combined(monkeypatch):
    """Both overrides set: verify routes to the override provider AND uses the override
    model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_backend="openai", ai_verify_model="gpt-4o-mini")
    _patch_render(monkeypatch)
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai",
                        lambda p, d, images, model=None: sent.__setitem__("model", model)
                        or {"verdict": "confirmed", "fields": [], "notes": ""})
    out = ai_verify.verify(b"%PDF fake", DRAFT)
    assert out["provider"] == "openai" and out["model"] == "gpt-4o-mini"
    assert sent["model"] == "gpt-4o-mini"


def test_unusable_backend_override_falls_back_to_capture(monkeypatch):
    """A verify-backend override whose KEY is missing is NOT usable -> verify FALLS BACK to
    the shared capture backend (fails toward doing the review), byte-identical to no override
    for the resolved provider/model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)        # openai key absent
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_backend="openai")         # unusable (no key)
    assert ai_verify._verify_provider() == "claude"            # fell back to capture's
    assert ai_verify._verify_model() == "claude-opus-4-8"      # capture's default model


# =============================================================== byte-identical default
def test_default_is_byte_identical_to_capture(monkeypatch):
    """With BOTH overrides unset, the verify provider+model are EXACTLY the shared
    capture/review backend + its default model — byte-identical to today's behaviour."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch)                                     # nothing overridden
    # provider + model identical to the shared provider + backend default
    assert ai_verify._verify_provider() == ai_verify._provider() == "claude"
    assert ai_verify._verify_model() == ai_verify.model_name("claude") == "claude-opus-4-8"
    assert ai_verify._verify_model() == VC.model_name("claude")     # same as capture


def test_default_request_model_is_backend_default(monkeypatch):
    """With no override, verify()'s outbound request carries the backend default model — the
    exact value the pre-change code sent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch)
    _patch_render(monkeypatch)
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda p, d, images, model=None: sent.__setitem__("model", model)
                        or {"verdict": "confirmed", "fields": [], "notes": ""})
    out = ai_verify.verify(b"%PDF fake", DRAFT)
    assert sent["model"] == "claude-opus-4-8"
    assert out["model"] == "claude-opus-4-8"


# =============================================================== status() reflects verify
def test_status_reports_verify_backend_and_model(monkeypatch):
    """status(SETTING) ACTIVE reflects the VERIFY backend+model, which may DIFFER from
    capture's (e.g. a different model on the same provider)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_enabled="on", ai_verify_model="claude-sonnet-4-6")
    st = ai_verify.status(ai_verify.SETTING)
    assert st["active"] is True
    assert st["provider"] == "claude"
    assert st["model"] == "claude-sonnet-4-6"             # the verify model, not capture's
    assert "Claude" in st["provider_label"]


def test_status_reports_verify_backend_override(monkeypatch):
    """status(SETTING) reflects an OVERRIDDEN verify provider (the other backend)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_enabled="on", ai_verify_backend="openai")
    st = ai_verify.status(ai_verify.SETTING)
    assert st["active"] is True
    assert st["provider"] == "openai" and st["model"] == "gpt-4o"


def test_status_default_unchanged(monkeypatch):
    """With no override, status(SETTING) is byte-identical to today (shared backend + default
    model)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_enabled="on")
    st = ai_verify.status(ai_verify.SETTING)
    assert st == {"active": True, "provider": "claude",
                  "provider_label": "Claude (Anthropic)", "model": "claude-opus-4-8",
                  "reason": ""}


def test_status_invalid_backend_override_inactive(monkeypatch):
    """A non-vision verify-backend override yields a clear INACTIVE status (not a silent
    fallback in the card)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_enabled="on", ai_verify_backend="azure")
    st = ai_verify.status(ai_verify.SETTING)
    assert st["active"] is False
    assert "ai_verify_backend" in st["reason"] and "azure" in st["reason"]


def test_capture_status_ignores_verify_override(monkeypatch):
    """The CAPTURE card's status (status(VC.SETTING)) is unaffected by the verify overrides —
    it always shows the shared backend + that backend's default model."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, **{VC.SETTING: "on", "ai_verify_model": "claude-sonnet-4-6",
                              "ai_verify_backend": "openai"})
    st = ai_verify.status(VC.SETTING)
    assert st["active"] is True
    assert st["provider"] == "claude" and st["model"] == "claude-opus-4-8"   # capture default


# =============================================================== test_connection probes verify
def test_test_connection_probes_verify_model(monkeypatch):
    """The 'Test AI connection' probe sends the VERIFY model (the override), so the admin
    tests exactly what verify() will use."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_model="claude-sonnet-4-6")
    sent = {}
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda p, d, images, model=None: sent.__setitem__("model", model)
                        or {"ok": True})
    res = ai_verify.test_connection()
    assert res["ok"] is True
    assert sent["model"] == "claude-sonnet-4-6"          # the verify model was probed
    assert res["model"] == "claude-sonnet-4-6"
    assert "claude-sonnet-4-6" in res["message"]


def test_test_connection_probes_verify_backend_override(monkeypatch):
    """test_connection routes to the overridden verify BACKEND (the other provider)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_backend="openai")
    routed = []
    monkeypatch.setitem(ai_verify._VISION_CALL, "claude",
                        lambda *a, **k: routed.append("claude") or {"ok": True})
    monkeypatch.setitem(ai_verify._VISION_CALL, "openai",
                        lambda *a, **k: routed.append("openai") or {"ok": True})
    res = ai_verify.test_connection()
    assert res["ok"] is True
    assert routed == ["openai"]
    assert res["model"] == "gpt-4o"


# =============================================================== key never leaked
def test_key_never_leaked_in_status_or_test(monkeypatch):
    """Neither status() nor test_connection() ever surfaces the API key, even with an
    override set and an error echoing the key."""
    secret = "sk-ANT-SUPER-SECRET-OVERRIDE-KEY"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setattr(ai_verify.ai_review, "resolve_backend", lambda *a, **k: "claude")
    _settings(monkeypatch, ai_verify_enabled="on", ai_verify_model="claude-sonnet-4-6")

    st = ai_verify.status(ai_verify.SETTING)
    assert secret not in repr(st)

    class _Resp:
        status_code = 401
        text = f'{{"error":"invalid api key {secret}"}}'

    def _boom(*a, **k):
        e = Exception(f"auth failed {secret}")
        e.response = _Resp()
        raise e

    monkeypatch.setitem(ai_verify._VISION_CALL, "claude", _boom)
    res = ai_verify.test_connection()
    assert secret not in res["message"]
    assert res["ok"] is False
