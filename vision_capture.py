"""
AI VISION CAPTURE — an OPT-IN, ADVISORY extraction path that reads the PAGE IMAGES of a
scanned / messy / unknown-layout invoice PDF with a VISION-capable model (Claude or
OpenAI) and produces a COMPREHENSIVE structured "capture document". That document is
MAPPED into the existing review-draft shape, so it flows into the SAME human review
screen, the SAME deterministic confirm/validate gate, and the existing AI verification
(`ai_verify.py`) — extraction is never the authority; a human still confirms.

This is the deliberate "AI for capture" EXCEPTION to the project's deterministic-first
capture rule (CLAUDE.md): like `ai_verify.py`, and UNLIKE `ai_review.py` (derived data
only), it renders the ORIGINAL PDF to page images and sends them to the external AI
provider. Because of that it is, by construction:

  * DEFAULT OFF (`enabled()`). Inert unless BOTH the admin setting
    `ai_vision_capture_enabled` is ON *and* a VISION-capable backend is configured (a
    Claude or OpenAI key present). With either missing NO network call is ever made and
    extraction behaves EXACTLY as today (byte-identical).
  * ADVISORY ONLY — the capture document is a DRAFT shown next to the PDF; the reviewer
    confirms/edits before the existing reconcile + commit runs. It mutates no DB, figure,
    status, lock, or fee.
  * STRICT, never-invents. The prompt + parser extract ONLY what is printed; a field not
    on the document is null/empty — NEVER guessed or estimated. Amounts go through
    `money.f2`; dates are normalized to ISO; currency codes ISO.
  * Best-effort / never-raises. A backend error / timeout / unparseable response yields
    None, and the caller (`extract.py`) FALLS BACK to the existing OCR→parser→text-AI→
    generic chain exactly as before.

Vision plumbing (page render + provider selection + the Claude/OpenAI image calls) is
REUSED from `ai_verify.py` — this module owns only its capture PROMPT, the capture-
document schema/parser, and the capture→draft mapping.
"""
import os
import json
import threading

import applog
import money
# Reuse ai_verify's vision plumbing: provider selection (_provider / enabled gate),
# page rendering (_render_pages), the per-backend image calls (_VISION_CALL) and the
# model-name/label helpers. Reuse extract's transient-error taxonomy. We do NOT reuse
# any verifier/extraction PROMPT — this module has its own capture prompt + schema.
import ai_verify
from extract import TransientExtractionError, is_transient_error

log = applog.get("vision_capture")

# The admin opt-in setting that turns vision capture ON (default OFF).
SETTING = "ai_vision_capture_enabled"

# Thread-local "last reason capture() returned None". It is set ONLY when capture was
# ENABLED and a BACKEND error (render/transport/unparseable) caused the fallback — NEVER
# for the OFF / not-configured path (that stays silent + byte-identical). The caller
# (extract.py) reads it via take_last_error() right after a capture() that returned None.
_LAST = threading.local()


def _set_last_error(reason):
    _LAST.reason = reason


def take_last_error():
    """Return and CLEAR the reason the most recent capture() on THIS thread fell back due
    to a backend error, or None when it was OFF / not-configured / succeeded. One-shot so a
    later OFF capture cannot resurface a stale reason."""
    reason = getattr(_LAST, "reason", None)
    _LAST.reason = None
    return reason

# Page cap — render at most this many leading PDF pages to images. Fuel invoices carry
# many transaction pages, so the default is higher than the verifier's; still bounded to
# cap tokens/cost. Adjustable via env without a code change.
VISION_CAPTURE_MAX_PAGES = int(os.environ.get("VISION_CAPTURE_MAX_PAGES", "8"))


