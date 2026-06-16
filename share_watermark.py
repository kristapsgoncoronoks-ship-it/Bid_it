"""
DYNAMIC WATERMARK (B2) — overlay a faint, diagonal, tiled watermark on every page of a
shared PDF, identifying the viewer (email / captured email / else client IP), a UTC
timestamp and "CONFIDENTIAL".

NO NEW DEPENDENCY. We use ONLY pypdf (already a declared dependency — see requirements.txt;
reportlab/pymupdf are NOT installed). pypdf has no text-rendering helper, so we build the
watermark page's content stream from raw PDF text operators (a Helvetica Type1 font, light
gray fill, a rotated text matrix tiled across the page) and merge it OVER each source page.

SAFETY. apply_watermark() NEVER raises: any failure (a malformed/encrypted PDF, an
unexpected pypdf error) is logged and the function returns None so the caller falls back to
streaming the ORIGINAL bytes — a watermark must never break the viewer.
"""
import io
import math

import applog

log = applog.get("share_watermark")

# Tiling + look. Faint gray, 45°, repeated across the page so a screenshot of any region
# carries the mark. Kept intentionally simple (no external font metrics).
_FONT_SIZE = 13
_STEP_X = 230
_STEP_Y = 150
_GRAY = "0.80 0.80 0.80 rg"


def _pdf_escape(s):
    """Escape a string for a PDF literal-string operand: backslash, parens, and drop any
    non-Latin-1 char (the standard Helvetica encoding can't render it)."""
    out = []
    for ch in s:
        if ch in ("\\", "(", ")"):
            out.append("\\" + ch)
        elif 32 <= ord(ch) < 127 or 160 <= ord(ch) <= 255:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out).encode("latin-1", "replace")


def _overlay_page(width, height, text):
    """Build a single watermark page (a pypdf PageObject) sized to (width, height) whose
    content stream tiles `text` diagonally in faint gray."""
    from pypdf import PageObject
    from pypdf.generic import (DecodedStreamObject, NameObject, DictionaryObject)

    page = PageObject.create_blank_page(width=width, height=height)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = font
    res = DictionaryObject()
    res[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = res

    cos = math.cos(math.radians(45))
    sin = math.sin(math.radians(45))
    body = _pdf_escape(text)

    ops = [b"q", _GRAY.encode("latin-1")]
    x = -int(width)
    while x < int(width) * 2:
        y = -int(height) // 2
        while y < int(height) + _STEP_Y:
            ops.append(b"BT")
            ops.append(f"/F1 {_FONT_SIZE} Tf".encode("latin-1"))
            ops.append(
                f"{cos:.4f} {sin:.4f} {-sin:.4f} {cos:.4f} {x} {y} Tm".encode("latin-1"))
            ops.append(b"(" + body + b") Tj")
            ops.append(b"ET")
            y += _STEP_Y
        x += _STEP_X
    ops.append(b"Q")

    stream = DecodedStreamObject()
    stream.set_data(b"\n".join(ops))
    page[NameObject("/Contents")] = stream
    return page


def apply_watermark(pdf_bytes, text):
    """Return new PDF bytes with `text` tiled diagonally over EVERY page, or None on any
    failure (caller falls back to the original bytes). Never raises."""
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as e:  # pypdf missing/broken — should not happen, it's a dependency
        log.warning("watermark unavailable (pypdf import failed): %s", e)
        return None
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            # We won't fight an encrypted/protected PDF — serve the original instead.
            log.info("watermark skipped: source PDF is encrypted")
            return None
        # Clone into the writer FIRST, then merge the overlay into the writer-attached
        # page (pypdf deprecates merging into an unattached page — that path is "unreliable").
        writer = PdfWriter(clone_from=reader)
        for page in writer.pages:
            box = page.mediabox
            page.merge_page(_overlay_page(float(box.width), float(box.height), text))
        out = io.BytesIO()
        writer.write(out)
        data = out.getvalue()
        # Defensive: only return something that re-parses and clearly differs.
        if not data or data == pdf_bytes:
            return None
        return data
    except Exception as e:
        log.warning("apply_watermark failed (%d bytes in): %s", len(pdf_bytes or b""), e)
        return None
