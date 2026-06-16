"""B3 — per-page ("page-by-page") view analytics for the secure share links.

Covers the new sharing.py page-engagement API (record_page_view validation/clamping +
the page_engagement / visitor_timeline aggregates) AND the web surface: the PUBLIC
pdf.js viewer page (scoped CSP, static viewer script, NO inline JS), the public
/s/<token>/event beacon (records ONLY after the per-token gates pass, rejects invalid
page/dwell), the static asset serving, and the authed views page showing engagement.

Crucially asserts that the SCOPED viewer CSP applies to the viewer response ONLY and
the strict GLOBAL CSP is unchanged on every other page.

Isolation mirrors tests/test_sharing.py: sharing.DB is repointed into tmp_path and a
vaulted PDF is staged so the demo DBs are never touched.
"""
import re

import pytest


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    import sharing
    import document_vault
    import vat_refund as VR

    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())

    docdir = str(tmp_path / "docs")
    # A real 2-page PDF so _share_doc_page_count (pypdf) can read numPages.
    pdf = _two_page_pdf()
    locator, _ = document_vault.LocalBackend(docdir).put("invoice.pdf", pdf)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    return sharing, locator, pdf


def _two_page_pdf():
    """A minimal valid 2-page PDF (enough for pypdf to count pages)."""
    try:
        from pypdf import PdfWriter
        import io
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        w.add_blank_page(width=200, height=200)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()
    except Exception:
        # Fallback: bytes that are at least a valid-looking PDF (page count unknown).
        return b"%PDF-1.4 shared-doc-bytes\n%%EOF"


# ---------------------------------------------------------------- module API
def test_record_page_view_and_aggregates(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "eng", "alice")
    assert sharing.record_page_view(link, "sessA", 1, 2000) is True
    assert sharing.record_page_view(link, "sessA", 2, 5000) is True
    assert sharing.record_page_view(link, "sessB", 1, 1000) is True

    eng = sharing.page_engagement(link["id"], total_pages=2)
    assert eng["pages_viewed"] == 2
    assert eng["total_ms"] == 8000
    assert eng["visitors"] == 2
    assert eng["completion_pct"] == 100.0
    by_page = {p["page"]: p for p in eng["pages"]}
    assert by_page[1]["dwell_ms"] == 3000 and by_page[1]["sessions"] == 2
    assert by_page[2]["dwell_ms"] == 5000 and by_page[2]["sessions"] == 1


def test_record_page_view_rejects_invalid(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "v", "alice")
    assert sharing.record_page_view(link, "s", 0, 100) is False         # page < 1
    assert sharing.record_page_view(link, "s", -3, 100) is False        # negative page
    assert sharing.record_page_view(link, "s", "abc", 100) is False     # non-int page
    assert sharing.record_page_view(link, "s", 999999, 100) is False    # > MAX_PAGE
    assert sharing.record_page_view(None, "s", 1, 100) is False         # no link
    # nothing recorded
    assert sharing.page_engagement(link["id"])["pages"] == []


def test_record_page_view_clamps_dwell(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "c", "alice")
    # negative dwell floored to 0; absurd dwell capped at MAX_DWELL_MS
    assert sharing.record_page_view(link, "s", 1, -500) is True
    assert sharing.record_page_view(link, "s", 2, 10 ** 12) is True
    eng = sharing.page_engagement(link["id"])
    by_page = {p["page"]: p for p in eng["pages"]}
    assert by_page[1]["dwell_ms"] == 0
    assert by_page[2]["dwell_ms"] == sharing.MAX_DWELL_MS


def test_visitor_timeline(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "tl", "alice")
    sharing.record_page_view(link, "sessA", 1, 1000)
    sharing.record_page_view(link, "sessA", 1, 500)   # same page accrues
    sharing.record_page_view(link, "sessB", 2, 2000)
    tl = sharing.visitor_timeline(link["id"])
    by_sess = {v["view_session"]: v for v in tl}
    assert by_sess["sessA"]["total_ms"] == 1500
    assert by_sess["sessA"]["pages"][0]["page"] == 1
    assert by_sess["sessB"]["total_ms"] == 2000


# ---------------------------------------------------------------- web: viewer page + CSP
GLOBAL_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
              "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
              "form-action 'self'; object-src 'none'")