CAPTURE_PROMPT = (
    "You are an invoice DATA-CAPTURE engine. You are given the PAGE IMAGES of a fuel / "
    "road-toll invoice or statement. Read EVERYTHING printed on the pages and return ONE "
    "JSON object — the capture document — with this exact structure:\n"
    "{\n"
    '  "header": {\n'
    '    "supplier": {"name": str|null, "vat_number": str|null, "address": str|null, "country": str|null},\n'
    '    "customer": {"name": str|null, "vat_number": str|null, "account_or_card_no": str|null},\n'
    '    "invoice": {"number": str|null, "issue_date": "YYYY-MM-DD"|null, "due_date": "YYYY-MM-DD"|null, "currency": str|null, "exchange_rate": number|null}\n'
    "  },\n"
    '  "lines": [ {"date": "YYYY-MM-DD"|null, "time": str|null, "station_name": str|null, '
    '"city": str|null, "country": str|null, "product": str|null, "quantity": number|null, '
    '"unit": str|null, "unit_price": number|null, "discount": number|null, "net": number|null, '
    '"vat_rate": number|null, "vat": number|null, "gross": number|null, "card_no": str|null, '
    '"receipt_no": str|null} ],\n'
    '  "totals": {"net_total": number|null, "discount_total": number|null, "vat_total": number|null, "gross_total": number|null}\n'
    "}\n"
    "ONE line object per fuel/toll transaction shown. STRICT RULES: extract ONLY what is "
    "actually printed on the page. If a field is not present, use null — NEVER invent, "
    "estimate, guess, or compute a value that is not shown. Do NOT recompute totals or VAT. "
    "Normalize every date to ISO YYYY-MM-DD. 'net' = amount excl. VAT, 'vat' = the VAT "
    "amount, 'gross' = amount incl. VAT, as printed. Currency = ISO 4217 code (e.g. EUR). "
    "Country = full English name. Return ONLY the JSON object, no prose, no markdown fence."
)


# ---------------------------------------------------------------- enablement
def _provider():
    """The VISION provider to use for capture, or None. Reuses ai_verify._provider() so
    capture and verify share ONE provider-selection path (Claude/OpenAI vision-capable +
    key present). Never raises -> None (fail toward OFF / no network call)."""
    try:
        return ai_verify._provider()
    except Exception as e:
        log.warning("_provider resolution failed — treating capture as OFF: %s", e)
        return None


def enabled():
    """True only when BOTH the admin opt-in setting is ON and a VISION-capable backend is
    configured (key present). Never raises -> False (fail toward OFF / no network call)."""
    try:
        import auth
        on = str(auth.get_setting(SETTING, "off") or "off").lower() in ("on", "1", "true", "yes")
        return bool(on) and _provider() is not None
    except Exception as e:
        log.warning("enabled() check failed — defaulting to OFF: %s", e)
        return False


def model_name(backend=None):
    """The active capture model name (for display / audit). Never a secret."""
    return ai_verify.model_name(backend or _provider())


def provider_label(backend=None):
    """Human label for the active capture provider ('Claude'/'OpenAI'), or ''."""
    return ai_verify.provider_label(backend or _provider())


# ---------------------------------------------------------------- parsing helpers
def _s(v):
    """A trimmed string or None — never invents content (empty/whitespace -> None)."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _amount(v):
    """A printed amount -> money.f2 float, or None when ABSENT. A field that is not on the
    document stays None — NEVER coerced to 0.0 (that would invent a figure)."""
    if v is None or v == "":
        return None
    try:
        return money.f2(float(v))
    except (TypeError, ValueError):
        return None


def _rate(v):
    """A printed rate (vat_rate / exchange_rate) -> float, or None when absent/malformed.
    Kept at full precision (not cent-quantized); a rate is not a currency amount."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_date(v):
    """Best-effort ISO date passthrough. The model is asked for YYYY-MM-DD already; we keep
    the first 10 chars of a string value and drop anything obviously non-date. Never raises;
    an absent/unreadable value stays None (NEVER fabricated)."""
    s = _s(v)
    if not s:
        return None
    return s[:10]


_LINE_KEYS = ("date", "time", "station_name", "city", "country", "product", "quantity",
              "unit", "unit_price", "discount", "net", "vat_rate", "vat", "gross",
              "card_no", "receipt_no")


