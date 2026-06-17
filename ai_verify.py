"""
AI INVOICE VERIFICATION (vision) — an OPT-IN, ADVISORY check that compares an already-
extracted DRAFT against the ORIGINAL invoice PDF using a VISION-capable model (Claude or
OpenAI). It flags discrepancies for the human; it NEVER auto-changes or gates a figure.

This is the deliberate, LOUDLY-GATED EXCEPTION to the project's AI-privacy rule: unlike
`ai_review.py` / `ai_assistant.py` (which send MINIMIZED DERIVED DATA only and never the
PDF), this feature renders the ORIGINAL PDF to page images and sends them to the external
AI provider. Because the PDF may carry IBANs / bank details, it is:

  * DEFAULT OFF (`enabled()` below). Inert unless BOTH the admin setting `ai_verify_enabled`
    is ON *and* a VISION-capable backend is configured (a Claude or OpenAI key present).
    With either missing NO network call is ever made.
  * ADVISORY ONLY — by construction. Everything here READS the PDF bytes + the draft and
    RETURNS a verdict for DISPLAY. There is NO write to any DB, NO mutation of a figure,
    status, lock, fee, or payment. `verify()` cannot change the draft.
  * Best-effort / never-raise. A transient backend error (rate-limit / quota / timeout)
    yields {"verdict":"unavailable", ...}, logged via applog; the review never crashes.

Returned by `verify()`:
    {"verdict": "confirmed"|"discrepancies"|"unreadable"|"unavailable",
     "fields": [{"name","extracted","document","match":bool}], "notes": str,
     "provider": str, "model": str, "pages": int}
The verdict is ADVISORY: the human reading the review screen still confirms the draft.

Provider selection is kept in ONE place (`_provider()` / `_VISION_CALL`) so a future
self-hosted vision endpoint can be added later; today Claude + OpenAI are implemented.
"""
import os
import io
import re
import json
import base64

import applog
# Reuse extract's transient-error taxonomy + the env-keyed model/key config; reuse
# ai_review.resolve_backend() for the shared backend selection. We do NOT reuse any
# extraction/review PROMPT — this module has its own vision verifier prompt.
import ai_review
from extract import TransientExtractionError, is_transient_error

log = applog.get("ai_verify")

# The admin opt-in setting that turns verification ON (default OFF).
SETTING = "ai_verify_enabled"

# Only these backends are VISION-capable here (Claude messages + OpenAI chat completions
# image inputs). Azure is intentionally excluded — its deployment/model is site-specific
# and not assumed vision-capable; the gate fails toward OFF for anything unlisted.
VISION_BACKENDS = ("claude", "openai")

# Page cap — render at most this many leading PDF pages to images, to bound tokens/cost.
MAX_PAGES = 3


VERIFY_PROMPT = (
    "You are an ADVISORY invoice verifier. You are given the page IMAGES of an ORIGINAL "
    "invoice PDF and a JSON object of data that was already EXTRACTED from it. Compare the "
    "extracted data to what you can read on the page images and report discrepancies. Do "
    "NOT recompute VAT or totals beyond reading them off the page; you are checking that "
    "the extraction matches the document. You CANNOT and MUST NOT change any value — your "
    "output is read-only commentary for a human. Return ONLY JSON: "
    "{\"verdict\":\"confirmed\"|\"discrepancies\"|\"unreadable\","
    "\"fields\":[{\"name\":str,\"extracted\":str,\"document\":str,\"match\":bool}],"
    "\"notes\":str}. Use verdict 'confirmed' when every checked field matches, "
    "'discrepancies' when at least one field does not match, and 'unreadable' when the "
    "images cannot be read well enough to verify. Check supplier, supplier_vat, "
    "statement_ref, statement_date, currency, the line items and the totals."
)


# ---------------------------------------------------------------- enablement
def _have_key(backend):
    """True if the API key the given vision backend needs is present in the environment.
    Mirrors extract.py's per-backend env keys. Never raises."""
    if backend == "claude":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if backend == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return False


