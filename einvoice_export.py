"""
E-INVOICE EXPORT — EN-16931 / UBL 2.1 Invoice XML EXPORT of registered invoices.

The intake side already PARSES inbound structured e-invoices (UBL/CII XML, incl.
Factur-X/ZUGFeRD embedded XML) deterministically in `extract.parse_einvoice`. This
module is the OUTBOUND counterpart: it EXPORTS a registered invoice as a well-formed
EN-16931-conformant UBL 2.1 `Invoice` document, mirroring the same field model so the
two halves round-trip cleanly.

READ-ONLY over the engine-owned product data. The canonical source is
`vat_refund.invoice_lines(con, entity, country, period)` — ONE row per
(invoice, product code) carrying supplier, issuer (seller name), supplier VAT id,
entity (buyer), invoice no/date, currency, per-line product/net/VAT and the goods
code/country. No figure is invented: every amount comes straight from the registered
NET EUR numbers, quantized HALF_UP via `money.f2`. No product-DB write happens here.

--------------------------------------------------------------------------------
DATA -> EN-16931 / UBL 2.1 BUSINESS-TERM MAPPING
--------------------------------------------------------------------------------
Invoice (root)
  cbc:CustomizationID            <- EN-16931 BIS customization URN (BT-24)
  cbc:ID                         <- invoice number                 (BT-1)
  cbc:IssueDate                  <- invoice date (ISO)             (BT-2)
  cbc:InvoiceTypeCode            <- 380 (commercial invoice)       (BT-3)
  cbc:DocumentCurrencyCode       <- currency (EUR)                 (BT-5)
  cac:AccountingSupplierParty                                       (BG-4)
    .../cbc:RegistrationName     <- supplier issuer / name         (BT-27)
    .../cac:PartyTaxScheme/cbc:CompanyID <- supplier VAT id        (BT-31)
  cac:AccountingCustomerParty                                      (BG-7)
    .../cbc:RegistrationName     <- entity (buyer) name            (BT-44)
    .../cac:PartyTaxScheme/cbc:CompanyID <- entity VAT id          (BT-48)
  cac:TaxTotal                                                     (BG-22 / BG-23)
    cbc:TaxAmount                <- document VAT total             (BT-110)
    cac:TaxSubtotal (per rate)                                     (BG-23)
      cbc:TaxableAmount          <- per-rate taxable base          (BT-116)
      cbc:TaxAmount              <- per-rate VAT amount            (BT-117)
      cac:TaxCategory/cbc:ID + cbc:Percent  <- category 'S' + rate (BT-118/BT-119)
  cac:LegalMonetaryTotal                                          (BG-22)
    cbc:LineExtensionAmount      <- sum of line nets               (BT-106)
    cbc:TaxExclusiveAmount       <- net total                      (BT-109)
    cbc:TaxInclusiveAmount       <- net + VAT                      (BT-112)
    cbc:PayableAmount            <- amount due                     (BT-115)
  cac:InvoiceLine (per registered product line)                    (BG-25)
    cbc:ID                       <- line number
    cbc:InvoicedQuantity         <- quantity (litres; unit code)   (BT-129/BT-130)
    cbc:LineExtensionAmount      <- line net                       (BT-131)
    cac:Item/cbc:Name            <- product / item name            (BT-153)
    cac:Item/.../ClassifiedTaxCategory + cbc:Percent <- rate       (BT-151/BT-152)
    cac:Price/cbc:PriceAmount    <- net unit price (NET EUR/L)      (BT-146)

PLACEHOLDERS (a field genuinely absent from the registered data — never a VAT
figure): a missing supplier/buyer VAT id, a missing seller/buyer name, or a missing
quantity. Each is filled with a clearly-marked "INPUT" placeholder PLUS an XML
comment, so a reviewer can complete it before any real submission. Amounts are NEVER
placeholdered — they come from the registered capture.

NET EUR basis, final (rebates applied); gross = net + VAT. The export is a clean,
universally-importable structured invoice — NOT a country-validated e-reporting
submission (validate against the destination's EN-16931 BIS / national CIUS XSD
before any legal filing).
"""
import io
import os
import zipfile
import xml.etree.ElementTree as ET