def _parse_line(raw):
    """Validate one raw transaction line into the capture-line shape. Drops malformed
    fields (each becomes null), NEVER raises. Returns None for a non-dict."""
    if not isinstance(raw, dict):
        return None
    return {
        "date": _iso_date(raw.get("date")),
        "time": _s(raw.get("time")),
        "station_name": _s(raw.get("station_name")),
        "city": _s(raw.get("city")),
        "country": _s(raw.get("country")),
        "product": _s(raw.get("product")),
        "quantity": _rate(raw.get("quantity")),
        "unit": _s(raw.get("unit")),
        "unit_price": _rate(raw.get("unit_price")),
        "discount": _amount(raw.get("discount")),
        "net": _amount(raw.get("net")),
        "vat_rate": _rate(raw.get("vat_rate")),
        "vat": _amount(raw.get("vat")),
        "gross": _amount(raw.get("gross")),
        "card_no": _s(raw.get("card_no")),
        "receipt_no": _s(raw.get("receipt_no")),
    }


def parse_capture(raw):
    """Validate a raw model response into the structured capture document. NEVER raises and
    NEVER invents: a missing field is null/empty, a malformed line is dropped, and a
    non-dict response yields None (so the caller falls back to the existing path). Pure."""
    if not isinstance(raw, dict):
        return None
    header = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    sup = header.get("supplier") if isinstance(header.get("supplier"), dict) else {}
    cust = header.get("customer") if isinstance(header.get("customer"), dict) else {}
    inv = header.get("invoice") if isinstance(header.get("invoice"), dict) else {}
    totals = raw.get("totals") if isinstance(raw.get("totals"), dict) else {}

    lines = []
    for it in (raw.get("lines") or []):
        ln = _parse_line(it)
        if ln is not None:
            lines.append(ln)

    return {
        "header": {
            "supplier": {"name": _s(sup.get("name")), "vat_number": _s(sup.get("vat_number")),
                         "address": _s(sup.get("address")), "country": _s(sup.get("country"))},
            "customer": {"name": _s(cust.get("name")), "vat_number": _s(cust.get("vat_number")),
                         "account_or_card_no": _s(cust.get("account_or_card_no"))},
            "invoice": {"number": _s(inv.get("number")), "issue_date": _iso_date(inv.get("issue_date")),
                        "due_date": _iso_date(inv.get("due_date")), "currency": _s(inv.get("currency")),
                        "exchange_rate": _rate(inv.get("exchange_rate"))},
        },
        "lines": lines,
        "totals": {"net_total": _amount(totals.get("net_total")),
                   "discount_total": _amount(totals.get("discount_total")),
                   "vat_total": _amount(totals.get("vat_total")),
                   "gross_total": _amount(totals.get("gross_total"))},
    }


# ---------------------------------------------------------------- capture -> draft
def to_draft(capture, files=None, backend="vision"):
    """Map a parsed CAPTURE DOCUMENT onto the existing review-draft shape so the rest of the
    pipeline (review screen, confirm/validate gate, registration, the engine) is unchanged.

    Header -> supplier / supplier_vat / statement_ref (=invoice number) / statement_date
    (=issue_date) / currency / customer. Each capture line -> the existing line keys
    {invoice_no,date,country,currency,net,vat,product,qty} PLUS the richer capture keys
    (time, station_name, city, unit_price, discount, gross, vat_rate, card_no, receipt_no)
    kept on the same line dict so nothing is lost. The FULL capture document is preserved
    under the draft's `capture` key for display/download. Pure — does not mutate `capture`."""
    capture = capture or {}
    hdr = capture.get("header") or {}
    sup = hdr.get("supplier") or {}
    cust = hdr.get("customer") or {}
    inv = hdr.get("invoice") or {}
    currency = inv.get("currency") or "EUR"
    stmt_ref = inv.get("number")
    stmt_date = inv.get("issue_date")

    lines = []
    for ln in (capture.get("lines") or []):
        lines.append({
            # the EXISTING draft-line keys the rest of the pipeline reads
            "invoice_no": stmt_ref,
            "date": ln.get("date") or stmt_date,
            "country": ln.get("country"),
            "currency": currency,
            "net": ln.get("net") if ln.get("net") is not None else 0.0,
            "vat": ln.get("vat") if ln.get("vat") is not None else 0.0,
            "product": ln.get("product"),
            "qty": ln.get("quantity"),
            "_source": "vision",
            # the RICHER capture keys, kept so they are not lost downstream
            "time": ln.get("time"),
            "station_name": ln.get("station_name"),
            "city": ln.get("city"),
            "unit": ln.get("unit"),
            "unit_price": ln.get("unit_price"),
            "discount": ln.get("discount"),
            "gross": ln.get("gross"),
            "vat_rate": ln.get("vat_rate"),
            "card_no": ln.get("card_no"),
            "receipt_no": ln.get("receipt_no"),
        })

    draft = {
        "supplier": sup.get("name"),
        "supplier_vat": sup.get("vat_number"),
        "statement_ref": stmt_ref,
        "statement_date": stmt_date,
        "currency": currency,
        "customer": cust.get("name"),
        "lines": lines,
        "capture": capture,                     # the full capture document (display/download)
        "notes": "AI-vision-captured from the page images (advisory) — verify every figure "
                 "against the PDF before confirming; values were read by a vision model, not "
                 "a deterministic parser.",
        "backend": backend,
        # confidence is 'low': vision capture is hallucination-prone like OCR/AI, so the
        # reviewer is told to scrutinise every figure. A structured/parser path still wins.
        "confidence": "low",
    }
    if files is not None:
        draft["files"] = [{"name": n, "size": len(b)} for n, b in files]
        draft["_pdf_bytes"] = files             # vault the ORIGINAL PDF(s) on confirm
    return draft


