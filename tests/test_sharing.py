"""B1 — Secure Share Links (Papermark/DocSend-style trackable public links).

Covers the sharing.py module API (create/get/gates/views/revoke + password &
require-email gates) AND the web surface: the authenticated management pages
(create / list / revoke) and the PUBLIC viewer + file stream with their per-token
gates (happy-path serves the PDF same-origin; revoked/expired/wrong-password block).

Everything is isolated into tmp_path: sharing.DB is repointed and a vaulted PDF is
written via document_vault into a tmp docdir that vat_refund.DOCDIR points at — so the
demo DBs are never touched.
"""
import datetime

import pytest


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Repoint sharing.DB to a throwaway file and stage a vaulted PDF whose locator is
    a valid doc_ref. Returns (sharing_module, doc_ref, pdf_bytes)."""
    import sharing
    import document_vault
    import vat_refund as VR

    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())

    docdir = str(tmp_path / "docs")
    pdf = b"%PDF-1.4 shared-doc-bytes\n%%EOF"
    locator, _ = document_vault.LocalBackend(docdir).put("invoice.pdf", pdf)
    # the public file route resolves bytes via document_vault.get_bytes(doc_ref, VR.DOCDIR)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    return sharing, locator, pdf


# ---------------------------------------------------------------- module API
def test_create_and_get_by_token(isolated):
    sharing, doc_ref, _ = isolated
    link, err = sharing.create_link(doc_ref, "Q2 invoice", "alice")
    assert err == "" and link is not None
    assert link["token"] and link["doc_ref"] == doc_ref
    again = sharing.get_by_token(link["token"])
    assert again and again["id"] == link["id"]
    assert sharing.get_by_token("nope-not-a-token") is None


def test_create_requires_doc_ref(isolated):
    sharing, _doc_ref, _ = isolated
    link, err = sharing.create_link("   ", "x", "alice")
    assert link is None and err


def test_expiry_blocks_is_active(isolated):
    sharing, doc_ref, _ = isolated
    past = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    future = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    expired, _ = sharing.create_link(doc_ref, "old", "alice", expires_at=past)
    live, _ = sharing.create_link(doc_ref, "live", "alice", expires_at=future)
    assert sharing.is_expired(expired) and not sharing.is_active(expired)
    assert not sharing.is_expired(live) and sharing.is_active(live)


def test_revoke_blocks(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "t", "alice")
    assert sharing.is_active(link)
    ok, err = sharing.revoke(link["id"], "alice")
    assert ok and err == ""
    assert not sharing.is_active(sharing.get_by_id(link["id"]))
    # revoking a missing link is a value error, never raises
    ok2, err2 = sharing.revoke(999999, "alice")
    assert not ok2 and err2


def test_password_gate(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "secret", "alice", password="hunter2")
    assert link["password_hash"] and "hunter2" not in link["password_hash"]
    assert sharing.check_password(link["password_hash"], "hunter2") is True
    assert sharing.check_password(link["password_hash"], "wrong") is False
    # a link with no password needs none
    open_link, _ = sharing.create_link(doc_ref, "open", "alice")
    assert sharing.check_password(open_link["password_hash"], "") is True


def test_require_email_flag(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "e", "alice", require_email=True)
    assert link["require_email"] == 1


def test_record_view_and_counts(isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "v", "alice")
    assert sharing.record_view(link, "v@x.com", "1.2.3.4", "UA/1") is True
    # an immediate refresh by the same (link, email) dedups (no new row, no re-notify)
    assert sharing.record_view(link, "v@x.com", "1.2.3.4", "UA/1") is False
    assert sharing.view_count(link["id"]) == 1
    views = sharing.views_for(link["id"])
    assert len(views) == 1 and views[0]["viewer_email"] == "v@x.com"
    assert views[0]["ip"] == "1.2.3.4"


def test_list_links_per_user(isolated):
    sharing, doc_ref, _ = isolated
    sharing.create_link(doc_ref, "a1", "alice")
    sharing.create_link(doc_ref, "b1", "bob")
    alice = sharing.list_links("alice")
    assert len(alice) == 1 and alice[0]["title"] == "a1"
    assert all(l["created_by"] == "alice" for l in alice)


# ---------------------------------------------------------------- web surface
def _tok(client):
    import re
    body = client.get("/share").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_authed_create_list_revoke(client, isolated):
    sharing, doc_ref, _ = isolated
    # create via the web form
    r = client.post("/share/create", data={
        "_csrf": _tok(client), "doc_ref_manual": doc_ref, "title": "Web link"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Share link created" in body and "/s/" in body
    # it shows on the list page with a revoke control
    page = client.get("/share").get_data(as_text=True)
    assert "Web link" in page
    link = sharing.list_links("pytest_admin")[0]
    # revoke it via the web form
    r2 = client.post("/share", data={
        "_csrf": _tok(client), "__act": "revoke", "link_id": str(link["id"])})
    assert "Link revoked" in r2.get_data(as_text=True)
    assert not sharing.is_active(sharing.get_by_id(link["id"]))


def test_create_form_escapes_title(client, isolated):
    sharing, doc_ref, _ = isolated
    client.post("/share/create", data={
        "_csrf": _tok(client), "doc_ref_manual": doc_ref,
        "title": "<script>alert(1)</script>"})
    body = client.get("/share").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_views_page_admin_only_owner(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "mine", "pytest_admin")
    other, _ = sharing.create_link(doc_ref, "theirs", "someone_else")
    assert client.get(f"/share/{link['id']}/views").status_code == 200
    # a link owned by another user is not visible (no enumeration)
    assert client.get(f"/share/{other['id']}/views").status_code == 404


def test_public_viewer_and_file_happy_path(client, isolated):
    sharing, doc_ref, pdf = isolated
    link, _ = sharing.create_link(doc_ref, "Public", "pytest_admin")
    # public viewer needs NO auth — use a fresh, unauthenticated client
    import app as A
    pub = A.app.test_client()
    r = pub.get(f"/s/{link['token']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # B3: the pdf.js page-by-page viewer replaced the iframe — assert its markers.
    assert f"/s/{link['token']}/file" in html and 'id="pdf-root"' in html
    assert "/static/share_viewer.js" in html
    # the view was recorded
    assert sharing.view_count(link["id"]) == 1
    # the file stream serves the PDF same-origin
    f = pub.get(f"/s/{link['token']}/file")
    assert f.status_code == 200
    assert f.data == pdf
    assert f.headers["Content-Type"] == "application/pdf"
    assert f.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_public_revoked_blocks(client, isolated):
    sharing, doc_ref, _ = isolated
    link, _ = sharing.create_link(doc_ref, "x", "pytest_admin")
    sharing.revoke(link["id"], "pytest_admin")
    import app as A
    pub = A.app.test_client()
    assert pub.get(f"/s/{link['token']}").status_code == 410
    assert pub.get(f"/s/{link['token']}/file").status_code == 410


def test_public_unknown_token_blocks(client, isolated):
    import app as A
    pub = A.app.test_client()
    assert pub.get("/s/totally-unknown-token").status_code == 410
    assert pub.get("/s/totally-unknown-token/file").status_code == 410


def test_public_password_gate(client, isolated):
    sharing, doc_ref, pdf = isolated
    link, _ = sharing.create_link(doc_ref, "pw", "pytest_admin", password="open-sesame")
    import app as A
    pub = A.app.test_client()
    # GET shows the password prompt, does NOT serve the file
    r = pub.get(f"/s/{link['token']}")
    assert r.status_code == 200 and "password-protected" in r.get_data(as_text=True)
    assert pub.get(f"/s/{link['token']}/file").status_code == 410
    assert sharing.view_count(link["id"]) == 0
    # wrong password -> still prompted
    bad = pub.post(f"/s/{link['token']}", data={"share_password": "nope"})
    assert "Incorrect password" in bad.get_data(as_text=True)
    # right password -> viewer renders, and the file now streams in the same session
    ok = pub.post(f"/s/{link['token']}", data={"share_password": "open-sesame"})
    assert 'id="pdf-root"' in ok.get_data(as_text=True)
    f = pub.get(f"/s/{link['token']}/file")
    assert f.status_code == 200 and f.data == pdf


def test_public_require_email_gate(client, isolated):
    sharing, doc_ref, pdf = isolated
    link, _ = sharing.create_link(doc_ref, "em", "pytest_admin", require_email=True)
    import app as A
    pub = A.app.test_client()
    # GET shows the email-capture form, no file served, no view recorded
    r = pub.get(f"/s/{link['token']}")
    assert r.status_code == 200 and "enter your email" in r.get_data(as_text=True).lower()
    assert pub.get(f"/s/{link['token']}/file").status_code == 410
    assert sharing.view_count(link["id"]) == 0
    # supply an email -> viewer renders and the view is recorded with the email
    ok = pub.post(f"/s/{link['token']}", data={"share_email": "lead@corp.com"})
    assert 'id="pdf-root"' in ok.get_data(as_text=True)
    f = pub.get(f"/s/{link['token']}/file")
    assert f.status_code == 200 and f.data == pdf
    views = sharing.views_for(link["id"])
    assert views and views[0]["viewer_email"] == "lead@corp.com"
