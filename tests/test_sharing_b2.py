"""B2 — NDA / agreement gate + dynamic watermark over the B1 Secure Share Links.

Covers:
  * the NDA gate: an `nda_required` link blocks BOTH /s/<token> and /s/<token>/file with
    an "I agree" page until the agreement is POSTed; accepting writes a share_agreements
    row (logged) and the document then serves in the same session;
  * the watermark: a `watermark` link's /file returns application/pdf whose bytes DIFFER
    from the original (overlay applied) while a non-watermarked link returns the original
    bytes unchanged;
  * the module-level helpers (record_agreement / has_accepted) and create_link's new
    opt-in flags.

Isolated into tmp_path exactly like test_sharing.py (the B1 fixture): sharing.DB is
repointed and a REAL one-page PDF is vaulted via document_vault so the watermark library
(pypdf) has something to overlay; the demo DBs are never touched.
"""
import io

import pytest


def _real_pdf():
    """A minimal but VALID one-page PDF (so pypdf can parse + overlay it)."""
    from pypdf import PdfWriter, PageObject
    w = PdfWriter()
    w.add_page(PageObject.create_blank_page(width=300, height=300))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Repoint sharing.DB to a throwaway file and stage a REAL vaulted PDF whose locator
    is a valid doc_ref. Returns (sharing_module, doc_ref, pdf_bytes)."""
    import sharing
    import document_vault
    import vat_refund as VR
    import auth

    # self-contained: keep the sharing module gate ON for the web surface.
    auth.set_setting("module_sharing", "on")

    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())

    docdir = str(tmp_path / "docs")
    pdf = _real_pdf()
    locator, _ = document_vault.LocalBackend(docdir).put("invoice.pdf", pdf)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    return sharing, locator, pdf


# ---------------------------------------------------------------- module API
def test_create_link_b2_flags(isolated):
    sharing, doc_ref, _ = isolated
    link, err = sharing.create_link(
        doc_ref, "nda+wm", "alice",
        nda_required=True, agreement_text="Keep it secret.", watermark=True)
    assert err == "" and link is not None
    assert link["nda_required"] == 1
    assert link["agreement_text"] == "Keep it secret."
    assert link["watermark"] == 1
    # agreement_text is ignored when nda_required is off (kept NULL)
    plain, _ = sharing.create_link(doc_ref, "plain", "alice",
                                   agreement_text="ignored")
    assert plain["nda_required"] == 0 and plain["agreement_text"] is None
    assert plain["watermark"] == 0


def test_has_accepted_and_record(isolated):
    sharing, doc_ref, _ = isolated
    plain, _ = sharing.create_link(doc_ref, "p", "alice")
    # no NDA required -> always satisfied
    assert sharing.has_accepted(plain, None) is True
    nda, _ = sharing.create_link(doc_ref, "n", "alice", nda_required=True)
    assert sharing.has_accepted(nda, None) is False
    assert sharing.has_accepted(nda, True) is True
    # record_agreement logs a row
    assert sharing.record_agreement(nda, "v@x.com", "9.9.9.9", "UA/2") is True
    rows = sharing.agreements_for(nda["id"])
    assert len(rows) == 1
    assert rows[0]["viewer_email"] == "v@x.com" and rows[0]["ip"] == "9.9.9.9"


# ---------------------------------------------------------------- NDA gate (web)
def test_nda_gate_blocks_until_agreed(client, isolated):
    sharing, doc_ref, pdf = isolated
    link, _ = sharing.create_link(
        doc_ref, "nda", "pytest_admin",
        nda_required=True, agreement_text="Confidential <do not share>")
    import app as A
    pub = A.app.test_client()
    # GET shows the agreement page (escaped), does NOT serve the file or record a view
    r = pub.get(f"/s/{link['token']}")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "accept to continue" in body
    assert "&lt;do not share&gt;" in body  # agreement text is escaped
    assert 'id="pdf-root"' not in body   # the document viewer is not yet served
    assert pub.get(f"/s/{link['token']}/file").status_code == 410
    assert sharing.view_count(link["id"]) == 0
    assert sharing.agreements_for(link["id"]) == []
    # POST "I agree" -> viewer renders (B3 pdf.js), an acceptance row is written, file streams
    ok = pub.post(f"/s/{link['token']}", data={"share_agree": "1"})
    assert 'id="pdf-root"' in ok.get_data(as_text=True)
    agreed = sharing.agreements_for(link["id"])
    assert len(agreed) == 1
    f = pub.get(f"/s/{link['token']}/file")
    assert f.status_code == 200 and f.data == pdf  # not watermarked -> original bytes


def test_nda_gate_blocks_file_route_directly(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "nda2", "pytest_admin", nda_required=True)
    import app as A
    pub = A.app.test_client()
    # hitting the file route first (before any agreement) must NOT serve bytes
    assert pub.get(f"/s/{link['token']}/file").status_code == 410
    assert sharing.agreements_for(link["id"]) == []


# ---------------------------------------------------------------- watermark (web)
def test_watermark_alters_bytes(client, isolated):
    sharing, doc_ref, pdf = isolated
    wm, _ = sharing.create_link(doc_ref, "wm", "pytest_admin", watermark=True)
    plain, _ = sharing.create_link(doc_ref, "plain", "pytest_admin")
    import app as A
    pub = A.app.test_client()

    f = pub.get(f"/s/{wm['token']}/file")
    assert f.status_code == 200
    assert f.headers["Content-Type"] == "application/pdf"
    assert f.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert f.data != pdf          # overlay applied -> bytes differ
    assert f.data.startswith(b"%PDF")  # still a PDF
    # the watermarked stream still parses as a valid one-page PDF
    from pypdf import PdfReader
    assert len(PdfReader(io.BytesIO(f.data)).pages) == 1

    # the non-watermarked link returns the ORIGINAL bytes untouched
    f2 = pub.get(f"/s/{plain['token']}/file")
    assert f2.status_code == 200 and f2.data == pdf


def test_watermark_falls_back_on_bad_pdf(client, monkeypatch, tmp_path):
    """If the vaulted bytes are not a parseable PDF, the watermark step must fall back to
    streaming the original bytes (never break the viewer)."""
    import sharing, document_vault
    import vat_refund as VR
    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())
    docdir = str(tmp_path / "docs")
    junk = b"%PDF-1.4 not-really-a-pdf\n%%EOF"
    locator, _ = document_vault.LocalBackend(docdir).put("x.pdf", junk)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    link, _ = sharing.create_link(locator, "wm-bad", "pytest_admin", watermark=True)
    import app as A
    pub = A.app.test_client()
    f = pub.get(f"/s/{link['token']}/file")
    assert f.status_code == 200
    assert f.headers["Content-Type"] == "application/pdf"
    assert f.data == junk  # fell back to the original bytes


# ---------------------------------------------------------------- create form (web)
def _tok(client):
    import re
    body = client.get("/share").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_create_form_offers_b2_options_and_persists(client, isolated):
    sharing, doc_ref, _ = isolated
    page = client.get("/share").get_data(as_text=True)
    assert 'name="nda_required"' in page and 'name="watermark"' in page
    assert 'name="agreement_text"' in page
    client.post("/share/create", data={
        "_csrf": _tok(client), "doc_ref_manual": doc_ref, "title": "B2 link",
        "nda_required": "1", "agreement_text": "Please keep confidential.",
        "watermark": "1"})
    link = [l for l in sharing.list_links("pytest_admin") if l["title"] == "B2 link"][0]
    assert link["nda_required"] == 1 and link["watermark"] == 1
    assert link["agreement_text"] == "Please keep confidential."
    # the list page surfaces the new gates
    listing = client.get("/share").get_data(as_text=True)
    assert "NDA" in listing and "watermark" in listing
