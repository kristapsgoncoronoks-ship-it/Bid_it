"""WO5 — the document-management UI surface on /customers.

The per-customer document-request register + generate-from-prepared-form + the
state-machine lifecycle actions, all on the (ADMIN_ONLY, CSRF-guarded) /customers
page. These tests drive the web route end-to-end against an ISOLATED customers DB
(monkeypatched CD.DB/DOCDIR -> tmp_path), so they never touch the demo DBs.

Covered:
  * new_doc_request opens a 'requested' row (admin); a processor is barred (403).
  * gen_doc_request generates + advances to 'generated' (a vaulted doc + a row link).
  * advance_doc_request walks sent -> signed -> received (received via a file upload
    that vaults the signed original); an out-of-state advance returns the illegal-
    transition banner and changes nothing.
  * every rendered request value is esc-escaped (an XSS payload in a note is neutered).
  * CSRF is required on the new-request / advance forms.
"""
import io
import re

import pytest


def _seed(monkeypatch, tmp_path):
    """Isolate the customers DB/vault into tmp_path and seed a customer + template.
    Returns the customer_master module (reconfigured) and the template id."""
    import customer_master as CD
    monkeypatch.setattr(CD, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(CD, "_SCHEMA_READY", set())
    monkeypatch.setattr(CD, "DOCDIR", str(tmp_path / "docs"))
    CD.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")
    con = CD.connect()
    # a text template fills with NO LibreOffice dependency (stays text/pdf bytes).
    tid = CD.add_template(con, "Contract", "signed_contract", "contract.txt",
                          b"Contract for {{company_name}}")
    con.close()
    return CD, tid


def _tok(client):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get("/customers").get_data(as_text=True)).group(1)


def _only_request(CD):
    con = CD.connect()
    rows = CD.list_document_requests(con, "ACME")
    con.close()
    assert len(rows) == 1, f"expected exactly one request, got {len(rows)}"
    return rows[0]


# ---------------------------------------------------------------- 1: new request
def test_new_doc_request_opens_requested_row(client, monkeypatch, tmp_path):
    CD, tid = _seed(monkeypatch, tmp_path)
    r = client.post("/customers", data={
        "_csrf": _tok(client), "__act": "new_doc_request", "code": "ACME",
        "template_id": str(tid), "kind": "power_of_attorney", "dr_country": "Belgium"})
    body = r.get_data(as_text=True)
    assert "opened for ACME" in body
    row = _only_request(CD)
    assert row["status"] == "requested"
    assert row["kind"] == "power_of_attorney"
    assert row["refund_country"] == "Belgium"
    # the register table renders the row + the new-request form is present
    page = client.get("/customers").get_data(as_text=True)
    assert "Document requests" in page
    assert 'name="__act" value="new_doc_request"' in page


def test_new_doc_request_admin_only_processor_403(admin_session, monkeypatch, tmp_path):
    """ADMIN_ONLY bars a processor from /customers entirely (CSRF seeded so the 403 is the
    auth guard, not CSRF)."""
    import app as A
    import auth
    try:
        auth.add_user("pytest_docproc", "Proc!Pw123", role="processor")
    except Exception:
        pass
    c = A.app.test_client()
    assert c.post("/login", data={"username": "pytest_docproc",
                                  "password": "Proc!Pw123"}).status_code == 302
    c.get("/")
    with c.session_transaction() as sess:
        tok = sess.get("_csrf") or "seed-token"
        sess["_csrf"] = tok
    r = c.post("/customers", data={"_csrf": tok, "__act": "new_doc_request",
                                   "code": "ACME", "template_id": "1",
                                   "kind": "contract"})
    assert r.status_code == 403
    assert c.get("/customers").status_code == 403


def test_new_doc_request_validates_kind_and_template(client, monkeypatch, tmp_path):
    CD, tid = _seed(monkeypatch, tmp_path)
    # missing template
    r = client.post("/customers", data={
        "_csrf": _tok(client), "__act": "new_doc_request", "code": "ACME",
        "template_id": "", "kind": "contract"})
    assert "prepared form" in r.get_data(as_text=True)
    # bad kind
    r = client.post("/customers", data={
        "_csrf": _tok(client), "__act": "new_doc_request", "code": "ACME",
        "template_id": str(tid), "kind": "not_a_kind"})
    assert "valid request kind" in r.get_data(as_text=True)
    con = CD.connect()
    assert CD.list_document_requests(con, "ACME") == []
    con.close()


