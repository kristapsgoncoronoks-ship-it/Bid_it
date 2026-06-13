"""
AI REVIEW ASSISTANT (v1) — an ADVISORY second opinion over data that has ALREADY been
extracted and validated deterministically. It is OFF by default, never mutates a figure
or a status, and never gates a commit.

What it is NOT: it is not extraction (`extract.py` owns that) and not the deterministic
validator (`validate.py` owns that). Arithmetic — VAT rates, totals, thresholds,
expenditure codes — is checked deterministically and the result is HANDED to the model
under `deterministic_findings`; the model is told NOT to recompute it. The model only
flags FUZZY concerns a regex/threshold can't: a supplier alias/name mismatch, an
implausible VAT id, a price that looks off versus history, "this looks wrong".

PRIVACY (hard invariants — see the work order / docs):
  * The payload NEVER contains the PDF. We send MINIMIZED DERIVED DATA only: a handful of
    draft header fields, per-line {invoice_no,date,country,currency,net,vat}, the
    supplier's expected VAT/name/aliases, the customer's contract terms, a price range
    learned from history, expenditure codes, reconciling-transaction summaries, and the
    deterministic findings. `pdf_text` is attached ONLY when include_text=True.
  * `build_payload` REDACTS every key in REDACT_FIELDS (recursively) and EVERY key whose
    name starts with '_' (so `_pdf_bytes` and any private field can never leak), unless a
    key is explicitly named in the `needs` allowlist.
  * Default backend is 'none' -> `review()` makes ZERO network calls and returns empty
    flags plus the deterministic block. `parser`/local paths keep every byte on the host.

Returned by `review()`:
    {"flags":[{"field","severity","message","suggestion"}], "note": str|None,
     "deterministic": <validate.validate_batch result>, "backend": str, ...}
The flags are ADVISORY: the human reading the review screen decides. Nothing here writes
a DB or changes the /extract/confirm deterministic gate.
"""
import os, re, json

import applog
import money
# Reuse extract's transient-error taxonomy + the env-keyed backend shapes, but NOT its
# extraction PROMPT — this module has its own reviewer prompt.
import extract
from extract import TransientExtractionError, is_transient_error

log = applog.get("ai_review")


REVIEW_PROMPT = (
    "You are an advisory reviewer of ALREADY-EXTRACTED invoice data. Do NOT recompute "
    "VAT arithmetic, totals, thresholds, or expenditure codes — those are checked "
    "deterministically and given under `deterministic_findings`. Flag only fuzzy "
    "concerns: supplier alias/name mismatch, VAT-id plausibility, price-vs-history "
    "anomaly, 'looks wrong'. Return ONLY JSON "
    "{\"flags\":[{\"field\":str,\"severity\":\"info|warn|error\",\"message\":str,"
    "\"suggestion\":str|null}],\"note\":str|null}."
)


# Keys that must NEVER reach the model — bank/secret material. Matched case-insensitively
# and recursively at every nesting level. Any key starting '_' is dropped unconditionally
# in addition to these (private fields like `_pdf_bytes`).
REDACT_FIELDS = {"iban", "swift", "bic", "bank", "account", "account_number",
                 "beneficiary", "password", "secret", "api_key",
                 "contact_name", "contact_email", "phone"}


# ---------------------------------------------------------------- redaction
def _redact(obj, needs):
    """Recursively drop any key in REDACT_FIELDS and any key starting '_', UNLESS the key
    is named in the `needs` allowlist. Pure — returns a new structure, never mutates."""
    needs = {str(n).lower() for n in (needs or ())}
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in needs:
                out[k] = _redact(v, needs)
                continue
            if str(k).startswith("_") or kl in REDACT_FIELDS:
                continue
            out[k] = _redact(v, needs)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact(v, needs) for v in obj]
    return obj


# ---------------------------------------------------------------- payload
def _num(x):
    """Quantize a single amount to cents (full precision summaries use money.f2)."""
    try:
        return money.f2(x)
    except Exception:
        return None


def _price_range(context):
    """min/median/max NET-EUR/L price range, learned from history. Accepts a precomputed
    sample under context['price_samples'] (a list of EUR/L) or a ready-made dict under
    context['price_range']; otherwise None. Uses anomaly._robust_stats for the median so
    the basis matches the analytics layer."""
    pr = (context or {}).get("price_range")
    if isinstance(pr, dict) and pr:
        return {k: _num(v) for k, v in pr.items() if v is not None}
    sample = [s for s in ((context or {}).get("price_samples") or []) if s is not None]
    if not sample:
        return None
    import anomaly
    stats = anomaly._robust_stats([float(s) for s in sample])
    med = stats[0] if stats else None
    return {"min": _num(min(sample)), "median": (_num(med) if med is not None else None),
            "max": _num(max(sample)), "n": len(sample)}


