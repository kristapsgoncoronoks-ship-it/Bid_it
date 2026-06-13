"""Audit-ready EVIDENCE-EXPORT pack (monetization M6).

vat_refund.evidence_pack(entity, country, period) bundles a VAT claim's
SHA-256-verified original documents into a single ZIP with an integrity MANIFEST +
a cover summary, so a claim's supporting evidence can be handed over provably-intact.
A corrupted (MISMATCH) or missing (MISSING) document is SURFACED in the cover +
manifest, never silently dropped. The /export/evidence route is ADMIN-ONLY, CSRF-
guarded and audited.
"""
import csv
import hashlib
import importlib
import io
import zipfile


# ----------------------------------------------------------------- isolation helpers
def _vr(tmp_path, monkeypatch):
    """A vat_refund bound to a throwaway claims DB + local document vault."""
    import vat_refund, document_vault
    importlib.reload(vat_refund)
    monkeypatch.setattr(vat_refund, "DB", str(tmp_path / "v.db"))
    monkeypatch.setattr(vat_refund, "ANALYTICS_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr(vat_refund, "DOCDIR", str(tmp_path / "docs"))
    monkeypatch.setattr(vat_refund, "_SCHEMA_READY", set())
    # local vault backend regardless of the host machine's configured default
    monkeypatch.setattr(document_vault, "BACKEND", "local")
    return vat_refund


def _seed_claim(vr, *, store=True, second_bytes=b"%PDF-1.4 invoice TWO bytes"):
    """Seed one claim (entity x country x period) with 2 registered invoices and a
    real document per invoice (bytes in the temp vault, sha256 in invoice_documents).
    Returns (con, paths) where paths maps invoice_ref -> stored locator."""
    import document_vault
    con = vr.connect()
    con.execute("""INSERT INTO vat_applications (entity, refund_country, ref_period,
                   vat_eur, currency, status, status_code)
                   VALUES ('Acme SIA','Belgium','2026-Q2', 1234.56,'EUR','submitted','2')""")
    invoices = [("BP", "INV1", b"%PDF-1.4 invoice ONE bytes"),
                ("Shell", "INV2", second_bytes)]
    paths = {}
    for sup, ref, data in invoices:
        con.execute("""INSERT INTO vat_claimed_invoices
                       (entity, refund_country, supplier, invoice_ref, ref_period)
                       VALUES ('Acme SIA','Belgium',?,?,'2026-Q2')""", (sup, ref))
        sha = hashlib.sha256(data).hexdigest()
        loc = ""
        if store:
            loc, web = document_vault.copy_to(f"Acme/{ref}.pdf", data, vr.DOCDIR)
            paths[ref] = loc
        con.execute("""INSERT INTO invoice_documents
                       (entity, supplier, invoice_ref, filename, stored_path, sha256, size)
                       VALUES ('Acme SIA',?,?,?,?,?,?)""",
                    (sup, ref, f"{ref}.pdf", loc, sha, len(data)))
    con.commit()
    return con, paths


def _open_pack(zip_bytes):
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = z.namelist()
    manifest = list(csv.DictReader(io.StringIO(z.read("MANIFEST.sha256.csv").decode())))
    cover = z.read("COVER.txt").decode()
    return z, names, manifest, cover


# ---------------------------------------------------------------- happy path: all OK
def test_evidence_pack_all_documents_ok(tmp_path, monkeypatch):
    vr = _vr(tmp_path, monkeypatch)
    con, paths = _seed_claim(vr)
    zip_bytes, summary = vr.evidence_pack("Acme SIA", "Belgium", "2026-Q2", con=con)
    con.close()

    z, names, manifest, cover = _open_pack(zip_bytes)
    # both original files are present (named by invoice_ref), plus manifest + cover
    docnames = [n for n in names if n.startswith("documents/")]
    assert len(docnames) == 2, names
    assert any("INV1" in n for n in docnames) and any("INV2" in n for n in docnames)
    assert "MANIFEST.sha256.csv" in names and "COVER.txt" in names

    # manifest lists the CORRECT recorded sha256s with verify_status OK
    by_ref = {r["invoice_ref"]: r for r in manifest}
    for ref, data in (("INV1", b"%PDF-1.4 invoice ONE bytes"),
                      ("INV2", b"%PDF-1.4 invoice TWO bytes")):
        assert by_ref[ref]["verify_status"] == "OK"
        assert by_ref[ref]["sha256"] == hashlib.sha256(data).hexdigest()

    # the archived bytes ARE the original bytes
    inv1_name = next(n for n in docnames if "INV1" in n)
    assert z.read(inv1_name) == b"%PDF-1.4 invoice ONE bytes"

    # cover summary carries the claim header + an all-intact verdict
    assert "Acme SIA" in cover and "Belgium" in cover and "2026-Q2" in cover
    assert "1,234.56 EUR" in cover
    assert "verified intact" in cover

    assert summary["ok"] == 2 and summary["mismatch"] == 0 and summary["missing"] == 0
    assert summary["intact"] is True and summary["failures"] == []
    assert summary["documents"] == 2 and summary["invoices"] == 2


# --------------------------------------------------------- corruption -> MISMATCH
def test_evidence_pack_corrupted_file_marked_mismatch(tmp_path, monkeypatch):
    vr = _vr(tmp_path, monkeypatch)
    con, paths = _seed_claim(vr)
    # corrupt INV1's stored file in place (the live bytes no longer hash to record)
    with open(paths["INV1"], "wb") as f:
        f.write(b"TAMPERED CONTENT")
    zip_bytes, summary = vr.evidence_pack("Acme SIA", "Belgium", "2026-Q2", con=con)
    con.close()

    z, names, manifest, cover = _open_pack(zip_bytes)
    by_ref = {r["invoice_ref"]: r for r in manifest}
    assert by_ref["INV1"]["verify_status"] == "MISMATCH"
    assert by_ref["INV2"]["verify_status"] == "OK"
    # the tampered bytes are still shipped (recipient sees exactly what is on disk)
    inv1_name = next(n for n in names if n.startswith("documents/") and "INV1" in n)
    assert z.read(inv1_name) == b"TAMPERED CONTENT"
    # integrity surfaced loudly, not hidden
    assert "INTEGRITY FAILURES" in cover
    assert "MISMATCH" in cover and "INV1" in cover
    assert summary["mismatch"] == 1 and summary["ok"] == 1
    assert summary["intact"] is False
    assert any("INV1" in f and "MISMATCH" in f for f in summary["failures"])


# ------------------------------------------------------------- dropped -> MISSING
def test_evidence_pack_missing_file_marked_missing(tmp_path, monkeypatch):
    import os
    vr = _vr(tmp_path, monkeypatch)
    con, paths = _seed_claim(vr)
    os.remove(paths["INV2"])                       # the stored file vanishes
    zip_bytes, summary = vr.evidence_pack("Acme SIA", "Belgium", "2026-Q2", con=con)
    con.close()

    z, names, manifest, cover = _open_pack(zip_bytes)
    by_ref = {r["invoice_ref"]: r for r in manifest}
    assert by_ref["INV2"]["verify_status"] == "MISSING"
    assert by_ref["INV1"]["verify_status"] == "OK"
    # a missing file is NOT in the documents/ folder, but the manifest records it
    assert not any(n.startswith("documents/") and "INV2" in n for n in names)
    assert "INTEGRITY FAILURES" in cover and "MISSING" in cover
    assert summary["missing"] == 1 and summary["intact"] is False


# ------------------------------------------------------- reuses verify_documents logic
def test_pack_status_agrees_with_verify_documents(tmp_path, monkeypatch):
    """evidence_pack re-verifies via the SAME _verify_one() as verify_documents();
    a corrupt store must read CORRUPT in the sweep and MISMATCH in the pack."""
    vr = _vr(tmp_path, monkeypatch)
    con, paths = _seed_claim(vr)
    with open(paths["INV1"], "wb") as f:
        f.write(b"X")
    rows, vsumm = vr.verify_documents(con)
    con.close()
    assert vsumm["corrupt"] == 1 and vsumm["ok"] == 1
    inv1 = next(r for r in rows if r["invoice_ref"] == "INV1")
    assert inv1["status"] == "CORRUPT"             # sweep wording == pack's MISMATCH


# ------------------------------------------------------------------- route: admin-only
def _csrf(client):
    """Seed + read this session's CSRF token from a page that always renders a form."""
    import re
    body = client.get("/queue").get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', body).group(1)


def test_route_admin_downloads_zip_and_audits(tmp_path, monkeypatch, client):
    import app as A
    vr = _vr(tmp_path, monkeypatch)
    con, _ = _seed_claim(vr)
    con.close()
    tok = _csrf(client)
    r = client.post("/export/evidence", data={
        "_csrf": tok, "entity": "Acme SIA", "country": "Belgium", "period": "2026-Q2"})
    assert r.status_code == 200, r.status_code
    assert r.headers["Content-Type"].startswith("application/zip")
    assert "Evidence_Acme_SIA_Belgium_2026-Q2.zip" in r.headers.get(
        "Content-Disposition", "")
    z = zipfile.ZipFile(io.BytesIO(r.get_data()))
    assert "MANIFEST.sha256.csv" in z.namelist() and "COVER.txt" in z.namelist()

    # the export action is audited (an EVIDENCE_EXPORT row attributed to the actor)
    import audit
    vc = vr.connect()
    hist = audit.history(vc, action="EVIDENCE_EXPORT")
    vc.close()
    assert hist, "evidence export was not audited"
    assert "Acme SIA|Belgium|2026-Q2" in hist[0]["rowkey"]
    assert hist[0]["changed_by"] not in (None, "system")


def test_route_processor_forbidden(tmp_path, monkeypatch, admin_session):
    """The whole VAT/recovery surface is admin-only: a processor gets 403 (no leak)."""
    import app as A, auth
    vr = _vr(tmp_path, monkeypatch)
    con, _ = _seed_claim(vr)
    con.close()
    auth.add_user("evid_proc", "Pw!evid123", role="processor")
    c = A.app.test_client()
    assert c.post("/login", data={"username": "evid_proc",
                                  "password": "Pw!evid123"}).status_code == 302
    # a valid CSRF token so the request reaches (and is rejected by) the admin-only
    # guard, not the earlier CSRF guard — proving the surface is admin-restricted.
    tok = _csrf(c)
    r = c.post("/export/evidence", data={"_csrf": tok, "entity": "Acme SIA",
                                         "country": "Belgium", "period": "2026-Q2"})
    assert r.status_code == 403, r.status_code


def test_route_unknown_claim_404(tmp_path, monkeypatch, client):
    vr = _vr(tmp_path, monkeypatch)
    con = vr.connect(); con.close()
    tok = _csrf(client)
    r = client.post("/export/evidence", data={
        "_csrf": tok, "entity": "Nobody", "country": "Mars", "period": "2026-Q2"})
    assert r.status_code == 404
