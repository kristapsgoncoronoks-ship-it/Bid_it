"""
CAPTURE-TIME DETERMINISTIC CHECKS (advisory) — IBAN, VAT-ID and duplicate detection.

Pure, offline, deterministic validations run over a freshly captured/extracted draft to
surface data-quality problems to the HUMAN reviewer before commit. They never mutate a
figure and never gate a legal VAT decision (that is the checklist's job in vat_refund.py)
— they raise FINDINGS the reviewer acts on, alongside validate.py's per-line checks.

  iban       ISO 13616 structure + ISO 7064 MOD-97 check digits (catches ~99% of
             transcription errors with zero network).
  vat_id     deterministic STRUCTURAL check (country prefix + national format). The live
             EU VIES lookup is intentionally NOT done inline: VIES is rate-limited and
             frequently unavailable, so an online verify belongs in a separate, cache-
             backed, failure-tolerant step (`vies_check` here is offline-graceful and
             returns "not checked" unless a fetcher is injected — it NEVER raises/blocks).
  duplicate  the same invoice (normalized number + amount) seen before — across ALL five
             entities, not just this supplier+statement — or repeated within the batch.

Severities: a bad IBAN or a prior-duplicate is "error" (block-worthy in review); a
malformed VAT-ID or an in-batch repeat is "warn". Unknown/again-uncheckable inputs yield
no finding (fail toward NOT crying wolf on data we can't adjudicate offline).
"""
import re

import money


def _f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


# ---------------------------------------------------------------- IBAN (MOD-97)
# Registered IBAN lengths (EU/EEA + common neighbours). A country we don't know is
# length-checked only by the MOD-97 math, never rejected for an unknown length.
IBAN_LENGTHS = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22,
    "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GI": 23,
    "GL": 18, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21,
    "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28,
    "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27,
}


def iban_valid(iban):
    """True iff `iban` passes ISO 13616 structure + ISO 7064 MOD-97 check digits.
    Spaces are ignored; case-insensitive. Unknown-country lengths are not rejected."""
    s = re.sub(r"\s+", "", (iban or "")).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", s):
        return False
    cc = s[:2]
    if cc in IBAN_LENGTHS and len(s) != IBAN_LENGTHS[cc]:
        return False
    rearranged = s[4:] + s[:4]                  # move the 4-char prefix to the end
    digits = "".join(str(int(ch, 36)) for ch in rearranged)   # A->10 .. Z->35, 0-9 as-is
    return int(digits) % 97 == 1


def iban_finding(iban):
    """A finding dict if `iban` is present and fails, else None (blank = nothing to check)."""
    if not (iban or "").strip():
        return None
    if iban_valid(iban):
        return None
    return {"field": "iban", "severity": "error",
            "message": f"IBAN '{iban.strip()}' fails the ISO 13616 / MOD-97 check"}


# ---------------------------------------------------------------- VAT-ID (structural)
# National VAT-number body formats (the part AFTER the 2-letter country prefix), as
# regexes. Greece uses the prefix EL (not GR). A country absent here -> "can't judge".
VAT_FORMATS = {
    "AT": r"U\d{8}", "BE": r"0?\d{9}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}", "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}",
    "EL": r"\d{9}", "ES": r"[A-Z0-9]\d{7}[A-Z0-9]", "FI": r"\d{8}", "FR": r"[A-Z0-9]{2}\d{9}",
    "HR": r"\d{11}", "HU": r"\d{8}", "IE": r"\d{7}[A-Z]{1,2}|\d[A-Z*+]\d{5}[A-Z]",
    "IT": r"\d{11}", "LT": r"\d{9}|\d{12}", "LU": r"\d{8}", "LV": r"\d{11}",
    "MT": r"\d{8}", "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}",
    "RO": r"\d{2,10}", "SE": r"\d{12}", "SI": r"\d{8}", "SK": r"\d{10}",
}


def vat_id_valid(vat_id):
    """True/False if the country is known and the structure matches/doesn't; None when the
    country prefix is missing/unknown (offline structural check, NOT a VIES existence check)."""
    s = re.sub(r"[\s.\-]", "", (vat_id or "")).upper()
    m = re.match(r"^([A-Z]{2})([A-Z0-9]+)$", s)
    if not m:
        return None
    cc, body = m.groups()
    pat = VAT_FORMATS.get(cc)
    if pat is None:
        return None
    return re.fullmatch(pat, body) is not None