def _provider(backend=None):
    """Resolve the VISION provider to use, or None when verification cannot run. An
    explicit `backend` arg wins; otherwise the shared backend selection
    (ai_review.resolve_backend()). Returns the backend name only when it is vision-capable
    AND its API key is present; else None. Never raises -> None (fail toward OFF)."""
    try:
        be = backend or ai_review.resolve_backend()
    except Exception as e:
        log.warning("_provider backend resolution failed — treating as OFF: %s", e)
        return None
    if be in VISION_BACKENDS and _have_key(be):
        return be
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


def model_name(backend):
    """The model name for a backend (for display / audit). Never a secret."""
    if backend == "claude":
        return os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
    if backend == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-4o")
    return ""


def provider_label(backend=None):
    """Human label for the active vision provider ('Claude'/'OpenAI'), or '' when none."""
    be = backend or _provider()
    return {"claude": "Claude (Anthropic)", "openai": "OpenAI"}.get(be, "")


# ---------------------------------------------------------------- PDF -> page images
def _render_pages(pdf_bytes, max_pages):
    """Render the FIRST `max_pages` pages of a PDF to PNG bytes via pdf2image (the same
    stack extract.py's OCR uses). Returns a list of PNG byte strings (capped). Raises on a
    rendering failure (the caller maps it to an 'unavailable' verdict)."""
    from pdf2image import convert_from_bytes
    cap = max(1, int(max_pages or MAX_PAGES))
    # last_page bounds the rasterisation itself, so a 200-page PDF never fully renders.
    pages = convert_from_bytes(pdf_bytes, first_page=1, last_page=cap)
    out = []
    for img in pages[:cap]:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _draft_summary(draft):
    """The DERIVED draft fields handed to the verifier alongside the images. We send the
    header + per-line {invoice_no,date,country,currency,net,vat} + a computed gross — the
    same display basis as the review screen. Pure; never mutates `draft`."""
    draft = draft or {}
    lines = []
    gross = 0.0
    for ln in draft.get("lines", []) or []:
        net = ln.get("net", 0) or 0
        vat = ln.get("vat", 0) or 0
        try:
            gross += float(net) + float(vat)
        except (TypeError, ValueError):
            pass
        lines.append({"invoice_no": ln.get("invoice_no"), "date": ln.get("date"),
                      "country": ln.get("country"),
                      "currency": ln.get("currency") or draft.get("currency"),
                      "net": net, "vat": vat})
    return {
        "supplier": draft.get("supplier"),
        "supplier_vat": draft.get("supplier_vat"),
        "statement_ref": draft.get("statement_ref"),
        "statement_date": draft.get("statement_date"),
        "currency": draft.get("currency"),
        "lines": lines,
        "gross_total": round(gross, 2),
    }


# ---------------------------------------------------------------- backends (vision)
def _unfence(raw):
    """Strip a ```json … ``` fence and parse the JSON object (same as extract/ai_review)."""
    raw = re.sub(r"^```(?:json)?|```$", "", str(raw).strip(), flags=re.M).strip()
    return json.loads(raw)


def _call_claude(prompt, data_str, images):
    """Claude messages API — text prompt + base64 PNG image content blocks. Returns the
    parsed JSON dict. Raises on transport/HTTP errors."""
    import requests
    key = os.environ["ANTHROPIC_API_KEY"]
    content = [{"type": "text", "text": prompt + "\n\nEXTRACTED DRAFT:\n" + data_str}]
    for png in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(png).decode("ascii")}})
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model_name("claude"), "max_tokens": 1500,
              "messages": [{"role": "user", "content": content}]},
        timeout=180)
    r.raise_for_status()
    raw = "".join(b.get("text", "") for b in r.json()["content"])
    return _unfence(raw)


def _call_openai(prompt, data_str, images):
    """OpenAI chat completions — text + base64 data-URL image_url content parts. Returns
    the parsed JSON dict. Raises on transport/HTTP errors."""
    import requests
    key = os.environ["OPENAI_API_KEY"]
    content = [{"type": "text", "text": prompt + "\n\nEXTRACTED DRAFT:\n" + data_str}]
    for png in images:
        b64 = base64.b64encode(png).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    r = requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={"model": model_name("openai"),
              "response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": content}]}, timeout=180)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    return _unfence(raw)


