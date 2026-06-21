"""audit_snapshot.py — compliance/audit COPY of an invoice PDF with the SUPPLIER and CLIENT
details boxed in colour (RED = supplier, BLUE = client).

A best-effort, dependency-light overlay: locate the captured detail strings via pypdf's
text-position visitor, then draw standard PDF *Square* annotations with pikepdf. The ORIGINAL
bytes are never rewritten — the annotations overlay the page — so the snapshot is a faithful
DUPLICATE of the original plus visual highlights (every PDF viewer renders /Square markup).

It NEVER raises into the caller: returns the annotated bytes, or None when it cannot build one
(no PDF, libraries missing, nothing located) so the caller simply keeps the original.
"""
import io
import re

import applog

log = applog.get("audit_snapshot")

RED = (1.0, 0.0, 0.0)      # supplier company details
BLUE = (0.0, 0.0, 1.0)     # client / customer details
_PAD = 2.0                 # points of padding around a located text box


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _mul(a, b):
    """Compose two PDF affine matrices (6-tuples): a then b."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _page_chunks(page):
    """[(matrix, size, est_width, text), ...] for a page. `matrix` is the COMBINED text→user
    matrix (tm × cm) — combining the text matrix with the CTM is essential, since a rotated /
    scaled page (e.g. /Rotate 90 invoices) carries the transform in the CTM; using tm alone
    mis-places every box. Width is estimated from the character count. Best-effort -> []."""
    out = []

    def v(text, cm, tm, font, size):
        t = (text or "").strip()
        if not t:
            return
        sz = abs(size) or 8.0
        out.append((_mul(tm, cm), sz, len(text) * sz * 0.5, t))

    try:
        page.extract_text(visitor_text=v)
    except Exception as e:
        log.warning("page chunk extract failed (best-effort): %s", e)
    return out


def _chunk_box(matrix, w, sz):
    """Axis-aligned bounding box (x0,y0,x1,y1) in PDF user space of a chunk's text rectangle,
    obtained by transforming the four corners (0,−0.2·sz)…(w,0.9·sz) of the text-space box
    through `matrix`. Transforming the corners (not just the origin) makes the box correct
    under ANY rotation/skew the CTM encodes."""
    cs = [_apply(matrix, 0, -0.2 * sz), _apply(matrix, w, -0.2 * sz),
          _apply(matrix, w, 0.9 * sz), _apply(matrix, 0, 0.9 * sz)]
    xs = [c[0] for c in cs]
    ys = [c[1] for c in cs]
    return (min(xs) - _PAD, min(ys) - _PAD, max(xs) + _PAD, max(ys) + _PAD)


def _boxes(chunks, targets):
    """Rectangles (x0,y0,x1,y1) for chunks that match any target string. A chunk matches when
    its normalized text CONTAINS a target (e.g. a footer line containing the VAT) or — for a
    name split across chunks — is contained BY a target. Short/garbage chunks are ignored."""
    nts = [_norm(t) for t in targets if t and len(_norm(t)) >= 4]
    if not nts:
        return []
    boxes = []
    for matrix, sz, w, t in chunks:
        nt = _norm(t)
        if not nt:
            continue
        if any(g in nt or (len(nt) >= 6 and nt in g) for g in nts):
            boxes.append(_chunk_box(matrix, w, sz))
    return boxes


def _clip(boxes, bounds):
    """Clamp boxes to the page rectangle `bounds`=(x0,y0,x1,y1); drop any that collapse. The
    width estimate can overshoot a long line past the page edge — clipping keeps the highlight
    on the page."""
    bx0, by0, bx1, by1 = bounds
    out = []
    for x0, y0, x1, y1 in boxes:
        cx0, cy0 = max(x0, bx0), max(y0, by0)
        cx1, cy1 = min(x1, bx1), min(y1, by1)
        if cx1 - cx0 > 1 and cy1 - cy0 > 1:
            out.append((cx0, cy0, cx1, cy1))
    return out


def build(pdf_bytes, supplier_strings, client_strings, max_pages=8):
    """Return an annotated COPY of `pdf_bytes` with supplier details boxed RED and client
    details boxed BLUE, or None when nothing could be located / a dependency is missing / the
    input isn't a PDF. Never raises."""
    try:
        from pypdf import PdfReader
        import pikepdf
        from pikepdf import Name, Dictionary, Array
    except Exception as e:
        log.warning("audit snapshot needs pypdf + pikepdf: %s", e)
        return None
    if not pdf_bytes or pdf_bytes[:5] != b"%PDF-":
        return None
    sup = [s for s in (supplier_strings or []) if s]
    cli = [s for s in (client_strings or []) if s]
    if not (sup or cli):
        return None
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        n = min(len(reader.pages), max_pages)
        per_page = []
        any_box = False
        for i in range(n):
            page = reader.pages[i]
            mb = page.mediabox
            bounds = (float(mb.left), float(mb.bottom), float(mb.right), float(mb.top))
            ch = _page_chunks(page)
            sb = _clip(_boxes(ch, sup), bounds)
            cb = _clip(_boxes(ch, cli), bounds)
            per_page.append((sb, cb))
            any_box = any_box or bool(sb or cb)
        if not any_box:
            return None

        pdf = pikepdf.open(io.BytesIO(pdf_bytes))

        def mk(box, rgb, label):
            x0, y0, x1, y1 = box
            return pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Square,
                Rect=Array([float(x0), float(y0), float(x1), float(y1)]),
                C=Array([float(c) for c in rgb]),       # border colour
                CA=1, F=4,                                # opaque; Print flag set
                BS=Dictionary(W=1.5, S=Name.S),           # solid border, 1.5pt
                T="audit-highlight", Contents=label))

        for i, (sb, cb) in enumerate(per_page):
            if i >= len(pdf.pages):
                break
            anns = ([mk(b, RED, "Supplier details") for b in sb]
                    + [mk(b, BLUE, "Client details") for b in cb])
            if not anns:
                continue
            pg = pdf.pages[i]
            existing = list(pg.Annots) if "/Annots" in pg else []
            pg.Annots = Array(existing + anns)

        buf = io.BytesIO()
        pdf.save(buf)
        return buf.getvalue()
    except Exception as e:
        log.warning("audit snapshot build failed (best-effort): %s", e)
        return None


def strings_from_draft(draft):
    """(supplier_strings, client_strings) to highlight, taken from a captured/confirmed draft.
    Supplier = the read-off-invoice legal entity (name / VAT / reg-no / address); client = the
    customer name and any captured client VAT."""
    d = draft or {}
    # Match the supplier by VAT / reg-no / address — these appear ONLY in the real supplier
    # identification block. The bare legal NAME is deliberately excluded: it also appears in a
    # cession/factoring note ("…ceded to W.A.G. Issuing Services…"), which is not supplier data.
    supplier = [d.get("supplier_vat"), d.get("supplier_reg_no"), d.get("supplier_address")]
    client = [d.get("customer"), d.get("customer_vat"), d.get("client_vat")]
    return [s for s in supplier if s], [s for s in client if s]
