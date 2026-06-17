"""② E-SIGNATURE (SES) — a Simple Electronic Signature with an audit trail over the
secure-sharing module + the generated CRM contracts.

Covers:
  * the module API: create_request -> record_signature binds the signed bytes' SHA-256,
    produces + vaults a signed PDF (retrievable via document_vault) whose recorded sha256
    matches, verify() PASSES untampered and FAILS when bytes are altered; void flips status;
  * graceful fallback: a non-PDF "document" still records the signature event (no signed
    locator) rather than losing it;
  * the certificate page is generated (signed PDF has MORE pages than the original);
  * the public signing GATE step: a require_signature share link only records a signature
    AFTER the gates pass (consent + name required; pre-gate / no-consent rejected);
  * the internal surface: /esign + /esign/send render, the verify page escapes a planted XSS
    signer name and shows a PASS verify result, void flips status.

Isolated into tmp_path exactly like test_sharing_b2.py: esign.DB + sharing.DB are repointed
and a REAL one-page PDF is vaulted via document_vault; the demo DBs are never touched.
"""
import io

import pytest


def _real_pdf():
    """A minimal but VALID one-page PDF (so pypdf can parse + stamp + append a cert page)."""
    from pypdf import PdfWriter, PageObject
    w = PdfWriter()
    w.add_page(PageObject.create_blank_page(width=300, height=300))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    """Repoint esign.DB + sharing.DB to throwaway files and stage a REAL vaulted PDF.
    Returns (esign, sharing, doc_ref, pdf_bytes, docdir)."""
    import esign
    import sharing
    import document_vault
    import vat_refund as VR

    monkeypatch.setattr(esign, "DB", str(tmp_path / "esign.db"))
    monkeypatch.setattr(esign, "_SCHEMA_READY", set())
    monkeypatch.setattr(sharing, "DB", str(tmp_path / "sharing.db"))
    monkeypatch.setattr(sharing, "_SCHEMA_READY", set())

    docdir = str(tmp_path / "docs")
    pdf = _real_pdf()
    locator, _ = document_vault.LocalBackend(docdir).put("invoice.pdf", pdf)
    monkeypatch.setattr(VR, "DOCDIR", docdir)
    return esign, sharing, locator, pdf, docdir


# ---------------------------------------------------------------- module API
def test_create_and_record_binds_sha256(isolated):
    esign, _sharing, doc_ref, pdf, docdir = isolated
    req, err = esign.create_request(doc_ref, "Contract A", "alice")
    assert err == "" and req is not None and req["status"] == "pending"

    sig, e2 = esign.record_signature(
        req["id"], "Bob Signer", pdf, signer_email="bob@example.com",
        ip="9.9.9.9", user_agent="UA/9", docdir=docdir)
    assert e2 == "" and sig is not None
    import hashlib
    assert sig["signed_doc_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert sig["signer_name"] == "Bob Signer" and sig["ip"] == "9.9.9.9"
    # the request flipped to 'signed'
    assert esign.get_request(req["id"])["status"] == "signed"
    # the signed PDF was produced + vaulted and is retrievable
    assert sig["signed_locator"]
    import document_vault
    data = document_vault.get_bytes(sig["signed_locator"], docdir)
    assert data.startswith(b"%PDF")
    assert sig["signed_sha256"] == hashlib.sha256(data).hexdigest()


def test_certificate_page_appended(isolated):
    esign, _sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "Cert", "alice")
    sig, _ = esign.record_signature(req["id"], "Cara", pdf, docdir=docdir)
    import document_vault
    from pypdf import PdfReader
    signed = document_vault.get_bytes(sig["signed_locator"], docdir)
    orig_pages = len(PdfReader(io.BytesIO(pdf)).pages)
    signed_pages = len(PdfReader(io.BytesIO(signed)).pages)
    # the appended Signature Certificate page makes the signed PDF strictly longer
    assert signed_pages > orig_pages


def test_verify_passes_and_fails_on_tamper(isolated):
    esign, _sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "V", "alice")
    sig, _ = esign.record_signature(req["id"], "Dee", pdf, docdir=docdir)

    ok = esign.verify(sig["id"], original_bytes=pdf, docdir=docdir)
    assert ok["ok"] is True and ok["doc_ok"] is True and ok["signed_ok"] is True

    bad = esign.verify(sig["id"], original_bytes=pdf + b"tampered", docdir=docdir)
    assert bad["ok"] is False and bad["doc_ok"] is False

    # tamper the produced signed PDF in the vault -> signed_ok must FAIL
    import document_vault
    backend = document_vault.LocalBackend(docdir)
    real = backend._safe_path(sig["signed_locator"])
    with open(real, "ab") as fh:
        fh.write(b"corruption")
    bad2 = esign.verify(sig["id"], docdir=docdir)
    assert bad2["ok"] is False and bad2["signed_ok"] is False


def test_record_signature_requires_name(isolated):
    esign, _sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "N", "alice")
    sig, err = esign.record_signature(req["id"], "   ", pdf, docdir=docdir)
    assert sig is None and "name" in err.lower()


def test_void_flips_status(isolated):
    esign, _sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "Z", "alice")
    ok, msg = esign.void_request(req["id"], "alice")
    assert ok and esign.get_request(req["id"])["status"] == "void"
    # a voided request refuses a signature
    sig, err = esign.record_signature(req["id"], "Late", pdf, docdir=docdir)
    assert sig is None and "void" in err.lower()


