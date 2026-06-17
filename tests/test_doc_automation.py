"""Item ① — "Document generation & contracts" automation hub: the ADDITIVE gap.

The generator already EXISTS (templates + fill_template + docx_to_pdf + vaulting the
generated draft into customer_documents). This suite covers ONLY the new work:

  * vault_generated_document() promotes a generated draft to a FIRST-CLASS platform
    document — it records the (request -> vault-locator) link in document_request_links,
    the locator is the customer_documents.stored_path, the bytes are retrievable through
    document_vault.get_bytes(), and a versioning chain is seeded for the same subject_ref.
  * generate_request_document() auto-promotes (the link exists right after generate), and
    a re-generate refreshes the link to the new file (idempotent, one row per request).
  * document_request_board() returns a cross-customer view with derived status + overdue,
    filtered by status / customer / kind, carrying the company name + vault ref.
  * the /doc-requests control board renders requests, escapes a planted XSS customer/
    template/note value, and its quick actions (generate, save-to-vault, mark received)
    flip status; it is admin-only.
"""
import importlib
import io
import re

import pytest


@pytest.fixture()
def cd(tmp_path, monkeypatch):
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_master, "_SCHEMA_READY", set())
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    # point the versioning overlay at the same temp vault + an isolated DB
    import versioning
    importlib.reload(versioning)
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"))
    monkeypatch.setattr(versioning, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(versioning, "_SCHEMA_READY", set())
    return customer_master


def _tpl(cd, con, kind="contract"):
    return cd.add_template(con, "Service Contract", kind, "contract.txt",
                           b"Contract for {{company_name}} ({{code}}).")


# ---------------------------------------------------------------- generate-and-vault
def test_generate_promotes_to_platform_document(cd):
    import document_vault
    cd.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")
    con = cd.connect()
    tid = _tpl(cd, con)
    rid = cd.create_document_request(con, "ACME", "contract", tid)
    filled, name, ext = cd.generate_request_document(con, rid)

    # the (request -> vault locator) link exists right after generate (auto-promoted)
    ref = cd.generated_vault_ref(con, rid)
    assert ref, "generate must record the subject<->vault-locator link"

    # the link row points at the generated customer_documents row's stored_path
    r = cd.get_document_request(con, rid)
    doc = con.execute("SELECT stored_path, sha256 FROM customer_documents WHERE id=?",
                      (r["generated_doc_id"],)).fetchone()
    assert ref == doc["stored_path"]

    # the bytes are retrievable via the SAME platform path the rest of the app uses
    data = document_vault.get_bytes(ref, cd.DOCDIR)
    assert data == filled
    assert b"Acme SIA" in data

    # a versioning chain was seeded for that subject_ref (A4 sees it as first-class)
    import versioning
    cur = versioning.current(ref)
    assert cur is not None and cur["version_no"] == 1
    con.close()


def test_vault_generated_document_is_idempotent_and_refreshes_on_regenerate(cd):
    cd.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")
    con = cd.connect()
    tid = _tpl(cd, con)
    rid = cd.create_document_request(con, "ACME", "contract", tid)
    cd.generate_request_document(con, rid)

    # calling promote again is a no-op (exactly one link row per request)
    ref1, err = cd.vault_generated_document(con, rid)
    assert err == "" and ref1
    n = con.execute("SELECT COUNT(*) FROM document_request_links WHERE request_id=?",
                    (rid,)).fetchone()[0]
    assert n == 1
    first_doc_id = cd.get_document_request(con, rid)["generated_doc_id"]

    # a re-generate produces a NEW customer_documents row; the link follows the new draft
    # (still exactly one row per request — the prior draft is not orphaned in the link).
    cd.generate_request_document(con, rid)
    r = cd.get_document_request(con, rid)
    assert r["generated_doc_id"] != first_doc_id
    link = con.execute("SELECT doc_id FROM document_request_links WHERE request_id=?",
                       (rid,)).fetchone()
    assert link["doc_id"] == r["generated_doc_id"], "the link must follow the regenerated draft"
    assert con.execute("SELECT COUNT(*) FROM document_request_links WHERE request_id=?",
                       (rid,)).fetchone()[0] == 1
    con.close()


def test_vault_generated_document_no_draft_is_handled(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    rid = cd.create_document_request(con, "ACME", "contract", None)
    ref, msg = cd.vault_generated_document(con, rid)
    assert ref is None and "no generated draft" in msg
    con.close()


# ---------------------------------------------------------------- the control board
def test_board_lists_across_customers_with_status_and_filters(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    cd.add_customer("BETA", "Beta SIA", "LV")
    con = cd.connect()
    a = cd.create_document_request(con, "ACME", "contract", None)
    cd.create_document_request(con, "BETA", "power_of_attorney", None, country="Poland")
    # an aged sent_for_signature row -> overdue
    over = cd.create_document_request(con, "ACME", "power_of_attorney", None, country="France")
    con.execute("UPDATE document_requests SET status='sent_for_signature', "
                "sent_at=datetime('now','-30 days') WHERE id=?", (over,))
    con.commit()

    board = cd.document_request_board(con)
    by_id = {r["id"]: r for r in board}
    assert by_id[a]["company_name"] == "Acme SIA"
    assert by_id[over]["overdue"] is True
    assert by_id[a]["overdue"] is False
    # filters
    assert {r["id"] for r in cd.document_request_board(con, customer="BETA")} == \
        {r["id"] for r in board if r["customer"] == "BETA"}
    assert all(r["kind"] == "contract"
               for r in cd.document_request_board(con, kind="contract"))
    assert all(r["status"] == "sent_for_signature"
               for r in cd.document_request_board(con, status="sent_for_signature"))
    con.close()


# ---------------------------------------------------------------- web: the dashboard
def _seed_web(monkeypatch, tmp_path):
    import customer_master as CD
    monkeypatch.setattr(CD, "DB", str(tmp_path / "c.db"))
    monkeypatch.setattr(CD, "_SCHEMA_READY", set())
    monkeypatch.setattr(CD, "DOCDIR", str(tmp_path / "docs"))
    import versioning
    monkeypatch.setattr(versioning, "DB", str(tmp_path / "versions.db"))
    monkeypatch.setattr(versioning, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(versioning, "_SCHEMA_READY", set())
    CD.add_customer("ACME", "Acme SIA", "LV", reg_number="LV123")
    con = CD.connect()
    tid = CD.add_template(con, "Contract", "contract", "contract.txt",
                          b"Contract for {{company_name}}")
    con.close()
    return CD, tid


def _tok(client, path="/doc-requests"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def test_board_renders_and_escapes_xss(client, monkeypatch, tmp_path):
    CD, tid = _seed_web(monkeypatch, tmp_path)
    payload = "<script>alert(1)</script>"
    con = CD.connect()
    CD.create_document_request(con, "ACME", "contract", tid, country=payload, note=payload)
    con.close()
    page = client.get("/doc-requests").get_data(as_text=True)
    assert "Document requests — control board" in page
    assert "Acme SIA" in page
    assert payload not in page
    assert "&lt;script&gt;" in page


def test_board_generate_and_vault_then_mark_received(client, monkeypatch, tmp_path):
    CD, tid = _seed_web(monkeypatch, tmp_path)
    con = CD.connect()
    rid = CD.create_document_request(con, "ACME", "contract", tid)
    con.close()

    # generate from the board (auto-vaults + auto-promotes to a platform doc)
    r = client.post("/doc-requests", data={
        "_csrf": _tok(client), "__act": "gen_doc_request", "req_id": str(rid)})
    assert "generated and vaulted" in r.get_data(as_text=True)
    con = CD.connect()
    assert CD.get_document_request(con, rid)["status"] == "generated"
    assert CD.generated_vault_ref(con, rid), "the platform vault link must be recorded"
    con.close()
    # the board shows the vaulted indicator -> /share?doc_ref=
    page = client.get("/doc-requests").get_data(as_text=True)
    assert "vaulted" in page and "/share?doc_ref=" in page

    # walk to signed so 'mark received' is a legal transition, then mark received
    con = CD.connect()
    CD.advance_document_request(con, rid, "sent_for_signature")
    CD.advance_document_request(con, rid, "signed")
    con.close()
    r = client.post("/doc-requests", data={
        "_csrf": _tok(client), "__act": "mark_received", "req_id": str(rid)})
    assert "signed -&gt; received" in r.get_data(as_text=True)
    con = CD.connect()
    assert CD.get_document_request(con, rid)["status"] == "received"
    con.close()


def test_board_is_admin_only(admin_session, monkeypatch, tmp_path):
    import app as A
    import auth
    try:
        auth.add_user("pytest_boardproc", "Proc!Pw123", role="processor")
    except Exception:
        pass
    c = A.app.test_client()
    assert c.post("/login", data={"username": "pytest_boardproc",
                                  "password": "Proc!Pw123"}).status_code == 302
    assert c.get("/doc-requests").status_code == 403
