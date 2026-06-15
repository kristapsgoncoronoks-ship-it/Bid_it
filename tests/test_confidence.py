"""
Confidence-learning model — per-(supplier x country) trust + append-only event ledger.

CARDINAL RULE under test: confidence reduces redundant WORK, it NEVER bypasses a legal
gate. The ONLY consumer is the advisory AI review: a trusted pair SKIPS computing the AI
panel; an untrusted one runs it. Every public function NEVER raises and `should_skip_ai`
fails toward DOING the review (False) on error.

Determinism: each test owns a throwaway confidence.db (the module DB path is monkeypatched
to a tmp file) so there is no cross-test bleed and no real calendar dependence beyond the
created_at stamp.
"""
import re

import pytest


# ---------------------------------------------------------------- isolation
@pytest.fixture()
def conf(tmp_path, monkeypatch):
    """confidence module pointed at a throwaway DB (no shared state, no churn)."""
    import confidence
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    return confidence


# ---------------------------------------------------------------- growth math
def test_growth_math_hand_checked(conf):
    # INIT 0.50 -> one clean -> 0.50 + 0.25*(0.95-0.50) = 0.6125
    t1 = conf.record_validation("DKV", "Belgium", clean=True)
    assert t1 == pytest.approx(0.6125)
    # a second clean -> 0.6125 + 0.25*(0.95-0.6125) = 0.696875
    t2 = conf.record_validation("DKV", "Belgium", clean=True)
    assert t2 == pytest.approx(0.696875)
    assert conf.trust("DKV", "Belgium") == pytest.approx(0.696875)


def test_decay_math_hand_checked(conf):
    # build up to 0.6125, then a not-clean -> 0.6125 - 0.30 = 0.3125
    conf.record_validation("DKV", "Belgium", clean=True)        # -> 0.6125
    t = conf.record_validation("DKV", "Belgium", clean=False)   # -> 0.3125
    assert t == pytest.approx(0.3125)


def test_floor_clamp(conf):
    # repeated decay floors at FLOOR (0.10) and never goes below
    last = None
    for _ in range(10):
        last = conf.record_validation("S", "C", clean=False)
    assert last == pytest.approx(conf.FLOOR)
    assert conf.trust("S", "C") == pytest.approx(0.10)


def test_ceil_clamp(conf):
    # repeated clean caps at CEIL (0.95) and never exceeds
    last = None
    for _ in range(50):
        last = conf.record_validation("S", "C", clean=True)
    assert last == pytest.approx(conf.CEIL)
    assert conf.trust("S", "C") == pytest.approx(0.95)


# ---------------------------------------------------------------- defaults + thresholds
def test_trust_defaults_to_init_for_unseen(conf):
    assert conf.trust("UNSEEN", "ZZ") == pytest.approx(conf.INIT)
    assert conf.INIT == 0.50


def test_skip_and_human_review_thresholds(conf):
    # below skip threshold at INIT -> do the review
    assert conf.should_skip_ai("X", "Y") is False
    # push above SKIP_AI_TRUST (0.85): from 0.50 it takes 4 cleans
    #   0.6125 -> 0.696875 -> 0.7601... -> 0.8200... (>= 0.85? not yet) keep going
    for _ in range(6):
        conf.record_validation("X", "Y", clean=True)
    assert conf.trust("X", "Y") >= conf.SKIP_AI_TRUST
    assert conf.should_skip_ai("X", "Y") is True
    # human-review threshold (< 0.30): drive an unseen pair down past it
    conf.record_validation("LOW", "C", clean=False)   # 0.50 -> 0.20
    assert conf.trust("LOW", "C") < conf.HUMAN_REVIEW_TRUST
    assert conf.should_human_review("LOW", "C") is True
    # a fresh pair at INIT is NOT below the human threshold
    assert conf.should_human_review("FRESH", "C") is False


