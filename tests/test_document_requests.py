"""Tests for the document_requests register + lifecycle state machine (WO3).

Covers: create (audited), the full generate -> sent -> signed -> received pipeline
with auto-vaulting of the generated draft and the signed original, the *_at stamps,
illegal-transition rejection, the cancelled terminal state, kind/customer validation,
the pending worklist (age/overdue), the broken-soffice .docx fallback, and re-generate
replacing (not orphaning) the prior draft."""
import importlib

import pytest


@pytest.fixture()
def cd(tmp_path, monkeypatch):
    import customer_master
    importlib.reload(customer_master)
    monkeypatch.setattr(customer_master, "DB", str(tmp_path / "cust.db"))
    monkeypatch.setattr(customer_master, "DOCDIR", str(tmp_path / "docs"))
    return customer_master


def _tpl(cd, con, kind="contract"):
    """Register a trivial text template (text path: no soffice needed, leftover-free)."""
    return cd.add_template(con, "Service Contract", kind, "contract.txt",
                           b"Contract for {{company_name}} ({{code}}).")


def _docx_tpl(cd, con, kind="power_of_attorney"):
    """A minimal valid .docx so the as_pdf path exercises the soffice/.docx fallback."""
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"/>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="x"><w:body><w:p><w:r>'
                   '<w:t>POA for {{company_name}}</w:t></w:r></w:p></w:body></w:document>')
    return cd.add_template(con, "Power of Attorney", kind, "poa.docx", buf.getvalue())


# -------------------------------------------------------------------- create
def test_create_request_is_requested_and_audited(cd):
    import audit
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    audit.set_actor(con, "alice")
    rid = cd.create_document_request(con, "ACME", "contract", None, requested_by="alice")
    audit.reset_actor(con)
    r = cd.get_document_request(con, rid)
    assert r["status"] == "requested"
    assert r["customer"] == "ACME" and r["kind"] == "contract"
    assert r["requested_at"]
    # the INSERT trigger logged it against the configured actor
    row = con.execute("SELECT changed_by, action FROM audit_log WHERE tbl='document_requests' "
                      "AND rowkey=? AND action='INSERT'", (str(rid),)).fetchone()
    con.close()
    assert row is not None and row["changed_by"] == "alice"


