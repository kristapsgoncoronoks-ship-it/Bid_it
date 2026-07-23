"""Build EN 16931 / UN-CEFACT CII XML for an issued invoice (Factur-X profile).

This is the outbound counterpart to `einvoice._parse_cii`: the XML produced here
round-trips through our own reader and through other Factur-X consumers. It is
embedded into the invoice PDF (see `invoice_pdf.py`) to make a hybrid document.
"""

from __future__ import annotations

from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

from app.services.vat import VatResult

RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

register_namespace("rsm", RSM)
register_namespace("ram", RAM)
register_namespace("udt", UDT)

# EN 16931 (Factur-X "EN 16931" / Comfort) guideline id.
EN16931_GUIDELINE = "urn:cen.eu:en16931:2017"
INVOICE_TYPE_CODE = "380"  # commercial invoice
CREDIT_NOTE_TYPE_CODE = "381"  # credit note (UNTDID 1001)


def _rsm(tag: str) -> str:
    return f"{{{RSM}}}{tag}"


def _ram(tag: str) -> str:
    return f"{{{RAM}}}{tag}"


def _udt(tag: str) -> str:
    return f"{{{UDT}}}{tag}"


def _t(parent: Element, tag: str, text: str | None, **attrs) -> Element:
    el = SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _amt(v: Decimal) -> str:
    return f"{Decimal(v):.2f}"


def _date(d) -> str:
    return d.strftime("%Y%m%d")


def _party(
    parent: Element, role_tag: str, *, name, vat, line1, line2, city, postal, country
) -> Element:
    party = SubElement(parent, _ram(role_tag))
    _t(party, _ram("Name"), name)
    addr = SubElement(party, _ram("PostalTradeAddress"))
    if postal:
        _t(addr, _ram("PostcodeCode"), postal)
    if line1:
        _t(addr, _ram("LineOne"), line1)
    if line2:
        _t(addr, _ram("LineTwo"), line2)
    if city:
        _t(addr, _ram("CityName"), city)
    if country:
        _t(addr, _ram("CountryID"), country.upper())
    if vat:
        reg = SubElement(party, _ram("SpecifiedTaxRegistration"))
        _t(reg, _ram("ID"), vat, schemeID="VA")
    return party


