"""
UI VISUALS smoke — the presentation-only upgrade (nav glyphs, the home tile grid,
the reusable .chip/.tiles/.tile CSS). These assert the NEW markup is present and the
pages still serve 200; they do NOT touch routes, data, or behaviour.
"""


def test_home_renders_tile_grid(client):
    html = client.get("/").get_data(as_text=True)
    # the home page is now an action center; the section nav is preserved below it
    assert "Jump to" in html
    # the section list is still a visual tile grid, not text+link cards
    assert 'class="tiles"' in html
    assert 'class="tile"' in html
    # each tile is a link to its section (admin client sees Analytics + Admin)
    assert 'href="/analytics"' in html
    assert 'href="/admin"' in html


def test_nav_has_icon_glyphs(client):
    html = client.get("/").get_data(as_text=True)
    # leading wayfinding glyph on the Home item + the brand truck
    assert '<span class="ic">🏠</span>Home' in html
    assert "🚛" in html


def test_base_css_defines_visual_classes(client):
    # the reusable visual primitives live in the inline BASE <style> (CSP-safe)
    html = client.get("/").get_data(as_text=True)
    assert ".chip{" in html
    assert ".tiles{" in html
    assert ".tile{" in html


def test_dashboard_close_status_uses_chip(client):
    # the month-close status detail lines render as status pills (.chip)
    html = client.get("/analytics").get_data(as_text=True)
    assert "Month-close status" in html
    assert 'class="chip' in html


# ----------------------------------------------------------------- design-system pass
def test_base_css_defines_design_system_primitives(client):
    # the polish pass adds: spacing tokens, button hierarchy, toast system, semantic
    # status chips, empty-state + money helpers — all in the inline BASE <style> (CSP-safe)
    html = client.get("/").get_data(as_text=True)
    assert "--s1:4px" in html            # spacing scale token
    assert ".btn-secondary" in html      # button hierarchy
    assert ".btn-danger" in html
    assert ".toast{" in html             # toast system
    assert "#toasts{" in html
    assert ".chip.s-done" in html        # semantic status-chip variants
    assert ".chip.s-blocked" in html
    assert ".empty{" in html             # empty-state block
    assert "button.working" in html      # submit/loading state


def test_toasts_container_present(client):
    # app.js reads a server-rendered #flash[data-toast] and pops it; the live region
    # container is always in the chrome
    html = client.get("/").get_data(as_text=True)
    assert 'id="toasts"' in html


def test_app_js_exposes_toast_and_loading(client):
    js = client.get("/app.js").get_data(as_text=True)
    assert "function toast(" in js
    assert "data-toast" in js            # reads the server flash element
    assert "classList.add('working')" in js  # submit/loading state


def test_status_chip_helper_maps_codes():
    import app
    # fixed colour mapping: missing-docs blocked (red), money received done (green),
    # rejection blocked (red), closed done (green)
    assert "s-blocked" in app._status_chip("1A")
    assert "s-done" in app._status_chip("3A")
    assert "s-blocked" in app._status_chip("3B")
    assert "s-done" in app._status_chip("5")
    # unknown code degrades to neutral, text is escaped
    assert "s-neutral" in app._status_chip("ZZ")
    assert "<script>" not in app._status_chip("<script>", "x")


def test_eur_helper_format():
    import app
    # chosen format: "€1,234.56" (€ prefix, comma thousands, dot decimal)
    assert app._eur(1234.5) == "€1,234.50"
    assert app._eur(0) == "€0.00"
    assert app._eur(None) == "—"


def test_recovery_page_has_legend_and_sortable_or_empty(client):
    html = client.get("/recovery").get_data(as_text=True)
    # either the sortable claims table with a status legend, or a friendly empty state
    assert ("chip-legend" in html and "sortable" in html) or "empty" in html
    assert "NET EUR" in html


def test_suppliers_page_renders(client):
    html = client.get("/suppliers").get_data(as_text=True)
    # supplier status pill or an empty-state when there are none
    assert ('class="chip s-done"' in html or 'class="chip s-neutral"' in html
            or 'class="empty"' in html)


# ----------------------------------------------------------- mobile help declutter
def test_mobile_help_collapse_css_present(client):
    """On phones (<=640px) plain advisory .note help collapses and only reveals
    when the card carries .help-open; the toggle is hidden on desktop. All these
    rules live in the inline BASE <style> (CSP-safe)."""
    html = client.get("/").get_data(as_text=True)
    # the affordance is hidden by default (desktop shows everything as today)
    assert ".helptoggle{display:none}" in html
    # collapse only PLAIN notes, never status notes (.bad / .ok)
    assert ".has-help>.note:not(.bad):not(.ok)" in html
    assert ".has-help .note:not(.bad):not(.ok){display:none}" in html
    # the reveal rule re-shows them when .help-open is set (gated by the toggle)
    assert ".has-help.help-open" in html
    # the whole mechanism is inside the <=640px media query (desktop untouched)
    media = html.split("@media (max-width:640px){", 1)[1]
    assert ".has-help>.note:not(.bad):not(.ok)" in media
    assert ".helptoggle{display:inline-flex" in media


def test_mobile_help_toggle_in_app_js(client):
    """app.js wires the per-card toggle as progressive enhancement: it marks
    cards that hold plain help with .has-help and flips .help-open on tap. With
    JS off no card gets .has-help, so the CSS collapse is inert (help shows)."""
    js = client.get("/app.js").get_data(as_text=True)
    assert "helptoggle" in js
    assert "has-help" in js          # the JS-off fallback gate
    assert "help-open" in js
    # status/error notes are excluded from the collapse (never hidden)
    assert "classList.contains('bad')" in js
    assert "classList.contains('ok')" in js
    # only cards that actually contain help get a toggle (no toggle wall)
    assert "if(!notes.length) return" in js


def test_help_text_still_in_dom_server_side(client):
    """The help text must remain server-rendered (hidden via CSS on mobile only),
    so the dense admin/recovery pages keep their .note help in the DOM."""
    for path in ("/admin", "/recovery", "/vat", "/suppliers"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="note"' in html, path
