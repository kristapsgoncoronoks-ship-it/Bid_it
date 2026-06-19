"""
AI review assistant — web surface (Admin toggle + advisory draft-review panel).

The Admin form persists `ai_review_backend` (default 'none' = OFF), and the draft
review screen shows an advisory panel ONLY when a backend is configured. `ai_review.review`
is monkeypatched to canned flags+note (NO live API). The panel is advisory: the commit
gate (`/extract/confirm` / can_commit) is unaffected and every cell is escaped.
"""
import re

import pytest

import ai_review


def _tok(client, path="/admin"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_admin_persists_ai_review_backend(client):
    import auth
    # default OFF
    auth.set_setting("ai_review_backend", "none")
    assert ai_review.resolve_backend() == "none"
    # persist via the admin form
    client.post("/admin", data={"_csrf": _tok(client), "__act": "set_ai_review",
                                "ai_review_backend": "claude"})
    assert auth.get_setting("ai_review_backend") == "claude"
    assert ai_review.resolve_backend() == "claude"
    # an unknown value is coerced to 'none'
    client.post("/admin", data={"_csrf": _tok(client), "__act": "set_ai_review",
                                "ai_review_backend": "bogus"})
    assert ai_review.resolve_backend() == "none"
    # restore (don't leak to other tests)
    auth.set_setting("ai_review_backend", "none")


def test_admin_card_renders_select(client):
    html = client.get("/admin").get_data(as_text=True)
    assert "AI review assistant" in html
    assert 'name="ai_review_backend"' in html


# --------------------------------------------------------------- advisory panel (A3)
DRAFT = {
    "supplier": "DKV", "supplier_vat": "LV40003XXXX", "statement_ref": "S-9",
    "statement_date": "2026-05-31", "currency": "EUR", "customer": "",
    "backend": "parser", "confidence": "medium", "files": [],
    "lines": [{"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
               "net": 1000.0, "vat": 210.0, "_source": "doc.pdf"}],
}


def _stash(token):
    import app as A
    A._stash_draft(token, DRAFT)


def test_ai_review_off_shows_note_and_no_call(client, monkeypatch):
    import auth, ai_review
    auth.set_setting("ai_review_backend", "none")
    called = []
    monkeypatch.setattr(ai_review, "review", lambda *a, **k: called.append(1) or {})
    _stash("00000000000a0001")
    # the review screen shows the muted "off" note when backend is none
    r = client.post("/extract/ai-review",
                    data={"_csrf": _tok(client, "/extract"), "token": "00000000000a0001",
                          "period": "2026-05"})
    html = r.get_data(as_text=True)
    assert "AI review is off" in html
    assert called == []                                 # NO review() call when off


def test_ai_review_panel_escapes_and_does_not_gate(client, monkeypatch):
    import auth, ai_review
    auth.set_setting("ai_review_backend", "claude")
    injection = '<img src=x onerror=alert(1)>'
    canned = {
        "flags": [{"field": "supplier", "severity": "warn", "message": injection,
                   "suggestion": None}],
        "note": "Prices look in range.",
        "deterministic": {"errors": 0, "warnings": 0, "can_commit": True, "lines": []},
        "backend": "claude", "model": "claude-opus-4-8",
        "sent_keys": ["supplier", "lines"],
    }
    seen = {}
    def _review(draft, ctx=None, *a, **k):
        seen["draft"] = dict(draft)                      # capture to assert no mutation
        return canned
    monkeypatch.setattr(ai_review, "review", _review)
    _stash("00000000000a0002")
    r = client.post("/extract/ai-review",
                    data={"_csrf": _tok(client, "/extract"), "token": "00000000000a0002",
                          "period": "2026-05"})
    html = r.get_data(as_text=True)
    # the HTML-injection flag message is ESCAPED, never rendered as a live tag
    assert injection not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    # note + provenance present
    assert "Prices look in range." in html
    assert "advisory only" in html
    assert "claude-opus-4-8" in html
    # the deterministic block, not the AI, owns the commit decision
    assert "can commit" in html.lower()
    # the confirm form (commit path) is still on the page, unaffected
    assert 'action="/extract/confirm"' in html
    auth.set_setting("ai_review_backend", "none")


# ----------------------------------------------------- period default from month_config
def test_period_default_comes_from_month_config(client, monkeypatch):
    """The draft-review period field defaults to month_config's current/active PERIOD —
    NOT a stale literal. Monkeypatch month_config.PERIOD and assert it is what the
    re-rendered confirm form / AI-review button prefill (and the import form too)."""
    import month_config, ai_review, auth
    monkeypatch.setattr(month_config, "PERIOD", "2099-12")
    import app as A
    # the helper sources the active period straight from month_config
    assert A._default_period() == "2099-12"
    # off-path AI-review re-render: no explicit period -> falls back to month_config
    auth.set_setting("ai_review_backend", "none")
    _stash("00000000000a0003")
    r = client.post("/extract/ai-review",
                    data={"_csrf": _tok(client, "/extract"), "token": "00000000000a0003"})
    html = r.get_data(as_text=True)
    assert 'value="2099-12"' in html          # month_config period, not "2026-05"
    assert 'value="2026-05"' not in html      # the old stale literal is gone


# ------------------------------------------- _contract_price_terms merges matching rules
def test_contract_price_terms_merges_rebate_and_ceiling_rules():
    """A supplier with a rebate-ONLY rule and a separate ceiling-ONLY rule must surface
    BOTH (first non-None of each), not just the first matching rule's pair."""
    import app as A

    class _SM:
        def discount_rules(self):
            return [
                # rebate-only rule (no ceiling)
                {"supplier": "DKV", "country": "%", "product_group": "Diesel",
                 "expected_discount_eur_l": 0.045, "max_net_eur_l": None},
                # separate ceiling-only rule (no rebate)
                {"supplier": "DKV", "country": "Belgium", "product_group": "Diesel",
                 "expected_discount_eur_l": None, "max_net_eur_l": 1.62},
            ]

    exp_disc, ceiling = A._contract_price_terms(_SM(), "dkv", "Belgium")
    assert exp_disc == 0.045        # from the rebate-only rule
    assert ceiling == 1.62          # merged in from the separate ceiling-only rule


def test_contract_price_terms_no_match_returns_none():
    import app as A

    class _SM:
        def discount_rules(self):
            return [{"supplier": "OTHER", "country": "%", "product_group": "Diesel",
                     "expected_discount_eur_l": 0.05, "max_net_eur_l": 1.5}]

    assert A._contract_price_terms(_SM(), "DKV", "Belgium") == (None, None)
