"""
INVOICE EXTRACTION - turn PDF / ZIP batches into a DRAFT of statement lines that a
human reviews and confirms before anything is committed.

Pluggable providers (choose via env EXTRACT_BACKEND, default 'auto'):
    auto       deterministic parser if the supplier is recognised, else the
               configured AI backend, else 'none' (manual entry)
    parser     deterministic pdftotext parsing only (offline, free, no data leaves host)
    claude     Anthropic Claude API           (ANTHROPIC_API_KEY)
    openai     OpenAI API                      (OPENAI_API_KEY)
    azure      Azure OpenAI, inside your tenant(AZURE_OPENAI_ENDPOINT, _KEY, _DEPLOYMENT)
    none       no extraction - returns empty draft for fully manual entry

Structured e-invoices bypass the backend entirely: a plain UBL/CII/XML upload, and a
hybrid PDF that embeds the EN 16931 invoice as an attachment (Factur-X / ZUGFeRD /
Order-X / XRechnung), are both parsed DETERMINISTICALLY at high confidence with NO AI,
regardless of the configured backend. For a hybrid PDF the embedded XML drives the
draft but the ORIGINAL human-readable PDF is what gets vaulted on confirm.

PRIVACY: 'parser' and 'none' keep every byte on this server. 'claude'/'openai'/
'azure' send the PDF text/content to that processor over TLS for the extraction
call only - permitted here because a DPA is in place (see SECURITY.md). The model
output is a DRAFT: it is shown next to the PDF and a person confirms/edits before
the existing reconcile + commit runs. Extraction is never the authority.

Returned draft shape (per batch):
    {"supplier": str|None, "supplier_vat": str|None, "statement_ref": str|None,
     "statement_date": "YYYY-MM-DD"|None, "currency": "EUR", "customer": str|None,
     "lines": [{"invoice_no","date","country","currency","net","vat","fx_rate","_source"}],
     "notes": str, "backend": str, "confidence": "high|medium|low"}
"""
import os, io, re, json, zipfile, subprocess, tempfile

import applog
import money

log = applog.get("extract")

EXTRACT_BACKEND = os.environ.get("EXTRACT_BACKEND", "auto")

# ZIP decompression caps (zip-bomb guard): a crafted archive of a few KB can
# decompress into gigabytes. Limits are generous for real statement batches.
ZIP_MAX_MEMBERS = 500
ZIP_MAX_MEMBER_BYTES = 50 * 1024 * 1024     # 50 MB per extracted file
ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024     # 200 MB per archive


class ZipLimitError(ValueError):
    """The ZIP exceeds the decompression caps — rejected, never extracted."""


class TransientExtractionError(Exception):
    """The AI backend failed for a reason that is expected to clear on its own —
    out of tokens/quota, rate-limited, overloaded, or a temporary 5xx/timeout.
    The intake queue catches this and retries the job later instead of giving up."""


# substrings that mark an upstream error as "retry later, not our fault"
_TRANSIENT_SIGNS = (
    "insufficient_quota", "exceeded your current quota", "quota", "credit",
    "billing", "out of tokens", "tokens exhausted", "token limit", "ran out",
    "rate limit", "rate_limit", "ratelimit", "too many requests", "429",
    "overloaded", "overloaded_error", "capacity", "temporarily", "try again",
    "service unavailable", "unavailable", "timeout", "timed out", "503", "502", "504",
)

def is_transient_error(msg):
    """True if an extraction error looks like a recoverable upstream condition
    (quota/rate-limit/overload/timeout) rather than a permanent problem."""
    m = str(msg).lower()
    return any(s in m for s in _TRANSIENT_SIGNS)

PROMPT = (
 "You extract structured data from fuel-invoice PDFs. Return ONLY JSON, no prose. "
 "Schema: {\"supplier\":string|null,\"supplier_vat\":string|null,"
 "\"statement_ref\":string|null,"
 "\"statement_date\":\"YYYY-MM-DD\"|null,\"currency\":string,\"customer\":string|null,"
 "\"lines\":[{\"invoice_no\":string,\"date\":\"YYYY-MM-DD\",\"country\":string,"
 "\"currency\":string,\"net\":number,\"vat\":number,\"fx_rate\":number|null}]}. "
 "One line per issued invoice. 'net' = taxable base excl. VAT, 'vat' = VAT amount. "
 "'supplier_vat' = the issuer's VAT/tax id if shown. 'fx_rate' = the exchange rate "
 "printed on the invoice to convert the line currency into EUR, as units of the line "
 "currency per 1 EUR (e.g. 4.27 for PLN); use null when the line is already EUR or no "
 "rate is shown — never invent a rate. Country = full English name. If a value is "
 "unreadable use null; never invent."
)


# ---------------------------------------------------------------- ZIP / PDF intake
def _read_capped(f, name, budget):
    """Read an open ZIP member in chunks, raising once `budget` bytes are crossed.
    The header's declared size is checked separately — but headers can lie, so the
    actual decompressed stream is metered too (never z.read() blind)."""
    chunks, size = [], 0
    while True:
        chunk = f.read(1 << 20)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > budget:
            log.warning("ZIP rejected: member '%s' decompresses past %d bytes "
                        "(declared size was smaller)", name, budget)
            raise ZipLimitError(f"ZIP rejected: '{name}' decompresses past the "
                                "safety caps (possible zip-bomb)")
        chunks.append(chunk)


def _zip_extract(upload_bytes, suffix):
    """-> list of (basename, bytes) for ZIP members ending in `suffix`, enforcing
    the decompression caps (member count, per-member bytes, cumulative total)."""
    out, total = [], 0
    with zipfile.ZipFile(io.BytesIO(upload_bytes)) as z:
        infos = z.infolist()
        if len(infos) > ZIP_MAX_MEMBERS:
            log.warning("ZIP rejected: %d members (cap %d)", len(infos), ZIP_MAX_MEMBERS)
            raise ZipLimitError(f"ZIP rejected: {len(infos)} members exceed the "
                                f"{ZIP_MAX_MEMBERS}-file cap")
        for info in infos:
            n = info.filename
            if not n.lower().endswith(suffix) or n.startswith("__MACOSX"):
                continue
            budget = min(ZIP_MAX_MEMBER_BYTES, ZIP_MAX_TOTAL_BYTES - total)
            if info.file_size > budget:
                log.warning("ZIP rejected: member '%s' declares %d bytes (caps: "
                            "%d per file, %d total)", n, info.file_size,
                            ZIP_MAX_MEMBER_BYTES, ZIP_MAX_TOTAL_BYTES)
                raise ZipLimitError(f"ZIP rejected: '{n}' is too large to extract "
                                    "safely (possible zip-bomb)")
            with z.open(n) as f:
                data = _read_capped(f, n, budget)
            total += len(data)
            out.append((os.path.basename(n), data))
    return out


def unpack(upload_bytes, filename):
    """-> list of (name, pdf_bytes). Accepts a single PDF or a ZIP of PDFs."""
    if filename.lower().endswith(".zip"):
        return _zip_extract(upload_bytes, ".pdf")
    if filename.lower().endswith(".pdf"):
        return [(os.path.basename(filename), upload_bytes)]
    return []


def _have_pdftotext():
    import shutil
    return shutil.which("pdftotext") is not None

def pdf_text(pdf_bytes):
    """Extract text. Prefers poppler's pdftotext (best layout); falls back to
    pure-Python pypdf so it works on a stock Windows box with no poppler."""
    if _have_pdftotext():
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes); path = f.name
        try:
            return subprocess.run(["pdftotext", "-layout", path, "-"],
                                  capture_output=True, text=True, timeout=30).stdout
        finally:
            try: os.unlink(path)
            except OSError as e:
                log.debug("extract: temp cleanup failed for %s: %s", path, e)
    # fallback: pypdf (pip install pypdf) - no system dependency
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("PDF text extraction needs either poppler (pdftotext) "
                           "or the 'pypdf' package: pip install pypdf")
    import io
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