def build_cii(invoice, seller: dict, vat: VatResult) -> bytes:
    root = Element(_rsm("CrossIndustryInvoice"))

    # --- document context (EN 16931 guideline) ---
    ctx = SubElement(root, _rsm("ExchangedDocumentContext"))
    guide = SubElement(ctx, _ram("GuidelineSpecifiedDocumentContextParameter"))
    _t(guide, _ram("ID"), EN16931_GUIDELINE)

    # --- header ---
    doc = SubElement(root, _rsm("ExchangedDocument"))
    _t(doc, _ram("ID"), invoice.number)
    # 380 = commercial invoice, 381 = credit note (UNTDID 1001).
    is_credit = getattr(invoice, "doc_type", "invoice") == "credit_note"
    _t(doc, _ram("TypeCode"), CREDIT_NOTE_TYPE_CODE if is_credit else INVOICE_TYPE_CODE)
    idt = SubElement(doc, _ram("IssueDateTime"))
    _t(idt, _udt("DateTimeString"), _date(invoice.issue_date), format="102")
    if invoice.note:
        note = SubElement(doc, _ram("IncludedNote"))
        _t(note, _ram("Content"), invoice.note)

    txn = SubElement(root, _rsm("SupplyChainTradeTransaction"))

    # --- line items ---
    for i, line in enumerate(vat.lines, start=1):
        li = SubElement(txn, _ram("IncludedSupplyChainTradeLineItem"))
        doc_line = SubElement(li, _ram("AssociatedDocumentLineDocument"))
        _t(doc_line, _ram("LineID"), str(i))
        prod = SubElement(li, _ram("SpecifiedTradeProduct"))
        _t(prod, _ram("Name"), line["description"])
        agr = SubElement(li, _ram("SpecifiedLineTradeAgreement"))
        price = SubElement(agr, _ram("NetPriceProductTradePrice"))
        # BT-146 net price is post-discount. Without a discount this equals the
        # unit price; with one, derive it from the (discounted) line net so
        # net_price × quantity stays consistent with the line total.
        disc = Decimal(str(line.get("discount_percent", 0) or 0))
        qty = Decimal(str(line["quantity"]))
        if disc > 0 and qty:
            net_price = Decimal(str(line["net_amount"])) / qty
        else:
            net_price = Decimal(str(line["unit_price"]))
        _t(price, _ram("ChargeAmount"), _amt(net_price))
        deliv = SubElement(li, _ram("SpecifiedLineTradeDelivery"))
        _t(deliv, _ram("BilledQuantity"), f"{Decimal(line['quantity']):.3f}", unitCode=line["unit"])
        settle = SubElement(li, _ram("SpecifiedLineTradeSettlement"))
        tax = SubElement(settle, _ram("ApplicableTradeTax"))
        _t(tax, _ram("TypeCode"), "VAT")
        _t(tax, _ram("CategoryCode"), "S" if line["vat_rate"] > 0 else "Z")
        _t(tax, _ram("RateApplicablePercent"), f"{Decimal(line['vat_rate']):.2f}")
        summ = SubElement(settle, _ram("SpecifiedTradeSettlementLineMonetarySummation"))
        _t(summ, _ram("LineTotalAmount"), _amt(line["net_amount"]))

    # --- seller / buyer ---
    hagr = SubElement(txn, _ram("ApplicableHeaderTradeAgreement"))
    _party(
        hagr,
        "SellerTradeParty",
        name=seller.get("legal_name"),
        vat=seller.get("vat_number"),
        line1=seller.get("address_line1"),
        line2=seller.get("address_line2"),
        city=seller.get("city"),
        postal=seller.get("postal_code"),
        country=seller.get("country"),
    )
    _party(
        hagr,
        "BuyerTradeParty",
        name=invoice.buyer_name,
        vat=invoice.buyer_vat_number,
        line1=invoice.buyer_address_line1,
        line2=None,
        city=invoice.buyer_city,
        postal=invoice.buyer_postal_code,
        country=invoice.buyer_country,
    )
    # BT-13 buyer's purchase-order reference.
    if getattr(invoice, "po_reference", None):
        po = SubElement(hagr, _ram("BuyerOrderReferencedDocument"))
        _t(po, _ram("IssuerAssignedID"), invoice.po_reference)

    hdel = SubElement(txn, _ram("ApplicableHeaderTradeDelivery"))
    if invoice.supply_date:
        occ = SubElement(hdel, _ram("ActualDeliverySupplyChainEvent"))
        odt = SubElement(occ, _ram("OccurrenceDateTime"))
        _t(odt, _udt("DateTimeString"), _date(invoice.supply_date), format="102")

    hset = SubElement(txn, _ram("ApplicableHeaderTradeSettlement"))
    _t(hset, _ram("InvoiceCurrencyCode"), invoice.currency)
    if seller.get("iban"):
        pm = SubElement(hset, _ram("SpecifiedTradeSettlementPaymentMeans"))
        _t(pm, _ram("TypeCode"), "58")  # SEPA credit transfer
        acct = SubElement(pm, _ram("PayeePartyCreditorFinancialAccount"))
        _t(acct, _ram("IBANID"), seller["iban"])

    # VAT breakdown (one ApplicableTradeTax per rate). For a zero-rate bucket under
    # a no-VAT scheme, state the exemption reason (BT-120) in CII element order.
    exemption = getattr(invoice, "tax_exemption_reason", None)
    for b in vat.breakdown:
        tax = SubElement(hset, _ram("ApplicableTradeTax"))
        _t(tax, _ram("CalculatedAmount"), _amt(b.vat))
        _t(tax, _ram("TypeCode"), "VAT")
        if b.rate == 0 and exemption:
            _t(tax, _ram("ExemptionReason"), exemption)
        _t(tax, _ram("BasisAmount"), _amt(b.base))
        _t(tax, _ram("CategoryCode"), "S" if b.rate > 0 else "Z")
        _t(tax, _ram("RateApplicablePercent"), f"{Decimal(b.rate):.2f}")

    if invoice.due_date:
        terms = SubElement(hset, _ram("SpecifiedTradePaymentTerms"))
        due = SubElement(terms, _ram("DueDateDateTime"))
        _t(due, _udt("DateTimeString"), _date(invoice.due_date), format="102")

    summ = SubElement(hset, _ram("SpecifiedTradeSettlementHeaderMonetarySummation"))
    _t(summ, _ram("LineTotalAmount"), _amt(vat.subtotal))
    _t(summ, _ram("TaxBasisTotalAmount"), _amt(vat.subtotal))
    _t(summ, _ram("TaxTotalAmount"), _amt(vat.tax_total), currencyID=invoice.currency)
    _t(summ, _ram("GrandTotalAmount"), _amt(vat.total))
    _t(summ, _ram("DuePayableAmount"), _amt(vat.total))

    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="utf-8")
