"""
HOME action center — the "what needs me today" landing page.

Presentation + READ-ONLY aggregation over CANONICAL sources (recovery_report,
claims_overview, waiting_room, supplier_sync, auth.recent_errors). These tests assert:
  * the action center renders (needs-attention row + recoverable-VAT KPI + section nav);
  * role-gating: an admin sees the admin-only tiles (pending supplier changes, error
    log), a processor does NOT;
  * the attention counts reflect their sources (a seeded pending supplier change is 1
    for the admin, and the tile is simply absent for a processor);
  * the page never 500s — even with a fresh/empty DB and even when a single data source
    raises (the tile degrades, the page stays 200);
  * no horizontal overflow at 390px (the auto-fit grids collapse).
"""
import auth


def _login(user, pw, role):
    import app as A
    try:
        auth.add_user(user, pw, role=role)
    except Exception:
        pass
    c = A.app.test_client()
    assert c.post("/login", data={"username": user, "password": pw}).status_code == 302
    return c


def _processor_client(admin_session):
    return _login("home_proc", "Proc!Pw123", "processor")


# ---------------------------------------------------------------- structure / KPIs
def test_home_is_action_center_for_admin(client):
    html = client.get("/").get_data(as_text=True)
    assert "Needs attention" in html                 # the action row
    assert "Jump to" in html                          # section nav preserved
    assert 'class="atiles"' in html                   # the action-tile grid
    assert "Invoices awaiting review" in html         # the intake review tile
    # headline KPI — recoverable VAT outstanding, NET-EUR basis
    assert "Recoverable VAT outstanding" in html
    assert "NET EUR" in html


def test_home_status_is_200_admin_and_processor(client, admin_session):
    assert client.get("/").status_code == 200
    p = _processor_client(admin_session)
    assert p.get("/").status_code == 200


# ---------------------------------------------------------------- role gating
def test_admin_only_tiles_hidden_from_processor(client, admin_session):
    admin_html = client.get("/").get_data(as_text=True)
    # admin sees the admin-only action tiles
    assert "Pending supplier changes" in admin_html
    assert "Recent errors" in admin_html
    assert 'href="/supplier-changes"' in admin_html

    proc_html = _processor_client(admin_session).get("/").get_data(as_text=True)
    # a processor does NOT see the admin-only tiles (or the admin-only VAT KPI)
    assert "Pending supplier changes" not in proc_html
    assert "Recent errors" not in proc_html
    assert "Recoverable VAT outstanding" not in proc_html
    # but DOES see its own work tile
    assert "Invoices awaiting review" in proc_html


# ---------------------------------------------------------------- counts reflect source
def test_pending_supplier_change_count_reflects_source(client, admin_session, monkeypatch):
    import app as A
    import supplier_sync as ss

    # Force a non-zero pending-supplier-change count via the CANONICAL helper.
    monkeypatch.setattr(ss, "pending_count", lambda *a, **k: 3)
    html = client.get("/").get_data(as_text=True)
    # the count appears on the admin's pending-supplier-changes tile (amber/needs-action)
    assert "Pending supplier changes" in html
    assert ">3<" in html

    # a processor never sees this tile regardless of the count
    proc_html = _processor_client(admin_session).get("/").get_data(as_text=True)
    assert "Pending supplier changes" not in proc_html


def test_invoices_awaiting_review_reflects_queue(client, monkeypatch):
    import waiting_room as wr

    base = wr.counts()
    # patch counts() to report a known number of drafts ready for human review
    fake = dict(base); fake["ready"] = 7
    monkeypatch.setattr(wr, "counts", lambda: fake)
    html = client.get("/").get_data(as_text=True)
    assert "Invoices awaiting review" in html
    assert ">7<" in html
    assert 'href="/extract"' in html


def test_recent_errors_count_reflects_source(client, monkeypatch):
    import auth as A2

    monkeypatch.setattr(A2, "recent_errors", lambda *a, **k: [{"id": 1}, {"id": 2}])
    html = client.get("/").get_data(as_text=True)
    assert "Recent errors" in html
    assert ">2<" in html


# ---------------------------------------------------------------- crash safety
def test_home_renders_when_a_source_raises(client, monkeypatch):
    """If a single data source raises, that tile degrades to a neutral 'unavailable'
    tile and the home page still renders 200 — it never 500s."""
    import supplier_sync as ss

    def boom(*a, **k):
        raise RuntimeError("supplier_sync exploded")

    monkeypatch.setattr(ss, "pending_count", boom)
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # the tile degraded to a neutral unavailable state, page still rendered
    assert "Pending supplier changes (unavailable)" in html
    # the rest of the action center is intact
    assert "Needs attention" in html


def test_home_renders_when_recovery_raises(client, monkeypatch):
    """The headline VAT KPI degrades to an 'unavailable' note if recovery_report
    raises — the page still renders 200."""
    import vat_refund as VR

    def boom(*a, **k):
        raise RuntimeError("recovery exploded")

    monkeypatch.setattr(VR, "recovery_report", boom)
    r = client.get("/")
    assert r.status_code == 200
    assert "temporarily unavailable" in r.get_data(as_text=True)


def test_home_renders_with_vat_module_off(client):
    """With the VAT module switched off, the admin-only VAT KPI + claims-ready tile
    are absent and the page still renders 200 (no VAT KPI leak)."""
    import app as A
    A._auth.set_setting("module_vat", "off")
    try:
        r = client.get("/")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Recoverable VAT outstanding" not in html
        assert "Claims ready to file" not in html
    finally:
        A._auth.set_setting("module_vat", "on")


# ---------------------------------------------------------------- mobile / no overflow
def test_home_uses_responsive_autofit_grids(client):
    """The action tiles + KPIs reuse the existing auto-fit grids, which collapse to
    1-2 columns on a narrow viewport — assert the grid classes + the 480px media rule
    are present so nothing overflows at 390px."""
    html = client.get("/").get_data(as_text=True)
    # the action-tile grid class is present
    assert 'class="atiles"' in html
    # the mobile media rule collapses both grids (atiles -> 2 col, kpis -> 1 col)
    assert "@media (max-width:480px)" in html
    assert ".atiles{grid-template-columns:1fr 1fr}" in html
    # the KPI grid is the existing auto-fit one (shared design system)
    assert "repeat(auto-fit,minmax(160px,1fr))" in html