# ---------------------------------------------------------------- OCR (scanned PDFs)
# A scanned / image-only PDF carries NO embedded text, so pdf_text() returns ~nothing and
# the parser/AI path either produces an empty draft or hallucinates off garbage. OCR is the
# only way to recover such a document. It is a PLUGGABLE, default-'auto' seam (like the AI
# backends): 'auto' uses a local Tesseract install if present, else degrades to no-OCR
# (behaviour byte-identical to before); 'tesseract' forces it; 'none' disables it. OCR runs
# fully ON-PREM — no bytes leave the host — and feeds the SAME parser/AI/validation path, so
# OCR never produces an authoritative figure; the draft is flagged for careful review.
OCR_BACKEND = os.environ.get("EXTRACT_OCR_BACKEND", "auto")   # auto | tesseract | none
# Languages Tesseract reads, as a '+'-joined list (default covers the fleet's footprint:
# English + the Baltic/EU supplier-invoice scripts). Adjustable without a code change via
# EXTRACT_OCR_LANGS; a requested language with no installed pack is dropped gracefully.
OCR_LANGS = os.environ.get("EXTRACT_OCR_LANGS", "eng+deu+pol+swe+lit+lav+est")
_MIN_TEXT_CHARS = 24            # below this, treat the PDF as image-only / scanned

def _looks_scanned(text):
    return len((text or "").strip()) < _MIN_TEXT_CHARS

def _tesseract_ready():
    """True if the local Tesseract OCR stack (pytesseract + pdf2image + the tesseract
    binary) is importable/usable. Never raises."""
    try:
        import pytesseract, pdf2image      # noqa: F401
        import shutil
        return shutil.which("tesseract") is not None
    except Exception:
        return False

def _pick_langs(requested, have):
    """Intersect the requested '+'-joined languages with the packs actually installed,
    preserving request order; fall back to 'eng' if present, else None (let Tesseract
    use its built-in default). Pure/​testable — no Tesseract needed."""
    use = [l for l in (requested or "").split("+") if l and l in have]
    if not use:
        use = ["eng"] if "eng" in have else []
    return "+".join(use) or None

def _ocr_langs_available(requested):
    """Resolve EXTRACT_OCR_LANGS against the locally installed Tesseract language packs,
    so a missing pack degrades gracefully instead of erroring. Returns a tesseract lang
    string (e.g. 'eng+deu') or None. Never raises."""
    try:
        import pytesseract
        have = set(pytesseract.get_languages(config=""))
    except Exception:
        return None
    return _pick_langs(requested, have)

def _ocr_tesseract(pdf_bytes):
    import pytesseract
    from pdf2image import convert_from_bytes
    pages = convert_from_bytes(pdf_bytes)
    lang = _ocr_langs_available(OCR_LANGS)          # multilingual, install-aware
    if lang:
        return "\n".join(pytesseract.image_to_string(p, lang=lang) for p in pages)
    return "\n".join(pytesseract.image_to_string(p) for p in pages)

_OCR = {"tesseract": _ocr_tesseract}

def ocr_text(pdf_bytes, backend=None):
    """OCR a scanned/image PDF to text on-prem. Returns '' (NEVER raises) when no OCR
    backend is available or configured, so the caller degrades exactly as before."""
    backend = backend or OCR_BACKEND
    if backend == "none":
        return ""
    fn = _OCR.get(backend)
    if backend == "auto":
        fn = _OCR["tesseract"] if _tesseract_ready() else None
    if fn is None:
        return ""
    try:
        return fn(pdf_bytes) or ""
    except Exception as e:
        log.warning("OCR (%s) failed — treating as no text: %s", backend, e)
        return ""

def pdf_text_or_ocr(pdf_bytes, backend=None):
    """Text for a PDF, falling back to OCR when the PDF appears scanned/image-only.
    Returns (text, used_ocr). Never raises."""
    txt = ""
    try:
        txt = pdf_text(pdf_bytes) or ""
    except Exception as e:
        log.warning("pdf_text failed (%s) — attempting OCR", e)
    if _looks_scanned(txt):
        otext = ocr_text(pdf_bytes, backend)
        if otext.strip():
            return otext, True
    return txt, False


# ---------------------------------------------------------------- deterministic parser
def _num(s):
    """Currency amount -> float, money.f2-quantized (HALF_UP). Handles European
    PDF-text amounts ('7 059,83' / '7\u00a0059,83' / '1.776,96' / '1 776,96') and
    plain dot-decimal e-invoice XML amounts ('1234.56'). Returns 0.0 on unparseable
    or None input (callers rely on a numeric fallback, never None/raise)."""
    s = str(s).replace("\u00a0", " ").strip()
    s = re.sub(r"(?<=\d)[ .](?=\d{3}\b)", "", s)   # strip thousands sep (space or dot)
    s = s.replace(",", ".")
    try: return money.f2(float(s))
    except (TypeError, ValueError): return 0.0

def parse_eurowag(texts):
    """Deterministic parser for Eurowag/W.A.G. coversheet + country invoices.
    texts: list of (name, extracted_text). Returns a draft dict or None if not Eurowag."""
    joined = " ".join(t for _, t in texts)
    if "W.A.G." not in joined and "Eurowag" not in joined:
        return None
    cover = next((t for n, t in texts if "COVER" in n.upper() or "Kopsavilkums" in t or "SUMMARY OVERVIEW" in t), None)
    ref = None
    m = re.search(r"(?:Total payment|Kopējais maksājums)\D*(\d{6,})", cover or joined) \
        or re.search(r"(?:Payment Reference|Mainīgais simbols)\D*(\d{6,})", cover or joined)
    if m: ref = m.group(1)
    customer = None
    mc = re.search(r"(?:Buyer|Pircējs)[^\n]*\n\s*([A-ZĀ-Ž][\w .'-]+?(?:SIA|UAB|AS|s\.r\.o\.|d\.o\.o\.))", joined)
    if not mc:
        mc = re.search(r"\b((?:SIA|UAB)\s+[\w .'-]+|[\w .'-]+\s+(?:SIA|UAB|AS))\b", joined)
    if mc: customer = re.sub(r"\s+", " ", mc.group(1)).strip()
    lines = []
    # each country invoice: read its VAT specification block (PVN specifikācija)
    for name, t in texts:
        if "COVER" in name.upper():
            continue
        inv = re.search(r"(?:Document Number|Dokumenta numurs|Belegnummer|Bewijsnummer|Zahlungsreferenz)\D*([A-Z]{2}\d{10,})", t)
        ctry = re.search(r"(?:Izpildes valsts|Land der|Land van|Pays|Paese|Kraj|Country|Šalis|Država|Uppfyllelseland)[^\n:]*[:/][^\n]*?([A-ZĀ-Ža-zā-ž]+)\s*/\s*([A-ZÀ-ÿ][\w]+)", t)
        # "Kopsumma / Total  <net>  <vat>  <gross>  EUR" - the VAT-spec summary line
        AMT = r"(\d[\d \u00a0.]*,\d{2})"
        msum = re.search(r"(?:Kopsumma|Kopsumma /|Summe|Total|Totaal|Totale|Skupaj|Viso|Razem|Att betala totalt|Insgesamt)\D*?"
                         + AMT + r"\s+" + AMT + r"\s+" + AMT + r"\s+EUR", t)
        if msum:
            net, vat = _num(msum.group(1)), _num(msum.group(2))
        else:  # fallback: sum per-rate spec rows "<rate>  <net>  <vat>  <gross> EUR"
            nets, vats = [], []
            for mm in re.finditer(r"\n\s*\d{1,2}\s+" + AMT + r"\s+" + AMT + r"\s+" + AMT + r"\s+EUR", t):
                nets.append(_num(mm.group(1))); vats.append(_num(mm.group(2)))
            net, vat = money.fsum(nets), money.fsum(vats)
        # country: second token after "Izpildes valsts ... / <native> / <local>"
        country = None
        mcn = re.search(r"Izpildes valsts[^\n/]*/?[^\n]*?([A-ZÀ-Ž][a-zà-ž]+)\s*$", t, re.M)
        if mcn: country = mcn.group(1)
        lines.append({"invoice_no": inv.group(1) if inv else None,
                      "date": None, "country": country,
                      "currency": "EUR", "net": net, "vat": vat, "_source": name})
    NAT = {"België":"Belgium","Belgique":"Belgium","Deutschland":"Germany","Österreich":"Austria",
           "France":"France","Italia":"Italy","Polska":"Poland","Sverige":"Sweden",
           "Slovenija":"Slovenia","Lietuva":"Lithuania","Latvija":"Latvia","Latvia":"Latvia"}
    for ln in lines:
        if ln["country"] in NAT: ln["country"] = NAT[ln["country"]]
    return {"supplier": "EUROWAG", "statement_ref": ref, "statement_date": None,
            "currency": "EUR", "customer": customer, "lines": lines,
            "notes": "deterministic parser (Eurowag) - verify country & dates",
            "backend": "parser", "confidence": "medium"}


