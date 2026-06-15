"""Tests for the visual analytics page (/reports) and the inline-SVG line chart.

The /reports page is the charts companion to the table-heavy analytics pages: it
renders dependency-free inline SVG (no external chart library, CSP-safe) over the
SAME canonical queries.py aggregates the dashboard/savings pages use. These tests
cover the page (auth, chart markup, section headings, a working period filter) and
the svg_line toolkit helper (renders for multi-series, doesn't crash on empty /
single-point input).
"""


def test_reports_page_loads(client):
    """A logged-in user GETs /reports -> 200, with inline-SVG charts present."""
    r = client.get("/reports")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<svg" in body                      # at least one inline-SVG chart
    # the spend/volume/price trends + the two spend-split bar charts each carry an svg
    assert body.count("<svg") >= 5


def test_reports_page_headings(client):
    """The expected chart section headings are all present (NET basis stated)."""
    body = client.get("/reports").get_data(as_text=True)
    for heading in ("Monthly net spend (EUR)", "Monthly volume (litres)",
                    "Effective net price by country", "Spend by supplier",
                    "Spend by country"):
        assert heading in body, heading
    # the NET basis is stated on the page (project convention)
    assert "NET EUR" in body


def test_reports_period_filter(client):
    """The period filter form drives the spend-split cards: a valid period -> 200
    and the chosen period shows up in the rendered page."""
    import app as A
    con = A.DB()
    try:
        from queries import q_periods
        periods = q_periods(con)
    finally:
        con.close()
    if not periods:
        # no loaded data in this checkout — the page must still render.
        assert client.get("/reports").status_code == 200
        return
    period = periods[-1]
    r = client.get("/reports", query_string={"period": period})
    assert r.status_code == 200
    assert period in r.get_data(as_text=True)


def test_reports_requires_login(client):
    """Anonymous access is redirected to login (analytics module gating)."""
    import app as A
    anon = A.app.test_client()
    r = anon.get("/reports")
    assert r.status_code in (301, 302)         # bounced to /login


# --------------------------------------------------------------- svg_line unit
def test_svg_line_basic_multiseries():
    """Two aligned series over shared labels -> a single <svg ...> string with a
    polyline per series and the legend names escaped in."""
    import app as A
    svg = A.svg_line([("Alpha", [1.0, 2.0, 3.0]), ("Beta", [3.0, 1.5, 2.0])],
                     ["2026-01", "2026-02", "2026-03"], unit=" €", title="t")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert 'role="img"' in svg
    assert svg.count("<polyline") == 2         # one trend line per series
    assert "Alpha" in svg and "Beta" in svg    # legend labels


def test_svg_line_empty_and_single_point():
    """Empty input degrades to a 'no data' note; a single-point series renders an
    <svg> (a dot, no polyline) without crashing."""
    import app as A
    # no labels / no values -> graceful note, never an exception
    assert "<svg" not in A.svg_line([], [])
    assert "<svg" not in A.svg_line([("X", [])], [])
    # single point: an <svg> with a marker dot but no 2-point polyline
    svg = A.svg_line([("X", [5.0])], ["2026-01"])
    assert "<svg" in svg
    assert "<polyline" not in svg
    assert "<circle" in svg


def test_svg_line_handles_gaps():
    """None values are gaps (skipped), not zeros — a sparse multi-series set still
    renders an <svg> without raising."""
    import app as A
    svg = A.svg_line([("A", [1.0, None, 3.0]), ("B", [None, 2.0, None])],
                     ["p1", "p2", "p3"])
    assert "<svg" in svg
