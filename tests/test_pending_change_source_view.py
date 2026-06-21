"""Admin VISUAL CONTROL of a pending high-risk supplier change: the approval surface must
link the SOURCE invoice PDF so an admin can verify the proposed value against the document
before approving (and serve it inline for in-browser viewing)."""
import app as A
import supplier_master as SM
import supplier_sync as SS
import vat_refund as VR
import audit


def _queue_change_with_doc():
    con = SM.connect(); audit.set_actor(con, "t"); SS._ensure_schema(con)
    SS._queue_change(con, "E100", "vat", "", "BE0676647155", "BE94032/5363090", "t")
    con.commit(); con.close()
    vc = VR.connect(); audit.set_actor(vc, "t")
    VR.attach_document(vc, "AdverzaTEST", "E100", "BE94032/5363090",
                       file_bytes=b"%PDF-1.4 source", filename="BE94032_5363090.pdf")
    vc.commit(); vc.close()


def test_pending_change_card_links_source_pdf():
    _queue_change_with_doc()
    with A.app.test_request_context("/"):
        html = A._pending_changes_card(standalone=True)
    # leads with the legal entity (not the bare code) AND offers a view-source link
    assert "E100 International Trade sp. z o.o." in html
    assert "?inline=1" in html and "/doc/" in html


def test_change_source_docs_matches_statement_and_per_line_refs():
    _queue_change_with_doc()
    # exact statement ref resolves to the vaulted doc
    assert _ids(A._change_source_docs("E100", "BE94032/5363090"))
    # a per-line ref ('<stmt> #41') still resolves to the statement-ref document
    assert _ids(A._change_source_docs("E100", "BE94032/5363090 #41"))
    # wrong supplier / blank ref -> nothing (never raises)
    assert A._change_source_docs("NOPE", "BE94032/5363090") == []
    assert A._change_source_docs("E100", "") == []


def test_doc_inline_serves_pdf_for_viewing(client):
    _queue_change_with_doc()
    did = _ids(A._change_source_docs("E100", "BE94032/5363090"))[0]
    r = client.get(f"/doc/{did}?inline=1")
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("application/pdf")
    assert "inline" in r.headers.get("Content-Disposition", "")
    # default (no inline) still downloads as an attachment
    r2 = client.get(f"/doc/{did}")
    assert "attachment" in r2.headers.get("Content-Disposition", "")


def _ids(docs):
    return [d[0] for d in docs]
