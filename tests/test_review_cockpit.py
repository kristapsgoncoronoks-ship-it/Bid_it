"""
Intake REVIEW COCKPIT (Build 2) — the high-traffic "verify the extracted draft before
confirm" screen. These tests cover the six pieces SURFACED additively on the review form,
without weakening the existing confirm/cancel flow or the deterministic legal gate:

  1. inline BRAND→legal-entity linking (admin control / processor guidance; the alias is
     recorded and the draft re-resolves to the legal entity).
  2. live tie-out — the client-side markup/data-attributes app.js reads (server renders a
     neutral placeholder + the parsed document total).
  3. confidence cues — the prominent overall chip + amber .needs-check on risky fields.
  4. "why can't I file this?" checklist — a read-only mirror of validate.validate_batch:
     green "Ready to file" when can_commit, else the specific blockers + a disabled Confirm.
  5. PDF source pane — the /extract/review/pdf/<token> stream (token-gated, nosniff) shown
     for a PDF intake and HIDDEN for a non-PDF one; a forged/bad token is rejected.
  6. keyboard niceties — the confirm form carries data-noenter (Enter-guard hook).

The review screen is rendered through /extract/ai-review with the backend OFF (it simply
re-renders _review_form), and drafts are staged in .extract_tmp via _stash_draft (the same
hook the upload path uses). Per-test DB isolation comes from the conftest FFS_DATA_DIR
redirect (suppliers.db is a per-test copy of the demo DB: DKV/BP/EUROWAG/…).
"""
import os
import pickle
import re

import pytest

import app as A
import supplier_master as SM
import validate as VAL


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _csrf(client, path="/extract"):
    return re.search(r'name="_csrf" value="([^"]+)"',
                     client.get(path).get_data(as_text=True)).group(1)


def _stash(token, draft, pdf=True):
    """Stage a review draft (and optionally its PDF-bytes pkl) under a valid token."""
    A._stash_draft(token, draft)
    tmp = os.path.join(A.WORKDIR, ".extract_tmp")
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, token + ".pkl"), "wb") as f:
        pickle.dump([("inv.pdf", PDF_BYTES)] if pdf else [], f)


def _drop(token):
    for ext in (".draft.json", ".pkl"):
        try:
            os.unlink(os.path.join(A.WORKDIR, ".extract_tmp", token + ext))
        except OSError:
            pass


def _render(client, token, period="2026-05"):
    """Re-render the review cockpit (AI review OFF -> plain _review_form re-render)."""
    import auth
    auth.set_setting("ai_review_backend", "none")
    return client.post("/extract/ai-review",
                       data={"_csrf": _csrf(client), "token": token, "period": period})


def _draft(supplier="DKV", cover=1210.0, vat=210.0, conf="high"):
    d = {"supplier": supplier, "supplier_vat": "", "statement_ref": "S-RVK",
         "statement_date": "2026-05-31", "backend": "parser", "confidence": conf,
         "files": [],
         "lines": [{"invoice_no": "BE001", "date": "2026-05-31", "country": "Belgium",
                    "net": 1000.0, "vat": vat, "_source": "parser"}]}
    if cover is not None:
        d["coversheet_total"] = cover
    return d


# ───────────────────────────────────────── 1. inline brand-linking
def test_unrecognised_brand_shows_admin_link_control(client):
    token = "00000000000c0001"
    _stash(token, _draft(supplier="ZZZBrandNew", cover=None, conf="low"))
    try:
        html = _render(client, token).get_data(as_text=True)
        assert "Link this brand to a legal entity" in html
        # the <select> of existing suppliers (read via dataproduct) is populated
        assert 'name="code"' in html and 'value="DKV"' in html
    finally:
        _drop(token)


def test_recognised_supplier_hides_link_control(client):
    # DKV is a real supplier code -> no link control needed
    token = "00000000000c0002"
    _stash(token, _draft(supplier="DKV"))
    try:
        html = _render(client, token).get_data(as_text=True)
        assert "Link this brand to a legal entity" not in html
    finally:
        _drop(token)


