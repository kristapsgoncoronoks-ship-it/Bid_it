"""C3 — three secure-sharing enhancements on top of B1–B5:

  1. PER-LINK (per-recipient) document permissions in data rooms. An optional allow-list
     (`dataroom_link_documents`) narrows a room link to a SUBSET of the room's documents;
     a link with NO allow-list exposes ALL docs (B4 behaviour unchanged). Enforced on the
     /r index (absence), the doc viewer, the file stream and the page-event beacon.

  2. PER-OWNER email alerts. A view/sign/Q&A alert targets the LINK CREATOR's own email
     (auth users.email) when set, and falls back to the team notify relay otherwise —
     never raising.

  3. CUSTOM BRANDING for the PUBLIC viewer/room/gate pages (org name + accent + logo data
     URL). Applied to /s, /r and the gate forms ONLY; an unset brand falls back to the
     generic header; the authed app chrome is unchanged.

Isolation mirrors tests/test_dataroom_b4.py: sharing.DB is repointed into tmp_path and
TWO vaulted PDFs are staged so the demo DBs are never touched.
"""
import io
import json

import pytest


def _pdf(pages=2):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    import sharing
    import document_vault
    import vat_refund as VR

    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())

    docdir = str(tmp_path / "docs")
    backend = document_vault.LocalBackend(docdir)
    a, b = _pdf(2), _pdf(3)
    loc_a, _ = backend.put("alpha.pdf", a)
    loc_b, _ = backend.put("beta.pdf", b)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    return sharing, [(loc_a, a), (loc_b, b)]


def _public():
    import app as A
    return A.app.test_client()


# ================================================================ 1) per-link doc perms
def test_allow_list_restricts_module_api(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="A")
    d2, _ = sharing.add_document(room["id"], docs[1][0], title="B")
    # restricted link -> only d1
    link, err = sharing.create_room_link(room["id"], "alice",
                                         allow_doc_ids=[d1["id"]])
    assert err == "" and link
    assert sharing.link_allowed_doc_ids(link["id"]) == {d1["id"]}
    assert sharing.doc_permitted(link, d1["id"]) is True
    assert sharing.doc_permitted(link, d2["id"]) is False
    permitted = sharing.list_permitted_documents(link)
    assert [d["id"] for d in permitted] == [d1["id"]]
    assert sharing.get_permitted_document(link, d1["id"])["id"] == d1["id"]
    assert sharing.get_permitted_document(link, d2["id"]) is None


def test_empty_allow_list_exposes_all(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="A")
    d2, _ = sharing.add_document(room["id"], docs[1][0], title="B")
    link, _ = sharing.create_room_link(room["id"], "alice")  # no allow-list
    assert sharing.link_allowed_doc_ids(link["id"]) == set()
    assert sharing.doc_permitted(link, d1["id"]) is True
    assert sharing.doc_permitted(link, d2["id"]) is True
    assert {d["id"] for d in sharing.list_permitted_documents(link)} == {d1["id"], d2["id"]}


def test_allow_list_drops_foreign_ids(isolated):
    """An id that is not in the room must never end up in the allow-list (no widening)."""
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="A")
    other, _ = sharing.create_room("other", None, "alice")
    od, _ = sharing.add_document(other["id"], docs[1][0], title="Foreign")
    link, _ = sharing.create_room_link(room["id"], "alice",
                                       allow_doc_ids=[d1["id"], od["id"], 9999])
    assert sharing.link_allowed_doc_ids(link["id"]) == {d1["id"]}


def test_list_room_links_reports_allowed_count(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="A")
    sharing.add_document(room["id"], docs[1][0], title="B")
    sharing.create_room_link(room["id"], "alice", allow_doc_ids=[d1["id"]])
    sharing.create_room_link(room["id"], "alice")  # all docs
    counts = sorted(l["allowed_docs"] for l in sharing.list_room_links(room["id"]))
    assert counts == [0, 1]