# Provider dispatch in ONE place — add a self-hosted vision endpoint here later.
_VISION_CALL = {"claude": _call_claude, "openai": _call_openai}


# ---------------------------------------------------------------- parsing
_VERDICTS = {"confirmed", "discrepancies", "unreadable"}


def parse_verdict(raw):
    """Validate a raw model response into the structured verdict. Never raises: an unknown
    verdict is coerced to 'unreadable', malformed fields are dropped. Pure."""
    if not isinstance(raw, dict):
        return {"verdict": "unreadable", "fields": [], "notes": ""}
    verdict = raw.get("verdict")
    if verdict not in _VERDICTS:
        verdict = "unreadable"
    fields = []
    for it in (raw.get("fields") or []):
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        if not (isinstance(name, str) and name.strip()):
            continue
        fields.append({
            "name": name,
            "extracted": "" if it.get("extracted") is None else str(it.get("extracted")),
            "document": "" if it.get("document") is None else str(it.get("document")),
            "match": bool(it.get("match")),
        })
    notes = raw.get("notes")
    notes = notes if isinstance(notes, str) else ""
    return {"verdict": verdict, "fields": fields, "notes": notes}


# ---------------------------------------------------------------- orchestration
def verify(pdf_bytes, draft, backend=None, max_pages=None):
    """Verify an already-extracted `draft` against the ORIGINAL `pdf_bytes` with a vision
    model. ADVISORY + READ-ONLY: returns a verdict dict for DISPLAY; it NEVER mutates the
    draft, a figure, a status, a lock, or a fee, and writes NO DB.

    Best-effort / NEVER raises: when no vision provider is configured it makes ZERO network
    calls and returns {"verdict":"unavailable"}; a transient backend error (quota / rate-
    limit / timeout) or a render failure likewise returns "unavailable" (logged via applog).
    The PDF is rendered to at most `max_pages` (default MAX_PAGES=3) leading page images."""
    be = _provider(backend)
    if be is None:
        return {"verdict": "unavailable", "fields": [],
                "notes": "AI verification is disabled — enable it in Admin and configure a "
                         "vision backend. (No request was sent.)",
                "provider": "", "model": "", "pages": 0}
    if not pdf_bytes:
        return {"verdict": "unavailable", "fields": [],
                "notes": "No original PDF was available to verify against.",
                "provider": be, "model": model_name(be), "pages": 0}

    cap = MAX_PAGES if max_pages is None else max_pages
    try:
        images = _render_pages(pdf_bytes, cap)
    except Exception as e:
        log.warning("AI verify could not render the PDF (%s) — advisory only: %s", be, e)
        return {"verdict": "unavailable", "fields": [],
                "notes": "Could not render the PDF to images for verification.",
                "provider": be, "model": model_name(be), "pages": 0}

    data_str = json.dumps(_draft_summary(draft), ensure_ascii=False, default=str)
    try:
        raw = _VISION_CALL[be](VERIFY_PROMPT, data_str, images)
    except TransientExtractionError as e:
        log.warning("AI verify transient error (%s) — advisory only: %s", be, e)
        return _unavailable(be, len(images), "busy")
    except Exception as e:
        if is_transient_error(e):
            log.warning("AI verify transient error (%s) — advisory only: %s", be, e)
            return _unavailable(be, len(images), "busy")
        log.warning("AI verify failed (%s: %s) — advisory only, returning unavailable", be, e)
        return _unavailable(be, len(images), "error")

    out = parse_verdict(raw)
    out.update(provider=be, model=model_name(be), pages=len(images))
    return out


def _unavailable(be, pages, why):
    notes = ("The AI verifier is busy right now — please try again in a moment. "
             "(Advisory only; nothing was changed.)" if why == "busy"
             else "The AI verifier is unavailable right now. It is advisory only — nothing "
                  "was changed; you can keep working.")
    return {"verdict": "unavailable", "fields": [], "notes": notes,
            "provider": be, "model": model_name(be), "pages": pages}