# E100 -------------------------------------------------------------------------------------
# E100 issues ONE country-specific invoice: its number and every station code carry the SAME
# 2-letter country prefix (BE…/PL…/DE…). Page 1 is a per-PRODUCT summary with explicit NET
# ("Montant hors TVA") and VAT columns; the following pages are a per-transaction annexe. We
# read the clean page-1 summary for the authoritative net/VAT, cross-check it against the
# stated document total (tie-out), and derive the single refund country from the station
# prefixes (cross-checked against the invoice-number prefix). One registry line per (invoice,
# country) — the same granularity as the Eurowag parser. If the station prefixes are NOT
# uniform (a multi-country statement, which E100 does not issue today) we DON'T guess the
# country: the line is emitted with country=None and a loud note so the operator assigns it.
_E100_MARKER = "E100 International Trade"

_E100_COUNTRY = {
    "BE": "Belgium", "PL": "Poland", "DE": "Germany", "FR": "France", "NL": "Netherlands",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia", "AT": "Austria", "IT": "Italy",
    "ES": "Spain", "CZ": "Czech Republic", "SK": "Slovakia", "HU": "Hungary",
    "RO": "Romania", "SI": "Slovenia", "HR": "Croatia", "LU": "Luxembourg",
    "DK": "Denmark", "SE": "Sweden", "FI": "Finland", "PT": "Portugal", "NO": "Norway",
}

# A page-1 SUMMARY row: "<LP> <product> <code> l <qty> <prix_brut> <remise> <prix_net>
# <NET> <tva%> <TVA> <gross>" — 8 decimals after the unit. NET = group 7, TVA = group 9.
_E100_SUMROW = re.compile(
    r"^\s*\d+\s+(.+?)\s+(\d+)\s+[lL]\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", re.M)


def parse_e100(texts):
    """Deterministic parser for E100 International Trade fuel invoices (page-1 product
    summary + per-transaction annexe). Returns a draft dict, or None when this isn't an
    E100 invoice / its summary layout isn't recognised (let the AI/generic path try)."""
    joined = "\n".join(t for _, t in texts)
    if _E100_MARKER not in joined:
        return None

    m = re.search(r"\bNr\.?\s+([A-Z]{2}\d{2,}[/\-]\d{2,})", joined)
    inv_no = m.group(1) if m else None
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*-\s*Date\s*/\s*Datums", joined)
    date = m.group(1) if m else None
    m = re.search(r"Client\s*/\s*Pirc[ēe]j\w*\s*\n\s*([^\n]+)", joined)
    customer = re.sub(r"\s+", " ", m.group(1)).strip() if m else None

    # Per-product summary -> authoritative net/VAT + a human-readable breakdown.
    prods = []
    for r in _E100_SUMROW.finditer(joined):
        prods.append((re.sub(r"\s+", " ", r.group(1)).strip(), r.group(2),
                      _num(r.group(3)), _num(r.group(7)), _num(r.group(9))))
    if not prods:
        return None

    # Independent document total for the tie-out gate: the largest amount on the
    # "Total / Kopā …" line (the gross). Tolerant of space thousands separators.
    stated = None
    mt = re.search(r"Total\s*/\s*Kop[aā][^\n]*", joined)
    if mt:
        amts = [_num(x) for x in re.findall(r"\d[\d ]*\.\d{2}", mt.group(0))]
        stated = max(amts) if amts else None

    # Single refund country from the station-code prefixes (BE167, PL1042 …), cross-checked
    # against the invoice-number prefix. Mixed prefixes -> don't guess (operator assigns).
    prefixes = set(re.findall(r"\d{2}:\d{2}\s+([A-Z]{2})\d", joined))
    inv_prefix = inv_no[:2] if (inv_no and inv_no[:2].isalpha()) else None
    multi = len(prefixes) > 1
    cc = None if multi else (next(iter(prefixes)) if len(prefixes) == 1 else inv_prefix)
    country = _E100_COUNTRY.get(cc) if cc else None

    _, svat = _seller_identity(joined)              # seller VAT for registration recognition
    breakdown = "; ".join(
        f"{n} (code {c}): net {money.f2(nt):,.2f} / VAT {money.f2(vt):,.2f}"
        for n, c, _q, nt, vt in prods)
    note = f"deterministic parser (E100) — products: {breakdown}"
    conf = "medium"
    if multi:
        note += (" | MULTIPLE supply countries detected in the annexe — set the country per "
                 "line before confirming (net/VAT above is the whole-invoice total)")
        conf = "low"
    elif not country:
        note += " | could not derive the supply country — set it before confirming"

    products = [{"name": n, "code": c, "qty": q, "net": nt, "vat": vt}
                for n, c, q, nt, vt in prods]
    # ONE claim line PER PRODUCT (line-by-line fuel detail): each carries its own net/VAT and
    # a product-qualified invoice ref ("<inv> #<code>") so the invoice registry — keyed on
    # invoice_no — keeps them as distinct, individually-checkable lines instead of collapsing
    # them. The real invoice number stays the identifiable prefix; the line sum still ties out
    # to the stated document total. A single-product invoice yields a single line.
    lines = [{"invoice_no": (f"{inv_no} #{c}" if inv_no else None),
              "date": date, "country": country, "currency": "EUR",
              "net": nt, "vat": vt, "product": f"{n} ({c})", "_source": "E100 summary"}
             for n, c, q, nt, vt in prods]
    draft = {"supplier": "E100", "supplier_vat": svat, "statement_ref": inv_no,
             "statement_date": date, "currency": _detect_currency(joined),
             "customer": customer, "products": products, "lines": lines,
             "notes": note, "backend": "parser", "confidence": conf}
    if stated is not None:
        draft["coversheet_total"] = stated
    return draft


# PARSER REGISTRY - add one function per recurring PDF-only supplier here.
# Each takes [(name, text)] and returns a draft dict (or None if not its supplier).
# Recognised suppliers extract for free, offline; everything else falls to AI/manual.
# Template:
#   def parse_<supplier>(texts):
#       if "<marker>" not in " ".join(t for _, t in texts): return None
#       ... regex the totals ...
#       return {"supplier": "<CODE>", "statement_ref": ..., "lines": [...],
#               "backend": "parser", "confidence": "medium", ...}
PARSERS = [parse_eurowag, parse_e100]


# ---------------------------------------------------------------- AI backends
# Per-document character budget sent to the AI backend. A statement longer than this is
# CHUNKED into labelled parts (not silently truncated) so no invoice line is ever dropped
# from the prompt — the old fixed `[:6000]` cut quietly lost every line past ~6 KB on long
# multi-country statements. A document longer than budget*max-chunks is hard-capped at the
# last chunk with a VISIBLE marker and a logged warning, so truncation is never silent.
AI_DOC_CHAR_BUDGET = int(os.environ.get("EXTRACT_AI_DOC_CHARS", "60000"))
AI_DOC_MAX_CHUNKS = int(os.environ.get("EXTRACT_AI_DOC_MAX_CHUNKS", "8"))