# ---- web enforcement: index absence + viewer/file/event 404 for a non-permitted doc ----
def test_web_allow_list_enforced_everywhere(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("Pack", "ACME", "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="Permitted")
    d2, _ = sharing.add_document(room["id"], docs[1][0], title="Hidden")
    link, _ = sharing.create_room_link(room["id"], "alice", allow_doc_ids=[d1["id"]])
    tok = link["token"]
    pub = _public()

    # index: lists only the permitted doc
    r = pub.get(f"/r/{tok}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Permitted" in html and "Hidden" not in html
    assert f"/r/{tok}/doc/{d1['id']}" in html
    assert f"/r/{tok}/doc/{d2['id']}" not in html

    # permitted doc: viewer 200, file 200
    assert pub.get(f"/r/{tok}/doc/{d1['id']}").status_code == 200
    rf = pub.get(f"/r/{tok}/doc/{d1['id']}/file")
    assert rf.status_code == 200 and rf.data == docs[0][1]

    # non-permitted doc: viewer + file 404/410, even though it IS in the room
    assert pub.get(f"/r/{tok}/doc/{d2['id']}").status_code in (404, 410)
    assert pub.get(f"/r/{tok}/doc/{d2['id']}/file").status_code in (404, 410)

    # event beacon for the non-permitted doc records nothing
    pub.get(f"/r/{tok}")  # establish the view-session
    rev = pub.post(f"/r/{tok}/doc/{d2['id']}/event",
                   data=json.dumps({"pages": [{"page": 1, "dwell_ms": 5000}]}),
                   content_type="application/json")
    assert rev.status_code == 204
    eng = {e["doc_id"]: e for e in sharing.room_engagement(room["id"])}
    assert eng[d2["id"]]["pages"] == []
    # the permitted doc records normally
    pub.post(f"/r/{tok}/doc/{d1['id']}/event",
             data=json.dumps({"pages": [{"page": 1, "dwell_ms": 1000}]}),
             content_type="application/json")
    eng = {e["doc_id"]: e for e in sharing.room_engagement(room["id"])}
    assert eng[d1["id"]]["pages_viewed"] == 1


def test_web_empty_allow_list_serves_all(isolated):
    """B4 behaviour unchanged: a link with no allow-list serves every room doc."""
    sharing, docs = isolated
    room, _ = sharing.create_room("Pack", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="One")
    d2, _ = sharing.add_document(room["id"], docs[1][0], title="Two")
    link, _ = sharing.create_room_link(room["id"], "alice")
    tok = link["token"]
    pub = _public()
    html = pub.get(f"/r/{tok}").get_data(as_text=True)
    assert "One" in html and "Two" in html
    assert pub.get(f"/r/{tok}/doc/{d1['id']}/file").status_code == 200
    assert pub.get(f"/r/{tok}/doc/{d2['id']}/file").status_code == 200


# ================================================================ 2) per-owner email
class _CapturingTransport:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, html, text):
        self.sent.append((to, subject))


def test_send_alert_targets_explicit_recipient():
    import notify
    t = _CapturingTransport()
    res = notify.send_alert("subj", ["line"], transport=t,
                            recipients="owner@example.com")
    assert res == notify.SENT
    assert t.sent == [(["owner@example.com"], "subj")]


def test_send_alert_falls_back_to_relay(monkeypatch):
    import notify
    monkeypatch.setattr(notify, "_recipients", lambda: ["team@example.com"])
    t = _CapturingTransport()
    res = notify.send_alert("subj", ["line"], transport=t, recipients=None)
    assert res == notify.SENT
    assert t.sent == [(["team@example.com"], "subj")]


def test_send_alert_noop_when_no_recipient(monkeypatch):
    import notify
    monkeypatch.setattr(notify, "_recipients", lambda: [])
    t = _CapturingTransport()
    res = notify.send_alert("subj", ["line"], transport=t, recipients=None)
    assert res == notify.NOOP
    assert t.sent == []


def test_user_email_store_roundtrip():
    import auth
    auth.add_user("c3_owner", "Pw!123456", role="processor")
    try:
        assert auth.user_email("c3_owner") is None
        auth.set_email("c3_owner", "c3@example.com")
        assert auth.user_email("c3_owner") == "c3@example.com"
        assert auth.get_user("c3_owner")["email"] == "c3@example.com"
        auth.set_email("c3_owner", "")   # clear
        assert auth.user_email("c3_owner") is None
    finally:
        con = auth.connect()
        con.execute("DELETE FROM users WHERE username=?", ("c3_owner",))
        con.commit(); con.close()


def test_user_email_never_raises_for_unknown():
    import auth
    assert auth.user_email("nobody_at_all_xyz") is None
    assert auth.user_email(None) is None


def test_creator_recipients_used_for_share_view(monkeypatch, isolated):
    """A view alert routes to the creator's own email when set."""
    import app as A
    import notify
    sharing, docs = isolated
    monkeypatch.setattr(A._auth, "user_email",
                        lambda who: "creator@example.com" if who == "alice" else None)
    captured = {}

    def fake_send_alert(subject, lines, transport=None, recipients=None):
        captured["recipients"] = recipients
        return notify.SENT

    monkeypatch.setattr(notify, "send_alert", fake_send_alert)
    link, _ = sharing.create_link(docs[0][0], "doc", "alice")
    A._notify_share_owner(link, "viewer@x.com")
    assert captured["recipients"] == "creator@example.com"


def test_creator_recipients_none_when_unset(monkeypatch, isolated):
    import app as A
    import notify
    sharing, docs = isolated
    monkeypatch.setattr(A._auth, "user_email", lambda who: None)
    captured = {}
    monkeypatch.setattr(notify, "send_alert",
                        lambda s, l, transport=None, recipients=None:
                        captured.update(recipients=recipients) or notify.SENT)
    link, _ = sharing.create_link(docs[0][0], "doc", "bob")
    A._notify_share_owner(link, None)
    assert captured["recipients"] is None   # -> notify falls back to the relay


# ================================================================ 3) custom branding
@pytest.fixture()
def brand_reset():
    """Save and restore the brand_* app_settings so the test never pollutes them."""
    import auth
    keys = ("brand_name", "brand_accent", "brand_logo")
    saved = {k: auth.get_setting(k) for k in keys}
    yield auth
    for k, v in saved.items():
        auth.set_setting(k, v if v is not None else "")


def test_brand_applies_to_public_room_and_gate(brand_reset, isolated):
    auth = brand_reset
    sharing, docs = isolated
    auth.set_setting("brand_name", "<Acme & Co>")
    auth.set_setting("brand_accent", "#abcdef")
    room, _ = sharing.create_room("r", None, "alice")
    sharing.add_document(room["id"], docs[0][0], title="Doc")
    open_link, _ = sharing.create_room_link(room["id"], "alice")
    gated_link, _ = sharing.create_room_link(room["id"], "alice", password="s3cret")
    pub = _public()

    # branded room index: name (XSS-escaped) + accent both present
    html = pub.get(f"/r/{open_link['token']}").get_data(as_text=True)
    assert "&lt;Acme &amp; Co&gt;" in html
    assert "<script>" not in html.lower() or "alert" not in html.lower()
    assert "#abcdef" in html
    assert 'class="brandbar"' in html

    # branded password GATE page too
    g = pub.get(f"/r/{gated_link['token']}").get_data(as_text=True)
    assert "&lt;Acme &amp; Co&gt;" in g and "#abcdef" in g
    assert 'class="brandbar"' in g


def test_brand_applies_to_share_document_viewer(brand_reset, isolated):
    auth = brand_reset
    sharing, docs = isolated
    auth.set_setting("brand_name", "BrandX")
    link, _ = sharing.create_link(docs[0][0], "Pub", "alice")
    html = _public().get(f"/s/{link['token']}").get_data(as_text=True)
    assert "BrandX" in html and 'class="brandbar"' in html


def test_unset_brand_falls_back_to_default(brand_reset, isolated):
    auth = brand_reset
    sharing, docs = isolated
    auth.set_setting("brand_name", "")
    auth.set_setting("brand_accent", "")
    auth.set_setting("brand_logo", "")
    link, _ = sharing.create_link(docs[0][0], "Pub", "alice")
    html = _public().get(f"/s/{link['token']}").get_data(as_text=True)
    assert 'class="brandbar"' not in html   # generic header, no brand bar


def test_brand_rejects_remote_logo_and_bad_accent(brand_reset):
    """Only a data: image logo and a hex accent survive (CSP-safe / injection-safe)."""
    auth = brand_reset
    auth.set_setting("brand_logo", "https://evil.example/logo.png")
    auth.set_setting("brand_accent", "red; } body{display:none")
    import app as A
    brand = A._share_brand()
    assert brand["logo"] is None
    assert brand["accent"] is None
    # a valid data: logo + hex accent survive
    auth.set_setting("brand_logo", "data:image/png;base64,iVBORw0KGgo=")
    auth.set_setting("brand_accent", "#123abc")
    brand = A._share_brand()
    assert brand["logo"].startswith("data:image/")
    assert brand["accent"] == "#123abc"


def test_brand_does_not_change_authed_pages(brand_reset, client):
    """The configured brand must NOT leak into the signed-in app chrome."""
    auth = brand_reset
    auth.set_setting("brand_name", "PublicBrandOnly")
    r = client.get("/")
    assert r.status_code == 200
    assert "PublicBrandOnly" not in r.get_data(as_text=True)
    assert 'class="brandbar"' not in r.get_data(as_text=True)
