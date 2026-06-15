"""
SAF-T (Standard Audit File for Tax) — programmable OECD-core XML generator.

PREWORK + a CONFIGURABLE GENERATOR. This module builds an OECD-SAF-T-*core*
structure (the common AuditFile skeleton shared by the national variants) and is
parameterised by a COUNTRY PROFILE so any jurisdiction can be specialised later
WITHOUT rewriting the generator. The output is deliberately marked as a CORE
STRUCTURE — it is NOT a validated submission for any specific tax authority. To
produce a real, schema-valid file for a country you plug in a `CountryProfile`
that carries that country's namespace, schema version and required-element rules,
and you validate the result against the authority's published XSD.

Read-only over the engine-owned product DB (reuses `queries.q_ledger` —
the SAME ledger math used by the accounting CSV export, so the totals reconcile
with `queries.q_expense`). NET EUR basis, final (rebates applied); gross = net + VAT.

--------------------------------------------------------------------------------
DATA -> SAF-T ELEMENT MAPPING (core)
--------------------------------------------------------------------------------
AuditFile
  Header
    AuditFileVersion        <- profile.audit_file_version
    AuditFileCountry        <- entity country (customer_master), else ""
    AuditFileDateCreated    <- today (date.today, ISO)
    SoftwareCompanyName     <- this platform
    SoftwareID / ProductID  <- this platform
    Company
      RegistrationNumber    <- customer_master.reg_number (entity) or placeholder
      Name                  <- customer_master.company_name (entity) / "(all entities)"
      TaxRegistration/TaxRegistrationNumber <- customer_master.vat_number
      Address/Country       <- customer_master.country
    DefaultCurrencyCode     <- profile.currency
    SelectionCriteria
      PeriodStart/PeriodEnd <- min/max ledger `date` for the period
      SelectionStartDate/EndDate  (same range)
  MasterFiles
    Suppliers
      Supplier              <- one per distinct ledger `supplier`
        SupplierID / AccountID / Name <- supplier code/name
        TaxRegistration/TaxRegistrationNumber <- supplier_master.get_issuer VAT id
    TaxTable
      TaxTableEntry         <- one per distinct ledger `vat_rate_pct`
        TaxType=VAT, Description, TaxPercentage
  GeneralLedgerEntries        (the transactions section; core picks GLEntries)
    NumberOfEntries / TotalDebit / TotalCredit (net+VAT reconciliation totals)
    Journal/Transaction       <- one Transaction per ledger ROW
      TransactionDate         <- ledger `date`
      Description             <- note / product
      Lines (DebitLine net, Credit/Tax VAT) carrying net_eur, vat_eur, gross_eur,
      currency, SupplierID. Per-row amounts are money.f2 (the same per-line
      quantization as q_ledger), so SUM(net)+SUM(vat) ties to q_expense within
      per-line rounding.

--------------------------------------------------------------------------------
COUNTRY PROFILE SEAM — how to add a real country
--------------------------------------------------------------------------------
A `CountryProfile` carries the parameterisable bits: `code`, `namespace`,
`schema_version`, `audit_file_version`, `file_name_pattern`, `sections` (which of
Header/MasterFiles/GLEntries to emit) and a default `currency`. `DEFAULT_PROFILE`
is a GENERIC OECD-core profile (placeholder namespace + version, clearly marked
generic). Register a real profile in `PROFILES` and resolve it via `get_profile`.

To make a VALID submission for, e.g.:
  * Lithuania (SAF-T / i.SAF-T, v2.01)  — namespace/version per VMI, required
    elements, the LT-specific MasterFiles/GLEntries shape; validate vs the VMI XSD.
  * Poland (JPK / JPK_VAT)              — JPK is a different schema family entirely;
    a profile here only abstracts namespace/version/sections — the element set and
    XSD differ and MUST be validated against the MF schema.
  * Portugal (SAF-T PT)                 — the original SAF-T; AuditFileVersion and
    required Header/MasterFiles fields per Portaria; validate vs the AT XSD.
still requires: the correct XSD namespace + schema version, the country's REQUIRED
elements/ordering, and validation against that authority's published schema. This
module gives you the reconciled core; the profile + XSD validation make it real.

RECONCILIATION GUARANTEE: every ledger row becomes exactly one GL Transaction; no
row is silently dropped (a single malformed row is skipped AND logged). The XML's
SUM of entry net / VAT ties to `queries.q_expense(period).totals` within per-line
rounding (each row is money.f2-quantized, like q_ledger), the same tolerance the
accounting-CSV reconciliation test asserts.
"""
import datetime
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import applog
import money
import queries