def _join_source_text(texts):
    """The FULL text READ from the upload, verbatim, for the human "verify what was read"
    view on the review screen (and a .txt download). `texts` is the list of
    (filename, extracted_text) pairs produced by pdf_text/OCR. We join them with a per-file
    header so a multi-file batch stays legible, and we NEVER truncate — the whole point is
    that the operator can see every line that was read. Returns "" when nothing was read
    (e.g. an image-only PDF with OCR off)."""
    parts = []
    for n, t in (texts or []):
        t = (t or "").strip()
        header = f"===== {n} =====" if n else "====="
        parts.append(header + "\n" + (t if t else "(no text could be read from this file)"))
    return "\n\n".join(parts).strip()


def _doc_blocks(texts):
    """Build the labelled per-document blocks for the AI prompt, chunking any document
    that exceeds AI_DOC_CHAR_BUDGET rather than truncating it. Returns list[str]."""
    blocks = []
    for n, t in texts:
        t = t or ""
        if len(t) <= AI_DOC_CHAR_BUDGET:
            blocks.append(f"[{n}]\n{t}")
            continue
        chunks = [t[i:i + AI_DOC_CHAR_BUDGET]
                  for i in range(0, len(t), AI_DOC_CHAR_BUDGET)]
        total = len(chunks)
        if total > AI_DOC_MAX_CHUNKS:
            log.warning("AI extract: '%s' is %d chars (%d chunks) — sending the first %d; "
                        "raise EXTRACT_AI_DOC_MAX_CHUNKS to include all", n, len(t), total,
                        AI_DOC_MAX_CHUNKS)
            chunks = chunks[:AI_DOC_MAX_CHUNKS]
            chunks[-1] += "\n[TRUNCATED — document exceeds the AI character budget]"
        for i, c in enumerate(chunks, 1):
            blocks.append(f"[{n} — part {i}/{total}]\n{c}")
    return blocks

def _ai_content(texts):
    """The full prompt + DOCUMENTS payload (shared by every backend, with chunking)."""
    return PROMPT + "\n\nDOCUMENTS:\n" + "\n\n---\n\n".join(_doc_blocks(texts))

def _ai_claude(texts):
    import requests
    key = os.environ["ANTHROPIC_API_KEY"]
    content = _ai_content(texts)
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
              "max_tokens": 2000, "messages": [{"role": "user", "content": content}]},
        timeout=120)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])

def _ai_openai(texts):
    import requests
    key = os.environ["OPENAI_API_KEY"]
    content = _ai_content(texts)
    r = requests.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
              "response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": content}]}, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _ai_azure(texts):
    import requests
    ep = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
    dep = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
    content = _ai_content(texts)
    r = requests.post(f"{ep}/openai/deployments/{dep}/chat/completions?api-version={ver}",
        headers={"api-key": os.environ["AZURE_OPENAI_KEY"], "content-type": "application/json"},
        json={"response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": content}]}, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

_AI = {"claude": _ai_claude, "openai": _ai_openai, "azure": _ai_azure}

def _ai_extract(backend, texts):
    raw = _AI[backend](texts)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    d = json.loads(raw)
    for ln in d.get("lines", []):
        ln.setdefault("_source", "ai")
        for k in ("net", "vat"):
            try: ln[k] = money.f2(float(ln.get(k) or 0))
            except (TypeError, ValueError): ln[k] = 0.0
        # exchange rate from the invoice (units of line currency per 1 EUR); null if absent
        fx = ln.get("fx_rate")
        try: ln["fx_rate"] = round(float(fx), 6) if fx not in (None, "", 0) else None
        except (TypeError, ValueError): ln["fx_rate"] = None
    d.update(backend=backend, confidence="medium",
             notes=d.get("notes", "") + f" | AI draft ({backend}) - verify every value before commit")
    return d


# ---------------------------------------------------------------- structured e-invoices
# EU e-invoicing (EN 16931) ships as structured XML — UBL Invoice or UN/CEFACT CII.
# Those parse deterministically at 100% confidence (no AI). We read them
# namespace-agnostically (by local element name), which also handles simpler
# in-house XML invoice formats.
def _xml_local(tag):
    return tag.rsplit("}", 1)[-1]

# Recognised invoice document roots (namespace-agnostic local names): UBL `Invoice`,
# UBL `CreditNote` (a PEPPOL credit note BT-3=381 is a UBL CreditNote, NOT an Invoice with
# InvoiceTypeCode 381), UN/CEFACT CII `CrossIndustryInvoice` (the Factur-X/ZUGFeRD/Order-X
# format) and the older `CrossIndustryDocument`.
_INVOICE_ROOTS = ("Invoice", "CreditNote", "CrossIndustryInvoice", "CrossIndustryDocument")

# Factur-X / ZUGFeRD / EN-16931 PROFILE (a.k.a. conformance level), declared in the
# document context — CII `GuidelineSpecifiedDocumentContextParameter/ID` or UBL
# `CustomizationID`. This matters for capture trust: only the EN 16931 ("comfort") and
# EXTENDED profiles are guaranteed to carry per-LINE detail. MINIMUM and BASIC-WL
# ("without lines") legally OMIT invoice lines — they ship header/VAT totals only and
# are NOT stand-alone EN 16931 invoices — so a draft built from them must NOT be trusted
# as a complete high-confidence capture (the per-product fuel/VAT line detail this system
# needs simply is not in the XML; it lives only in the human-readable PDF). We detect the
# profile, record it on the draft, and downgrade confidence when line detail is absent.
# (substring on the lower-cased, separator-stripped guideline URN, first match wins —
# order matters: 'basic' must be tested before 'en16931' because a BASIC URN contains both.)
_PROFILE_URNS = (
    ("minimum", "minimum"),
    ("basicwl", "basicwl"),
    ("extended", "extended"),
    ("basic", "basic"),
    ("xrechnung", "xrechnung"),
    ("en16931", "en16931"),
    ("comfort", "en16931"),
)
_PROFILE_NO_LINES = {"minimum", "basicwl"}     # profiles that omit invoice-line detail

def _einvoice_profile(root):
    """Normalized Factur-X/ZUGFeRD/EN-16931 profile id from the document context
    (CII GuidelineSpecifiedDocumentContextParameter/ID or UBL CustomizationID), or
    None if not declared. Matched case-insensitively on the guideline URN."""
    ln = _xml_local
    urn = ""
    for e in root.iter():
        if ln(e.tag) == "CustomizationID" and (e.text or "").strip():
            urn = e.text.strip(); break
    if not urn:
        for e in root.iter():
            if ln(e.tag) == "GuidelineSpecifiedDocumentContextParameter":
                for c in e.iter():
                    if ln(c.tag) == "ID" and (c.text or "").strip():
                        urn = c.text.strip(); break
            if urn:
                break
    if not urn:
        return None
    key = re.sub(r"[-_ ]", "", urn.lower())
    for sub, norm in _PROFILE_URNS:
        if sub in key:
            return norm
    return "other"

def _is_xml(filename, data):
    if filename.lower().endswith(".xml"):
        return True
    head = data[:256].lstrip()[:64].lower()
    return head.startswith(b"<?xml") or head.startswith(b"<invoice") or head.startswith(b"<rsm:") \
        or head.startswith(b"<crossindustryinvoice")

def _xml_invoice_root(data):
    """True if `data` parses as XML whose root local-name is an invoice root."""
    import safexml  # defused parse of UNTRUSTED uploaded XML (billion-laughs safe)
    try:
        return _xml_local(safexml.fromstring(data).tag) in _INVOICE_ROOTS
    except Exception:
        return False

def _collect_xml(upload_bytes, filename):
    """-> list of (name, xml_bytes): a single .xml, or the .xml entries in a ZIP."""
    if filename.lower().endswith(".zip"):
        try:
            return _zip_extract(upload_bytes, ".xml")   # same zip-bomb caps as PDFs
        except zipfile.BadZipFile:
            return []
    if _is_xml(filename, upload_bytes):
        return [(os.path.basename(filename), upload_bytes)]
    return []

def _norm_date(s):
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():            # CII format 102: YYYYMMDD
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]