import applog
import money

WORKDIR = os.path.dirname(os.path.abspath(__file__))
log = applog.get("einvoice_export")

# UBL 2.1 namespaces (the EN-16931 invoice syntax binding).
NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

# EN-16931 BIS customization + commercial-invoice type code (BT-24 / BT-3).
CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017"
INVOICE_TYPE_CODE = "380"           # commercial invoice
TAX_CATEGORY_STANDARD = "S"         # standard-rated (a supplied/charged VAT line)
TAX_SCHEME_VAT = "VAT"
UNIT_LITRE = "LTR"                  # UN/ECE Rec 20 unit code for litre
UNIT_ONE = "C62"                   # "one" (dimensionless) when no quantity is known

# Marks a genuinely-absent (non-figure) field that a reviewer must complete.
INPUT_PLACEHOLDER = "INPUT"

# Stamped onto every file: this is a clean structured invoice, not a validated
# country-specific e-reporting submission.
EXPORT_BANNER = (
    "EN-16931 / UBL 2.1 Invoice EXPORT from the Fleet Fuel & VAT Refund System. "
    "NET EUR basis, final (rebates applied); gross = net + VAT. Amounts are the "
    "REGISTERED capture (no figure invented). Validate against the destination's "
    "EN-16931 BIS / national CIUS XSD before any legal e-reporting submission."
)


def _q(tag):
    """Resolve a 'prefix:local' tag to a Clark-notation qualified name."""
    pref, local = tag.split(":", 1)
    return "{%s}%s" % (NS[pref], local)


def _sub(parent, tag, text=None, attrib=None):
    """Append a namespaced child; stringify text when given (ET escapes it)."""
    el = ET.SubElement(parent, _q(tag), attrib or {})
    if text is not None:
        el.text = str(text)
    return el


def _money(v):
    """Quantize a EUR figure HALF_UP and render with 2 decimals (q_ledger basis)."""
    return f"{money.f2(v or 0):.2f}"


def _amt(parent, tag, value, currency):
    """A UBL amount element (carries the mandatory currencyID attribute)."""
    return _sub(parent, tag, _money(value), {"currencyID": currency})


def _rate_key(net, vat):
    """Implied VAT rate (1 dp) for a line, so lines group into BG-23 subtotals.
    0.0 when net is 0 (avoids a divide-by-zero). The rate is DERIVED from the
    registered net/VAT — never invented."""
    n = money.f2(net or 0)
    if not n:
        return 0.0
    return round(100.0 * money.f2(vat or 0) / n, 1)


def _placeholder_comment(parent, text):
    """Append an XML comment flagging a placeholder substitution for the reviewer."""
    parent.append(ET.Comment(" " + text + " "))


# ---------------------------------------------------------------- invoice model
def _lines_to_invoice(lines, *, invoice_no, issue_date, currency, seller_name,
                      seller_vat, buyer_name, buyer_vat):
    """Normalise a list of registered `invoice_lines` rows (already filtered to ONE
    invoice) into the dict an `ubl_invoice_xml` build consumes. Read-only; never
    raises on a missing field — absent non-figure fields become placeholders."""
    items = []
    for L in lines or []:
        net = money.f2(L.get("net_eur") or 0)
        vat = money.f2(L.get("vat_eur") or 0)
        qty = L.get("qty")
        items.append({
            "product": L.get("product") or L.get("desc") or "Fuel/toll",
            "code": str(L.get("code") or ""),
            "qty": qty,
            "net_eur": net,
            "vat_eur": vat,
            "rate": _rate_key(net, vat),
        })
    return {
        "invoice_no": invoice_no or "",
        "issue_date": issue_date or "",
        "currency": currency or "EUR",
        "seller_name": seller_name or "",
        "seller_vat": seller_vat or "",
        "buyer_name": buyer_name or "",
        "buyer_vat": buyer_vat or "",
        "lines": items,
    }


