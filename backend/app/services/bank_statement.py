"""Bank-statement transaction reader for expense imports.

Built on the SAME extraction engine as the invoice transaction reader
(`pdf_ocr`): text-layer-first with an OCR fallback, date masking, row
reconstruction and locale-aware money parsing. It is a *separate* parser because
a statement's rows differ from an invoice's — most importantly the trailing
**running-balance** column, which must not be mistaken for the transaction
amount. We disambiguate with a balance-delta check: on a genuine balance column,
`balance[i] - balance[i-1] ≈ ±amount[i]`, and the sign tells debit from credit.

Output is a *draft* set of expense items (the debits/outflows) that the employee
reviews before saving — the same advisory, human-confirmed pattern as invoices.
CSV statements (the common bank export) are parsed directly.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services import pdf_ocr

_CENTS = Decimal("0.01")
_BALANCE_TOL = Decimal("0.02")


@dataclass
class Txn:
    date: date
    description: str
    amount: Decimal  # positive magnitude
    direction: str  # "debit" (money out) | "credit" (money in)
    balance: Decimal | None = None


@dataclass
class StatementResult:
    method: str  # text-layer | ocr | csv
    transactions: list[Txn]
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# PDF / OCR path (reuses pdf_ocr primitives)
# --------------------------------------------------------------------------- #
def _rows_from_text(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        date_str, rest = pdf_ocr._split_leading_date(line)
        if not date_str:
            continue  # transaction rows start with a date
        d = pdf_ocr._to_date(date_str)
        if d is None:
            continue
        masked = pdf_ocr._mask(rest)
        matches = list(pdf_ocr._MONEY_RE.finditer(masked))
        if not matches:
            continue
        desc = rest[: matches[0].start()].strip(" .\t-|:*")
        amounts = [pdf_ocr._num(m.group()) for m in matches]
        rows.append({"date": d, "description": desc or "Transaction", "amounts": amounts})
    return rows


def _looks_like_running_balance(rows: list[dict]) -> bool:
    """True if the LAST money token behaves like a running balance across rows."""
    matched = total = 0
    prev = None
    for r in rows:
        amts = r["amounts"]
        if len(amts) < 2:
            prev = None
            continue
        cur_balance = amts[-1]
        if prev is not None:
            total += 1
            if abs(abs(cur_balance - prev) - amts[-2]) <= _BALANCE_TOL:
                matched += 1
        prev = cur_balance
    return total > 0 and matched / total >= 0.6


def _transactions_from_rows(rows: list[dict], warnings: list[str]) -> list[Txn]:
    has_balance = _looks_like_running_balance(rows)
    txns: list[Txn] = []
    prev_balance: Decimal | None = None

    for r in rows:
        amts = r["amounts"]
        if has_balance and len(amts) >= 2:
            balance = amts[-1]
            amount = amts[-2]
            if prev_balance is not None:
                direction = "debit" if balance < prev_balance else "credit"
            else:
                direction = "debit"
            prev_balance = balance
        else:
            balance = None
            amount = amts[-1]
            # No running-balance column and no reliable sign → default to an
            # outflow, so the row still appears in the reviewable 'available
            # expenses' inbox (only debits are expensable). The user confirms or
            # discards. Sign-based guessing would be worse here: unsigned positive
            # amounts (the norm on such statements) would look like credits and
            # silently vanish from the inbox.
            direction = "debit"
        txns.append(
            Txn(
                date=r["date"],
                description=r["description"][:300],
                amount=abs(amount).quantize(_CENTS),
                direction=direction,
                balance=balance,
            )
        )

    if not has_balance:
        warnings.append(
            "No running-balance column detected — amounts taken as-is; verify debit/credit."
        )
    return txns


# --------------------------------------------------------------------------- #
# CSV path (common bank export)
# --------------------------------------------------------------------------- #
_DATE_COLS = ("date", "booking date", "value date", "transaction date", "posted")
_DESC_COLS = ("description", "details", "payee", "reference", "narrative", "memo", "counterparty")
_AMOUNT_COLS = ("amount", "value")
_DEBIT_COLS = ("debit", "withdrawal", "paid out", "money out", "out")
_CREDIT_COLS = ("credit", "deposit", "paid in", "money in", "in")


def _pick(fieldmap: dict, names) -> str | None:
    for n in names:
        if n in fieldmap:
            return fieldmap[n]
    return None


def _parse_csv(content: bytes, warnings: list[str]) -> list[Txn]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    fmap = {(f or "").strip().lower(): f for f in reader.fieldnames}
    date_c = _pick(fmap, _DATE_COLS)
    desc_c = _pick(fmap, _DESC_COLS)
    amount_c = _pick(fmap, _AMOUNT_COLS)
    debit_c = _pick(fmap, _DEBIT_COLS)
    credit_c = _pick(fmap, _CREDIT_COLS)
    if not date_c or (not amount_c and not debit_c and not credit_c):
        raise ValueError("CSV must have a date column and an amount (or debit/credit) column")

    txns: list[Txn] = []
    for row in reader:
        d = pdf_ocr._to_date((row.get(date_c) or "").strip())
        if d is None:
            continue
        desc = (row.get(desc_c) or "").strip() if desc_c else "Transaction"
        if amount_c:
            amt = pdf_ocr._num(row.get(amount_c) or "0")
            direction = "credit" if amt > 0 else "debit"
            amount = abs(amt)
        else:
            debit = pdf_ocr._num(row.get(debit_c) or "0") if debit_c else Decimal("0")
            credit = pdf_ocr._num(row.get(credit_c) or "0") if credit_c else Decimal("0")
            if debit:
                direction, amount = "debit", abs(debit)
            elif credit:
                direction, amount = "credit", abs(credit)
            else:
                continue
        if amount == 0:
            continue
        txns.append(
            Txn(
                date=d,
                description=(desc or "Transaction")[:300],
                amount=amount.quantize(_CENTS),
                direction=direction,
            )
        )
    return txns


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# camt.053 path (ISO 20022 bank-to-customer statement, the modern bank export)
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    """The local element name, dropping any XML namespace."""
    return tag.rsplit("}", 1)[-1]


def _looks_like_camt(content: bytes) -> bool:
    head = content[:2000].lower()
    return b"camt.053" in head or b"bktocstmrstmt" in head


def _parse_camt053(content: bytes, warnings: list[str]) -> list[Txn]:
    from defusedxml import ElementTree as DET  # XXE-hardened parse

    try:
        root = DET.fromstring(content)
    except Exception as exc:
        raise ValueError(f"Could not parse camt.053 XML: {exc}") from exc

    def child(el, name):
        for c in el:
            if _local(c.tag) == name:
                return c
        return None

    def descr(el) -> str:
        parts = [
            c.text.strip()
            for c in el.iter()
            if _local(c.tag) in ("Ustrd", "AddtlNtryInf") and c.text and c.text.strip()
        ]
        return "; ".join(parts)[:300] or "Transaction"

    txns: list[Txn] = []
    for ntry in (e for e in root.iter() if _local(e.tag) == "Ntry"):
        amt_el = child(ntry, "Amt")
        cd_el = child(ntry, "CdtDbtInd")
        if amt_el is None or amt_el.text is None or cd_el is None:
            continue
        try:
            amount = Decimal(amt_el.text.strip()).quantize(_CENTS)
        except (ArithmeticError, ValueError):
            continue
        direction = "credit" if (cd_el.text or "").strip().upper() == "CRDT" else "debit"
        # NB: `X or Y` is unsafe for ElementTree elements — an element with no
        # sub-elements is FALSY — so resolve the date holder/field with `is None`.
        dt_holder = child(ntry, "BookgDt")
        if dt_holder is None:
            dt_holder = child(ntry, "ValDt")
        d: date | None = None
        if dt_holder is not None:
            dt_el = child(dt_holder, "Dt")
            if dt_el is None:
                dt_el = child(dt_holder, "DtTm")
            if dt_el is not None and dt_el.text:
                try:
                    d = date.fromisoformat(dt_el.text.strip()[:10])
                except ValueError:
                    d = None
        if d is None:
            continue  # a bookable entry must carry a date
        txns.append(Txn(date=d, description=descr(ntry), amount=amount, direction=direction))
    if not txns:
        raise ValueError("No bookable entries (Ntry) found in the camt.053 statement.")
    return txns


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def parse(filename: str, content: bytes) -> StatementResult:
    warnings: list[str] = []
    lower = filename.lower()
    if lower.endswith(".csv"):
        return StatementResult(
            method="csv", transactions=_parse_csv(content, warnings), warnings=warnings
        )
    if lower.endswith(".xml") or _looks_like_camt(content):
        return StatementResult(
            method="camt.053", transactions=_parse_camt053(content, warnings), warnings=warnings
        )
    if lower.endswith(".pdf"):
        text, method = pdf_ocr.extract_text(content)
        if not text:
            raise ValueError("Could not read any text from the statement PDF.")
        rows = _rows_from_text(text)
        if not rows:
            raise ValueError("No transaction rows recognised in the statement.")
        return StatementResult(
            method=method, transactions=_transactions_from_rows(rows, warnings), warnings=warnings
        )
    raise ValueError("Unsupported file type. Upload a .pdf or .csv bank statement.")