def test_graceful_fallback_on_non_pdf(isolated):
    """A non-parseable 'document' must still record the signature EVENT (audit record stands)
    with no signed locator — never lose a signature to a stamping failure."""
    esign, _sharing, _doc_ref, _pdf, docdir = isolated
    req, _ = esign.create_request("doc:junk", "Junk", "alice")
    junk = b"not a pdf at all"
    sig, err = esign.record_signature(req["id"], "Eve", junk, docdir=docdir)
    assert err == "" and sig is not None
    import hashlib
    assert sig["signed_doc_sha256"] == hashlib.sha256(junk).hexdigest()
    assert sig["signed_locator"] is None      # stamping fell back gracefully
    # verify still works against the original bytes (the binding the audit record holds)
    v = esign.verify(sig["id"], original_bytes=junk, docdir=docdir)
    assert v["ok"] is True and v["doc_ok"] is True
    bad = esign.verify(sig["id"], original_bytes=junk + b"x", docdir=docdir)
    assert bad["ok"] is False


# ---------------------------------------------------------------- public signing gate
def test_public_sign_flow_records_after_gates(client, isolated):
    esign, sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "Public", "pytest_admin")
    link, _ = sharing.create_link(
        doc_ref, "Sign me", "pytest_admin",
        require_signature=True, signature_request_id=req["id"])
    import app as A
    pub = A.app.test_client()

    # GET the public link -> the SIGNING page (not the plain viewer), labelled SES
    r = pub.get(f"/s/{link['token']}")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Sign this document" in body
    assert "not a qualified electronic signature" in body
    assert 'name="signer_name"' in body
    # nothing recorded yet
    assert esign.signatures_for(req["id"]) == []

    # POST WITHOUT consent -> rejected, no signature recorded
    r2 = pub.post(f"/s/{link['token']}/sign",
                  data={"signer_name": "No Consent"})
    assert "consent" in r2.get_data(as_text=True).lower()
    assert esign.signatures_for(req["id"]) == []

    # POST WITHOUT a name -> rejected
    r3 = pub.post(f"/s/{link['token']}/sign",
                  data={"consent": "1", "signer_name": "  "})
    assert esign.signatures_for(req["id"]) == []

    # POST WITH consent + name -> signature recorded, request flips to signed
    r4 = pub.post(f"/s/{link['token']}/sign",
                  data={"consent": "1", "signer_name": "Grace Hopper",
                        "signer_email": "grace@example.com"})
    assert r4.status_code in (302, 303)
    sigs = esign.signatures_for(req["id"])
    assert len(sigs) == 1 and sigs[0]["signer_name"] == "Grace Hopper"
    assert esign.get_request(req["id"])["status"] == "signed"
    import hashlib
    assert sigs[0]["signed_doc_sha256"] == hashlib.sha256(pdf).hexdigest()


def test_public_sign_blocked_before_nda_gate(client, isolated):
    """require_signature combined with an NDA: no signature can be recorded until the NDA
    gate is satisfied (bytes/sign before gates pass is impossible)."""
    esign, sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "Gated", "pytest_admin")
    link, _ = sharing.create_link(
        doc_ref, "NDA+sign", "pytest_admin", nda_required=True,
        require_signature=True, signature_request_id=req["id"])
    import app as A
    pub = A.app.test_client()
    # GET shows the NDA gate first (not the signing page)
    body = pub.get(f"/s/{link['token']}").get_data(as_text=True)
    assert "accept to continue" in body
    assert "Sign this document" not in body
    # a direct sign POST before the NDA is accepted records NOTHING
    pub.post(f"/s/{link['token']}/sign", data={"consent": "1", "signer_name": "Sneaky"})
    assert esign.signatures_for(req["id"]) == []


def test_void_link_refuses_signature(client, isolated):
    esign, sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "Voidable", "pytest_admin")
    link, _ = sharing.create_link(
        doc_ref, "v", "pytest_admin",
        require_signature=True, signature_request_id=req["id"])
    esign.void_request(req["id"], "pytest_admin")
    import app as A
    pub = A.app.test_client()
    pub.post(f"/s/{link['token']}/sign", data={"consent": "1", "signer_name": "Too Late"})
    assert esign.signatures_for(req["id"]) == []


# ---------------------------------------------------------------- internal surface
def test_internal_dashboard_and_send(client, isolated):
    esign, sharing, doc_ref, pdf, docdir = isolated
    page = client.get("/esign").get_data(as_text=True)
    assert "Send a document for signature" in page
    assert "not a qualified electronic signature" in page

    import re
    tok = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    r = client.post("/esign/send", data={
        "_csrf": tok, "subject_ref_manual": doc_ref, "title": "Internal send"})
    body = r.get_data(as_text=True)
    assert "Signing link created" in body and "/s/" in body
    # a request was created
    reqs = [q for q in esign.list_requests() if q["title"] == "Internal send"]
    assert len(reqs) == 1


def test_verify_page_escapes_xss_signer(client, isolated):
    esign, sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "XSS", "pytest_admin")
    xss = '<script>alert(1)</script>'
    sig, _ = esign.record_signature(req["id"], xss, pdf, docdir=docdir)
    body = client.get(f"/esign/{req['id']}/verify").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    # the live verify result is shown and PASSES for the untampered signed PDF
    assert "PASS" in body


def test_internal_void(client, isolated):
    esign, sharing, doc_ref, pdf, docdir = isolated
    req, _ = esign.create_request(doc_ref, "VoidUI", "pytest_admin")
    import re
    tok = re.search(r'name="_csrf" value="([^"]+)"',
                    client.get("/esign").get_data(as_text=True)).group(1)
    client.post("/esign/void", data={"_csrf": tok, "request_id": req["id"]})
    assert esign.get_request(req["id"])["status"] == "void"