def test_link_brand_records_alias_and_reresolves(client):
    token = "00000000000c0003"
    brand = "ZZZ Cockpit Brand"
    _stash(token, _draft(supplier=brand, cover=None, conf="low"))
    try:
        assert SM.code_for_brand(brand) is None          # not linked yet
        r = client.post("/extract/link-brand",
                        data={"_csrf": _csrf(client), "token": token, "brand": brand,
                              "code": "DKV", "period": "2026-05"})
        body = r.get_data(as_text=True)
        assert r.status_code == 200
        # the alias is recorded in master data (audited via add_brand)
        assert SM.code_for_brand(brand) == "DKV"
        # success toast + the re-render leads with the legal entity (supplier prefilled DKV)
        assert "data-toast" in body and "Linked" in body
        assert 'name="supplier" value="DKV"' in body
        # the stash was updated so a reload keeps the resolved code
        assert (A._load_draft(token) or {}).get("supplier") == "DKV"
    finally:
        SM.remove_brand("DKV", brand)
        _drop(token)


def test_processor_sees_guidance_not_control(client):
    # a non-admin processor must NOT get the curation control — only guidance
    token = "00000000000c0004"
    _stash(token, _draft(supplier="ZZZBrandProc", cover=None, conf="low"))
    try:
        with client.session_transaction() as s:
            s["role"] = "processor"
        html = _render(client, token).get_data(as_text=True)
        assert "Unrecognised supplier" in html
        assert "Link this brand to a legal entity" not in html
    finally:
        with client.session_transaction() as s:
            s["role"] = "admin"
        _drop(token)


def test_processor_blocked_from_link_brand_route(client):
    token = "00000000000c0005"
    _stash(token, _draft(supplier="ZZZBrandProc2", cover=None, conf="low"))
    try:
        with client.session_transaction() as s:
            s["role"] = "processor"
        r = client.post("/extract/link-brand",
                        data={"_csrf": _csrf(client), "token": token,
                              "brand": "ZZZBrandProc2", "code": "DKV", "period": "2026-05"})
        # ADMIN_ONLY -> _guard refuses (403); the alias is NOT recorded
        assert r.status_code in (302, 403)
        assert SM.code_for_brand("ZZZBrandProc2") is None
    finally:
        with client.session_transaction() as s:
            s["role"] = "admin"
        _drop(token)


# ───────────────────────────────────────── 2. live tie-out (client-side)
def test_tieout_markup_and_data_attributes(client):
    token = "00000000000c0006"
    _stash(token, _draft(cover=1210.0))
    try:
        html = _render(client, token).get_data(as_text=True)
        # the live indicator element + the parsed document total the JS reads
        assert 'id="tieout"' in html
        assert 'data-cover-total="1210.0"' in html
        # the editable amount inputs the JS sums are marked
        assert "data-tieout-net" in html and "data-tieout-vat" in html
    finally:
        _drop(token)


def test_tieout_absent_total_has_no_cover_attr(client):
    token = "00000000000c0007"
    _stash(token, _draft(cover=None))
    try:
        html = _render(client, token).get_data(as_text=True)
        assert 'id="tieout"' in html
        assert "data-cover-total=" not in html        # no parsed total -> sum-only mode
    finally:
        _drop(token)


# ───────────────────────────────────────── 3. confidence cues
def test_confidence_chip_and_needs_check_on_low(client):
    token = "00000000000c0008"
    _stash(token, _draft(conf="low"))
    try:
        html = _render(client, token).get_data(as_text=True)
        assert "Extraction confidence" in html
        # low confidence tints the supplier + statement-ref fields amber (class APPLIED to
        # an input, not just present in the head CSS)
        assert 'class="needs-check"' in html
    finally:
        _drop(token)


def test_high_confidence_complete_has_no_needs_check(client):
    token = "00000000000c0009"
    _stash(token, _draft(conf="high"))
    try:
        html = _render(client, token).get_data(as_text=True)
        # a complete, high-confidence draft has no empty-required highlight APPLIED to a
        # field (the class name still appears in the <head> CSS — that's expected).
        assert 'class="needs-check"' not in html      # supplier/ref/date field flag
        assert 'class="r needs-check"' not in html    # net amount field flag
    finally:
        _drop(token)


# ───────────────────────────────────────── 4. "why can't I file this?" checklist
def test_blockers_mirror_validate_batch_blocked(client):
    token = "00000000000c0010"
    # line sum (1000+90=1090) does not tie the 1210 document total -> blocked
    _stash(token, _draft(cover=1210.0, vat=90.0))
    try:
        # the helper mirrors the deterministic gate exactly
        ok, blockers = A._review_blockers(_draft(cover=1210.0, vat=90.0))
        assert ok is False and any("Tie-out mismatch" in b for b in blockers)
        # and the SAME gate is authoritative
        vr = VAL.validate_batch([{"invoice_no": "BE001", "country": "Belgium",
                                  "net": 1000.0, "vat": 90.0}], coversheet_total=1210.0)
        assert vr["can_commit"] is False
        # the rendered checklist shows the blocker and disables Confirm
        html = _render(client, token).get_data(as_text=True)
        assert "file this yet" in html
        assert "Tie-out mismatch" in html
        assert "disabled aria-disabled" in html
    finally:
        _drop(token)