def vat_id_finding(vat_id):
    """A 'warn' finding if a present VAT-ID is structurally malformed for its country, else
    None (blank, or a country we can't check offline -> no finding)."""
    if not (vat_id or "").strip():
        return None
    if vat_id_valid(vat_id) is False:
        return {"field": "vat_id", "severity": "warn",
                "message": f"VAT-ID '{vat_id.strip()}' does not match its country's format"}
    return None


def vies_check(vat_id, fetcher=None):
    """Offline-GRACEFUL EU VIES hook. Returns a dict and NEVER raises/blocks:
        {checked: bool, valid: bool|None, note: str}
    With no `fetcher` (the default everywhere outside an explicit, cache-backed online
    step) it reports checked=False — the deterministic structural check above stands in.
    A `fetcher(country, body) -> bool|None` can be injected (tests / a future AISP-style
    cached client); any exception from it degrades to checked=False, never an error."""
    s = re.sub(r"[\s.\-]", "", (vat_id or "")).upper()
    m = re.match(r"^([A-Z]{2})([A-Z0-9]+)$", s)
    if not m:
        return {"checked": False, "valid": None, "note": "no parseable VAT-ID"}
    if fetcher is None:
        return {"checked": False, "valid": None, "note": "VIES not queried (offline-graceful)"}
    try:
        valid = fetcher(*m.groups())
    except Exception as e:
        return {"checked": False, "valid": None, "note": f"VIES unavailable ({e})"}
    return {"checked": True, "valid": valid,
            "note": "VIES valid" if valid else "VIES could not confirm this VAT-ID"}


# ---------------------------------------------------------------- duplicate detection
def _norm_no(invoice_no):
    """Normalize an invoice number for matching: strip separators, upper-case."""
    return re.sub(r"[\s.\-/]", "", (invoice_no or "")).upper()


def invoice_key(supplier, ln):
    """Canonical (supplier, normalized-invoice-no, net, vat) key for duplicate matching.
    Amounts are money-quantized so float noise can't split a true duplicate."""
    return ((supplier or "").strip().upper(), _norm_no(ln.get("invoice_no")),
            money.f2(_f(ln.get("net")) or 0.0), money.f2(_f(ln.get("vat")) or 0.0))


def find_duplicates(supplier, lines, seen=()):
    """Findings for invoices that duplicate prior-confirmed history (`seen`, an iterable of
    invoice_key tuples — built cross-entity from confirmed transactions) or repeat within
    this batch. Lines with no invoice number are skipped (can't adjudicate)."""
    seen = set(seen)
    out, within = [], set()
    for ln in lines:
        ino = (ln.get("invoice_no") or "").strip()
        if not ino:
            continue
        k = invoice_key(supplier, ln)
        if k in seen:
            out.append({"field": "duplicate", "severity": "error", "invoice_no": ino,
                        "message": f"invoice {ino} ({k[2]:.2f}/{k[3]:.2f}) was already "
                                   "captured previously — possible duplicate"})
        elif k in within:
            out.append({"field": "duplicate", "severity": "warn", "invoice_no": ino,
                        "message": f"invoice {ino} appears more than once in this batch"})
        within.add(k)
    return out


# ---------------------------------------------------------------- combined entry point
def run(lines, supplier=None, supplier_vat=None, iban=None, seen=()):
    """Run every capture check over a draft and return a flat list of finding dicts
    ({field, severity, message[, invoice_no]}). Pure and offline; safe to call on any
    draft. `seen` is the cross-entity confirmed-invoice key set (default empty)."""
    findings = []
    f = iban_finding(iban)
    if f:
        findings.append(f)
    f = vat_id_finding(supplier_vat)
    if f:
        findings.append(f)
    findings.extend(find_duplicates(supplier, lines, seen))
    return findings


if __name__ == "__main__":
    demo_iban = "DE89370400440532013000"
    print("IBAN", demo_iban, "->", iban_valid(demo_iban))
    print("IBAN bad ->", iban_valid("DE89370400440532013001"))
    print("VAT DE811569869 ->", vat_id_valid("DE811569869"))
    print("findings:", run(
        [{"invoice_no": "DE-001", "net": 100, "vat": 19},
         {"invoice_no": "DE 001", "net": 100, "vat": 19}],   # in-batch dup (normalized)
        supplier="DKV", supplier_vat="DE99", iban="DE89370400440532013001",
        seen=[invoice_key("DKV", {"invoice_no": "OLD-1", "net": 50, "vat": 10})]))