# ---------------------------------------------------------------- orchestration
def capture(pdf_bytes, backend=None, max_pages=None, files=None):
    """Read a PLAIN invoice PDF's page images with a vision model and return a REVIEW DRAFT
    (capture document mapped onto the existing draft shape), or None.

    Returns None — so the caller falls back to the existing OCR→parser→text-AI→generic
    chain — when: capture is OFF / no provider configured (ZERO network call), the PDF is
    empty, rendering fails, the backend errors/times out, or the response is unparseable.
    NEVER raises. ADVISORY: the returned draft is a draft a human confirms; nothing here
    writes a DB or mutates a figure.

    `files` (a list of (name, bytes)) is the original PDF batch to vault on confirm; when
    given, the draft carries `files` + `_pdf_bytes` exactly like the other extract paths."""
    # Clear any stale reason FIRST. The OFF / not-configured branch below returns without
    # ever setting one (so OFF stays silent — no note, no error-log entry, byte-identical).
    _set_last_error(None)
    be = backend or _provider()
    if be is None or not pdf_bytes:
        return None

    cap = VISION_CAPTURE_MAX_PAGES if max_pages is None else max_pages
    try:
        images = ai_verify._render_pages(pdf_bytes, cap)
    except Exception as e:
        log.warning("vision capture could not render the PDF (%s) — falling back: %s", be, e)
        _set_last_error(f"could not render the PDF to images ({e})")
        return None
    if not images:
        _set_last_error("the PDF produced no page images to read")
        return None

    try:
        raw = ai_verify._VISION_CALL[be](CAPTURE_PROMPT, "", images)
    except TransientExtractionError as e:
        log.warning("vision capture transient error (%s) — falling back: %s", be, e)
        _set_last_error(f"the {provider_label(be)} backend is busy / transient error ({e})")
        return None
    except Exception as e:
        if is_transient_error(e):
            log.warning("vision capture transient error (%s) — falling back: %s", be, e)
            _set_last_error(f"the {provider_label(be)} backend is busy / transient error ({e})")
        else:
            log.warning("vision capture failed (%s: %s) — falling back to the existing path", be, e)
            _set_last_error(f"the {provider_label(be)} backend call failed: {ai_verify._map_provider_error(e)}")
        return None

    cap_doc = parse_capture(raw)
    if cap_doc is None:
        log.warning("vision capture (%s) returned an unparseable document — falling back", be)
        _set_last_error(f"the {provider_label(be)} backend returned an unreadable response")
        return None

    draft = to_draft(cap_doc, files=files, backend="vision")
    draft["_vision_pages"] = len(images)        # for audit (job/doc id + provider + pages)
    draft["notes"] = (draft.get("notes") or "") + f" | {provider_label(be)} · {model_name(be)}"
    return draft


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        data = open(sys.argv[1], "rb").read()
        d = capture(data)
        if d is None:
            print("vision capture is OFF or produced nothing (fell back to existing path)")
        else:
            d.pop("_pdf_bytes", None)
            print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
    else:
        print("usage: python3 vision_capture.py <invoice.pdf>  (requires the feature ON)")