def test_blockers_ready_when_can_commit(client):
    token = "00000000000c0011"
    _stash(token, _draft(cover=1210.0, vat=210.0))
    try:
        ok, blockers = A._review_blockers(_draft(cover=1210.0, vat=210.0))
        assert ok is True and blockers == []
        html = _render(client, token).get_data(as_text=True)
        assert "Ready to file" in html
        assert "disabled aria-disabled" not in html   # Confirm is enabled
    finally:
        _drop(token)


def test_blockers_does_not_weaken_confirm_gate(client):
    """The checklist is read-only: a draft the cockpit shows as blocked is STILL refused by
    the real server gate (the cockpit never let it through)."""
    token = "00000000000c0012"
    _stash(token, _draft(cover=1210.0, vat=90.0))
    try:
        form = {"_csrf": _csrf(client), "token": token, "nlines": "1",
                "supplier": "DKV", "period": "2026-05", "stmt_ref": "S-RVK",
                "stmt_date": "2026-05-31", "customer": "OUR ENTITY",
                "inv_0": "BE001", "date_0": "2026-05-31", "ctry_0": "Belgium",
                "ccy_0": "EUR", "net_0": "1000", "vat_0": "90"}
        r = client.post("/extract/confirm", data=form)
        body = r.get_data(as_text=True)
        assert "Commit blocked" in body and "Tie-out FAILED" in body
        assert "queued for registration" not in body
    finally:
        _drop(token)


# ───────────────────────────────────────── 5. PDF source pane + stream
def test_pdf_pane_shown_for_pdf_intake(client):
    token = "00000000000c0013"
    _stash(token, _draft(), pdf=True)
    try:
        r = _render(client, token)
        html = r.get_data(as_text=True)
        assert f"/extract/review/pdf/{token}" in html
        assert '<object data="/extract/review/pdf/' in html
        # the review page relaxes object-src so the same-origin embed renders
        assert "object-src 'self'" in r.headers.get("Content-Security-Policy", "")
    finally:
        _drop(token)


def test_pdf_pane_hidden_for_non_pdf_intake(client):
    token = "00000000000c0014"
    _stash(token, _draft(), pdf=False)        # no PDF bytes (xlsx/xml/e-invoice)
    try:
        html = _render(client, token).get_data(as_text=True)
        assert f"/extract/review/pdf/{token}" not in html
        assert "<object" not in html
    finally:
        _drop(token)


def test_pdf_route_serves_valid_token(client):
    token = "00000000000c0015"
    _stash(token, _draft(), pdf=True)
    try:
        r = client.get(f"/extract/review/pdf/{token}")
        assert r.status_code == 200
        assert r.headers.get("Content-Type") == "application/pdf"
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.get_data().startswith(b"%PDF-")
    finally:
        _drop(token)


def test_pdf_route_rejects_forged_token(client):
    # a request-forged token is rejected BEFORE any path join / unpickle (C1 gate)
    for bad in ("zzzz", "../../etc/passwd", "00000000000000000000", "g0000000000000aa"):
        r = client.get("/extract/review/pdf/" + bad)
        assert r.status_code == 404, bad


def test_pdf_route_404_for_non_pdf_draft(client):
    token = "00000000000c0016"
    _stash(token, _draft(), pdf=False)
    try:
        r = client.get(f"/extract/review/pdf/{token}")
        assert r.status_code == 404
    finally:
        _drop(token)


def test_pdf_route_requires_login():
    # no session -> the central guard bounces to /login (never serves the PDF)
    c = A.app.test_client()
    r = c.get("/extract/review/pdf/00000000000c0017")
    assert r.status_code in (302, 401, 403)
    assert r.headers.get("Content-Type") != "application/pdf"


# ───────────────────────────────────────── 6. keyboard niceties
def test_confirm_form_carries_enter_guard_hook(client):
    token = "00000000000c0018"
    _stash(token, _draft())
    try:
        html = _render(client, token).get_data(as_text=True)
        # the confirm form opts into the app.js Enter-guard (no accidental register)
        assert 'action="/extract/confirm"' in html
        assert "data-noenter" in html
    finally:
        _drop(token)