def test_should_skip_ai_fails_safe_on_broken_db(conf, monkeypatch):
    # a broken connect() must yield False (do the review), never True (skip).
    def _boom():
        raise RuntimeError("db is on fire")
    monkeypatch.setattr(conf, "connect", _boom)
    assert conf.should_skip_ai("X", "Y") is False
    # trust falls back to INIT, scoreboard/recent_events fall back to []
    assert conf.trust("X", "Y") == pytest.approx(conf.INIT)
    assert conf.scoreboard() == []
    assert conf.recent_events() == []
    # record_validation never raises either
    assert conf.record_validation("X", "Y", clean=True) == pytest.approx(conf.INIT)


# ---------------------------------------------------------------- ledger + counters
def test_ledger_appends_one_row_per_record(conf):
    conf.record_validation("DKV", "BE", clean=True, source="ai_review", detail="ok")
    conf.record_validation("DKV", "BE", clean=False, source="ai_review", detail="flag")
    conf.record_validation("E100", "PL", clean=True)
    events = conf.recent_events()
    assert len(events) == 3
    # newest first
    assert events[0]["supplier"] == "E100" and events[0]["country"] == "PL"
    assert events[0]["clean"] is True
    assert events[1]["supplier"] == "DKV" and events[1]["clean"] is False
    assert events[1]["detail"] == "flag"


def test_counters_track_clean_and_flagged(conf):
    conf.record_validation("DKV", "BE", clean=True)
    conf.record_validation("DKV", "BE", clean=True)
    conf.record_validation("DKV", "BE", clean=False)
    board = {(r["supplier"], r["country"]): r for r in conf.scoreboard()}
    row = board[("DKV", "BE")]
    assert row["n_clean"] == 2
    assert row["n_flagged"] == 1


# ---------------------------------------------------------------- scoreboard shape
def test_scoreboard_sorted_desc_and_shape(conf):
    conf.record_validation("LOW", "C", clean=False)    # -> 0.20
    conf.record_validation("HIGH", "C", clean=True)    # -> 0.6125
    board = conf.scoreboard()
    assert [r["supplier"] for r in board] == ["HIGH", "LOW"]   # desc by trust
    r = board[0]
    assert set(r) == {"supplier", "country", "trust", "n_clean", "n_flagged", "updated_at"}
    assert isinstance(r["trust"], float)
    assert r["updated_at"]   # a timestamp was stamped


def test_recent_events_limit(conf):
    for i in range(5):
        conf.record_validation("S", "C", clean=True, detail=str(i))
    assert len(conf.recent_events(limit=2)) == 2
    assert len(conf.recent_events()) == 5
    # a bogus limit falls back to the default (100), never raises
    assert len(conf.recent_events(limit="bogus")) == 5


def test_never_raise_on_missing_db(conf, tmp_path, monkeypatch):
    # point at a path whose parent does not exist -> connect fails; all readers -> safe
    monkeypatch.setattr(conf, "DB", str(tmp_path / "nope" / "confidence.db"))
    assert conf.scoreboard() == []
    assert conf.recent_events() == []
    assert conf.trust("S", "C") == pytest.approx(conf.INIT)
    assert conf.should_skip_ai("S", "C") is False


# ---------------------------------------------------------------- web integration
DRAFT = {
    "supplier": "DKV", "supplier_vat": "LV40003XXXX", "statement_ref": "S-CONF",
    "statement_date": "2026-05-31", "currency": "EUR", "customer": "",
    "backend": "parser", "confidence": "medium", "files": [],
    "lines": [{"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
               "net": 1000.0, "vat": 210.0, "_source": "doc.pdf"}],
}


