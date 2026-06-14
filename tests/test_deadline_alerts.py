"""Filing-deadline alerts for the VAT-recovery module (2008/9/EC: 30-Sep of the year
AFTER the claim period's year; a miss forfeits the whole refund).

Covers vat_refund.approaching_deadlines (the single source of truth) and the notify
digest "Filing deadlines approaching" section. The core regression: a PRIOR-YEAR period
whose 30-Sep deadline lands within the window must surface even though the period's own
year != today.year (the old dashboard only scanned the displayed period's year).

`approaching_deadlines` scans both today.year and today.year-1 and computes filing
`deadline_days` inside claims_overview against the real calendar; to keep these tests
deterministic we MONKEYPATCH claims_overview to a constructed claims state and pass an
explicit `today=` so the action-deadline math and year-scan are pinned.
"""
import datetime
import importlib


def _vr():
    import vat_refund
    importlib.reload(vat_refund)
    return vat_refund


def _overview(to_submit=None, open_claims=None):
    return {"to_submit": list(to_submit or []), "open": list(open_claims or [])}


def _filing(entity, country, period, vat_eur, days_left, ready=True, issues=None):
    """A to_submit row as claims_overview emits it (deadline ISO + deadline_days)."""
    # the deadline string is cosmetic for the helper; days_left is what gates.
    return dict(entity=entity, country=country, period=period, vat_eur=vat_eur,
                ready=ready, issues=issues or [], deadline=f"{int(period[:4]) + 1}-09-30",
                deadline_days=days_left, code="1E")


def _open(entity, country, period, code, action_deadline):
    return dict(entity=entity, country=country, period=period, code=code,
                action_deadline=action_deadline)


# ---------------------------------------------------------------- core helper logic
def test_prior_year_period_with_near_deadline_surfaces(monkeypatch):
    """THE BUG: a claimable, not-submitted period in 2025 (deadline 2025+1 = 2026-09-30)
    must surface when 'today' makes that deadline near, even though 2025 != today.year."""
    vr = _vr()
    today = datetime.date(2026, 8, 1)            # 60 days before 2026-09-30
    # claims_overview(2025) carries the prior-year period (deadline 60d out);
    # claims_overview(2026) is empty.
    def fake(year):
        if year == 2025:
            return _overview(to_submit=[_filing("Acme SIA", "Belgium", "2025-Q1",
                                                1000.0, 60)])
        return _overview()
    monkeypatch.setattr(vr, "claims_overview", fake)

    out = vr.approaching_deadlines(within_days=60, today=today)
    assert len(out) == 1
    item = out[0]
    assert item["kind"] == "filing" and item["period"] == "2025-Q1"
    assert item["overdue"] is False and item["days_left"] == 60
    assert item["vat_eur"] == 1000.0


def test_overdue_flagged_and_sorts_first(monkeypatch):
    vr = _vr()
    today = datetime.date(2026, 10, 15)          # past 2026-09-30
    def fake(year):
        if year == 2025:
            return _overview(to_submit=[
                _filing("Acme SIA", "Belgium", "2025-Q1", 1000.0, -15),    # overdue
                _filing("Beta UAB", "Poland", "2025-Q2", 500.0, 40),       # still live
            ])
        return _overview()
    monkeypatch.setattr(vr, "claims_overview", fake)

    out = vr.approaching_deadlines(within_days=60, today=today)
    assert [o["days_left"] for o in out] == [-15, 40]      # most overdue first
    assert out[0]["overdue"] is True and out[1]["overdue"] is False


def test_zero_vat_and_submitted_not_flagged(monkeypatch):
    vr = _vr()
    today = datetime.date(2026, 8, 1)
    def fake(year):
        if year == 2025:
            # vat_eur <= 0 -> not a filing risk; submitted/paid claims are not in
            # to_submit at all so they never appear.
            return _overview(to_submit=[_filing("Acme SIA", "Belgium", "2025-Q1", 0.0, 30)])
        return _overview()
    monkeypatch.setattr(vr, "claims_overview", fake)

    assert vr.approaching_deadlines(within_days=60, today=today) == []


