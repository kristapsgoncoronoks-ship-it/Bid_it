"""B4 — DATA ROOMS over the B1-B3 secure-sharing infrastructure.

A data room groups several vaulted documents into one branded, access-controlled space
behind a SINGLE gated link. This builds ON the existing sharing module: it REUSES the
per-token gate (_share_gate_or_form), the scoped-CSP pdf.js viewer + the page-engagement
beacon machinery. These tests cover:

  * sharing.py room API: create_room / add_document (folders + order) / list_documents,
    create_room_link (the gate columns), get_document membership, the Q&A helpers
    (ask_question / answer_question / questions_for), the room-page-view beacon +
    room_engagement aggregate;
  * the PUBLIC room surface (no auth): /r/<token> blocked until the gates pass
    (password / NDA / email), then lists docs; /r/<token>/doc/<id>/file serves the right
    vault bytes ONLY after the gates (and 404 for a doc not in the room); the per-page
    beacon records ONLY post-gate; the Q&A ask form stores a row + (mock) notifies;
  * the authed management surface (room engagement aggregates per document).

Isolation mirrors tests/test_sharing_b3.py: sharing.DB is repointed into tmp_path and a
real multi-page PDF is staged so the demo DBs are never touched.
"""
import io
import json
import re

import pytest


def _pdf(pages=2):
    """A minimal valid N-page PDF (enough for pypdf to count pages)."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Repoint sharing.DB and stage TWO distinct vaulted PDFs. Returns
    (sharing_module, [(doc_ref, bytes), (doc_ref2, bytes2)])."""
    import sharing
    import document_vault
    import vat_refund as VR
    import auth

    # self-contained: the dataroom web surface lives under the sharing module gate.
    auth.set_setting("module_sharing", "on")

    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())

    docdir = str(tmp_path / "docs")
    backend = document_vault.LocalBackend(docdir)
    a = _pdf(2)
    b = _pdf(3)
    loc_a, _ = backend.put("alpha.pdf", a)
    loc_b, _ = backend.put("beta.pdf", b)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    return sharing, [(loc_a, a), (loc_b, b)]


# ================================================================ module API
def test_create_room_and_add_documents(isolated):
    sharing, docs = isolated
    room, err = sharing.create_room("DD pack", "Acme branding", "alice")
    assert err == "" and room and room["name"] == "DD pack"
    assert room["title"] == "Acme branding"

    d1, e1 = sharing.add_document(room["id"], docs[0][0], title="Contract",
                                  folder="Legal")
    d2, e2 = sharing.add_document(room["id"], docs[1][0], title="Invoice",
                                  folder="Finance")
    assert e1 == "" and e2 == "" and d1 and d2
    # folder + auto sort_order
    assert d1["folder"] == "Legal" and d1["sort_order"] == 0
    assert d2["folder"] == "Finance" and d2["sort_order"] == 1

    listed = sharing.list_documents(room["id"])
    assert {d["title"] for d in listed} == {"Contract", "Invoice"}
    # blank ref rejected
    assert sharing.add_document(room["id"], "  ") == (None, "a document reference is required")


def test_list_rooms_scoped_to_owner(isolated):
    sharing, _ = isolated
    sharing.create_room("mine", None, "alice")
    sharing.create_room("theirs", None, "bob")
    assert [r["name"] for r in sharing.list_rooms("alice")] == ["mine"]
    assert [r["name"] for r in sharing.list_rooms("bob")] == ["theirs"]


def test_get_document_enforces_membership(isolated):
    sharing, docs = isolated
    r1, _ = sharing.create_room("r1", None, "alice")
    r2, _ = sharing.create_room("r2", None, "alice")
    d, _ = sharing.add_document(r1["id"], docs[0][0])
    assert sharing.get_document(r1["id"], d["id"]) is not None
    # the same doc id queried under a DIFFERENT room is not a member -> None
    assert sharing.get_document(r2["id"], d["id"]) is None


