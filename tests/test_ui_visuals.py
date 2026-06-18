"""
UI VISUALS smoke — the presentation-only upgrade (nav glyphs, the home tile grid,
the reusable .chip/.tiles/.tile CSS). These assert the NEW markup is present and the
pages still serve 200; they do NOT touch routes, data, or behaviour.
"""


def test_home_renders_tile_grid(client):
    html = client.get("/").get_data(as_text=True)
    assert "Welcome to Fleet Fuel" in html
    # the section list is now a visual tile grid, not text+link cards
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