def build_payload(draft, context, *, include_text=False, needs=()):
    """Assemble the MINIMIZED DERIVED payload for the reviewer. NEVER includes the PDF
    bytes or any key starting '_'; REDACTS bank/secret fields. Pure — does not mutate
    `draft` or `context`."""
    draft = draft or {}
    context = context or {}

    # 1) draft header (NET basis figures only) + minimized per-line view
    payload = {
        "supplier": draft.get("supplier"),
        "supplier_vat": draft.get("supplier_vat"),
        "statement_ref": draft.get("statement_ref"),
        "statement_date": draft.get("statement_date"),
        "currency": draft.get("currency"),
        "customer": draft.get("customer"),
        "lines": [{"invoice_no": ln.get("invoice_no"), "date": ln.get("date"),
                   "country": ln.get("country"), "currency": ln.get("currency"),
                   "net": _num(ln.get("net")), "vat": _num(ln.get("vat"))}
                  for ln in draft.get("lines", [])],
    }

    # 2) supplier expectations (name / aliases / expected VAT) — NO bank details — plus
    # the contracted PURCHASE-price terms (NET EUR/L) for price-vs-contract reasoning,
    # sourced from supplier_discounts (same as contract_audit): the contracted rebate and
    # the price ceiling. These are the supplier purchase contract, NOT the agency fee.
    sup = context.get("supplier") or {}
    if sup:
        sc = {
            "expected_name": sup.get("expected_name") or sup.get("name"),
            "expected_vat": sup.get("expected_vat") or sup.get("vat"),
            "aliases": list(sup.get("aliases") or []),
        }
        # EUR/L price terms keep full precision (not cents) — contract_audit compares to
        # 4 dp; cent-quantization would corrupt a sub-cent rebate.
        for k in ("expected_discount_eur_l", "price_ceiling_eur_l"):
            v = sup.get(k)
            if v is not None:
                try:
                    sc[k] = float(v)
                except (TypeError, ValueError) as e:
                    log.debug("supplier context: non-numeric %s dropped: %s", k, e)
        # carry an EXPLICITLY allow-listed field (e.g. needs=("iban",)) through verbatim;
        # the recursive redactor below keeps it only because it is in `needs`.
        for n in (needs or ()):
            if n in sup:
                sc[n] = sup[n]
        payload["supplier_context"] = sc

    # 3) customer identity only — the agency service-fee terms (fee_pct/fee_min) are NOT
    # part of invoice validation and are never sent. NO bank/contact fields.
    cust = context.get("customer") or {}
    if cust:
        payload["customer_context"] = {
            "name": cust.get("name") or cust.get("company_name"),
        }

    # 4) price range (NET EUR/L) learned from history
    pr = _price_range(context)
    if pr:
        payload["price_range"] = pr

    # 5) expenditure codes (2008/9/EC Art. 9 goods codes)
    if context.get("expenditure_codes") is not None:
        payload["expenditure_codes"] = context.get("expenditure_codes")

    # 6) reconciling-transaction summaries
    if context.get("reconciliation") is not None:
        payload["reconciliation"] = context.get("reconciliation")

    # 7) the deterministic findings the model must NOT recompute. Prefer a precomputed
    # result; else compute it here from the draft lines (advisory only — never gates).
    det = context.get("deterministic_findings")
    if det is None:
        det = _deterministic(draft, {})
    payload["deterministic_findings"] = det

    # 8) optional invoice TEXT (default OFF — never the PDF bytes)
    if include_text and draft.get("pdf_text"):
        payload["pdf_text"] = draft.get("pdf_text")

    # Final guard: recursively strip secrets and any '_'-prefixed key (e.g. `_pdf_bytes`),
    # honouring the `needs` allowlist.
    return _redact(payload, needs)


# ---------------------------------------------------------------- backends
def _unfence(raw):
    """Strip a ```json … ``` fence (same as extract) and parse the JSON object."""
    raw = re.sub(r"^```(?:json)?|```$", "", str(raw).strip(), flags=re.M).strip()
    return json.loads(raw)


def _call(backend, prompt, content_str):
    """A thin per-backend HTTP call that mirrors extract.py's claude/openai/azure shapes
    and env vars, but sends OUR review prompt + the minimized derived payload (NEVER a
    PDF). Returns the parsed JSON dict. Raises on transport/HTTP errors (the caller maps
    transient ones to TransientExtractionError)."""
    import requests
    msg = prompt + "\n\nDATA:\n" + content_str
    if backend == "claude":
        key = os.environ["ANTHROPIC_API_KEY"]
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
                  "max_tokens": 1500, "messages": [{"role": "user", "content": msg}]},
            timeout=120)
        r.raise_for_status()
        raw = "".join(b.get("text", "") for b in r.json()["content"])
    elif backend == "openai":
        key = os.environ["OPENAI_API_KEY"]
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
            json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": msg}]}, timeout=120)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
    elif backend == "azure":
        ep = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        dep = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
        r = requests.post(
            f"{ep}/openai/deployments/{dep}/chat/completions?api-version={ver}",
            headers={"api-key": os.environ["AZURE_OPENAI_KEY"],
                     "content-type": "application/json"},
            json={"response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": msg}]}, timeout=120)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
    else:
        raise ValueError(f"unknown ai_review backend: {backend!r}")
    return _unfence(raw)


