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

EXTRACT_BACKEND = os.environ.get("EXTRACT_BACKEND", "auto")

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
def unpack(upload_bytes, filename):
    """-> list of (name, pdf_bytes). Accepts a single PDF or a ZIP of PDFs."""
    out = []
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(upload_bytes)) as z:
            for n in z.namelist():
                if n.lower().endswith(".pdf") and not n.startswith("__MACOSX"):
                    out.append((os.path.basename(n), z.read(n)))
    elif filename.lower().endswith(".pdf"):
        out.append((os.path.basename(filename), upload_bytes))
    return out


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
            except OSError: pass
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
    try: return round(float(s), 2)
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
            net, vat = round(sum(nets), 2), round(sum(vats), 2)
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
            try: ln[k] = round(float(ln.get(k) or 0), 2)
            except (TypeError, ValueError): ln[k] = 0.0
        # exchange rate from the invoice (units of line currency per 1 EUR); null if absent
        fx = ln.get("fx_rate")
        try: ln["fx_rate"] = round(float(fx), 6) if fx not in (None, "", 0) else None
        except (TypeError, ValueError): ln["fx_rate"] = None
    d.update(backend=backend, confidence="medium",
             notes=d.get("notes", "") + f" | AI draft ({backend}) - verify every value before commit")
    return d


# ---------------------------------------------------------------- orchestration
def extract(upload_bytes, filename, backend=None):
    backend = backend or EXTRACT_BACKEND
    files = unpack(upload_bytes, filename)
    if not files:
        return {"error": "no PDF found in upload", "lines": [], "files": []}
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
            except Exception as e:
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
