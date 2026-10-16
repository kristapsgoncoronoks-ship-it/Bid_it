"""Structured e-invoice XML recognition (deterministic — no OCR, no AI).

Recognises the two EN-16931 syntaxes:
  • UBL 2.1  — <Invoice> / <CreditNote> (OASIS UBL)
  • UN-CEFACT CII — <CrossIndustryInvoice> (used by Factur-X / ZUGFeRD / XRechnung)

and extracts the embedded CII XML from **hybrid PDFs** (Factur-X/ZUGFeRD), so a
PDF that carries a machine-readable invoice is read exactly, never guessed.

This is the highest-confidence capture path: every figure is read from a typed
field, so it runs BEFORE the PDF text-layer/OCR heuristics. Parsing uses
`defusedxml` to neutralise XXE / entity-expansion attacks on uploaded files.

No third-party XML libraries: navigation is by *local* element name, ignoring
namespace URIs, which keeps it robust across UBL/CII minor versions.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring

from app.schemas.invoice import InvoiceCreate, LineItemIn, ParsedInvoiceDraft
from app.services import received_bank, received_lines


# --------------------------------------------------------------------------- #
# Namespace-agnostic XML helpers (match by local name)
# --------------------------------------------------------------------------- #
def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct(el: Element, name: str) -> Element | None:
    for c in list(el):
        if _ln(c.tag) == name:
            return c
    return None


def _direct_any(el: Element, *names: str) -> Element | None:
    # NB: never use `a or b` on Elements — a childless (leaf) Element is falsy
    # in ElementTree, so `or` silently drops it. Match by name with None checks.
    for name in names:
        found = _direct(el, name)
        if found is not None:
            return found
    return None


def _first_any(el: Element, *names: str) -> Element | None:
    for name in names:
        found = _first(el, name)
        if found is not None:
            return found
    return None


def _direct_all(el: Element, name: str) -> list[Element]:
    return [c for c in list(el) if _ln(c.tag) == name]


def _first(el: Element, name: str) -> Element | None:
    for c in el.iter():
        if c is not el and _ln(c.tag) == name:
            return c
    return None


def _first_all(el: Element, name: str) -> list[Element]:
    return [c for c in el.iter() if c is not el and _ln(c.tag) == name]


def _txt(el: Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def _first_txt(el: Element | None, name: str) -> str | None:
    if el is None:
        return None
    return _txt(_first(el, name))


def _dec(value: str | None) -> Decimal:
    if not value:
        return Decimal("0")
    try:
        return Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        return Decimal("0")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# UBL 2.1  (Invoice / CreditNote)
# --------------------------------------------------------------------------- #
def _parse_ubl(root: Element, filename: str) -> ParsedInvoiceDraft:
    warnings = ["Read structured e-invoice (UBL 2.1 / EN-16931) — exact fields, no OCR."]

    number = _txt(_direct(root, "ID")) or _stem(filename)
    issue = _parse_date(_txt(_direct(root, "IssueDate")))
    due = _parse_date(_txt(_direct(root, "DueDate")))
    currency = _txt(_direct(root, "DocumentCurrencyCode")) or "EUR"

    supplier = _first(root, "AccountingSupplierParty")
    vendor = None
    if supplier is not None:
        vendor = _first_txt(supplier, "RegistrationName") or _first_txt(supplier, "Name")

    # BT-30 belongs to the seller's legal entity; BT-31 belongs ONLY to a
    # VAT tax scheme. Party IDs and other tax registrations are different facts.
    party = _direct(supplier, "Party") if supplier is not None else None
    legal = _direct(party, "PartyLegalEntity") if party is not None else None
    registration = _txt(_direct(legal, "CompanyID")) if legal is not None else None
    vat_id = None
    if party is not None:
        for tax in _direct_all(party, "PartyTaxScheme"):
            scheme = _direct(tax, "TaxScheme")
            scheme_id = _txt(_direct(scheme, "ID")) if scheme is not None else None
            if scheme_id == "VAT":
                vat_id = _txt(_direct(tax, "CompanyID"))
                if vat_id is not None:
                    break

    # BT-84: only header payee accounts, never the payer's mandate account.
    supplier_iban = received_bank.from_candidates(
        (
            _txt(_direct(account, "ID"))
            for means in _direct_all(root, "PaymentMeans")
            for account in _direct_all(means, "PayeeFinancialAccount")
        ),
        warnings,
    )

    # BT-13, the buyer's purchase-order reference, which UBL puts in
    # cac:OrderReference/cbc:ID. Read from the ORDER REFERENCE element only —
    # `cbc:ID` on its own is the invoice number, and a looser search would
    # quietly file the invoice number as its own PO.
    po_reference = _first_txt(_direct(root, "OrderReference"), "ID")

    lines: list[LineItemIn] = []
    line_els = _direct_all(root, "InvoiceLine") or _direct_all(root, "CreditNoteLine")
    for line in line_els:
        qty_el = _direct_any(line, "InvoicedQuantity", "CreditedQuantity")
        qty = _dec(_txt(qty_el)) or Decimal("1")
        amount = _dec(_txt(_direct(line, "LineExtensionAmount")))

        item = _direct(line, "Item")
        desc = (
            (_first_txt(item, "Name") or _first_txt(item, "Description") or "Item")
            if item is not None
            else "Item"
        )

        price_el = _direct(line, "Price")
        unit = _dec(_first_txt(price_el, "PriceAmount")) if price_el is not None else Decimal("0")
        if unit == 0 and qty:
            unit = (amount / qty) if qty else amount

        percent = _dec(_first_txt(line, "Percent"))
        parsed_line = _line(desc, qty, unit, amount, percent)
        totals = _direct(line, "TaxTotal")
        parsed_line.captured_tax_amount = _captured_amount(
            _direct(totals, "TaxAmount") if totals is not None else None,
            currency,
            warnings,
            "Line tax",
        )
        parsed_line.captured_gross_amount = _captured_amount(
            _direct(line, "TaxInclusiveAmount"), currency, warnings, "Line gross"
        )
        lines.append(parsed_line)

    stated_total = _stated_total_ubl(root)
    return _draft(
        number,
        issue,
        due,
        currency,
        vendor,
        lines,
        filename,
        warnings,
        stated_total,
        po_reference,
        supplier_registration_number=registration,
        supplier_vat_id=vat_id,
        supplier_iban=supplier_iban,
    )


def _stated_total_ubl(root: Element) -> Decimal | None:
    total = _first(root, "LegalMonetaryTotal")
    if total is None:
        return None
    return (
        _dec(_first_txt(total, "PayableAmount") or _first_txt(total, "TaxInclusiveAmount")) or None
    )


# --------------------------------------------------------------------------- #
# UN-CEFACT CII  (CrossIndustryInvoice — Factur-X / ZUGFeRD / XRechnung)
# --------------------------------------------------------------------------- #
def _parse_cii(root: Element, filename: str) -> ParsedInvoiceDraft:
    warnings = ["Read structured e-invoice (UN-CEFACT CII / Factur-X) — exact fields, no OCR."]

    exdoc = _first(root, "ExchangedDocument")
    number = (_first_txt(exdoc, "ID") if exdoc is not None else None) or _stem(filename)
    issue = None
    if exdoc is not None:
        dt = _first(exdoc, "DateTimeString")
        issue = _parse_date(_txt(dt))

    txn = _first(root, "SupplyChainTradeTransaction")
    if txn is None:
        txn = root
    agreement = _direct(txn, "ApplicableHeaderTradeAgreement")
    seller = _direct(agreement, "SellerTradeParty") if agreement is not None else None
    vendor = _first_txt(seller, "Name") if seller is not None else None

    organization = _direct(seller, "SpecifiedLegalOrganization") if seller is not None else None
    registration = _txt(_direct(organization, "ID")) if organization is not None else None
    vat_id = None
    if seller is not None:
        for tax in _direct_all(seller, "SpecifiedTaxRegistration"):
            identifier = _direct(tax, "ID")
            # VA = VAT; FC is the seller's separate fiscal identifier (BT-32).
            if identifier is not None and identifier.get("schemeID") == "VA":
                vat_id = _txt(identifier)
                if vat_id is not None:
                    break

    # BT-13 lives on the AGREEMENT side in CII, as the issuer-assigned id of the
    # buyer's order document.
    po_reference = _first_txt(_first(txn, "BuyerOrderReferencedDocument"), "IssuerAssignedID")

    settlement = _first(txn, "ApplicableHeaderTradeSettlement")
    currency = (
        _first_txt(settlement, "InvoiceCurrencyCode") if settlement is not None else None
    ) or "EUR"

    supplier_iban = received_bank.from_candidates(
        (
            _txt(_direct(account, "IBANID"))
            for means in (
                _direct_all(settlement, "SpecifiedTradeSettlementPaymentMeans")
                if settlement is not None
                else []
            )
            for account in _direct_all(means, "PayeePartyCreditorFinancialAccount")
        ),
        warnings,
    )

    lines: list[LineItemIn] = []
    for li in _first_all(txn, "IncludedSupplyChainTradeLineItem"):
        desc = _first_txt(li, "Name") or "Item"
        qty = _dec(_first_txt(li, "BilledQuantity")) or Decimal("1")
        unit = _dec(_first_txt(li, "ChargeAmount"))
        amount = _dec(_first_txt(li, "LineTotalAmount"))
        if unit == 0 and qty:
            unit = (amount / qty) if qty else amount
        percent = _dec(_first_txt(li, "RateApplicablePercent"))
        parsed_line = _line(desc, qty, unit, amount, percent)
        line_settlement = _direct(li, "SpecifiedLineTradeSettlement")
        line_tax = (
            _direct(line_settlement, "ApplicableTradeTax") if line_settlement is not None else None
        )
        line_sum = (
            _direct(line_settlement, "SpecifiedTradeSettlementLineMonetarySummation")
            if line_settlement is not None
            else None
        )
        parsed_line.captured_tax_amount = _captured_amount(
            _direct(line_tax, "CalculatedAmount") if line_tax is not None else None,
            currency,
            warnings,
            "Line tax",
        )
        parsed_line.captured_gross_amount = _captured_amount(
            _direct(line_sum, "GrandTotalAmount") if line_sum is not None else None,
            currency,
            warnings,
            "Line gross",
        )
        lines.append(parsed_line)

    stated_total = None
    if settlement is not None:
        summ = _first(settlement, "SpecifiedTradeSettlementHeaderMonetarySummation")
        if summ is not None:
            stated_total = (
                _dec(_first_txt(summ, "GrandTotalAmount") or _first_txt(summ, "DuePayableAmount"))
                or None
            )

    return _draft(
        number,
        issue,
        None,
        currency,
        vendor,
        lines,
        filename,
        warnings,
        stated_total,
        po_reference,
        supplier_registration_number=registration,
        supplier_vat_id=vat_id,
        supplier_iban=supplier_iban,
    )


# --------------------------------------------------------------------------- #
# Shared construction
# --------------------------------------------------------------------------- #
def _captured_amount(
    el: Element | None, currency: str, warnings: list[str], label: str
) -> Decimal | None:
    if el is None:
        return None
    stated_currency = el.get("currencyID")
    if stated_currency is not None and stated_currency.upper() != currency.upper():
        warnings.append(
            f"{label}: currency differs from the invoice; left missing for source review."
        )
        return None
    return received_lines.capture_amount(_txt(el), label, warnings)


def _line(desc: str, qty: Decimal, unit: Decimal, amount: Decimal, percent: Decimal) -> LineItemIn:
    if amount == 0 and qty and unit:
        amount = qty * unit
    return LineItemIn(
        description=(desc or "Item")[:500],
        category="uncategorized",
        quantity=qty,
        unit_price=unit,
        amount=amount,
        tax_rate=percent if percent <= 100 else Decimal("0"),
    )


def _draft(
    number: str,
    issue: date | None,
    due: date | None,
    currency: str,
    vendor: str | None,
    lines: list[LineItemIn],
    filename: str,
    warnings: list[str],
    stated_total: Decimal | None,
    po_reference: str | None = None,
    *,
    supplier_registration_number: str | None = None,
    supplier_vat_id: str | None = None,
    supplier_iban: str | None = None,
) -> ParsedInvoiceDraft:
    for index, line in enumerate(lines):
        received_lines.warn(line, warnings, index)
    if issue is None:
        issue = date.today()
        warnings.append("Issue date missing from the XML; defaulted to today.")
    if not lines:
        warnings.append("No invoice lines found in the XML.")
    if stated_total is not None:
        warnings.append(
            f"Document payable total: {currency} {stated_total} — reconcile against the lines."
        )

    draft = InvoiceCreate(
        vendor_name=vendor,
        supplier_iban=supplier_iban,
        invoice_number=number,
        issue_date=issue,
        due_date=due,
        currency=(currency or "EUR")[:3].upper(),
        source_filename=filename,
        # Trimmed to the column width, like every other captured string here: a
        # supplier's oversized order reference must not fail the whole intake on
        # a field nobody is required to send.
        po_reference=(po_reference.strip()[:60] or None) if po_reference else None,
        supplier_registration_number=(supplier_registration_number.strip()[:80] or None)
        if supplier_registration_number
        else None,
        supplier_vat_id=(supplier_vat_id.strip()[:80] or None) if supplier_vat_id else None,
        line_items=lines,
    )
    return ParsedInvoiceDraft(draft=draft, warnings=warnings, method="e-invoice-xml")


def _stem(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0][:120] or "INV"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def looks_like_einvoice(content: bytes) -> bool:
    head = content[:4096].lstrip()
    if not head.startswith(b"<"):
        return False
    return (
        b"CrossIndustryInvoice" in content[:8192]
        or b":Invoice-2" in content[:8192]
        or b"<Invoice" in content[:8192]
        or b"<CreditNote" in content[:8192]
    )


def parse_xml_bytes(content: bytes, filename: str) -> ParsedInvoiceDraft:
    """Parse standalone UBL or CII invoice XML into a draft. Raises ValueError
    if the XML is malformed or not a recognised e-invoice syntax."""
    try:
        root = fromstring(content)
    except Exception as exc:  # defusedxml raises on unsafe/broken XML
        raise ValueError(f"Invalid or unsafe XML: {exc}")

    name = _ln(root.tag)
    if name in ("Invoice", "CreditNote"):
        return _parse_ubl(root, filename)
    if name == "CrossIndustryInvoice":
        return _parse_cii(root, filename)
    raise ValueError(
        f"Unrecognised e-invoice XML root <{name}>. Expected UBL Invoice/CreditNote "
        "or UN-CEFACT CrossIndustryInvoice."
    )


def extract_embedded_xml(pdf_bytes: bytes) -> bytes | None:
    """Return the embedded e-invoice XML from a Factur-X/ZUGFeRD hybrid PDF,
    or None if there is no such attachment."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return None
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        attachments = reader.attachments  # {name: [bytes, ...]}
    except Exception:
        return None

    # Prefer the canonical filenames, then any XML that looks like an e-invoice.
    preferred = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "cii.xml", "invoice.xml")
    items = {name.lower(): blobs for name, blobs in (attachments or {}).items() if blobs}
    for key in preferred:
        if key in items:
            return items[key][0]
    for name, blobs in items.items():
        if name.endswith(".xml") and looks_like_einvoice(blobs[0]):
            return blobs[0]
    return None
