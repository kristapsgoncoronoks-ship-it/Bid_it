"""
AI INVOICE VERIFICATION (vision) — an OPT-IN, ADVISORY check that compares an already-
captured DRAFT against the ORIGINAL invoice PDF using a VISION-capable model (Claude or
OpenAI). The PDF page images are the SOURCE OF TRUTH; each field of the captured data is
verified against them in ONE batch. When the draft carries a full `capture` document (the
vision-capture path) the WHOLE capture document — every header field, every transaction
line with all its fields, and the totals — is verified field-by-field; otherwise the
condensed summary is sent (backward compatible). It flags discrepancies for the human;
it NEVER auto-changes or gates a figure.

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
import copy
import json
import base64
import datetime

import applog
import money
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
    "invoice PDF and a JSON 'capture document' of data that was already captured from it. "
    "THE PDF PAGE IMAGES ARE THE SOURCE OF TRUTH / THE MAIN DOCUMENT. Verify EACH field of "
    "the provided capture document against the PDF. Where the capture DISAGREES with the "
    "PDF, the PDF is correct and the captured value is flagged wrong. Do NOT invent values "
    "that are not present in the PDF, and do NOT recompute VAT or totals beyond reading "
    "them off the page. You CANNOT and MUST NOT change any value — your output is read-only "
    "commentary for a human. Report PER FIELD: the field name, the captured value, the "
    "value the PDF actually shows, and match true/false. Return ONLY JSON: "
    "{\"verdict\":\"confirmed\"|\"discrepancies\"|\"unreadable\","
    "\"fields\":[{\"name\":str,\"extracted\":str,\"document\":str,\"match\":bool}], "
    "\"notes\":str}, where 'extracted' is the captured value and 'document' is the value "
    "the PDF actually shows. Use verdict 'confirmed' when every field matches the PDF, "
    "'discrepancies' when at least one captured field does not match the PDF, and "
    "'unreadable' when the images cannot be read well enough to verify. Cover the header "
    "fields (supplier, customer, invoice incl. due_date and exchange_rate), EVERY "
    "transaction line with all its fields, and the totals. Use clear field names like "
    "'invoice.due_date', 'line[2].vat', 'totals.gross'."
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


def _setting_on(setting):
    """True if the named admin opt-in setting is ON. Never raises -> False (fail to OFF)."""
    try:
        import auth
        return str(auth.get_setting(setting, "off") or "off").lower() in ("on", "1", "true", "yes")
    except Exception as e:
        log.warning("setting %s check failed — treating as OFF: %s", setting, e)
        return False


def enabled():
    """True only when BOTH the admin opt-in setting is ON and a VISION-capable backend is
    configured (key present). Never raises -> False (fail toward OFF / no network call)."""
    try:
        return _setting_on(SETTING) and _provider() is not None
    except Exception as e:
        log.warning("enabled() check failed — defaulting to OFF: %s", e)
        return False


def status(setting=SETTING):
    """The PRECISE live state of a vision pipeline (verify or capture), computed from the
    gating conditions, for DISPLAY on the AI settings page. Returns a dict:
        {"active": bool, "provider": <backend|None>, "provider_label": str,
         "model": str, "reason": str}
    `reason` is "" when ACTIVE, else the FIRST failing condition in gate order:
    setting off -> no vision backend selected (review backend not claude/openai)
    -> no API key loaded. Never raises (any failure -> INACTIVE with the reason)."""
    try:
        if not _setting_on(setting):
            return {"active": False, "provider": None, "provider_label": "", "model": "",
                    "reason": f"the admin setting '{setting}' is off"}
        # The review backend drives provider selection; surface WHICH condition fails.
        try:
            be = ai_review.resolve_backend()
        except Exception as e:
            log.warning("status: backend resolution failed: %s", e)
            be = None
        if be not in VISION_BACKENDS:
            cur = be or "none"
            return {"active": False, "provider": None, "provider_label": "", "model": "",
                    "reason": f"the AI review backend is '{cur}' — set it to 'claude' or "
                              f"'openai' (the only vision-capable backends)"}
        if not _have_key(be):
            envkey = "ANTHROPIC_API_KEY" if be == "claude" else "OPENAI_API_KEY"
            return {"active": False, "provider": None, "provider_label": "", "model": "",
                    "reason": f"no API key is loaded for '{be}' (set {envkey})"}
        return {"active": True, "provider": be, "provider_label": provider_label(be),
                "model": model_name(be), "reason": ""}
    except Exception as e:
        log.warning("status() check failed — reporting INACTIVE: %s", e)
        return {"active": False, "provider": None, "provider_label": "", "model": "",
                "reason": "the AI pipeline status could not be determined"}


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


def _verify_payload(draft):
    """The data object handed to the verifier alongside the PDF page images.

    When the draft carries a `capture` document (the VISION-capture path), send the FULL
    capture document — every header field (supplier, customer, invoice incl. due_date +
    exchange_rate), EVERY transaction line with ALL its fields, and the totals — so the
    verifier checks the rich capture field-by-field against the PDF. Otherwise (a
    deterministic / parser / OCR draft) keep the existing condensed summary (backward
    compatible). Pure; never mutates `draft`."""
    draft = draft or {}
    cap = draft.get("capture")
    if isinstance(cap, dict):
        return cap
    return _draft_summary(draft)


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
    content = [{"type": "text", "text": prompt + "\n\nCAPTURE DOCUMENT TO VERIFY:\n" + data_str}]
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
    content = [{"type": "text", "text": prompt + "\n\nCAPTURE DOCUMENT TO VERIFY:\n" + data_str}]
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


# ---------------------------------------------------------------- connection self-test
def _map_provider_error(e):
    """Map a raw provider/transport error to a SHORT, human message for the admin — never
    leaks the API key (the key is in a header, not the message). Recognises the common
    auth / billing / model / network failures; falls back to the exception text."""
    code = None
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        try:
            body = (resp.text or "")[:300]
        except Exception:
            body = ""
    blob = (body + " " + str(e)).lower()
    if code == 401 or "invalid api key" in blob or "invalid_api_key" in blob or \
       "incorrect api key" in blob or "authentication" in blob or "unauthorized" in blob:
        return "401 invalid API key"
    if code == 403 or "permission" in blob or "forbidden" in blob:
        return "403 forbidden — the key lacks access to this model/endpoint"
    if "insufficient_quota" in blob or "insufficient credit" in blob or \
       "exceeded your current quota" in blob or "billing" in blob or "out of credit" in blob:
        return "insufficient credit / quota — top up the account"
    if code == 404 or "model_not_found" in blob or "model not found" in blob or \
       "does not exist" in blob or "unknown model" in blob:
        return f"model not found ({model_name_safe(e)})"
    if code == 429 or "rate limit" in blob or "rate_limit" in blob or "too many requests" in blob:
        return "rate limited — too many requests; try again shortly"
    if "timeout" in blob or "timed out" in blob or "connection" in blob or \
       "network" in blob or "name resolution" in blob or "getaddrinfo" in blob:
        return "network/timeout — could not reach the provider"
    if code:
        return f"HTTP {code} from the provider"
    msg = str(e).strip() or type(e).__name__
    return msg[:200]


def model_name_safe(e):
    """Best-effort model name for an error message (never a secret)."""
    try:
        return model_name(ai_review.resolve_backend())
    except Exception:
        return ""


def test_connection(backend=None):
    """Make a MINIMAL real call to the configured vision backend (a tiny TEXT prompt — no
    PDF, no image, negligible cost) and report the result for the admin 'Test AI connection'
    button. Returns {"ok": bool, "provider": str, "model": str, "message": str}.

    Never raises and NEVER logs/returns the API key. When no vision backend is configured it
    returns ok=False with a clear reason and makes ZERO network call (gate-identical to the
    other paths)."""
    be = _provider(backend)
    if be is None:
        st = status()
        return {"ok": False, "provider": "", "model": "",
                "message": st["reason"] or "no vision backend configured (no request was sent)"}
    prompt = ('Reply with ONLY this JSON and nothing else: {"ok": true}. '
              'This is a connection self-test.')
    try:
        # No images — a tiny text-only round-trip through the SAME provider call path.
        _VISION_CALL[be](prompt, "", [])
    except Exception as e:
        msg = _map_provider_error(e)
        log.warning("AI test connection failed (%s): %s", be, msg)
        return {"ok": False, "provider": provider_label(be), "model": model_name(be),
                "message": msg}
    return {"ok": True, "provider": provider_label(be), "model": model_name(be),
            "message": f"{provider_label(be)} responded (model {model_name(be)})"}


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


# ---------------------------------------------------------------- AI CORRECTION + RE-VERIFY
# The PDF-authoritative correction loop. When verify() reports that a captured field
# DISAGREES with the PDF (`match == False`), apply_corrections() writes the PDF value back
# INTO the draft's `capture` document AND the corresponding mapped draft field that
# registration consumes, so the fix flows downstream. It is best-effort and NEVER raises,
# NEVER blanks a field, NEVER mutates the input draft in place (it works on a deep copy),
# and only touches PATHS IT CAN RESOLVE (an unknown path is skipped, left flagged for the
# human). The human Confirm gate still stands — this only edits the PRE-commit draft.

# How a verdict field NAME maps to (capture-doc location, mapped-draft location). The
# verifier is prompted to use names like 'invoice.due_date', 'line[2].vat', 'totals.gross';
# we accept the common spelling variants too. A field whose path we can't resolve is skipped.

# Capture header sub-objects keyed by their leading token (with/without a 'header.' prefix).
_HEADER_OBJ = {"supplier": "supplier", "customer": "customer", "invoice": "invoice"}

# A header (capture.header.<obj>.<key>) -> the mapped DRAFT key registration reads (or None
# when the value lives only on the capture doc / is derived, e.g. the customer VAT number).
# Mirrors vision_capture.to_draft()'s header mapping so a corrected capture value also moves
# the draft field the rest of the pipeline uses.
_HEADER_TO_DRAFT = {
    ("supplier", "name"): "supplier",
    ("supplier", "vat_number"): "supplier_vat",
    ("invoice", "number"): "statement_ref",
    ("invoice", "issue_date"): "statement_date",
    ("invoice", "currency"): "currency",
    ("customer", "name"): "customer",
}

# A capture LINE key -> the mapped DRAFT-line key registration reads (to_draft keeps the
# rich keys verbatim and additionally projects a few onto the existing line keys).
_LINE_TO_DRAFT = {
    "net": "net", "vat": "vat", "country": "country", "product": "product",
    "quantity": "qty", "date": "date",
}

# capture LINE keys that are money amounts (coerced via money.f2 — never bare round()).
_LINE_AMOUNT_KEYS = {"net", "vat", "gross", "discount", "unit_price"}
# capture LINE keys that are plain numerics (rates / quantities — full precision).
_LINE_NUMERIC_KEYS = {"vat_rate", "quantity"}
# capture LINE keys that are ISO dates.
_LINE_DATE_KEYS = {"date"}

# capture TOTALS: accept short aliases ('gross','net','vat','discount') and the full key.
_TOTALS_ALIASES = {
    "gross": "gross_total", "gross_total": "gross_total",
    "net": "net_total", "net_total": "net_total",
    "vat": "vat_total", "vat_total": "vat_total",
    "discount": "discount_total", "discount_total": "discount_total",
}

_LINE_RE = re.compile(r"^lines?\[(\d+)\]\.(.+)$")


def _coerce_amount(raw):
    """A PDF amount string -> a money.f2 float, or None when empty/unparseable. NEVER 0.0 by
    accident — an unparseable value returns None so the caller SKIPS (never blanks a field)."""
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None
    # tolerate thousands separators / currency symbols the model may echo from the page.
    cleaned = re.sub(r"[^0-9,.\-]", "", s)
    if cleaned.count(",") and cleaned.count("."):
        cleaned = cleaned.replace(",", "")          # 1,234.56 -> 1234.56
    elif cleaned.count(","):
        cleaned = cleaned.replace(",", ".")         # 1234,56 -> 1234.56
    try:
        return money.f2(float(cleaned))
    except (TypeError, ValueError):
        return None


def _coerce_numeric(raw):
    """A PDF rate/qty -> float (full precision), or None when empty/unparseable."""
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None
    cleaned = re.sub(r"[^0-9,.\-]", "", s).replace(",", ".")
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _coerce_date(raw):
    """A PDF date -> ISO YYYY-MM-DD, or None when empty/unparseable (never blanks a field)."""
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _coerce_text(raw):
    """A PDF text value -> a trimmed string, or None when empty (never blanks a field)."""
    s = "" if raw is None else str(raw).strip()
    return s or None


def _resolve(name):
    """Resolve a verdict field NAME to a structured target we can apply, or None when the
    path is unknown / unresolvable (the caller then SKIPS it, leaving it flagged for a human).

    Returns one of:
      ("header", obj, key)        -> capture.header[obj][key] (+ a mapped draft key)
      ("line", index, key)        -> capture.lines[index][key] (+ a mapped draft-line key)
      ("totals", totals_key, None)-> capture.totals[totals_key]
    Pure; never raises."""
    n = (name or "").strip()
    if not n:
        return None
    low = n.lower()

    # line[i].key / lines[i].key  (1-based or 0-based handled at apply time)
    m = _LINE_RE.match(low)
    if m:
        idx = int(m.group(1))
        key = m.group(2).strip()
        # accept a couple of synonyms the model might use
        key = {"unit_price": "unit_price", "qty": "quantity",
               "vat_pct": "vat_rate", "vat%": "vat_rate"}.get(key, key)
        from vision_capture import _LINE_KEYS
        if key in _LINE_KEYS:
            return ("line", idx, key)
        return None

    parts = low.split(".")
    # drop a leading 'header' token: header.supplier.name -> supplier.name
    if parts and parts[0] == "header":
        parts = parts[1:]

    # totals.<key>
    if parts and parts[0] in ("totals", "total"):
        if len(parts) >= 2 and parts[1] in _TOTALS_ALIASES:
            return ("totals", _TOTALS_ALIASES[parts[1]], None)
        return None

    # currency is a header.invoice field exposed at the top level by the prompt
    if parts == ["currency"]:
        return ("header", "invoice", "currency")

    # <obj>.<key>  (supplier/customer/invoice)
    if len(parts) >= 2 and parts[0] in _HEADER_OBJ:
        obj = _HEADER_OBJ[parts[0]]
        key = parts[-1]
        return ("header", obj, key)

    # a bare leading token like 'supplier' / 'invoice' is too coarse to map to one field.
    return None


def _capture_line(cap_lines, idx):
    """Resolve a verdict line index against capture lines. The verifier prompt numbers lines
    1-based ('line[2]' = the 2nd transaction), so we resolve 1-based FIRST and fall back to a
    0-based reading only when that is out of range. Returns (real_index, line_dict) or
    (None, None) when neither resolves (the caller then SKIPS — leaves it flagged)."""
    if not isinstance(cap_lines, list) or not cap_lines:
        return None, None
    if 1 <= idx <= len(cap_lines):                  # 1-based (the prompt's convention)
        return idx - 1, cap_lines[idx - 1]
    if 0 <= idx < len(cap_lines):                   # 0-based fallback
        return idx, cap_lines[idx]
    return None, None


def apply_corrections(draft, verdict):
    """Apply the PDF-authoritative corrections from a verify() `verdict` onto a COPY of the
    draft, returning (corrected_draft, corrections).

    For each verdict field with `match == False` that carries a usable `document` (PDF) value
    we resolve its path in the capture document; when resolvable AND the coerced PDF value is
    non-empty we write it INTO the capture doc AND the corresponding mapped draft field
    (header key or lines[i] key) that registration consumes. We NEVER:
      * mutate the input `draft` in place (a deep copy is corrected and returned);
      * blank a field (an empty / unparseable PDF value is SKIPPED — left flagged);
      * touch a path we can't resolve (skipped — left flagged for the human).

    `corrections` is a list of {field, was, now, source:"ai-verify(PDF)", at}. Best-effort:
    NEVER raises (a single bad field is logged and skipped). Pure w.r.t. the input draft."""
    corrections = []
    try:
        corrected = copy.deepcopy(draft) if isinstance(draft, dict) else {}
    except Exception as e:
        log.warning("apply_corrections could not copy the draft — no corrections applied: %s", e)
        return draft, corrections
    cap = corrected.get("capture")
    if not isinstance(cap, dict):
        # Corrections only flow through the rich capture document (the vision-capture path).
        return corrected, corrections
    at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    fields = (verdict or {}).get("fields") or []

    for f in fields:
        if not isinstance(f, dict) or f.get("match"):
            continue
        name = f.get("name")
        target = _resolve(name)
        if target is None:
            continue                                 # unknown path -> skip, stay flagged
        try:
            applied = _apply_one(corrected, cap, target, f.get("document"))
        except Exception as e:                       # never let one bad field break the loop
            log.warning("apply_corrections skipped field %r: %s", name, e)
            applied = None
        if applied is None:
            continue                                 # unparseable/empty -> skip (never blank)
        was, now = applied
        corrections.append({"field": str(name), "was": was, "now": now,
                            "source": "ai-verify(PDF)", "at": at})
    return corrected, corrections


def _apply_one(draft, cap, target, document):
    """Apply ONE resolved correction to the capture doc + the mapped draft field. Returns
    (was, now) on success, or None when the coerced PDF value is empty/unparseable (skip —
    never blank). Coerces by field type (amounts via money.f2, dates ISO, rates numeric)."""
    kind = target[0]

    if kind == "header":
        _, obj, key = target
        header = cap.setdefault("header", {})
        sub = header.get(obj)
        if not isinstance(sub, dict):
            return None
        if key not in sub:
            return None                              # unknown header key -> skip
        if obj == "invoice" and key in ("issue_date", "due_date"):
            new = _coerce_date(document)
        elif obj == "invoice" and key == "exchange_rate":
            new = _coerce_numeric(document)
        else:
            new = _coerce_text(document)
        if new is None:
            return None
        was = sub.get(key)
        sub[key] = new
        dk = _HEADER_TO_DRAFT.get((obj, key))
        if dk is not None:
            draft[dk] = new
        return was, new

    if kind == "line":
        _, idx, key = target
        real, cline = _capture_line(cap.get("lines"), idx)
        if cline is None:
            return None
        if key in _LINE_AMOUNT_KEYS:
            new = _coerce_amount(document)
        elif key in _LINE_NUMERIC_KEYS:
            new = _coerce_numeric(document)
        elif key in _LINE_DATE_KEYS:
            new = _coerce_date(document)
        else:
            new = _coerce_text(document)
        if new is None:
            return None
        was = cline.get(key)
        cline[key] = new
        # mirror onto the mapped draft line registration reads (same positional index)
        dlines = draft.get("lines")
        if isinstance(dlines, list) and 0 <= real < len(dlines) and isinstance(dlines[real], dict):
            dline = dlines[real]
            dline[key] = new                         # the rich key is kept verbatim by to_draft
            mapped = _LINE_TO_DRAFT.get(key)
            if mapped is not None:
                dline[mapped] = new
        return was, new

    if kind == "totals":
        _, tkey, _ = target
        tot = cap.setdefault("totals", {})
        new = _coerce_amount(document)
        if new is None:
            return None
        was = tot.get(tkey)
        tot[tkey] = new
        return was, new

    return None


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

    data_str = json.dumps(_verify_payload(draft), ensure_ascii=False, default=str)
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
