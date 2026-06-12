"""
DOCUMENT MINING — fill the yellow INPUT gaps from documents you ALREADY have.

Re-reads the vaulted invoice documents (PDF text + structured XML) and extracts EU VAT
numbers, then proposes them as fills for missing supplier VAT registrations and customer
VAT numbers. Proposals are reviewed and applied by a human — nothing is written
automatically. Pure data, offline.

The matching is conservative: a VAT number is only proposed for a (supplier, country)
or a customer when its country code matches and the master field is still empty/INPUT.
"""
import os, re

import vat_refund
import supplier_master
import customer_master
import document_vault

# EU VAT number patterns by 2-letter country code (the common Member States here).
EU_VAT = {
    "AT": r"U\d{8}", "BE": r"0?\d{9,10}", "BG": r"\d{9,10}", "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}", "DE": r"\d{9}", "DK": r"\d{8}", "EE": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]", "FI": r"\d{8}", "FR": r"[A-Z0-9]{2}\d{9}",
    "GR": r"\d{9}", "HR": r"\d{11}", "HU": r"\d{8}", "IE": r"[A-Z0-9]{8,9}",
    "IT": r"\d{11}", "LT": r"(?:\d{12}|\d{9})", "LU": r"\d{8}", "LV": r"\d{11}",
    "MT": r"\d{8}", "NL": r"\d{9}B\d{2}", "PL": r"\d{10}", "PT": r"\d{9}",
    "RO": r"\d{2,10}", "SE": r"\d{12}", "SI": r"\d{8}", "SK": r"\d{10}",
}
_VAT_RE = re.compile(r"\b(" + "|".join(EU_VAT) + r")\s?(" +
                     "|".join(f"(?:{p})" for p in EU_VAT.values()) + r")\b")

COUNTRY_CODE = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR", "cyprus": "CY",
    "czech republic": "CZ", "czechia": "CZ", "denmark": "DK", "estonia": "EE",
    "finland": "FI", "france": "FR", "germany": "DE", "greece": "GR", "hungary": "HU",
    "ireland": "IE", "italy": "IT", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "malta": "MT", "netherlands": "NL", "poland": "PL", "portugal": "PT", "romania": "RO",
    "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE",
}

def country_code(name):
    return COUNTRY_CODE.get((name or "").strip().lower())


def extract_vat_numbers(text):
    """Return the set of normalized EU VAT numbers (e.g. 'SE502044770101') in `text`,
    validated against the per-country length/shape pattern."""
    out = set()
    for cc, num in _VAT_RE.findall(text or ""):
        cand = (cc + num).replace(" ", "").upper()
        if re.fullmatch(cc + EU_VAT[cc], cand):
            out.add(cand)
    return out


def _doc_text(stored_path, filename):
    """Best-effort text of a vaulted document: structured XML decoded directly,
    PDF via the extractor. Returns '' on any failure (never raises)."""
    try:
        data = document_vault.get_bytes(stored_path, vat_refund.DOCDIR)
    except Exception:
        return ""
    name = (filename or "").lower()
    if name.endswith(".xml") or data[:64].lstrip()[:5].lower() in (b"<?xml", b"<inv"):
        try:
            return data.decode("utf-8", "ignore")
        except Exception:
            return ""
    try:
        import extract
        return extract.pdf_text(data)
    except Exception:
        return ""


def scan():
    """Read every vaulted invoice document and collect the VAT numbers found,
    attributed to the document's supplier and entity. Returns
    {supplier: {code: set(vat)}}, {entity: set(vat)}."""
    con = vat_refund.connect()
    rows = con.execute("""SELECT entity, supplier, filename, stored_path
                          FROM invoice_documents""").fetchall()
    con.close()
    sup_vats, ent_vats = {}, {}
    for r in rows:
        vats = extract_vat_numbers(_doc_text(r["stored_path"], r["filename"]))
        if not vats:
            continue
        for v in vats:
            sup_vats.setdefault(r["supplier"], {}).setdefault(v[:2], set()).add(v)
            ent_vats.setdefault(r["entity"], set()).add(v)
    return sup_vats, ent_vats


def proposals():
    """Cross-reference mined VAT numbers against the master data and return fill
    proposals for genuinely missing fields. Each proposal:
        {kind, supplier|customer, country, code, field, value, reason}."""
    sup_vats, ent_vats = scan()
    out = []

    # supplier VAT registrations: fill a missing/INPUT vat_number, or propose a new
    # registration for a country we clearly transact in.
    regs = {(r["supplier"], r["country"]): r for r in supplier_master.vat_registrations()}
    for (sup, ctry), r in regs.items():
        cc = country_code(ctry)
        if not cc:
            continue
        have = (r["vat_number"] or "").strip().upper()
        if have and have != "INPUT":
            continue
        found = sup_vats.get(sup, {}).get(cc, set())
        for v in sorted(found):
            out.append({"kind": "supplier", "supplier": sup, "country": ctry, "code": cc,
                        "field": "vat_number", "value": v,
                        "reason": f"VAT {v} appears in {sup}'s {ctry} documents; registration is empty"})

    # customer VAT number (the entities we file for)
    for ent, vats in ent_vats.items():
        try:
            c = customer_master.get_customer(ent)
        except Exception:
            continue
        if not c:
            continue
        cur = (c.get("vat_number") or "").strip().upper()
        if cur and cur != "INPUT" and not cur.startswith("INPUT"):
            continue
        cc = country_code(c.get("country"))
        for v in sorted(vats):
            if cc and v.startswith(cc):
                out.append({"kind": "customer", "customer": c.get("code") or ent,
                            "country": c.get("country"), "code": cc, "field": "vat_number",
                            "value": v,
                            "reason": f"VAT {v} appears in {ent}'s documents; customer VAT is INPUT"})
    return out


def apply_supplier_vat(supplier, country, vat_number):
    supplier_master.set_vat_registration(supplier, country, vat_number, source="document mining")

def apply_customer_vat(code, vat_number):
    con = customer_master.connect()
    con.execute("UPDATE customers SET vat_number=? WHERE code=? OR company_name=?",
                (vat_number, code, code))
    con.commit(); con.close()


if __name__ == "__main__":
    ps = proposals()
    print(f"{len(ps)} proposal(s):")
    for p in ps:
        who = p.get("supplier") or p.get("customer")
        print(f"  {p['kind']:8} {who:14} {p.get('country',''):10} {p['field']}={p['value']}  ({p['reason']})")