def test_create_rejects_bad_kind_and_unknown_customer(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    with pytest.raises(ValueError):
        cd.create_document_request(con, "ACME", "nonsense_kind", None)
    with pytest.raises(ValueError):
        cd.create_document_request(con, "NOSUCH", "contract", None)
    # nothing was inserted
    assert con.execute("SELECT COUNT(*) FROM document_requests").fetchone()[0] == 0
    con.close()


# -------------------------------------------------------------------- lifecycle
def test_full_lifecycle_generate_sent_signed_received(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    tid = _tpl(cd, con)
    rid = cd.create_document_request(con, "ACME", "contract", tid)

    # generate -> auto-vault, sha + doc id recorded, generated_at stamped
    filled, name, ext = cd.generate_request_document(con, rid)
    assert filled and name and ext
    r = cd.get_document_request(con, rid)
    assert r["status"] == "generated"
    assert r["generated_at"] and r["generated_sha256"] and r["generated_doc_id"]
    import hashlib
    assert r["generated_sha256"] == hashlib.sha256(filled).hexdigest()
    doc = con.execute("SELECT * FROM customer_documents WHERE id=?",
                      (r["generated_doc_id"],)).fetchone()
    assert doc is not None and doc["customer"] == "ACME" and doc["kind"] == "contract"

    # sent -> sent_at
    ok, _ = cd.advance_document_request(con, rid, "sent_for_signature")
    assert ok and cd.get_document_request(con, rid)["sent_at"]

    # signed -> signed_at
    ok, _ = cd.advance_document_request(con, rid, "signed")
    assert ok and cd.get_document_request(con, rid)["signed_at"]

    # received WITH a signed original -> vaulted, signed_doc_id set, received_at stamped
    ok, _ = cd.advance_document_request(con, rid, "received",
                                        signed_file=b"WET-SIGNED-PDF",
                                        signed_filename="acme_signed.pdf")
    assert ok
    r = cd.get_document_request(con, rid)
    assert r["status"] == "received" and r["received_at"] and r["signed_doc_id"]
    sdoc = con.execute("SELECT * FROM customer_documents WHERE id=?",
                       (r["signed_doc_id"],)).fetchone()
    assert sdoc is not None and sdoc["filename"] == "acme_signed.pdf"
    assert sdoc["id"] != r["generated_doc_id"]
    con.close()


def test_illegal_transition_rejected_and_no_change(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    rid = cd.create_document_request(con, "ACME", "contract", None)
    ok, msg = cd.advance_document_request(con, rid, "received")
    assert ok is False and "illegal transition" in msg
    r = cd.get_document_request(con, rid)
    assert r["status"] == "requested" and r["received_at"] is None
    con.close()


def test_cancelled_is_terminal(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    rid = cd.create_document_request(con, "ACME", "contract", None)
    ok, _ = cd.advance_document_request(con, rid, "cancelled")
    assert ok and cd.get_document_request(con, rid)["status"] == "cancelled"
    # no transition out of cancelled (including back to generated)
    ok, msg = cd.advance_document_request(con, rid, "generated")
    assert ok is False and "illegal transition" in msg
    assert cd.get_document_request(con, rid)["status"] == "cancelled"
    con.close()


def test_advance_same_status_is_idempotent(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    rid = cd.create_document_request(con, "ACME", "contract", None)
    ok, _ = cd.advance_document_request(con, rid, "requested")
    assert ok and cd.get_document_request(con, rid)["status"] == "requested"
    con.close()


def test_generate_rejected_when_cancelled(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    tid = _tpl(cd, con)
    rid = cd.create_document_request(con, "ACME", "contract", tid)
    cd.advance_document_request(con, rid, "cancelled")
    with pytest.raises(ValueError):
        cd.generate_request_document(con, rid)
    con.close()


# -------------------------------------------------------------------- soffice fallback
def test_generate_docx_fallback_vaults_a_doc(cd):
    """With soffice broken in this sandbox the produced doc may be .docx (not pdf) — we
    only assert a doc was vaulted with a sha, not that it is a PDF."""
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    tid = _docx_tpl(cd, con)
    rid = cd.create_document_request(con, "ACME", "power_of_attorney", tid, country="Poland")
    filled, name, ext = cd.generate_request_document(con, rid)
    assert filled and ext in ("pdf", "docx")
    r = cd.get_document_request(con, rid)
    assert r["generated_doc_id"] and r["generated_sha256"]
    doc = con.execute("SELECT * FROM customer_documents WHERE id=?",
                      (r["generated_doc_id"],)).fetchone()
    assert doc is not None and doc["country"] == "Poland"
    con.close()


# -------------------------------------------------------------------- re-generate
def test_regenerate_replaces_prior_draft(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    tid = _tpl(cd, con)
    rid = cd.create_document_request(con, "ACME", "contract", tid)
    cd.generate_request_document(con, rid)
    first_doc_id = cd.get_document_request(con, rid)["generated_doc_id"]

    cd.generate_request_document(con, rid)  # re-generate from status 'generated'
    r = cd.get_document_request(con, rid)
    assert r["status"] == "generated"
    assert r["generated_doc_id"] != first_doc_id
    # the prior draft row is gone (not orphaned)
    assert con.execute("SELECT 1 FROM customer_documents WHERE id=?",
                       (first_doc_id,)).fetchone() is None
    # exactly one generated draft remains
    assert con.execute("SELECT COUNT(*) FROM customer_documents WHERE customer='ACME' "
                       "AND kind='contract'").fetchone()[0] == 1
    con.close()


# -------------------------------------------------------------------- pending worklist
def test_pending_excludes_received_and_cancelled_and_flags_overdue(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    con = cd.connect()
    open_rid = cd.create_document_request(con, "ACME", "contract", None)
    done_rid = cd.create_document_request(con, "ACME", "contract", None)
    cd.advance_document_request(con, done_rid, "cancelled")

    # an aged sent_for_signature request -> overdue
    overdue_rid = cd.create_document_request(con, "ACME", "power_of_attorney", None,
                                             country="Poland")
    con.execute("UPDATE document_requests SET status='sent_for_signature', "
                "sent_at=datetime('now','-30 days') WHERE id=?", (overdue_rid,))
    con.commit()

    pend = cd.pending_document_requests(con, overdue_days=14)
    ids = {p["id"] for p in pend}
    assert open_rid in ids and overdue_rid in ids and done_rid not in ids
    by_id = {p["id"]: p for p in pend}
    assert by_id[overdue_rid]["overdue"] is True
    assert by_id[open_rid]["overdue"] is False
    assert all("age_days" in p for p in pend)
    con.close()


def test_list_filters_by_customer_and_status(cd):
    cd.add_customer("ACME", "Acme SIA", "LV")
    cd.add_customer("BETA", "Beta SIA", "LV")
    con = cd.connect()
    a = cd.create_document_request(con, "ACME", "contract", None)
    cd.create_document_request(con, "BETA", "contract", None)
    assert [r["id"] for r in cd.list_document_requests(con, code="ACME")] == [a]
    assert all(r["status"] == "requested"
               for r in cd.list_document_requests(con, status="requested"))
    assert cd.get_document_request(con, 999999) is None
    con.close()