def test_create_room_link_mirrors_gate_columns(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    link, err = sharing.create_room_link(
        room["id"], "alice", password="s3cret", require_email=True,
        nda_required=True, agreement_text="Confidential.", watermark=True)
    assert err == "" and link
    assert link["password_hash"] and link["require_email"] == 1
    assert link["nda_required"] == 1 and link["agreement_text"] == "Confidential."
    assert link["watermark"] == 1 and link["revoked"] == 0
    # the gate helpers (B1/B2) operate over the room link unchanged
    assert sharing.is_active(link) is True
    assert sharing.check_password(link["password_hash"], "s3cret") is True
    assert sharing.get_room_link_by_token(link["token"])["id"] == link["id"]
    sharing.revoke_room_link(link["id"])
    assert sharing.is_active(sharing.get_room_link_by_token(link["token"])) is False


def test_room_page_view_and_engagement(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="A")
    d2, _ = sharing.add_document(room["id"], docs[1][0], title="B")
    link, _ = sharing.create_room_link(room["id"], "alice")

    assert sharing.record_room_page_view(link, d1["id"], "sessA", 1, 2000) is True
    assert sharing.record_room_page_view(link, d1["id"], "sessA", 2, 3000) is True
    assert sharing.record_room_page_view(link, d2["id"], "sessB", 1, 1000) is True
    # invalid page rejected; no link rejected
    assert sharing.record_room_page_view(link, d1["id"], "sessA", 0, 100) is False
    assert sharing.record_room_page_view(None, d1["id"], "sessA", 1, 100) is False

    eng = {e["doc_id"]: e for e in sharing.room_engagement(
        room["id"], {d1["id"]: 2, d2["id"]: 3})}
    assert eng[d1["id"]]["total_ms"] == 5000
    assert eng[d1["id"]]["pages_viewed"] == 2
    assert eng[d1["id"]]["completion_pct"] == 100.0
    assert eng[d1["id"]]["visitors"] == 1
    assert eng[d2["id"]]["total_ms"] == 1000
    assert eng[d2["id"]]["completion_pct"] == pytest.approx(33.3)


def test_qa_lifecycle(isolated):
    sharing, _ = isolated
    room, _ = sharing.create_room("r", None, "alice")
    q, err = sharing.ask_question(room["id"], "buyer@x.com", "What is the rebate?")
    assert err == "" and q and q["status"] == "open"
    assert sharing.ask_question(room["id"], "x", "") == (None, "a question is required")

    ok, err2 = sharing.answer_question(q["id"], "12 cents/L")
    assert ok and err2 == ""
    after = sharing.get_question(q["id"])
    assert after["status"] == "answered" and after["answer"] == "12 cents/L"
    assert after["answered_at"]
    # empty answer rejected; unknown question rejected
    assert sharing.answer_question(q["id"], "")[0] is False
    assert sharing.answer_question(999999, "x")[0] is False

    qs = sharing.questions_for(room["id"])
    assert len(qs) == 1 and qs[0]["id"] == q["id"]


# ================================================================ authed web surface
def _tok(client):
    body = client.get("/rooms").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_authed_create_room_add_doc_create_link(client, isolated):
    sharing, docs = isolated
    r = client.post("/rooms", data={"_csrf": _tok(client), "name": "Web room",
                                    "title": "Brand"})
    assert r.status_code == 200 and "Data room created" in r.get_data(as_text=True)
    room = sharing.list_rooms("pytest_admin")[0]

    # add a document via the management page
    rid = room["id"]
    r2 = client.post(f"/rooms/{rid}", data={
        "_csrf": _tok(client), "__act": "add_doc", "doc_ref_manual": docs[0][0],
        "title": "Contract", "folder": "Legal"})
    assert "Document added" in r2.get_data(as_text=True)
    assert sharing.list_documents(rid)[0]["title"] == "Contract"

    # create a gated room link via the management page
    r3 = client.post(f"/rooms/{rid}", data={
        "_csrf": _tok(client), "__act": "create_link", "password": "pw"})
    assert "Room link created" in r3.get_data(as_text=True)
    assert sharing.list_room_links(rid)[0]["password_hash"]


def test_room_page_owner_only(client, isolated):
    sharing, _ = isolated
    mine, _ = sharing.create_room("mine", None, "pytest_admin")
    theirs, _ = sharing.create_room("theirs", None, "someone_else")
    assert client.get(f"/rooms/{mine['id']}").status_code == 200
    assert client.get(f"/rooms/{theirs['id']}").status_code == 404


def test_room_management_escapes(client, isolated):
    sharing, _ = isolated
    sharing.create_room("<script>alert(1)</script>", None, "pytest_admin")
    body = client.get("/rooms").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_engagement_page_aggregates_per_document(client, isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "pytest_admin")
    d, _ = sharing.add_document(room["id"], docs[0][0], title="Alpha")
    link, _ = sharing.create_room_link(room["id"], "pytest_admin")
    sharing.record_room_page_view(link, d["id"], "sessA", 1, 4000)
    sharing.record_room_page_view(link, d["id"], "sessA", 2, 1000)
    r = client.get(f"/rooms/{room['id']}/engagement")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Engagement" in body and "Alpha" in body and "100" in body


# ================================================================ public room surface
def _public():
    import app as A
    return A.app.test_client()


def test_public_room_lists_documents_when_open(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("Pack", "ACME Inc", "alice")
    sharing.add_document(room["id"], docs[0][0], title="Contract", folder="Legal")
    sharing.add_document(room["id"], docs[1][0], title="Invoice", folder="Finance")
    link, _ = sharing.create_room_link(room["id"], "alice")
    d = sharing.list_documents(room["id"])

    pub = _public()
    r = pub.get(f"/r/{link['token']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "ACME Inc" in html              # branding/title
    assert "Contract" in html and "Invoice" in html
    assert "Legal" in html and "Finance" in html
    assert f"/r/{link['token']}/doc/{d[0]['id']}" in html
    assert "Ask a question" in html


def test_public_room_denied_for_revoked(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    link, _ = sharing.create_room_link(room["id"], "alice")
    sharing.revoke_room_link(link["id"])
    r = _public().get(f"/r/{link['token']}")
    assert r.status_code == 410


def test_public_room_password_gate(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    sharing.add_document(room["id"], docs[0][0], title="Secret doc")
    link, _ = sharing.create_room_link(room["id"], "alice", password="s3cret")
    pub = _public()
    # before password: prompted, no document titles leaked
    r = pub.get(f"/r/{link['token']}")
    assert r.status_code == 200
    assert "password-protected" in r.get_data(as_text=True)
    assert "Secret doc" not in r.get_data(as_text=True)
    # wrong password
    r2 = pub.post(f"/r/{link['token']}", data={"share_password": "nope"})
    assert "Incorrect password" in r2.get_data(as_text=True)
    # correct password -> room lists docs (form action points back at /r/<token>)
    pub.post(f"/r/{link['token']}", data={"share_password": "s3cret"})
    r3 = pub.get(f"/r/{link['token']}")
    assert "Secret doc" in r3.get_data(as_text=True)


def test_public_room_nda_gate(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    sharing.add_document(room["id"], docs[0][0], title="NDA doc")
    link, _ = sharing.create_room_link(room["id"], "alice", nda_required=True,
                                       agreement_text="Keep it secret.")
    pub = _public()
    r = pub.get(f"/r/{link['token']}")
    assert "Keep it secret." in r.get_data(as_text=True)
    assert "NDA doc" not in r.get_data(as_text=True)
    # accept -> logged into dataroom_agreements + room serves
    pub.post(f"/r/{link['token']}", data={"share_agree": "1"})
    r2 = pub.get(f"/r/{link['token']}")
    assert "NDA doc" in r2.get_data(as_text=True)
    con = sharing.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM dataroom_agreements WHERE room_link_id=?",
                        (link["id"],)).fetchone()[0]
        # and NO contamination of the document-link agreements table
        m = con.execute("SELECT COUNT(*) FROM share_agreements").fetchone()[0]
    finally:
        con.close()
    assert n == 1 and m == 0


def test_public_room_email_gate(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    sharing.add_document(room["id"], docs[0][0], title="Email doc")
    link, _ = sharing.create_room_link(room["id"], "alice", require_email=True)
    pub = _public()
    r = pub.get(f"/r/{link['token']}")
    assert "enter your email" in r.get_data(as_text=True).lower()
    assert "Email doc" not in r.get_data(as_text=True)
    pub.post(f"/r/{link['token']}", data={"share_email": "buyer@x.com"})
    r2 = pub.get(f"/r/{link['token']}")
    assert "Email doc" in r2.get_data(as_text=True)


def test_room_doc_viewer_scoped_csp_and_event_url(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d, _ = sharing.add_document(room["id"], docs[0][0], title="Doc")
    link, _ = sharing.create_room_link(room["id"], "alice")
    pub = _public()
    r = pub.get(f"/r/{link['token']}/doc/{d['id']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "/static/share_viewer.js" in html and 'id="pdf-root"' in html
    # the in-room viewer keys its beacon to the room-doc event URL
    assert f'data-event="/r/{link["token"]}/doc/{d["id"]}/event"' in html
    assert f'data-file="/r/{link["token"]}/doc/{d["id"]}/file"' in html
    csp = r.headers.get("Content-Security-Policy", "")
    assert "worker-src 'self'" in csp and "'wasm-unsafe-eval'" in csp


def test_room_doc_file_serves_right_bytes_after_gate(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d1, _ = sharing.add_document(room["id"], docs[0][0], title="A")
    d2, _ = sharing.add_document(room["id"], docs[1][0], title="B")
    link, _ = sharing.create_room_link(room["id"], "alice", password="pw")
    pub = _public()
    # pre-gate: file route does not serve bytes
    r0 = pub.get(f"/r/{link['token']}/doc/{d1['id']}/file")
    assert r0.status_code == 410
    # pass the gate
    pub.post(f"/r/{link['token']}", data={"share_password": "pw"})
    r1 = pub.get(f"/r/{link['token']}/doc/{d1['id']}/file")
    assert r1.status_code == 200 and r1.mimetype == "application/pdf"
    assert r1.get_data() == docs[0][1]
    r2 = pub.get(f"/r/{link['token']}/doc/{d2['id']}/file")
    assert r2.get_data() == docs[1][1]


def test_room_doc_file_404_for_doc_not_in_room(isolated):
    sharing, docs = isolated
    r1, _ = sharing.create_room("r1", None, "alice")
    r2, _ = sharing.create_room("r2", None, "alice")
    other, _ = sharing.add_document(r2["id"], docs[1][0])   # belongs to r2
    link, _ = sharing.create_room_link(r1["id"], "alice")   # link to r1
    pub = _public()
    pub.get(f"/r/{link['token']}")
    # the r2 document is not reachable through the r1 link
    assert pub.get(f"/r/{link['token']}/doc/{other['id']}/file").status_code == 410
    assert pub.get(f"/r/{link['token']}/doc/{other['id']}").status_code == 410


def _post_event(c, token, doc_id, pages):
    return c.post(f"/r/{token}/doc/{doc_id}/event", data=json.dumps({"pages": pages}),
                  content_type="application/json")


def test_room_beacon_records_only_after_gate(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    d, _ = sharing.add_document(room["id"], docs[0][0])
    link, _ = sharing.create_room_link(room["id"], "alice", password="pw")
    pub = _public()
    # pre-gate beacon records nothing (enumeration-safe 204)
    r = _post_event(pub, link["token"], d["id"], [{"page": 1, "dwell_ms": 9999}])
    assert r.status_code == 204
    assert sharing.room_engagement(room["id"]) and \
        sharing.room_engagement(room["id"])[0]["pages"] == []
    # pass gate + visit viewer (establishes view-session) THEN beacon records
    pub.post(f"/r/{link['token']}", data={"share_password": "pw"})
    pub.get(f"/r/{link['token']}/doc/{d['id']}")
    r2 = _post_event(pub, link["token"], d["id"],
                     [{"page": 1, "dwell_ms": 1200}, {"page": 2, "dwell_ms": 800}])
    assert r2.status_code == 204
    eng = sharing.room_engagement(room["id"])[0]
    assert eng["pages_viewed"] == 2 and eng["total_ms"] == 2000


def test_room_beacon_rejects_doc_not_in_room(isolated):
    sharing, docs = isolated
    r1, _ = sharing.create_room("r1", None, "alice")
    r2, _ = sharing.create_room("r2", None, "alice")
    foreign, _ = sharing.add_document(r2["id"], docs[1][0])
    link, _ = sharing.create_room_link(r1["id"], "alice")
    pub = _public()
    pub.get(f"/r/{link['token']}")
    r = _post_event(pub, link["token"], foreign["id"], [{"page": 1, "dwell_ms": 500}])
    assert r.status_code == 204
    assert sharing.room_engagement(r2["id"])[0]["pages"] == []


def test_public_ask_stores_and_notifies(isolated, monkeypatch):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    link, _ = sharing.create_room_link(room["id"], "alice")
    sent = {}

    import app as A

    def fake_notify(room_arg, q_arg):
        sent["room"] = room_arg["id"]
        sent["q"] = q_arg["question"]
    monkeypatch.setattr(A, "_notify_room_question", fake_notify)

    pub = _public()
    pub.get(f"/r/{link['token']}")
    r = pub.post(f"/r/{link['token']}/ask",
                 data={"email": "buyer@x.com", "question": "What is the price?"})
    # redirects back to the room index
    assert r.status_code in (302, 303)
    qs = sharing.questions_for(room["id"])
    assert len(qs) == 1 and qs[0]["question"] == "What is the price?"
    assert qs[0]["viewer_email"] == "buyer@x.com"
    assert sent["q"] == "What is the price?" and sent["room"] == room["id"]


def test_public_ask_blocked_pre_gate(isolated):
    sharing, docs = isolated
    room, _ = sharing.create_room("r", None, "alice")
    link, _ = sharing.create_room_link(room["id"], "alice", password="pw")
    pub = _public()
    # no password -> ask records nothing (enumeration-safe 204)
    r = pub.post(f"/r/{link['token']}/ask", data={"question": "leak?"})
    assert r.status_code == 204
    assert sharing.questions_for(room["id"]) == []


def test_authed_qa_answer_flow(client, isolated):
    sharing, _ = isolated
    room, _ = sharing.create_room("r", None, "pytest_admin")
    q, _ = sharing.ask_question(room["id"], "buyer@x.com", "How much?")
    # answer via the management page
    body = client.get(f"/rooms/{room['id']}/qa").get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', body).group(1)
    r = client.post(f"/rooms/{room['id']}/qa",
                    data={"_csrf": tok, "question_id": str(q["id"]),
                          "answer": "Twelve cents"})
    assert "Answer saved" in r.get_data(as_text=True)
    assert sharing.get_question(q["id"])["status"] == "answered"