def _tok(client, path="/extract"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


@pytest.fixture()
def web_conf(tmp_path, monkeypatch):
    """Point the confidence module (as app sees it via `import confidence`) at a tmp DB,
    and stub the AI review backend ON so the route reaches the trust gate."""
    import confidence, auth
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    monkeypatch.setattr(auth, "get_setting",
                        lambda k, d=None: "claude" if k == "ai_review_backend" else d)
    return confidence


def test_route_skips_review_when_trusted(client, monkeypatch, web_conf):
    import app as A, ai_review
    # make the (supplier, country) pair fully trusted
    for _ in range(8):
        web_conf.record_validation("DKV", "Belgium", clean=True)
    assert web_conf.should_skip_ai("DKV", "Belgium") is True
    # ai_review.review must NOT be called on a skip
    monkeypatch.setattr(ai_review, "review",
                        lambda *a, **k: pytest.fail("review() called for a trusted pair"))
    A._stash_draft("tok_skip", DRAFT)
    n_before = len(web_conf.recent_events())
    r = client.post("/extract/ai-review",
                    data={"_csrf": _tok(client), "token": "tok_skip", "period": "2026-05"})
    html = r.get_data(as_text=True)
    assert "AI review skipped" in html
    assert "is trusted" in html
    assert "DKV" in html and "Belgium" in html
    # NO event recorded on a skip
    assert len(web_conf.recent_events()) == n_before


def test_route_runs_review_for_untrusted_and_records(client, monkeypatch, web_conf):
    import app as A, ai_review
    # fresh pair (INIT 0.50) is not trusted -> review runs
    assert web_conf.should_skip_ai("DKV", "Belgium") is False
    no_flags = {"flags": [], "note": "Prices look in range.",
                "deterministic": {"errors": 0, "warnings": 0, "can_commit": True,
                                  "lines": []},
                "backend": "claude", "model": "claude-opus-4-8", "sent_keys": []}
    monkeypatch.setattr(ai_review, "review", lambda *a, **k: no_flags)
    A._stash_draft("tok_run", DRAFT)
    r = client.post("/extract/ai-review",
                    data={"_csrf": _tok(client), "token": "tok_run", "period": "2026-05"})
    assert "AI review skipped" not in r.get_data(as_text=True)
    # a NO-flag review records a CLEAN validation -> trust rises off INIT
    assert web_conf.trust("DKV", "Belgium") == pytest.approx(0.6125)
    ev = web_conf.recent_events()
    assert len(ev) == 1 and ev[0]["clean"] is True and ev[0]["source"] == "ai_review"


def test_route_flagged_review_records_not_clean(client, monkeypatch, web_conf):
    import app as A, ai_review
    flagged = {"flags": [{"field": "supplier", "severity": "warn",
                          "message": "name mismatch", "suggestion": None}],
               "note": None,
               "deterministic": {"errors": 0, "warnings": 0, "can_commit": True,
                                 "lines": []},
               "backend": "claude", "model": "claude-opus-4-8", "sent_keys": []}
    monkeypatch.setattr(ai_review, "review", lambda *a, **k: flagged)
    A._stash_draft("tok_flag", DRAFT)
    client.post("/extract/ai-review",
                data={"_csrf": _tok(client), "token": "tok_flag", "period": "2026-05"})
    # a FLAGGED review records a NOT-clean validation -> trust falls off INIT
    assert web_conf.trust("DKV", "Belgium") == pytest.approx(0.20)
    ev = web_conf.recent_events()
    assert len(ev) == 1 and ev[0]["clean"] is False


# ---------------------------------------------------------------- admin scoreboard page
def test_admin_scoreboard_page(client, monkeypatch, tmp_path):
    import confidence
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    confidence.record_validation("DKV", "Belgium", clean=True, source="ai_review")
    html = client.get("/admin/confidence").get_data(as_text=True)
    assert "Confidence-learning scoreboard" in html
    assert "DKV" in html and "Belgium" in html
    # the explainer states trust never touches a legal gate
    assert "never" in html.lower() and "legal gate" in html.lower()


def test_admin_scoreboard_escapes_values(client, monkeypatch, tmp_path):
    import confidence
    monkeypatch.setattr(confidence, "DB", str(tmp_path / "confidence.db"))
    confidence.record_validation("<img src=x onerror=alert(1)>", "BE", clean=True)
    html = client.get("/admin/confidence").get_data(as_text=True)
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