# ---------------------------------------------------------------- 2: generate
def test_gen_doc_request_generates_and_advances(client, monkeypatch, tmp_path):
    CD, tid = _seed(monkeypatch, tmp_path)
    client.post("/customers", data={
        "_csrf": _tok(client), "__act": "new_doc_request", "code": "ACME",
        "template_id": str(tid), "kind": "contract"})
    rid = _only_request(CD)["id"]
    r = client.post("/customers", data={
        "_csrf": _tok(client), "__act": "gen_doc_request", "code": "ACME",
        "req_id": str(rid)})
    # the produced bytes are offered as a download
    assert r.headers.get("Content-Disposition", "").startswith("attachment")
    assert b"Acme SIA" in r.get_data()
    row = _only_request(CD)
    assert row["status"] == "generated"
    assert row["generated_doc_id"], "a vaulted customer_documents row must be recorded"
    # the page shows a download link to the vaulted generated doc
    page = client.get("/customers").get_data(as_text=True)
    assert f'/customer-doc/{row["generated_doc_id"]}' in page


# ---------------------------------------------------------------- 3: lifecycle
def test_advance_through_sent_signed_received(client, monkeypatch, tmp_path):
    CD, tid = _seed(monkeypatch, tmp_path)
    client.post("/customers", data={
        "_csrf": _tok(client), "__act": "new_doc_request", "code": "ACME",
        "template_id": str(tid), "kind": "power_of_attorney", "dr_country": "Belgium"})
    rid = _only_request(CD)["id"]
    # requested -> generated
    client.post("/customers", data={"_csrf": _tok(client), "__act": "gen_doc_request",
                                    "code": "ACME", "req_id": str(rid)})

    def advance(new_status, **extra):
        return client.post("/customers", data={
            "_csrf": _tok(client), "__act": "advance_doc_request", "code": "ACME",
            "req_id": str(rid), "new_status": new_status, **extra})

    assert "generated -&gt; sent_for_signature" in advance("sent_for_signature").get_data(as_text=True)
    assert _only_request(CD)["status"] == "sent_for_signature"
    assert "sent_for_signature -&gt; signed" in advance("signed").get_data(as_text=True)
    assert _only_request(CD)["status"] == "signed"
    # received via a FILE upload -> the signed original is vaulted
    r = client.post("/customers", data={
        "_csrf": _tok(client), "__act": "advance_doc_request", "code": "ACME",
        "req_id": str(rid), "new_status": "received",
        "signed_file": (io.BytesIO(b"WET-SIGNED-PDF"), "signed_poa.pdf")},
        content_type="multipart/form-data")
    assert "signed -&gt; received" in r.get_data(as_text=True)
    row = _only_request(CD)
    assert row["status"] == "received"
    assert row["signed_doc_id"], "the signed original must be vaulted as a customer_documents row"
    # the row links to BOTH the generated draft and the signed original
    page = client.get("/customers").get_data(as_text=True)
    assert f'/customer-doc/{row["generated_doc_id"]}' in page
    assert f'/customer-doc/{row["signed_doc_id"]}' in page


def test_out_of_state_advance_is_illegal_and_changes_nothing(client, monkeypatch, tmp_path):
    """The buttons only show valid actions, but a forged/stale advance is rejected
    server-side by advance_document_request -> illegal-transition banner, no change."""
    CD, tid = _seed(monkeypatch, tmp_path)
    client.post("/customers", data={
        "_csrf": _tok(client), "__act": "new_doc_request", "code": "ACME",
        "template_id": str(tid), "kind": "contract"})
    rid = _only_request(CD)["id"]
    # status is 'requested'; jump straight to 'received' is not a legal transition
    r = client.post("/customers", data={
        "_csrf": _tok(client), "__act": "advance_doc_request", "code": "ACME",
        "req_id": str(rid), "new_status": "received"})
    assert "illegal transition" in r.get_data(as_text=True)
    assert _only_request(CD)["status"] == "requested"      # unchanged


# ---------------------------------------------------------------- 4: escaping
def test_request_values_are_escaped(client, monkeypatch, tmp_path):
    """An XSS payload anywhere a request value renders must come out escaped — assert the
    raw <script> never appears in the page."""
    CD, tid = _seed(monkeypatch, tmp_path)
    payload = "<script>alert(1)</script>"
    con = CD.connect()
    CD.create_document_request(con, "ACME", "contract", tid, country=payload, note=payload)
    con.close()
    page = client.get("/customers").get_data(as_text=True)
    assert payload not in page
    assert "&lt;script&gt;" in page


# ---------------------------------------------------------------- 5: CSRF
def test_csrf_required_on_doc_request_forms(client, monkeypatch, tmp_path):
    CD, tid = _seed(monkeypatch, tmp_path)
    # tokenless new-request POST is rejected before any row is created
    r = client.post("/customers", data={"__act": "new_doc_request", "code": "ACME",
                                        "template_id": str(tid), "kind": "contract"})
    assert r.status_code in (400, 403)
    con = CD.connect()
    assert CD.list_document_requests(con, "ACME") == []
    con.close()
    # tokenless advance POST is likewise rejected
    r = client.post("/customers", data={"__act": "advance_doc_request", "code": "ACME",
                                        "req_id": "1", "new_status": "cancelled"})
    assert r.status_code in (400, 403)