WORKDIR = os.path.dirname(os.path.abspath(__file__))
log = applog.get("saft")

# Banner stamped onto every file (top-level comment + a Header note element) so the
# output can never be mistaken for a validated, country-specific submission.
CORE_BANNER = (
    "OECD SAF-T CORE STRUCTURE - generic profile; specialize per jurisdiction "
    "(namespace/version/required fields) via a CountryProfile before any real "
    "tax-authority submission. NET EUR basis; gross = net + VAT."
)

# Identifies this platform in the SAF-T software block.
SOFTWARE_COMPANY = "Fleet Fuel & VAT Refund System"
PRODUCT_ID = "FFS-SAFT-core"


@dataclass(frozen=True)
class CountryProfile:
    """The parameterisable bits of a SAF-T file — the seam where a real
    jurisdiction (correct XSD namespace/version, required fields) plugs in.

    code               short profile id, e.g. "OECD"/"LT"/"PL" (used in filename).
    namespace          XML namespace for the AuditFile root (XSD target namespace).
    schema_version     human schema version label (informational).
    audit_file_version value emitted as Header/AuditFileVersion.
    file_name_pattern  download filename, formatted with {code}/{entity}/{period}.
    sections           which top-level blocks to emit (subset of SECTIONS).
    currency           default currency code for the Header DefaultCurrencyCode.
    """
    code: str
    namespace: str
    schema_version: str
    audit_file_version: str
    file_name_pattern: str = "SAFT_{code}_{entity}_{period}.xml"
    sections: tuple = ("Header", "MasterFiles", "GeneralLedgerEntries")
    currency: str = "EUR"


# All top-level sections this core generator knows how to emit, in document order.
SECTIONS = ("Header", "MasterFiles", "GeneralLedgerEntries")

# GENERIC OECD-core profile. The namespace/version are PLACEHOLDERS, clearly marked
# generic — they are NOT a real tax authority's schema. Specialise per jurisdiction.
DEFAULT_PROFILE = CountryProfile(
    code="OECD",
    namespace="urn:oecd:ces:std:saf-t:core:generic",   # placeholder - NOT a real XSD ns
    schema_version="OECD-SAF-T-core (generic placeholder)",
    audit_file_version="2.00-core-generic",
    file_name_pattern="SAFT_{code}_{entity}_{period}.xml",
    sections=SECTIONS,
    currency="EUR",
)

# Registry of known profiles. A real country profile (correct namespace/version/
# required fields) registers here; `get_profile` resolves by code.
PROFILES = {"OECD": DEFAULT_PROFILE}


def get_profile(code):
    """Resolve a profile by code; unknown -> DEFAULT_PROFILE (warned, not raised)."""
    if not code:
        return DEFAULT_PROFILE
    prof = PROFILES.get(code)
    if prof is None:
        log.warning("unknown SAF-T profile %r - falling back to DEFAULT_PROFILE", code)
        return DEFAULT_PROFILE
    return prof


def _safe(text):
    """Filesystem-safe token for the download filename."""
    return "".join(ch if ch.isalnum() else "_" for ch in str(text or ""))