def parse_einvoice(xml_bytes):
    """Parse one UBL/CII/XML invoice into the standard draft shape. Lines are grouped
    by country (delivery/origin where present) so each becomes one claimable invoice
    row. Confidence 'high' — these are structured, not OCR'd."""
    import safexml  # defused parse of UNTRUSTED e-invoice XML (billion-laughs safe)
    root = safexml.fromstring(xml_bytes)
    ln = _xml_local

    def first(elem, *names):
        for e in elem.iter():
            if ln(e.tag) in names and (e.text or "").strip():
                return e.text.strip()
        return None

    def party(*kinds):
        """Find a supplier/seller party element and return (name, vat)."""
        for e in root.iter():
            if ln(e.tag) in kinds:
                name = first(e, "RegistrationName", "Name")
                vat = first(e, "CompanyID", "ID")
                return name, (vat if vat and any(c.isalpha() for c in vat) else vat)
        return None, None

    doc_id = first(root, "ID") or ""
    issue = _norm_date(first(root, "IssueDate", "IssueDateTime", "DateTimeString"))
    currency = first(root, "DocumentCurrencyCode", "InvoiceCurrencyCode") or "EUR"
    sup_name, sup_vat = party("AccountingSupplierParty", "SellerTradeParty")
    cust_name, _ = party("AccountingCustomerParty", "BuyerTradeParty")

    line_elems = [e for e in root.iter()
                  if ln(e.tag) in ("InvoiceLine", "CreditNoteLine",
                                   "IncludedSupplyChainTradeLineItem", "Line")]
    by_country = {}
    for le in line_elems:
        net = _num(first(le, "LineExtensionAmount", "NetAmount", "LineTotalAmount"))
        vat = _num(first(le, "VatAmount", "TaxAmount"))
        ctry = (first(le, "IdentificationCode", "CountryID", "Country", "OriginCountry") or "").strip()
        agg = by_country.setdefault(ctry, [0.0, 0.0])
        agg[0] += net; agg[1] += vat

    lines = []
    for ctry, (net, vat) in by_country.items():
        lines.append({"invoice_no": doc_id, "date": issue, "country": ctry,
                      "currency": currency, "net": money.f2(net), "vat": money.f2(vat),
                      "_source": "e-invoice"})
    # No structured invoice lines: a MINIMUM/BASIC-WL profile (lines omitted by design) or
    # a totals-only document. Fall back to the header totals so the figure isn't lost, but
    # remember the line detail was NOT in the structured data.
    from_totals = not lines
    if from_totals:
        net = _num(first(root, "TaxExclusiveAmount", "LineExtensionAmount"))
        vat = _num(first(root, "TaxAmount"))
        if net or vat:
            lines = [{"invoice_no": doc_id, "date": issue, "country": "", "currency": currency,
                      "net": net, "vat": vat, "_source": "e-invoice"}]

    # Document-level GROSS total (net + VAT) for the tie-out gate. EN-16931 BT-112
    # (UBL cbc:TaxInclusiveAmount / CII GrandTotalAmount) is the net+VAT figure — the
    # SAME basis validate_batch ties on (sum(net+vat)). We read ONLY that basis: the
    # amount-DUE figures (PayableAmount / DuePayableAmount = TaxInclusive − Prepaid) are
    # a DIFFERENT basis and would falsely fail the tie-out on an invoice with a prepayment,
    # so they are deliberately NOT used. Absent when no net+VAT total is stated -> no tie.
    _gross_raw = first(root, "TaxInclusiveAmount", "GrandTotalAmount")
    gross_total = _num(_gross_raw) if _gross_raw is not None else None

    # Capture trust: high ONLY when real per-line detail was present AND the declared
    # profile (if any) is one that carries lines. A line-less profile, or a totals-only
    # fall-back, downgrades to 'medium' and tells the reviewer to confirm lines against
    # the PDF — header totals alone are not a fileable per-product capture.
    profile = _einvoice_profile(root)
    low_detail = from_totals or (profile in _PROFILE_NO_LINES)
    note = "structured e-invoice (UBL/CII/XML)"
    if profile:
        note += f" [profile: {profile}]"
    if low_detail:
        note += (" — line-item detail not present in the structured data (header/VAT "
                 "totals only); confirm lines against the PDF before submitting")
    else:
        note += " — verify on review"
    out = {"supplier": sup_name, "supplier_vat": sup_vat, "statement_ref": doc_id,
           "statement_date": issue, "currency": currency, "customer": cust_name,
           "lines": lines, "notes": note, "profile": profile,
           "backend": "e-invoice", "confidence": "medium" if low_detail else "high"}
    # Document gross total for the confirm tie-out (net+VAT basis); absent if not stated.
    if gross_total is not None:
        out["coversheet_total"] = gross_total
    return out

def _einvoice_draft(xmls):
    """Merge one or more parsed e-invoice XMLs into a single review draft. Confidence is
    the WEAKEST of the merged invoices: a parse error or any line-less/low-detail profile
    drags the whole draft to 'medium' so a reviewer doesn't trust an incomplete capture."""
    merged, hdr = [], None
    confidence, profile = "high", None
    for name, data in xmls:
        try:
            d = parse_einvoice(data)
        except Exception as e:
            merged.append({"invoice_no": name, "date": "", "country": "", "currency": "EUR",
                           "net": 0, "vat": 0, "_source": f"parse error: {e}"})
            confidence = "medium"
            continue
        hdr = hdr or d
        profile = profile or d.get("profile")
        if d.get("confidence") == "medium":
            confidence = "medium"
        merged.extend(d.get("lines", []))
    draft = dict(hdr or {"supplier": None, "currency": "EUR", "backend": "e-invoice",
                         "notes": "structured e-invoice (UBL/CII/XML) — verify on review"})
    draft["lines"] = merged
    draft["confidence"] = confidence
    # The tie-out total on `hdr` covers ONLY the first invoice's lines; a multi-invoice
    # merge has a combined line set the single header total cannot tie against — drop it
    # so we never block on a mismatched scope (no total => no gate, exactly as before).
    if len(xmls) != 1:
        draft.pop("coversheet_total", None)
    if profile:
        draft["profile"] = profile
    if confidence == "medium" and "line-item detail" not in (draft.get("notes") or ""):
        draft["notes"] = (draft.get("notes") or "") + (
            " — some lines lack structured line-item detail; verify against the source "
            "before submitting")
    draft["files"] = [{"name": n, "size": len(b)} for n, b in xmls]
    draft["_pdf_bytes"] = list(xmls)           # vault the XML source(s) on confirm
    draft["_source_text"] = _join_source_text(   # the structured XML IS the source read
        [(n, b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b))
         for n, b in xmls])
    return draft


# ---------------------------------------------------------------- hybrid PDFs (Factur-X)
# Factur-X / ZUGFeRD / Order-X / XRechnung ship a HUMAN-readable PDF that ALSO carries the
# EN 16931 invoice as an embedded XML attachment (CII `CrossIndustryInvoice`, or UBL). We
# pull that XML out and route it through the SAME deterministic `parse_einvoice` path — so a
# hybrid invoice extracts at high confidence with zero AI, exactly like a plain e-invoice.

# Conventional attachment filenames for the embedded invoice (case-insensitive).
_FACTURX_NAMES = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml",
                  "cii.xml", "order-x.xml")