def _model_name(backend):
    if backend == "claude":
        return os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
    if backend == "openai":
        return os.environ.get("OPENAI_MODEL", "gpt-4o")
    if backend == "azure":
        return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
    return ""


# ---------------------------------------------------------------- parsing
_SEVERITIES = {"info", "warn", "error"}

def parse_flags(raw):
    """Validate a raw model response into (flags, note). Never raises: a malformed flag
    is dropped, an unknown severity is coerced to 'info'. Requires `field` + `message`."""
    flags = []
    note = None
    if isinstance(raw, dict):
        note = raw.get("note")
        items = raw.get("flags")
    elif isinstance(raw, list):
        items = raw
    else:
        items = None
    if not isinstance(note, str):
        note = None
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        field = it.get("field")
        message = it.get("message")
        if not (isinstance(field, str) and field.strip()
                and isinstance(message, str) and message.strip()):
            continue
        sev = it.get("severity")
        if sev not in _SEVERITIES:
            sev = "info"
        sugg = it.get("suggestion")
        if sugg is not None and not isinstance(sugg, str):
            sugg = None
        flags.append({"field": field, "severity": sev, "message": message,
                      "suggestion": sugg})
    return flags, note


# ---------------------------------------------------------------- orchestration
def resolve_backend(backend=None):
    """The backend to use: an explicit arg wins, else the admin setting (default 'none')."""
    if backend:
        return backend
    import auth
    return auth.get_setting("ai_review_backend", "none") or "none"


def _deterministic(draft, context):
    """The deterministic findings handed to the model. Prefer a precomputed result under
    context['deterministic_findings']; else run validate.validate_batch over the draft's
    lines (advisory — never gates here)."""
    pre = (context or {}).get("deterministic_findings")
    if pre is not None:
        return pre
    import validate
    lines = [{"invoice_no": ln.get("invoice_no"), "date": ln.get("date"),
              "country": ln.get("country"), "currency": ln.get("currency"),
              "net": ln.get("net"), "vat": ln.get("vat")}
             for ln in (draft or {}).get("lines", [])]
    return validate.validate_batch(lines)


def review(draft, context=None, backend=None, **opts):
    """Advisory review of an already-extracted draft. PURE w.r.t. `draft` (never mutates
    it) and writes NO DB. With backend 'none' (the default) makes ZERO network calls.
    Returns a dict with advisory flags, an analytics note, and the deterministic block."""
    context = dict(context or {})
    backend = resolve_backend(backend)
    deterministic = _deterministic(draft, context)
    context.setdefault("deterministic_findings", deterministic)

    if backend == "none":
        return {"flags": [], "note": None, "deterministic": deterministic,
                "backend": "none"}

    payload = build_payload(draft, context,
                            include_text=bool(opts.get("include_text", False)),
                            needs=opts.get("needs", ()))
    content_str = json.dumps(payload, ensure_ascii=False, default=str)
    try:
        raw = _call(backend, REVIEW_PROMPT, content_str)
    except TransientExtractionError:
        raise
    except Exception as e:
        if is_transient_error(e):
            raise TransientExtractionError(f"{backend}: {e}")
        log.warning("AI review failed (%s: %s) — advisory only, ignoring", backend, e)
        return {"flags": [], "note": None, "deterministic": deterministic,
                "backend": backend, "error": str(e)}

    flags, note = parse_flags(raw)
    # Archive the sent field-KEYS (never the values/secrets) + the response, sibling to
    # data_lake.put_extraction. Best-effort: archival failure never breaks the review.
    try:
        import data_lake
        archive = {"sent_keys": sorted(payload.keys()), "flags": flags, "note": note}
        data_lake.put(
            json.dumps(archive, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            f"{(draft or {}).get('statement_ref') or 'review'}.json",
            kind="ai_review", supplier=(draft or {}).get("supplier"),
            period=((draft or {}).get("statement_date") or "")[:7] or None,
            meta={"backend": backend, "model": _model_name(backend),
                  "flags": len(flags), "sent_keys": sorted(payload.keys())})
    except Exception as e:
        log.warning("ai_review archive failed (%s) — review still returned", e)

    return {"flags": flags, "note": note, "deterministic": deterministic,
            "backend": backend, "model": _model_name(backend),
            "sent_keys": sorted(payload.keys())}