def _party_block(root, role_tag, name, vat_id):
    """Emit an AccountingSupplier/CustomerParty block (name + VAT scheme). A missing
    name or VAT id is placeholdered + commented (never a figure)."""
    apx = _sub(root, role_tag)
    party = _sub(apx, "cac:Party")
    pname = _sub(party, "cac:PartyName")
    if not name:
        _placeholder_comment(pname, "party name not in registered data - INPUT required")
    _sub(pname, "cbc:Name", name or INPUT_PLACEHOLDER)
    pts = _sub(party, "cac:PartyTaxScheme")
    if not vat_id:
        _placeholder_comment(pts, "VAT id not in registered data - INPUT required")
    _sub(pts, "cbc:CompanyID", vat_id or INPUT_PLACEHOLDER)
    ts = _sub(pts, "cac:TaxScheme")
    _sub(ts, "cbc:ID", TAX_SCHEME_VAT)
    # A minimal PostalAddress so the party block is structurally complete (no data
    # fabricated — only the mandatory empty Country wrapper).
    addr = _sub(party, "cac:PostalAddress")
    _sub(addr, "cac:Country")
    return apx


def ubl_invoice_xml(invoice):
    """Build a well-formed EN-16931-conformant UBL 2.1 Invoice for ONE registered
    invoice. `invoice` is the dict from `_lines_to_invoice` (or `build_invoice_for`).

    Returns (download_name, xml_bytes) — xml_bytes is an ElementTree-serialised utf-8
    document with an XML declaration. Read-only; best-effort — a missing non-figure
    field becomes a placeholder, never a crash, and no VAT figure is ever invented.

    NET EUR basis. Per-line money.f2 quantization, so the BG-22 totals and the BG-23
    per-rate subtotals tie to the registered capture within per-line rounding."""
    inv = invoice or {}
    currency = inv.get("currency") or "EUR"
    lines = inv.get("lines") or []

    # Register the UBL namespaces so the serialised tree carries clean prefixes.
    for pref, uri in NS.items():
        ET.register_namespace("" if pref == "inv" else pref, uri)

    root = ET.Element(_q("inv:Invoice"))
    root.insert(0, ET.Comment(" " + EXPORT_BANNER + " "))

    _sub(root, "cbc:CustomizationID", CUSTOMIZATION_ID)        # BT-24
    if not inv.get("invoice_no"):
        _placeholder_comment(root, "invoice number not in registered data - INPUT required")
    _sub(root, "cbc:ID", inv.get("invoice_no") or INPUT_PLACEHOLDER)   # BT-1
    if not inv.get("issue_date"):
        _placeholder_comment(root, "issue date not in registered data - INPUT required")
    _sub(root, "cbc:IssueDate", inv.get("issue_date") or "")          # BT-2
    _sub(root, "cbc:InvoiceTypeCode", INVOICE_TYPE_CODE)             # BT-3
    _sub(root, "cbc:DocumentCurrencyCode", currency)                # BT-5

    # ------------------------------------------------------------ parties
    _party_block(root, "cac:AccountingSupplierParty",
                 inv.get("seller_name"), inv.get("seller_vat"))      # BG-4 / BT-27,BT-31
    _party_block(root, "cac:AccountingCustomerParty",
                 inv.get("buyer_name"), inv.get("buyer_vat"))        # BG-7 / BT-44,BT-48

    # ------------------------------------------------------------ tax total (BG-23)
    # Group lines into per-rate subtotals; each subtotal carries the taxable base,
    # the VAT amount and the category/rate. Amounts are the registered figures.
    by_rate = {}
    for L in lines:
        b = by_rate.setdefault(L["rate"], [0.0, 0.0])
        b[0] += money.f2(L["net_eur"]); b[1] += money.f2(L["vat_eur"])
    tax_total = _sub(root, "cac:TaxTotal")
    doc_vat = money.fsum(L["vat_eur"] for L in lines)
    _amt(tax_total, "cbc:TaxAmount", doc_vat, currency)             # BT-110
    for rate, (base, vat) in sorted(by_rate.items()):
        sub = _sub(tax_total, "cac:TaxSubtotal")
        _amt(sub, "cbc:TaxableAmount", base, currency)             # BT-116
        _amt(sub, "cbc:TaxAmount", vat, currency)                  # BT-117
        cat = _sub(sub, "cac:TaxCategory")
        _sub(cat, "cbc:ID", TAX_CATEGORY_STANDARD)                 # BT-118
        _sub(cat, "cbc:Percent", f"{rate:g}")                     # BT-119
        ts = _sub(cat, "cac:TaxScheme")
        _sub(ts, "cbc:ID", TAX_SCHEME_VAT)

    # ------------------------------------------------------------ monetary totals (BG-22)
    line_total = money.fsum(L["net_eur"] for L in lines)
    lmt = _sub(root, "cac:LegalMonetaryTotal")
    _amt(lmt, "cbc:LineExtensionAmount", line_total, currency)      # BT-106
    _amt(lmt, "cbc:TaxExclusiveAmount", line_total, currency)       # BT-109
    _amt(lmt, "cbc:TaxInclusiveAmount", money.f2(line_total + doc_vat), currency)  # BT-112
    _amt(lmt, "cbc:PayableAmount", money.f2(line_total + doc_vat), currency)       # BT-115

    # ------------------------------------------------------------ invoice lines (BG-25)
    for i, L in enumerate(lines, 1):
        il = _sub(root, "cac:InvoiceLine")
        _sub(il, "cbc:ID", str(i))
        qty = L.get("qty")
        unit = UNIT_LITRE if qty not in (None, "") else UNIT_ONE
        if qty in (None, ""):
            _placeholder_comment(il, "quantity not in registered data - defaulted to 1 (C62)")
            qty_text = "1"
        else:
            qty_text = f"{float(qty):g}"
        _sub(il, "cbc:InvoicedQuantity", qty_text, {"unitCode": unit})   # BT-129/BT-130
        _amt(il, "cbc:LineExtensionAmount", L["net_eur"], currency)       # BT-131
        item = _sub(il, "cac:Item")
        _sub(item, "cbc:Name", L["product"])                            # BT-153
        ctc = _sub(item, "cac:ClassifiedTaxCategory")
        _sub(ctc, "cbc:ID", TAX_CATEGORY_STANDARD)                      # BT-151
        _sub(ctc, "cbc:Percent", f"{L['rate']:g}")                     # BT-152
        cts = _sub(ctc, "cac:TaxScheme")
        _sub(cts, "cbc:ID", TAX_SCHEME_VAT)
        price = _sub(il, "cac:Price")
        # NET unit price (NET EUR/L) — derived, divide-by-0 guarded; full-precision
        # display value (the legal figure is the LineExtensionAmount above).
        unit_price = (L["net_eur"] / float(qty)) if qty not in (None, "", 0) and float(qty) else L["net_eur"]
        _amt(price, "cbc:PriceAmount", unit_price, currency)            # BT-146

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    download_name = "Invoice_%s.xml" % _safe(inv.get("invoice_no") or "INPUT")
    return download_name, xml_bytes