def test_action_deadline_window(monkeypatch):
    vr = _vr()
    today = datetime.date(2026, 6, 14)
    def fake(year):
        if year == 2026:
            return _overview(open_claims=[
                _open("Acme SIA", "Belgium", "2026-Q1", "2B", "2026-07-01"),   # 17d -> in
                _open("Beta UAB", "Poland", "2026-Q1", "3D", "2026-12-31"),    # ~200d -> out
            ])
        return _overview()
    monkeypatch.setattr(vr, "claims_overview", fake)

    out = vr.approaching_deadlines(within_days=60, today=today)
    assert len(out) == 1
    assert out[0]["kind"] == "action" and out[0]["code"] == "2B"
    assert out[0]["days_left"] == 17 and out[0]["overdue"] is False


def test_action_deadline_overdue(monkeypatch):
    vr = _vr()
    today = datetime.date(2026, 6, 14)
    def fake(year):
        if year == 2026:
            return _overview(open_claims=[
                _open("Acme SIA", "Belgium", "2026-Q1", "3D", "2026-06-01")])  # -13d
        return _overview()
    monkeypatch.setattr(vr, "claims_overview", fake)

    out = vr.approaching_deadlines(within_days=60, today=today)
    assert len(out) == 1 and out[0]["overdue"] is True and out[0]["days_left"] == -13


def test_dedup_across_years(monkeypatch):
    vr = _vr()
    today = datetime.date(2026, 8, 1)
    item = _filing("Acme SIA", "Belgium", "2025-Q1", 1000.0, 60)
    # both scanned years accidentally return the same claim -> emit once.
    monkeypatch.setattr(vr, "claims_overview", lambda year: _overview(to_submit=[item]))
    out = vr.approaching_deadlines(within_days=60, today=today)
    assert len(out) == 1


def test_never_raises_on_bogus_state(monkeypatch, tmp_path):
    """Read-only contract: any error returns [] (never raises). Exercise BOTH a source
    that raises and the real empty-DB path."""
    vr = _vr()
    def boom(year):
        raise RuntimeError("db gone")
    monkeypatch.setattr(vr, "claims_overview", boom)
    assert vr.approaching_deadlines(within_days=60, today=datetime.date(2026, 6, 14)) == []

    # real path over an empty/unseeded DB must also just return [].
    vr2 = _vr()
    monkeypatch.setattr(vr2, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vr2, "ANALYTICS_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(vr2, "_SCHEMA_READY", set())
    assert vr2.approaching_deadlines(within_days=60, today=datetime.date(2026, 6, 14)) == []


# ---------------------------------------------------------------- notify digest
def test_digest_has_filing_section_and_overdue_wording(monkeypatch):
    import notify
    importlib.reload(notify)
    import vat_refund
    importlib.reload(vat_refund)

    # one overdue filing + one live action; pin approaching_deadlines so the digest is
    # deterministic regardless of the real calendar / DB.
    monkeypatch.setattr(vat_refund, "approaching_deadlines", lambda within_days=60, today=None: [
        dict(kind="filing", entity="Acme SIA", country="Belgium", period="2025-Q1",
             vat_eur=1234.5, deadline="2026-09-30", days_left=-5, overdue=True,
             ready=True, issues=[]),
        dict(kind="action", entity="Beta UAB", country="Poland", period="2026-Q1",
             code="2B", deadline="2026-07-01", days_left=17, overdue=False),
    ])
    # silence the other sources so the test isolates this section.
    for name in ("claims_overview", "recovery_report"):
        monkeypatch.setattr(vat_refund, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))

    sections = notify._digest_sections(2026)
    headings = [h for h, _ in sections]
    assert "Filing deadlines approaching" in headings
    lines = dict(sections)["Filing deadlines approaching"]
    assert any(l.startswith("FILE Acme SIA") and "OVERDUE by 5d" in l for l in lines)
    assert any(l.startswith("RESPOND (document request) Beta UAB") and "17d left" in l
               for l in lines)

    text, html = notify.render_digest(2026)
    assert "Filing deadlines approaching" in text and "OVERDUE by 5d" in text
    assert "Filing deadlines approaching" in html
