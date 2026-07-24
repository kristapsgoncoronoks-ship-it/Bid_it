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
    IBAN or an empty transfer list."""
    if not debtor_iban:
        raise SepaError("the issuer profile has no IBAN — set one before exporting SEPA")
    if not transfers:
        raise SepaError("no invoice in this run has a supplier IBAN to pay to")
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
        instd.set("Ccy", "EUR")
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
        amount = q2(Decimal(inv.total_eur if inv.total_eur is not None else (inv.total or 0)))
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