def _safe(text):
    """Filesystem-safe token for a download filename."""
    return "".join(ch if ch.isalnum() else "_" for ch in str(text or "")).strip("_") or "x"


# ---------------------------------------------------------------- product-data reads
def build_invoice_for(entity, country, period, invoice_ref):
    """Assemble the export-invoice dict for ONE registered invoice, reading the
    canonical lines via `vat_refund.invoice_lines` (READ-ONLY over the product DB).
    Returns None when the invoice has no registered lines. Never raises."""
    import vat_refund
    try:
        con = vat_refund.connect()
        try:
            rows = vat_refund.invoice_lines(con, entity, country, period)
        finally:
            con.close()
    except Exception:
        log.exception("einvoice_export: invoice_lines failed for %r/%r/%r",
                      entity, country, period)
        return None
    sel = [r for r in rows if str(r.get("invoice")) == str(invoice_ref)]
    if not sel:
        return None
    first = sel[0]
    buyer_name, buyer_vat = _buyer(entity)
    return _lines_to_invoice(
        sel,
        invoice_no=first.get("invoice"),
        issue_date=first.get("inv_date"),
        currency=first.get("currency") or "EUR",
        seller_name=first.get("issuer") or first.get("supplier"),
        seller_vat=_clean_vat(first.get("vat_id")),
        buyer_name=buyer_name,
        buyer_vat=buyer_vat,
    )


