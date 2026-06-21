"""Audit/compliance snapshot: a DUPLICATE of the original invoice PDF with the SUPPLIER
details boxed RED and the CLIENT details boxed BLUE (standard PDF Square annotations, drawn
with pypdf-located coordinates + pikepdf). The original bytes are never rewritten."""
import glob
import io
import os

import pytest

import audit_snapshot as AS

_BE = sorted(glob.glob("samples/documents/*EUROWAG*BE3026001012765*"))


def _annot_colors(pdf_bytes):
    """Colours (r,g,b rounded) of the audit-highlight annotations — extracted INSIDE the open
    context so no live pikepdf object escapes (they get destroyed when the pdf is freed)."""
    import pikepdf
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        return [tuple(round(float(c), 1) for c in a.C)
                for pg in pdf.pages if "/Annots" in pg for a in pg.Annots
                if a.get("/T") == "audit-highlight"]


def test_build_returns_none_without_targets_or_pdf():
    assert AS.build(b"%PDF-1.4 x", [], []) is None        # nothing to highlight
    assert AS.build(b"not a pdf", ["X"], ["Y"]) is None    # not a PDF
    assert AS.build(b"", ["X"], []) is None


@pytest.mark.skipif(not _BE, reason="Eurowag sample PDF not present")
def test_eurowag_snapshot_boxes_supplier_red_and_client_blue():
    import extract as EX
    pb = open(_BE[0], "rb").read()
    d = EX.parse_eurowag([(os.path.basename(_BE[0]), EX.pdf_text(pb))])
    sup, cli = AS.strings_from_draft(d)
    # supplier matched by VAT/reg/address (NOT the bare name — that also appears in the
    # cession/factoring note, which is not supplier data)
    assert "BE0648861506" in sup
    assert "W.A.G. payment solutions BE BVBA" not in sup
    assert "Adverza Germany SIA" in cli
    out = AS.build(pb, sup, cli)
    assert out and out[:5] == b"%PDF-"
    cols = _annot_colors(out)
    assert (1.0, 0.0, 0.0) in cols          # at least one RED supplier box
    assert (0.0, 0.0, 1.0) in cols          # at least one BLUE client box
    # every box stays within its page (no overflow off the right edge)
    import pikepdf
    with pikepdf.open(io.BytesIO(out)) as pdf:
        for pg in pdf.pages:
            W, H = float(pg.MediaBox[2]), float(pg.MediaBox[3])
            for a in (pg.Annots if "/Annots" in pg else []):
                if a.get("/T") != "audit-highlight":
                    continue
                x0, y0, x1, y1 = [float(v) for v in a.Rect]
                assert 0 <= x0 < x1 <= W + 0.5 and 0 <= y0 < y1 <= H + 0.5


@pytest.mark.skipif(not _BE, reason="Eurowag sample PDF not present")
def test_snapshot_preserves_original_page_count_and_is_a_copy():
    import extract as EX, pikepdf
    pb = open(_BE[0], "rb").read()
    d = EX.parse_eurowag([(os.path.basename(_BE[0]), EX.pdf_text(pb))])
    out = AS.build(pb, *AS.strings_from_draft(d))
    assert len(pikepdf.open(io.BytesIO(out)).pages) == len(pikepdf.open(io.BytesIO(pb)).pages)
    assert out != pb                         # it's a distinct annotated copy


@pytest.mark.skipif(not _BE, reason="Eurowag sample PDF not present")
def test_confirm_hook_vaults_audit_snapshot_linked_to_invoice():
    # the confirm-time helper stores the snapshot as kind='audit_snapshot', linked to the same
    # (entity, supplier, invoice) as the original — viewable later via /doc/<id>.
    import app as A, extract as EX, vat_refund as VR
    pb = open(_BE[0], "rb").read()
    d = EX.parse_eurowag([(os.path.basename(_BE[0]), EX.pdf_text(pb))])
    con = VR.connect()
    import audit
    audit.set_actor(con, "t")
    ok = A._attach_audit_snapshot(con, "Adverza Germany SIA", "EUROWAG",
                                  "BE3026001012765", pb, d, "2026-05", "Belgium")
    con.commit()
    assert ok is True
    row = con.execute("SELECT kind, filename FROM invoice_documents WHERE supplier='EUROWAG' "
                      "AND invoice_ref='BE3026001012765' AND kind='audit_snapshot'").fetchone()
    con.close()
    assert row is not None and row["kind"] == "audit_snapshot"
    assert row["filename"].endswith("_audit.pdf")
