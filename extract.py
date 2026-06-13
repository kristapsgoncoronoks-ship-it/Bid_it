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


# ---------------------------------------------------------------- deterministic parser
def _num(s):
    """European amount: '7 059,83' / '7\u00a0059,83' / '1 776,96' -> float."""
    s = s.replace("\u00a0", " ").strip()
    s = re.sub(r"(?<=\d)[ .](?=\d{3}\b)", "", s)   # strip thousands sep (space or dot)
    s = s.replace(",", ".")
    try: return money.f2(float(s))
    except ValueError: return 0.0

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

# PARSER REGISTRY - add one function per recurring PDF-only supplier here.
# Each takes [(name, text)] and returns a draft dict (or None if not its supplier).
# Recognised suppliers extract for free, offline; everything else falls to AI/manual.
# Template:
#   def parse_<supplier>(texts):
#       if "<marker>" not in " ".join(t for _, t in texts): return None
#       ... regex the totals ...
#       return {"supplier": "<CODE>", "statement_ref": ..., "lines": [...],
#               "backend": "parser", "confidence": "medium", ...}
PARSERS = [parse_eurowag]


# ---------------------------------------------------------------- AI backends
def _ai_claude(texts):
    import requests
    key = os.environ["ANTHROPIC_API_KEY"]
    content = PROMPT + "\n\nDOCUMENTS:\n" + "\n\n---\n\n".join(
        f"[{n}]\n{t[:6000]}" for n, t in texts)
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
    content = PROMPT + "\n\nDOCUMENTS:\n" + "\n\n---\n\n".join(
        f"[{n}]\n{t[:6000]}" for n, t in texts)
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
    content = PROMPT + "\n\nDOCUMENTS:\n" + "\n\n---\n\n".join(
        f"[{n}]\n{t[:6000]}" for n, t in texts)
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
# UN/CEFACT CII `CrossIndustryInvoice` (the Factur-X/ZUGFeRD/Order-X format) and the
# older `CrossIndustryDocument`.
_INVOICE_ROOTS = ("Invoice", "CrossIndustryInvoice", "CrossIndustryDocument")

def _is_xml(filename, data):
    if filename.lower().endswith(".xml"):
        return True
    head = data[:256].lstrip()[:64].lower()
    return head.startswith(b"<?xml") or head.startswith(b"<invoice") or head.startswith(b"<rsm:") \
        or head.startswith(b"<crossindustryinvoice")

def _xml_invoice_root(data):
    """True if `data` parses as XML whose root local-name is an invoice root."""
    import xml.etree.ElementTree as ET
    try:
        return _xml_local(ET.fromstring(data).tag) in _INVOICE_ROOTS
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

def _num(_s):
    try:
        return round(float(str(_s).replace(" ", "").replace(" ", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        return 0.0

def _norm_date(s):
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():            # CII format 102: YYYYMMDD
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]

def parse_einvoice(xml_bytes):
    """Parse one UBL/CII/XML invoice into the standard draft shape. Lines are grouped
    by country (delivery/origin where present) so each becomes one claimable invoice
    row. Confidence 'high' — these are structured, not OCR'd."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_bytes)
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
                  if ln(e.tag) in ("InvoiceLine", "IncludedSupplyChainTradeLineItem", "Line")]
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
    if not lines:                              # totals-only invoice: fall back to header totals
        net = _num(first(root, "TaxExclusiveAmount", "LineExtensionAmount"))
        vat = _num(first(root, "TaxAmount"))
        if net or vat:
            lines = [{"invoice_no": doc_id, "date": issue, "country": "", "currency": currency,
                      "net": net, "vat": vat, "_source": "e-invoice"}]
    return {"supplier": sup_name, "supplier_vat": sup_vat, "statement_ref": doc_id,
            "statement_date": issue, "currency": currency, "customer": cust_name,
            "lines": lines, "notes": "structured e-invoice (UBL/CII/XML) — verify on review",
            "backend": "e-invoice", "confidence": "high"}

def _einvoice_draft(xmls):
    """Merge one or more parsed e-invoice XMLs into a single review draft."""
    merged, hdr = [], None
    for name, data in xmls:
        try:
            d = parse_einvoice(data)
        except Exception as e:
            merged.append({"invoice_no": name, "date": "", "country": "", "currency": "EUR",
                           "net": 0, "vat": 0, "_source": f"parse error: {e}"})
            continue
        hdr = hdr or d
        merged.extend(d.get("lines", []))
    draft = dict(hdr or {"supplier": None, "currency": "EUR", "backend": "e-invoice",
                         "confidence": "high"})
    draft["lines"] = merged
    draft["files"] = [{"name": n, "size": len(b)} for n, b in xmls]
    draft["_pdf_bytes"] = list(xmls)           # vault the XML source(s) on confirm
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
    draft["confidence"] = "high"
    draft["notes"] = "Factur-X/ZUGFeRD embedded e-invoice (CII/UBL) — verify on review"
    return draft


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
    # embedded XML — extract it and parse deterministically (no AI). Only when EVERY PDF
    # in the batch yields an embedded invoice XML do we take this path; a mixed batch
    # (some hybrid, some not) falls through to the normal text/parser/AI path unchanged.
    embedded = [(n, _pdf_embedded_xml(b)) for n, b in files]
    if all(x is not None for _, x in embedded):
        return _facturx_draft(files, [(n, x) for n, x in embedded])
    texts = [(n, pdf_text(b)) for n, b in files]

    def empty(note, be):
        return {"supplier": None, "statement_ref": None, "statement_date": None,
                "currency": "EUR", "customer": None, "lines": [], "notes": note,
                "backend": be, "confidence": "low"}

    draft = None
    if backend in ("auto", "parser"):
        for p in PARSERS:
            draft = p(texts)
            if draft: break
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
                except Exception:
                    pass
            except Exception as e:
                # out-of-tokens / rate-limit / overload: let the queue retry later
                if strict and is_transient_error(e):
                    raise TransientExtractionError(f"{be}: {e}")
                draft = empty(f"AI extraction failed ({be}: {e}) - enter manually", "none")
    if draft is None:
        draft = empty("no parser matched and no AI backend configured - enter manually", "none")

    draft["files"] = [{"name": n, "size": len(b)} for n, b in files]
    draft["_pdf_bytes"] = files                    # kept for vault attach on confirm
    return draft


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        data = open(sys.argv[1], "rb").read()
        d = extract(data, os.path.basename(sys.argv[1]), backend="parser")
        d.pop("_pdf_bytes", None)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        print("usage: python3 extract.py <batch.zip|invoice.pdf>")