def _buyer(entity):
    """(name, vat_id) for the buying entity from customer_master; best-effort."""
    name, vat = entity or "", ""
    if entity:
        try:
            import customer_master
            cust = customer_master.get_customer(entity)
            name = cust.get("company_name") or entity
            vat = cust.get("vat_number") or ""
        except Exception:
            log.exception("einvoice_export: buyer lookup failed for %r", entity)
    return name, vat


def _clean_vat(vat_id):
    """Drop the synthetic 'INPUT: ...' marker invoice_lines uses for an un-resolved
    issuer VAT id, so the export carries a real id OR a clean placeholder."""
    s = str(vat_id or "")
    if not s or s.startswith("INPUT"):
        return ""
    return s


def _enumerate_invoices(supplier=None, period=None):
    """Yield (entity, country, period, invoice_ref) for every registered invoice,
    optionally filtered by supplier and/or period (claim period like '2026-Q2', a
    month like '2026-05', or a year '2026'). READ-ONLY; never raises (returns [])."""
    import vat_refund
    out = []
    try:
        acon = vat_refund.analytics_connect()
        try:
            rows = acon.execute(
                "SELECT DISTINCT entity, country, period FROM transactions").fetchall()
        finally:
            acon.close()
    except Exception:
        log.exception("einvoice_export: enumerate streams failed")
        return out
    # Resolve each (entity, country, claim-period) stream to its registered invoices.
    seen = set()
    for r in rows:
        ent, ctry, per = r["entity"], r["country"], r["period"]
        qtr = vat_refund.quarter(per)
        if period and not _period_match(period, per, qtr):
            continue
        key = (ent, ctry, qtr)
        if key in seen:
            continue
        seen.add(key)
        try:
            con = vat_refund.connect()
            try:
                lines = vat_refund.invoice_lines(con, ent, ctry, qtr)
            finally:
                con.close()
        except Exception:
            log.exception("einvoice_export: invoice_lines failed for %r/%r/%r",
                          ent, ctry, qtr)
            continue
        for L in lines:
            if supplier and L.get("supplier") != supplier:
                continue
            ref = L.get("invoice")
            if not ref:
                continue
            tup = (ent, ctry, qtr, ref)
            if tup not in out:
                out.append(tup)
    return out


def _period_match(wanted, month_period, claim_period):
    """True if `wanted` (a year '2026', month '2026-05' or claim period '2026-Q2'/
    '2026-YEAR') selects the transaction's month_period / claim_period."""
    w = str(wanted)
    if w == month_period or w == claim_period:
        return True
    # bare year matches any month/claim in that year
    return month_period.startswith(w + "-") or claim_period.startswith(w + "-")


