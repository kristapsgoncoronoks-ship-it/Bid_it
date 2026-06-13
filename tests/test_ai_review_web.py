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