def _sub(parent, tag, text=None):
    """Append a child element, stringifying text when given (ET escapes it)."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _money(v):
    """Quantize a EUR figure HALF_UP and render with 2 decimals (q_ledger basis)."""
    return f"{money.f2(v or 0):.2f}"


def _company_block(parent, entity):
    """Header Company block from customer_master for `entity`, or a generic
    placeholder when the entity is unknown / not given (never raises)."""
    name = entity or "(all entities)"
    reg = vat = country = ""
    if entity:
        try:
            import customer_master
            cust = customer_master.get_customer(entity)
            name = cust.get("company_name") or entity
            reg = cust.get("reg_number") or ""
            vat = cust.get("vat_number") or ""
            country = cust.get("country") or ""
        except Exception:
            log.exception("SAF-T: customer lookup failed for entity %r", entity)
    comp = _sub(parent, "Company")
    _sub(comp, "RegistrationNumber", reg or "INPUT")
    _sub(comp, "Name", name)
    treg = _sub(comp, "TaxRegistration")
    _sub(treg, "TaxRegistrationNumber", vat or "INPUT")
    addr = _sub(comp, "Address")
    _sub(addr, "Country", country)
    return country


def _supplier_vat(supplier, country):
    """Best-effort supplier VAT id via supplier_master (None when unavailable)."""
    try:
        import supplier_master
        _, vat_id, _ = supplier_master.get_issuer(supplier, country)
        return vat_id
    except Exception:
        log.exception("SAF-T: supplier VAT lookup failed for %r/%r", supplier, country)
        return None


def _clean(row):
    """Normalise one q_ledger row to the fields the SAF-T builder needs, quantizing
    money HALF_UP (the q_ledger per-line basis). Returns None for a malformed row
    (logged by the caller) so a single bad row can't take down the whole file."""
    net = money.f2(row.get("net_eur") or 0)
    vat = money.f2(row.get("vat_eur") or 0)
    return {
        "date": row.get("date") or "",
        "supplier": row.get("supplier") or "",
        "country": row.get("country") or "",
        "product_group": row.get("product_group") or "",
        "product": row.get("product") or "",
        "note": row.get("note") or "",
        "currency": row.get("currency") or "",
        "vat_rate_pct": row.get("vat_rate_pct") or 0.0,
        "net_eur": net,
        "vat_eur": vat,
        "gross_eur": money.f2(net + vat),
    }