def ubl_invoices_zip(filter=None):
    """Export a SET of registered invoices as a ZIP of UBL XML files (one per
    invoice). `filter` is a dict with optional keys `supplier` and `period`
    (year / month / claim period). READ-ONLY; best-effort — a single bad invoice is
    skipped + logged so it can't take down the batch.

    Returns (download_name, zip_bytes). Raises ValueError when the filter selects no
    registered invoice (the route renders a friendly 'nothing to export')."""
    filter = filter or {}
    supplier = filter.get("supplier") or None
    period = filter.get("period") or None
    refs = _enumerate_invoices(supplier=supplier, period=period)
    if not refs:
        raise ValueError("no registered invoices match - nothing to export")

    buf = io.BytesIO()
    n = 0
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ent, ctry, qtr, ref in refs:
            try:
                invoice = build_invoice_for(ent, ctry, qtr, ref)
                if not invoice:
                    continue
                name, xml_bytes = ubl_invoice_xml(invoice)
            except Exception:
                log.exception("einvoice_export: skipping invoice %r in batch", ref)
                continue
            # de-dup the archive member name (two entities can share a ref).
            base = name[:-4] if name.endswith(".xml") else name
            cand, k = name, 1
            while cand in used:
                k += 1
                cand = f"{base}__{k}.xml"
            used.add(cand)
            zf.writestr(cand, xml_bytes)
            n += 1
    if not n:
        raise ValueError("no registered invoices match - nothing to export")

    parts = ["einvoices"]
    if supplier:
        parts.append(_safe(supplier))
    if period:
        parts.append(_safe(period))
    download_name = "UBL_" + "_".join(parts) + ".zip"
    return download_name, buf.getvalue()


# ---------------------------------------------------------------- light validation
# Key EN-16931 elements that MUST be present for the document to be structurally
# usable (a light internal check — not a full XSD validation).
REQUIRED_ELEMENTS = (
    "cbc:CustomizationID", "cbc:ID", "cbc:IssueDate", "cbc:DocumentCurrencyCode",
    "cac:AccountingSupplierParty", "cac:AccountingCustomerParty",
    "cac:TaxTotal", "cac:LegalMonetaryTotal", "cac:InvoiceLine",
)


def validate_ubl(xml_bytes):
    """Light structural validation of a generated UBL invoice: it must be well-formed
    XML, the root must be a UBL Invoice, and the key EN-16931 elements must be present.

    If `lxml` is available AND a local UBL XSD is present, validate against it;
    otherwise fall back to the structural presence check. Returns True on success;
    raises ValueError describing the first problem. (No heavy dependency is added just
    for validation — lxml/XSD are used only opportunistically.)"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"not well-formed XML: {e}")
    if root.tag != _q("inv:Invoice"):
        raise ValueError(f"root is not a UBL Invoice: {root.tag}")
    present = {f"{_prefix(el.tag)}" for el in root.iter()}
    for want in REQUIRED_ELEMENTS:
        if want not in present:
            raise ValueError(f"missing required EN-16931 element {want}")

    # Opportunistic XSD validation when lxml + a local schema are both available.
    xsd_path = os.path.join(WORKDIR, "schemas", "UBL-Invoice-2.1.xsd")
    if os.path.exists(xsd_path):
        try:
            from lxml import etree
            schema = etree.XMLSchema(etree.parse(xsd_path))
            doc = etree.fromstring(xml_bytes)
            if not schema.validate(doc):
                raise ValueError(f"XSD validation failed: {schema.error_log}")
        except ImportError:
            log.debug("einvoice_export: lxml not available - structural check only")
    return True


def _prefix(clark_tag):
    """Map a Clark-notation '{ns}local' tag back to its 'prefix:local' form (for the
    presence check against REQUIRED_ELEMENTS). Unknown ns -> the bare local name."""
    if not clark_tag.startswith("{"):
        return clark_tag
    uri, local = clark_tag[1:].split("}", 1)
    for pref, u in NS.items():
        if u == uri:
            return f"{pref}:{local}"
    return local