def _pdf_embedded_xml(pdf_bytes):
    """Return the embedded invoice XML from a hybrid (Factur-X/ZUGFeRD) PDF, else None.
    Never raises — an encrypted/corrupt/attachment-less PDF returns None so the caller
    falls through to the normal text/parser/AI path. The extracted attachment is capped
    at the same per-member size as the ZIP path (zip-bomb guard)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))

        # name -> list[bytes] of embedded files. Prefer the modern `reader.attachments`
        # API; fall back to walking the catalog /Names /EmbeddedFiles name tree if that
        # attribute is missing (older pypdf) or raises on this document.
        attachments = {}
        try:
            for name, payloads in dict(reader.attachments).items():
                if payloads:
                    attachments[name] = payloads[0]
        except Exception:
            attachments = _embedded_files_via_catalog(reader)
        if not attachments:
            return None

        # 1) a conventionally-named Factur-X/ZUGFeRD/Order-X/XRechnung attachment wins.
        by_lower = {n.lower(): (n, b) for n, b in attachments.items()}
        for known in _FACTURX_NAMES:
            if known in by_lower:
                _, data = by_lower[known]
                return data if len(data) <= ZIP_MAX_MEMBER_BYTES else None
        # 2) else any .xml attachment whose root is an invoice document.
        for name, data in attachments.items():
            if name.lower().endswith(".xml") and len(data) <= ZIP_MAX_MEMBER_BYTES \
                    and _xml_invoice_root(data):
                return data
        return None
    except Exception as e:
        log.warning("embedded-XML probe failed (%s) - treating as plain PDF", e)
        return None

def _embedded_files_via_catalog(reader):
    """Fallback embedded-file reader: walk /Root /Names /EmbeddedFiles. Returns
    {name: bytes}. Best-effort; any malformed node is skipped."""
    out = {}
    try:
        names = reader.trailer["/Root"]["/Names"]["/EmbeddedFiles"]["/Names"]
        names = list(names)
        for i in range(0, len(names) - 1, 2):
            name = str(names[i])
            spec = names[i + 1].get_object()
            ef = spec.get("/EF", {})
            stream = (ef.get("/UF") or ef.get("/F"))
            if stream is None:
                continue
            out[name] = stream.get_object().get_data()
    except Exception:
        return {}
    return out

def _facturx_draft(files, xmls):
    """Build the review draft for a batch of hybrid PDFs from their embedded XMLs.
    Reuses `_einvoice_draft` for parsing + line merge, then OVERRIDES the vault payload
    so the stored document is the ORIGINAL human-readable PDF (which also carries the
    data), not the bare XML."""
    draft = _einvoice_draft(xmls)
    draft["files"] = [{"name": n, "size": len(b)} for n, b in files]
    draft["_pdf_bytes"] = files                # vault the original hybrid PDF, not the XML
    draft["backend"] = "e-invoice"
    # Confidence/profile come from _einvoice_draft (profile-aware): a MINIMUM/BASIC-WL
    # hybrid stays 'medium' because its line detail lives only in the PDF, not the XML.
    # Keep the Factur-X context on the note.
    draft["notes"] = "Factur-X/ZUGFeRD embedded e-invoice (CII/UBL) — " + (
        draft.get("notes") or "verify on review")
    return draft


# ---------------------------------------------------------------- generic last-resort
# A plain PDF with NO matching per-supplier parser and NO AI backend used to yield a
# fully EMPTY draft (a blank manual-entry form) even after OCR. This GENERIC, on-prem,
# DETERMINISTIC header heuristic runs as a LAST RESORT (after the parser registry and the
# AI path, before the empty() fallback) and best-effort PREFILLS the draft HEADER from the
# pdf/OCR text. It keeps every byte on the server (no AI, no network) and NEVER fabricates
# authoritative figures: it produces NO invoice lines (a VAT claim needs per-product-code
# lines) — any total/VAT it spots is recorded only as a TEXT HINT in `notes` for the
# reviewer. Confidence is always 'low'; a real per-supplier parser still WINS over this.

# Invoice / document number labels (case-insensitive, multilingual-ish). The captured
# group is the adjacent token: an alphanumeric ref with optional separators.
_REF_LABELS = (
    r"invoice\s*(?:no\.?|number|#|nr\.?)", r"inv\.?\s*(?:no\.?|#)",
    r"faktura\s*(?:nr\.?|nr|no\.?)?", r"rechnung(?:s)?\s*(?:nr\.?|nummer)?",
    r"facture\s*(?:n[o°]\.?|nr\.?)?", r"fattura\s*(?:n[o°]\.?|nr\.?)?",
    r"document\s*(?:no\.?|number|#|nr\.?)", r"dokument(?:a)?\s*(?:nr\.?|numurs|numer)?",
    r"s[ąa]skaitos\s*(?:nr\.?|numeris)?", r"nr\.?",
)
_REF_RE = re.compile(
    r"(?:" + "|".join(_REF_LABELS) + r")\s*[:#.\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,})",
    re.IGNORECASE)

# A plausible date in common printed forms: 2026-05-15, 15.05.2026, 15/05/2026, etc.
_DATE_RE = re.compile(
    r"\b(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b")

# EU VAT id, e.g. LV40003012345 / PL5270200000. Best-effort hint only.
_VATID_RE = re.compile(r"\b([A-Z]{2}\d{8,12})\b")

# Currency detection: symbol or ISO code -> ISO code. EUR is the default.
_CCY_SIGNS = (("€", "EUR"), ("$", "USD"), ("£", "GBP"))
_CCY_CODES = ("EUR", "USD", "GBP", "PLN", "SEK", "NOK", "DKK", "CHF", "CZK", "HUF")

# Amount labels for the TOTAL / VAT hints. Captures a printed amount token.
_AMT_TOKEN = r"([\d][\d  .,]*\d|\d)"
_TOTAL_RE = re.compile(
    r"(?:total(?:\s*amount)?|grand\s*total|amount\s*due|kopsumma|gesamt(?:betrag)?|"
    r"summe|totale|montant\s*total|razem|viso|att\s*betala)\s*[:=]?\s*"
    + _AMT_TOKEN, re.IGNORECASE)
_VAT_RE = re.compile(
    r"(?:vat(?:\s*amount)?|tax\s*amount|pvn|mwst\.?|tva|iva|btw|moms)\s*[:=]?\s*"
    + _AMT_TOKEN, re.IGNORECASE)


def _detect_currency(text):
    """Best-effort currency from a symbol or ISO code; defaults to EUR."""
    for sign, code in _CCY_SIGNS:
        if sign in text:
            return code
    up = text.upper()
    for code in _CCY_CODES:
        if re.search(r"\b" + code + r"\b", up):
            return code
    return "EUR"


# Seller-vs-buyer disambiguation for the generic header heuristic. Many invoices print the
# BUYER block (Client / Pircējs / Acheteur …) BEFORE the SELLER block (Vendeur / Pārdevējs /
# Fournisseur …), so the FIRST VAT-id on the page is the customer's, not the supplier's
# (this is exactly what mis-recognised an E100 invoice as its Latvian buyer). We label every
# VAT-id by the nearest PRECEDING party header and prefer the seller's.
_BUYER_KW = ("client", "pircēj", "pirkēj", "acheteur", "customer", "buyer", "käufer",
             "saņēmēj", "kupują")
_SELLER_KW = ("vendeur", "pārdevēj", "seller", "fournisseur", "supplier", "verkäufer",
              "lieferant", "sprzedaw", "pardavėj", "müüja", "tarnij")


def _kw_positions(low, kws):
    """All start offsets of any keyword in `kws` within the already-lowercased text."""
    out = []
    for kw in kws:
        i = low.find(kw)
        while i != -1:
            out.append(i)
            i = low.find(kw, i + 1)
    return out


def _seller_identity(joined):
    """Best-effort (name, vat) of the SELLER from a plain-text invoice. Each VAT-id is
    assigned to whichever party header (buyer/seller) most recently precedes it; we return
    the first VAT-id in a SELLER block — else the first VAT-id NOT in a buyer block, which
    preserves the historical 'first VAT-id' behaviour when no party headers are printed.
    The name is the first name-like line after the earliest seller header. Never raises."""
    try:
        low = joined.lower()
        buyer = _kw_positions(low, _BUYER_KW)
        seller = _kw_positions(low, _SELLER_KW)

        def block_of(p):
            b = max([x for x in buyer if x < p], default=-1)
            s = max([x for x in seller if x < p], default=-1)
            if s > b:
                return "seller"
            if b > s:
                return "buyer"
            return None

        vats = [(m.start(), m.group(1)) for m in _VATID_RE.finditer(joined)]
        vat = next((v for p, v in vats if block_of(p) == "seller"), None)
        if vat is None:
            vat = next((v for p, v in vats if block_of(p) != "buyer"), None)

        name = None
        if seller:
            tail = joined[min(seller):].splitlines()
            for ln in tail[1:8]:
                s = ln.strip(" \t:•-")
                if (len(s) >= 3 and any(c.isalpha() for c in s)
                        and not _VATID_RE.search(s)
                        and not re.match(r"^\d", s)
                        and not any(k in s.lower() for k in _SELLER_KW + _BUYER_KW)
                        and not re.search(r"tva|pvn|vat|mwst|date|datums|valūt|devise",
                                          s.lower())):
                    name = s
                    break
        return name, vat
    except Exception as e:
        log.warning("seller identity heuristic failed: %s", e)
        return None, None


def _generic_text_draft(texts):
    """LAST-RESORT, on-prem, deterministic best-effort header extractor for a plain PDF
    that no per-supplier parser recognised and that no AI backend processed. Best-effort
    parses the concatenated pdf/OCR text for a document number, a date, a currency and a
    supplier VAT-id hint, and records any detected TOTAL/VAT amount as a TEXT HINT in the
    notes (never as an invoice line — VAT claims need per-product-code lines, which this
    heuristic must not fabricate). Returns a draft dict in the same schema empty() uses
    with backend='generic'/confidence='low', or None when essentially nothing was found
    (so behaviour is byte-identical to before on an unusable/garbage document). NEVER
    raises — any error is logged and returns None."""
    try:
        joined = "\n".join(t or "" for _n, t in texts)
        if len(joined.strip()) < _MIN_TEXT_CHARS:
            return None

        m = _REF_RE.search(joined)
        ref = m.group(1).strip() if m else None

        date = None
        for m in _DATE_RE.finditer(joined):
            nd = _norm_date(m.group(1))
            if re.match(r"\d{4}-\d{1,2}-\d{1,2}", nd) or re.match(
                    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", nd):
                date = nd
                break

        currency = _detect_currency(joined)

        # Seller identity, distinguishing the supplier from the customer (so a buyer-first
        # layout like E100's doesn't mis-capture the client). Name is preferred when found;
        # the seller VAT-id is always carried so VAT-registration recognition can resolve it.
        sup_name, sup_vat = _seller_identity(joined)
        supplier = sup_name or sup_vat               # name if read, else VAT-id hint

        # Amounts are recorded as TEXT HINTS only: we show BOTH the raw printed token and
        # the `_num()` (European-basis) reading, because the printed grouping is ambiguous
        # on-prem (a US-format '1,234.56' reads differently than EU '1.234,56'). The reviewer
        # confirms the real figure — this is never an authoritative line.
        hints = []
        mt = _TOTAL_RE.search(joined)
        if mt:
            raw = mt.group(1).strip()
            hints.append(f"detected total '{raw}' (~{money.f2(_num(raw))}) {currency}")
        mvat = _VAT_RE.search(joined)
        if mvat:
            raw = mvat.group(1).strip()
            hints.append(f"detected VAT '{raw}' (~{money.f2(_num(raw))}) {currency}")

        # Nothing usable at all -> behave exactly as before (empty fallback / manual).
        if not (ref or date or supplier or hints):
            return None

        note = ("auto-prefilled by on-prem heuristics (no recognised layout) — verify "
                "and complete every figure")
        if supplier and not sup_name:
            note += " | supplier shown is a VAT-id hint, not a confirmed name"
        if hints:
            note += " | " + "; ".join(hints) + " (HINT only — not a claim line)"

        return {"supplier": supplier, "supplier_vat": sup_vat, "statement_ref": ref,
                "statement_date": date, "currency": currency, "customer": None,
                "lines": [], "notes": note, "backend": "generic", "confidence": "low"}
    except Exception as e:
        log.warning("generic text draft failed — falling back to empty draft: %s", e)
        return None


def _plain_draft(texts, files, backend, filename, strict, ocr_used=False):
    """Build the review draft for the PLAIN (non-hybrid) PDFs via the parser→AI→empty
    path, then attach the original PDF bytes for vaulting. This is the historical
    text/parser/AI logic, moved verbatim out of extract() so the same code serves both
    the ALL-plain batch and the plain subset of a MIXED batch. Under strict=True the AI
    path RAISES TransientExtractionError on transient failures so the queue can retry.
    `ocr_used` marks that some text was recovered by OCR (scanned PDF) — the draft is then
    flagged for careful review (OCR text is noisier than embedded text)."""
    def empty(note, be):
        return {"supplier": None, "statement_ref": None, "statement_date": None,
                "currency": "EUR", "customer": None, "lines": [], "notes": note,
                "backend": be, "confidence": "low"}

    # OPT-IN AI VISION CAPTURE (default OFF): for a PLAIN, unknown-layout / scanned PDF,
    # read the page IMAGES with a vision model and use that as the PREFERRED draft. This is
    # the deliberate "AI for capture" exception (CLAUDE.md) — gated behind
    # `vision_capture.enabled()` (admin setting ON + a vision backend), so when OFF NO
    # network call is made and the path below is byte-identical to before. It does NOT run
    # for structured e-invoice / hybrid Factur-X PDFs (those stay deterministic, AI-free) —
    # `_plain_draft` is only reached for plain PDFs. On None (off / render / backend error /
    # unparseable) we fall straight through to the existing OCR→parser→AI→generic chain.
    # AUTO-CLASSIFY (DLP): scan the document's available text ONCE so a sensitivity label
    # exists without manual action. Best-effort + never raises; stores ONLY {type,count} +
    # the label (NEVER the raw values). The label both surfaces on the review screen and
    # gates the OPT-IN external-AI vision path below. classify_result stays None when the
    # classifier is unavailable (extraction is byte-identical in that case).
    classify_result = None
    try:
        import classify
        classify_result = classify.scan_text("\n".join(t for _n, t in (texts or []) if t))
    except Exception as e:
        log.warning("auto-classification skipped (advisory) for %s: %s", filename, e)

    # DETERMINISTIC-FIRST: try the per-supplier parsers BEFORE any AI path. A recognised
    # layout (Eurowag/E100/…) must be parsed deterministically and must NEVER be handed to
    # the AI vision-capture model (which can mis-read e.g. the buyer vs the seller). Only an
    # UNRECOGNISED plain PDF (no parser match) falls through to vision capture / text AI /
    # generic. Skipped only in explicit manual mode (backend 'none').
    parser_draft = None
    if backend != "none":
        for p in PARSERS:
            parser_draft = p(texts)
            if parser_draft:
                break

    capture_failed_reason = None
    try:
        import vision_capture
        if parser_draft is None and vision_capture.enabled():
            # DLP gate (OPT-IN, default permissive): refuse to send the page images to the
            # external vision provider when the just-scanned label EXCEEDS the admin policy.
            dlp_block = None
            if classify_result is not None:
                try:
                    import classify
                    allowed, _info = classify.external_ai_allowed_for_label(
                        classify_result.get("label"))
                    if not allowed:
                        dlp_block = classify.blocked_result(_info)
                except Exception as e:
                    log.warning("DLP gate check skipped (failing OPEN) for %s: %s",
                                filename, e)
            if dlp_block:
                log.warning("vision capture refused by DLP policy for %s: %s",
                            filename, dlp_block)
                try:
                    import auth
                    auth.log_error("DLP / data classification", "DlpBlocked", dlp_block, "")
                except Exception as e:
                    log.warning("could not write DLP block to the admin error log: %s", e)
                capture_failed_reason = dlp_block
            else:
                pdf0 = files[0][1] if files else None
                vd = vision_capture.capture(pdf0, files=files)
                if vd is not None:
                    if classify_result is not None:
                        vd["classification"] = classify_result
                    return vd
            # capture() returned None. It was ENABLED, so distinguish a BACKEND error (loud:
            # note + admin error-log entry) from the OFF/not-configured path (which never
            # sets a reason). _surface_capture_failure() handles the visibility.
            capture_failed_reason = vision_capture.take_last_error()
    except Exception as e:
        log.warning("vision capture path failed (%s) — falling back to the existing path", e)
        capture_failed_reason = f"vision capture path failed: {e}"

    draft = parser_draft                           # deterministic-first result (computed above)
    fallback_note = "no parser matched and no AI backend configured - enter manually"
    if draft is None and backend in ("auto", "claude", "openai", "azure"):
        be = backend if backend in _AI else os.environ.get("AI_BACKEND", "")
        if be in _AI:
            try:
                draft = _ai_extract(be, texts)
                # archive the AI-processed result in the data lake (same storage logic
                # as the PDF vault) so any module can reuse it without re-calling the API.
                try:
                    import data_lake
                    data_lake.put_extraction(draft, filename, be)
                except Exception as e:
                    log.debug("data lake archival skipped for %s: %s", filename, e)
            except Exception as e:
                # out-of-tokens / rate-limit / overload: let the queue retry later
                if strict and is_transient_error(e):
                    raise TransientExtractionError(f"{be}: {e}")
                # AI failed: fall through to the generic heuristic (it keeps every byte
                # on-prem) rather than going straight to a blank form. Remember the reason
                # so the empty() fallback still surfaces it if the heuristic finds nothing.
                fallback_note = f"AI extraction failed ({be}: {e}) - enter manually"
    if draft is None and backend != "none":
        # LAST RESORT (deterministic tiers + the AI-empty fallback, never explicit manual
        # mode): best-effort on-prem header heuristics so an unrecognised invoice is a
        # prefilled draft, not a blank form. Returns None when nothing usable was found.
        draft = _generic_text_draft(texts)
    if draft is None:
        draft = empty(fallback_note, "none")

    if ocr_used:
        draft["ocr"] = True
        draft["notes"] = (draft.get("notes") or "") + (
            " | text recovered via on-prem OCR (scanned PDF) — verify every figure")
        if draft.get("confidence") == "high":
            draft["confidence"] = "medium"
    draft["files"] = [{"name": n, "size": len(b)} for n, b in files]
    draft["_pdf_bytes"] = files                    # kept for vault attach on confirm
    if classify_result is not None:
        draft["classification"] = classify_result   # advisory DLP label for the review screen
    draft["_source_text"] = _join_source_text(texts)   # full text READ from the PDF (verify view)
    _surface_capture_failure(draft, capture_failed_reason)
    return draft


def _surface_capture_failure(draft, reason):
    """When AI vision capture was ENABLED but failed for a BACKEND reason (not simply OFF),
    make that loud and visible: (a) attach a ⚠️ note to the (OCR-fallback) draft, and (b)
    write it to the admin error log via auth.log_error so it appears where the admin looks.
    A None reason (OFF / not-configured / capture succeeded) does NOTHING — OFF stays
    silent and byte-identical. Best-effort: never raises."""
    if not reason:
        return
    note = (f"⚠️ AI vision capture was ON but failed: {reason} — used OCR fallback; the "
            f"captured data may be lower quality.")
    draft["notes"] = (draft.get("notes") or "") + (" | " if draft.get("notes") else "") + note
    draft["capture_failed"] = reason
    if draft.get("confidence") == "high":
        draft["confidence"] = "medium"
    try:
        import auth
        auth.log_error("vision capture", "CaptureFailed", reason, "")
    except Exception as e:
        log.warning("could not write vision-capture failure to the admin error log: %s", e)


def _merge_mixed(h_draft, p_draft, all_files):
    """Merge the deterministic hybrid draft with the parser/AI plain draft for a MIXED
    batch. A human confirms every draft before it becomes a figure, so a best-effort
    merge is strictly better than degrading the whole batch — but it must NEVER lose a
    line or drop a PDF from the vault. The structured e-invoice values are authoritative
    for header fields; confidence is lowered because parser/AI-derived lines are mixed in."""
    merged = dict(h_draft)
    # Lines: union of both, never drop any.
    merged["lines"] = list(h_draft.get("lines", [])) + list(p_draft.get("lines", []))
    # Header fields: prefer the STRUCTURED hybrid value when truthy, else the plain value.
    for k in ("supplier", "statement_ref", "statement_date", "currency", "customer"):
        hv = h_draft.get(k)
        merged[k] = hv if hv else p_draft.get(k)
    # Vault EVERY original PDF (both subsets).
    merged["files"] = [{"name": n, "size": len(b)} for n, b in all_files]
    merged["_pdf_bytes"] = list(all_files)
    assert len(merged["_pdf_bytes"]) == len(all_files)
    merged["backend"] = "mixed"
    merged["confidence"] = "medium"               # contains parser/AI-derived lines
    n_h = len(h_draft.get("_pdf_bytes", []) or [])
    n_p = len(p_draft.get("_pdf_bytes", []) or [])
    note = (f"Mixed batch: {n_h} embedded e-invoice PDF(s) parsed deterministically + "
            f"{n_p} parsed/AI PDF(s) — verify before submitting.")
    prior = "; ".join(s for s in (h_draft.get("notes"), p_draft.get("notes")) if s)
    merged["notes"] = f"{note} ({prior})" if prior else note
    return merged


# ---------------------------------------------------------------- orchestration
def extract(upload_bytes, filename, backend=None, strict=False):
    """Turn an upload into a draft. `strict=True` (used by the deferred intake
    queue) RAISES TransientExtractionError when the AI backend is out of
    tokens/quota or rate-limited, so the job can be retried later rather than
    silently producing an empty draft. The interactive path leaves strict=False
    and degrades to manual entry on any AI failure."""
    backend = backend or EXTRACT_BACKEND
    # Structured e-invoices (UBL/CII/XML) parse deterministically at high confidence —
    # no AI, regardless of the configured backend.
    xmls = _collect_xml(upload_bytes, filename)
    if xmls:
        return _einvoice_draft(xmls)
    files = unpack(upload_bytes, filename)
    if not files:
        return {"error": "no PDF found in upload", "lines": [], "files": []}
    # Hybrid PDFs (Factur-X/ZUGFeRD/Order-X/XRechnung) carry the EN 16931 invoice as an
    # embedded XML — extract it and parse deterministically (no AI). We split the batch
    # per-file: hybrid PDFs go the deterministic embedded-XML path, plain PDFs go the
    # text/parser/AI path. An ALL-hybrid or ALL-plain batch is handled exactly as before;
    # only a MIXED batch is merged (best-effort, human-confirmed, never losing a line/PDF).
    embedded = [(n, _pdf_embedded_xml(b)) for n, b in files]
    hybrids = [(n, b, x) for (n, b), (_, x) in zip(files, embedded) if x is not None]
    plains = [(n, b) for (n, b), (_, x) in zip(files, embedded) if x is None]
    if not plains:                                 # ALL hybrid — unchanged
        return _facturx_draft(files, [(n, x) for n, x in embedded])
    if not hybrids:                                # ALL plain — OCR fallback for scans
        pairs = [(n, pdf_text_or_ocr(b)) for n, b in plains]
        texts = [(n, t) for n, (t, _o) in pairs]
        ocr_used = any(o for _n, (_t, o) in pairs)
        return _plain_draft(texts, plains, backend, filename, strict, ocr_used=ocr_used)
    # MIXED: parse the hybrids deterministically, the plains via parser/AI, then merge.
    h_draft = _facturx_draft([(n, b) for n, b, _ in hybrids],
                             [(n, x) for n, b, x in hybrids])
    pairs = [(n, pdf_text_or_ocr(b)) for n, b in plains]
    p_texts = [(n, t) for n, (t, _o) in pairs]
    ocr_used = any(o for _n, (_t, o) in pairs)
    p_draft = _plain_draft(p_texts, plains, backend, filename, strict, ocr_used=ocr_used)
    return _merge_mixed(h_draft, p_draft, files)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        data = open(sys.argv[1], "rb").read()
        d = extract(data, os.path.basename(sys.argv[1]), backend="parser")
        d.pop("_pdf_bytes", None)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        print("usage: python3 extract.py <batch.zip|invoice.pdf>")