def test_viewer_page_serves_pdfjs_and_scoped_csp(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "Public", "pytest_admin")
    import app as A
    pub = A.app.test_client()
    r = pub.get(f"/s/{link['token']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # pdf.js viewer wired up, NO inline <script> body — only the static module include.
    assert '/static/share_viewer.js' in html
    assert 'id="pdf-root"' in html
    assert f'data-file="/s/{link["token"]}/file"' in html
    assert "<iframe" not in html
    # the SCOPED viewer CSP allows the worker + wasm, ONLY on this response
    csp = r.headers.get("Content-Security-Policy", "")
    assert "worker-src 'self'" in csp
    assert "'wasm-unsafe-eval'" in csp
    assert "blob:" in csp


def test_global_csp_unchanged_on_other_pages(client, isolated):
    """The strict global CSP must be byte-identical on a non-viewer page (login)."""
    import app as A
    pub = A.app.test_client()
    r = pub.get("/login")
    assert r.headers.get("Content-Security-Policy") == GLOBAL_CSP
    assert "worker-src" not in r.headers.get("Content-Security-Policy", "")
    # the in-process constant is the strict policy
    assert A._CSP == GLOBAL_CSP


def test_static_viewer_script_served(client):
    import app as A
    c = A.app.test_client()
    r = c.get("/static/share_viewer.js")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "pdf-root" in body and "sendBeacon" in body
    # the vendored pdf.js build is present too
    r2 = c.get("/static/vendor/pdfjs/build/pdf.min.mjs")
    assert r2.status_code == 200


# ---------------------------------------------------------------- web: beacon gating
def _post_event(c, token, pages):
    import json
    return c.post(f"/s/{token}/event", data=json.dumps({"pages": pages}),
                  content_type="application/json")


def test_event_records_after_gate_passes(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "ev", "pytest_admin")
    import app as A
    pub = A.app.test_client()
    # visiting the viewer establishes the gate (open link) + the view-session id
    assert pub.get(f"/s/{link['token']}").status_code == 200
    r = _post_event(pub, link["token"], [{"page": 1, "dwell_ms": 1200},
                                         {"page": 2, "dwell_ms": 3400}])
    assert r.status_code == 204
    eng = sharing.page_engagement(link["id"], total_pages=2)
    assert eng["pages_viewed"] == 2 and eng["total_ms"] == 4600


def test_event_rejected_before_gate(client, isolated):
    """A password-gated link must record NOTHING from a beacon sent pre-gate."""
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "pw", "pytest_admin", password="s3cret")
    import app as A
    pub = A.app.test_client()
    # no password submitted -> gate not passed
    r = _post_event(pub, link["token"], [{"page": 1, "dwell_ms": 9999}])
    assert r.status_code == 204   # enumeration-safe
    assert sharing.page_engagement(link["id"])["pages"] == []
    # pass the gate, THEN the beacon records
    pub.post(f"/s/{link['token']}", data={"share_password": "s3cret"})
    pub.get(f"/s/{link['token']}")
    _post_event(pub, link["token"], [{"page": 1, "dwell_ms": 2000}])
    assert sharing.page_engagement(link["id"])["pages_viewed"] == 1


def test_event_rejected_for_revoked(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "rv", "pytest_admin")
    import app as A
    pub = A.app.test_client()
    pub.get(f"/s/{link['token']}")   # establish session
    sharing.revoke(link["id"], "pytest_admin")
    r = _post_event(pub, link["token"], [{"page": 1, "dwell_ms": 1000}])
    assert r.status_code == 204
    assert sharing.page_engagement(link["id"])["pages"] == []


def test_event_invalid_payload_ignored(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "bad", "pytest_admin")
    import app as A
    pub = A.app.test_client()
    pub.get(f"/s/{link['token']}")
    # invalid page numbers / non-dict items are silently dropped
    r = _post_event(pub, link["token"],
                    [{"page": 0, "dwell_ms": 100}, {"page": -1, "dwell_ms": 100},
                     "notadict", {"page": 99999999, "dwell_ms": 100}])
    assert r.status_code == 204
    assert sharing.page_engagement(link["id"])["pages"] == []
    # a non-list body is a no-op too
    import json
    r2 = pub.post(f"/s/{link['token']}/event", data=json.dumps({"pages": "x"}),
                  content_type="application/json")
    assert r2.status_code == 204


# ---------------------------------------------------------------- web: authed analytics
def test_views_page_shows_page_engagement(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "Mine", "pytest_admin")
    sharing.record_page_view(link, "sessA", 1, 4000)
    sharing.record_page_view(link, "sessA", 2, 1000)
    r = client.get(f"/share/{link['id']}/views")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Engagement" in body
    assert "Time per page" in body
    assert "Per-visitor breakdown" in body
    # completion % computed against the real 2-page PDF
    assert "100" in body
