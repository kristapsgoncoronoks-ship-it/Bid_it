"""Regression: a vaulted document whose stored file is missing/NULL must 404, not 500.

Previously doc_download passed a NULL stored_path straight to document_vault.get_bytes,
raising TypeError ('expected str... not NoneType') -> a 500 crash on the page.
"""
import vat_refund as VR


def _insert_orphan_doc(stored_path):
    con = VR.connect()
    try:
        cols = [r["name"] for r in con.execute("PRAGMA table_info(invoice_documents)")]
        row = {"entity": "ACME", "supplier": "TEST", "invoice_ref": "X1",
               "filename": "missing.pdf", "stored_path": stored_path,
               "sha256": "0" * 64, "bytes": 0}
        use = {k: v for k, v in row.items() if k in cols}
        ph = ",".join("?" for _ in use)
        con.execute(f"INSERT INTO invoice_documents ({','.join(use)}) VALUES ({ph})",
                    list(use.values()))
        con.commit()
        return con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    finally:
        con.close()


def _cleanup(doc_id):
    con = VR.connect()
    try:
        con.execute("DELETE FROM invoice_documents WHERE id=?", (doc_id,))
        con.commit()
    finally:
        con.close()


def test_doc_with_null_stored_path_404_not_500(client):
    did = _insert_orphan_doc(None)
    try:
        r = client.get(f"/doc/{did}")
        assert r.status_code == 404, f"expected clean 404, got {r.status_code}"
        assert "missing or unreadable" in r.get_data(as_text=True)
    finally:
        _cleanup(did)


def test_doc_with_missing_file_404_not_500(client):
    did = _insert_orphan_doc("does/not/exist/nope.pdf")
    try:
        r = client.get(f"/doc/{did}")
        assert r.status_code == 404
    finally:
        _cleanup(did)
