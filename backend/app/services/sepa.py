"""SEPA credit-transfer payment file (Phase 17).

Render a supplier payment run into an ISO 20022 `pain.001.001.03` Customer Credit
Transfer Initiation XML — the file a treasurer uploads to the bank to execute the
run. Debtor = our issuer profile; each creditor is a run invoice's vendor (only
those with an IBAN are included). Amounts are NET EUR. Read-only; builds a string.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import bank_id
from app.core.money import q2
from app.models.issuer import IssuerProfile
from app.models.payment_run import PaymentRun
from app.services import payment_run

_NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"


class SepaError(Exception):
    """The run cannot be rendered as SEPA (missing debtor IBAN / no creditor IBANs)."""


@dataclass
class CreditTransfer:
    end_to_end: str
    amount: Decimal
    creditor_name: str
    creditor_iban: str
    creditor_bic: str | None
    remittance: str
    # The currency `amount` is actually denominated in. SEPA credit transfers
    # are EUR-only; build_pain001 REFUSES any other value (WO-8) — the `Ccy`
    # attribute is emitted from here, never hardcoded over a foreign figure.
    currency: str = "EUR"


def _el(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    e = ET.SubElement(parent, tag)
    if text is not None:
        e.text = text
    return e


def build_pain001(
    *,
    msg_id: str,
    created_at: datetime,
    exec_date: date,
    debtor_name: str,
    debtor_iban: str | None,
    debtor_bic: str | None,
    transfers: list[CreditTransfer],
) -> str:
    """Serialize a pain.001.001.03 document. Raises SepaError on missing debtor
    IBAN or an empty transfer list, and on ANY structurally invalid IBAN/BIC.

    The IBAN/BIC re-validation here is deliberate defence in depth (WO-2):
    write-time validation in services/vendors.py can be bypassed by a data
    migration or a direct DB edit, and this file is the last line of defence
    before money moves — an invalid account means NO XML is produced at all,
    never a file with a bad creditor. Fail-closed by design."""
    if not debtor_iban:
        raise SepaError("the issuer profile has no IBAN — set one before exporting SEPA")
    if not transfers:
        raise SepaError("no invoice in this run has a supplier IBAN to pay to")
    if not bank_id.is_valid_iban(debtor_iban):
        raise SepaError("the issuer profile's IBAN is invalid — fix it before exporting SEPA")
    if debtor_bic and not bank_id.is_valid_bic(debtor_bic):
        raise SepaError("the issuer profile's BIC is invalid — fix it before exporting SEPA")
    for t in transfers:
        # WO-8 fail-closed currency gate: a pain.001 SEPA credit transfer is
        # EUR-only. An amount denominated in anything else must never be
        # serialized — labelling a foreign figure EUR instructs the bank to
        # pay the wrong value, so NO XML is produced at all.
        if t.currency != "EUR":
            raise SepaError(
                f"transfer '{t.end_to_end}' is denominated in {t.currency}; a SEPA "
                "credit transfer is EUR-only — the payment file was not produced"
            )
        if not bank_id.is_valid_iban(t.creditor_iban):
            raise SepaError(
                f"creditor '{t.creditor_name}' has a structurally invalid IBAN — "
                "the payment file was not produced"
            )
        if t.creditor_bic and not bank_id.is_valid_bic(t.creditor_bic):
            raise SepaError(
                f"creditor '{t.creditor_name}' has a structurally invalid BIC — "
                "the payment file was not produced"
            )
    total = q2(sum((t.amount for t in transfers), Decimal("0")))

    doc = ET.Element("Document", {"xmlns": _NS})
    cti = _el(doc, "CstmrCdtTrfInitn")

    grp = _el(cti, "GrpHdr")
    _el(grp, "MsgId", msg_id)
    _el(grp, "CreDtTm", created_at.replace(microsecond=0).isoformat())
    _el(grp, "NbOfTxs", str(len(transfers)))
    _el(grp, "CtrlSum", f"{total}")
    _el(_el(grp, "InitgPty"), "Nm", debtor_name)

    pmt = _el(cti, "PmtInf")
    _el(pmt, "PmtInfId", msg_id)
    _el(pmt, "PmtMtd", "TRF")
    _el(pmt, "NbOfTxs", str(len(transfers)))
    _el(pmt, "CtrlSum", f"{total}")
    _el(_el(_el(pmt, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    _el(pmt, "ReqdExctnDt", exec_date.isoformat())
    _el(_el(pmt, "Dbtr"), "Nm", debtor_name)
    _el(_el(_el(pmt, "DbtrAcct"), "Id"), "IBAN", debtor_iban)
    if debtor_bic:
        _el(_el(_el(pmt, "DbtrAgt"), "FinInstnId"), "BIC", debtor_bic)
    _el(pmt, "ChrgBr", "SLEV")

    for t in transfers:
        tx = _el(pmt, "CdtTrfTxInf")
        _el(_el(tx, "PmtId"), "EndToEndId", (t.end_to_end or "NOTPROVIDED")[:35])
        instd = _el(_el(tx, "Amt"), "InstdAmt", f"{q2(t.amount)}")
        # Emitted from the transfer's actual denomination (guarded EUR above) —
        # never a hardcoded label over an unconverted amount (WO-8).
        instd.set("Ccy", t.currency)
        if t.creditor_bic:
            _el(_el(_el(tx, "CdtrAgt"), "FinInstnId"), "BIC", t.creditor_bic)
        _el(_el(tx, "Cdtr"), "Nm", t.creditor_name[:70])
        _el(_el(_el(tx, "CdtrAcct"), "Id"), "IBAN", t.creditor_iban)
        _el(_el(tx, "RmtInf"), "Ustrd", (t.remittance or t.end_to_end)[:140])

    body = ET.tostring(doc, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


async def payment_run_sepa(db: AsyncSession, org_id: str, run: PaymentRun) -> tuple[str, int]:
    """Render a run as SEPA XML. Returns (xml, skipped) where `skipped` counts
    invoices whose vendor has no IBAN (and were therefore excluded). Raises
    SepaError if the issuer has no IBAN or no invoice has a payable creditor."""
    issuer = await db.scalar(select(IssuerProfile).where(IssuerProfile.org_id == org_id))
    debtor_name = (issuer.legal_name if issuer and issuer.legal_name else None) or "Our company"
    invoices = await payment_run.run_invoices(db, org_id, run.id)

    transfers: list[CreditTransfer] = []
    skipped = 0
    for inv in invoices:
        vendor = inv.vendor
        iban = (vendor.iban if vendor else None) or None
        if not iban:
            skipped += 1
            continue
        # WO-8: the reliable EUR figure or a refusal — never the raw foreign
        # total silently relabelled EUR (the verified 1,000-PLN → "EUR 1000.00"
        # defect). eur_of names the invoice and the missing rate.
        try:
            amount = payment_run.eur_of(inv)
        except payment_run.PaymentRunError as exc:
            raise SepaError(str(exc)) from exc
        transfers.append(
            CreditTransfer(
                end_to_end=inv.invoice_number,
                amount=amount,
                creditor_name=vendor.name,
                creditor_iban=iban,
                creditor_bic=vendor.bic,
                remittance=f"Invoice {inv.invoice_number}",
            )
        )

    xml = build_pain001(
        msg_id=f"RUN-{run.id[:8]}",
        created_at=run.created_at,
        exec_date=(run.paid_at.date() if run.paid_at else run.created_at.date()),
        debtor_name=debtor_name,
        debtor_iban=issuer.iban if issuer else None,
        debtor_bic=issuer.bic if issuer else None,
        transfers=transfers,
    )
    return xml, skipped