def build_saft(period=None, entity=None, profile=None):
    """Build the OECD-SAF-T-CORE AuditFile for `period` (default: latest loaded).

    Returns (download_name, xml_bytes) — xml_bytes is an ElementTree-serialised
    utf-8 document with an XML declaration. The output is a CORE STRUCTURE (see
    CORE_BANNER), not a validated country submission. Read-only.

    Raises ValueError when there is no data/period to export. Lower-level helpers
    never crash on a single bad row — a malformed row is skipped and logged so a
    bad row can't take down the whole file (the rest still reconciles)."""
    profile = profile or DEFAULT_PROFILE
    import reports
    c = reports.connect()
    try:
        ps = reports._periods(c)
        period = period or (ps[0] if ps else None)
        if period is None:
            raise ValueError("no data loaded - nothing to export")
        rows = queries.q_ledger(c, period, entity)
    finally:
        c.close()
    if not rows:
        # A resolved period with no rows is still "nothing to export" for this
        # entity/period combination.
        raise ValueError("no data loaded - nothing to export")

    # Normalise every row ONCE; a malformed row is skipped + logged (never silently
    # lost) so a single bad row can't take down the whole file. Every section below
    # is driven off `entries`, so all of them honour the same skip.
    entries = []
    for r in rows:
        try:
            entries.append(_clean(r))
        except Exception:
            log.exception("SAF-T: skipping malformed ledger row: %r", r)
    if not entries:
        raise ValueError("no data loaded - nothing to export")

    ns = profile.namespace
    root = ET.Element("AuditFile", {"xmlns": ns})
    # Top-level banner comment: this is a core structure, not a validated submission.
    root.insert(0, ET.Comment(" " + CORE_BANNER + " "))

    sections = profile.sections or SECTIONS

    # ----------------------------------------------------------------- Header
    country = ""
    if "Header" in sections:
        hdr = _sub(root, "Header")
        _sub(hdr, "AuditFileVersion", profile.audit_file_version)
        # AuditFileCountry filled after the company block (entity country) below.
        ac = _sub(hdr, "AuditFileCountry")
        _sub(hdr, "AuditFileDateCreated", datetime.date.today().isoformat())
        _sub(hdr, "SoftwareCompanyName", SOFTWARE_COMPANY)
        _sub(hdr, "SoftwareID", PRODUCT_ID)
        _sub(hdr, "ProductID", PRODUCT_ID)
        country = _company_block(hdr, entity)
        ac.text = country or ""
        _sub(hdr, "DefaultCurrencyCode", profile.currency)
        # Period bounds from the ACTUAL ledger date range (a fuelling can fall just
        # outside the label month, so derive from data rather than the period string).
        dates = sorted(e["date"] for e in entries if e["date"])
        if dates:
            sel = _sub(hdr, "SelectionCriteria")
            _sub(sel, "PeriodStartDate", dates[0])
            _sub(sel, "PeriodEndDate", dates[-1])
            _sub(sel, "SelectionStartDate", dates[0])
            _sub(sel, "SelectionEndDate", dates[-1])
            _sub(sel, "TaxReportingPeriod", period)
        # An explicit Header note element restating the core-structure caveat.
        _sub(hdr, "HeaderComment", CORE_BANNER)

    # ------------------------------------------------------------- MasterFiles
    if "MasterFiles" in sections:
        mf = _sub(root, "MasterFiles")
        sup_el = _sub(mf, "Suppliers")
        # one Supplier per distinct (supplier, country) so the VAT id can be
        # country-specific; SupplierID stays the supplier code.
        seen = {}
        for e in entries:
            seen.setdefault(e["supplier"], e["country"])  # first country seen, for VAT lookup
        for sup, ctry in sorted(seen.items()):
            s = _sub(sup_el, "Supplier")
            _sub(s, "SupplierID", sup)
            _sub(s, "AccountID", sup)
            _sub(s, "Name", sup)
            vat_id = _supplier_vat(sup, ctry)
            treg = _sub(s, "TaxRegistration")
            _sub(treg, "TaxRegistrationNumber", vat_id or "INPUT")

        # TaxTable: distinct VAT rates present in the ledger.
        tt = _sub(mf, "TaxTable")
        rates = sorted({e["vat_rate_pct"] for e in entries})
        for i, rate in enumerate(rates, 1):
            te = _sub(tt, "TaxTableEntry")
            _sub(te, "TaxType", "VAT")
            _sub(te, "Description", f"VAT {rate:g}%")
            det = _sub(te, "TaxCodeDetails")
            _sub(det, "TaxCode", f"VAT{i}")
            _sub(det, "TaxPercentage", f"{rate:g}")

    # ----------------------------------------------------- GeneralLedgerEntries
    if "GeneralLedgerEntries" in sections:
        gl = _sub(root, "GeneralLedgerEntries")
        n_el = _sub(gl, "NumberOfEntries")
        tot_net_el = _sub(gl, "TotalDebit")    # NET total (debit: expense)
        tot_vat_el = _sub(gl, "TotalCredit")   # VAT total (credit: recoverable VAT)
        journal = _sub(gl, "Journal")
        _sub(journal, "JournalID", "FUEL")
        _sub(journal, "Description", "Fuel & toll transactions (NET EUR; gross = net + VAT)")

        sum_net = 0.0
        sum_vat = 0.0
        for i, e in enumerate(entries, 1):
            net, vat, gross = e["net_eur"], e["vat_eur"], e["gross_eur"]
            tx = _sub(journal, "Transaction")
            _sub(tx, "TransactionID", str(i))
            _sub(tx, "TransactionDate", e["date"])
            _sub(tx, "Description", e["note"] or e["product"] or e["product_group"])
            _sub(tx, "SupplierID", e["supplier"])
            _sub(tx, "Currency", e["currency"] or profile.currency)
            # NET as a debit line, recoverable VAT carried on the same entry.
            dl = _sub(tx, "DebitLine")
            _sub(dl, "AccountID", e["product_group"] or "FUEL")
            _sub(dl, "DebitAmount", _money(net))
            tx_info = _sub(dl, "TaxInformation")
            _sub(tx_info, "TaxType", "VAT")
            _sub(tx_info, "TaxPercentage", f"{e['vat_rate_pct']:g}")
            _sub(tx_info, "TaxAmount", _money(vat))
            _sub(tx, "NetAmount", _money(net))
            _sub(tx, "TaxAmount", _money(vat))
            _sub(tx, "GrossAmount", _money(gross))
            sum_net += net
            sum_vat += vat

        n_el.text = str(len(entries))
        tot_net_el.text = _money(sum_net)
        tot_vat_el.text = _money(sum_vat)

    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    ent_token = _safe(entity) if entity else "ALL"
    per_token = _safe(period)
    download_name = profile.file_name_pattern.format(
        code=_safe(profile.code), entity=ent_token, period=per_token)
    return download_name, xml_bytes
