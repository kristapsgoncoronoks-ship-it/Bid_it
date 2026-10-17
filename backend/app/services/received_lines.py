"""Received-line calculation and explicit captured-versus-calculated evidence."""

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from app.core.money import q2
from app.models.invoice import LineItem
from app.schemas.approval import LineIn
from app.schemas.invoice import LineItemIn

CAPTURE_KEYS = {
    "captured_tax_amount": ("captured_tax_amount", "line_tax_amount", "tax_amount"),
    "captured_gross_amount": ("captured_gross_amount", "line_gross", "gross_amount"),
}


def source_cell(raw: dict, field: str):
    values = {str(k).strip().lower(): v for k, v in raw.items()}
    return next((values[k] for k in CAPTURE_KEYS[field] if values.get(k) not in (None, "")), None)


def capture_amount(raw, label: str, warnings: list[str]) -> Decimal | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        amount = Decimal(str(raw).replace(",", "").strip())
        if (
            not amount.is_finite()
            or amount < 0
            or amount >= Decimal("1000000000000")
            or q2(amount) != amount
        ):
            raise ValueError("not a bounded nonnegative cent amount")
        return q2(amount)
    except (InvalidOperation, ValueError):
        warnings.append(f"{label}: stated amount is invalid; left missing for source review.")
        return None


def calculated(line: LineItemIn | LineIn) -> tuple[Decimal, Decimal, Decimal]:
    net = q2(line.amount if line.amount is not None else line.quantity * line.unit_price)
    tax = q2(net * line.tax_rate / Decimal("100"))
    return net, tax, q2(net + tax)


def warn(line: LineItemIn | LineIn, warnings: list[str], index: int) -> None:
    _, tax, gross = calculated(line)
    if (line.captured_tax_amount is not None and line.captured_tax_amount != tax) or (
        line.captured_gross_amount is not None and line.captured_gross_amount != gross
    ):
        warnings.append(
            f"Line {index + 1}: captured tax/gross differs from calculated amounts; review the source. Captured values do not replace invoice totals."
        )


def build(lines: Iterable[LineItemIn | LineIn]) -> tuple[list[LineItem], Decimal, Decimal]:
    items: list[LineItem] = []
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for line in lines:
        net, tax, gross = calculated(line)
        items.append(
            LineItem(
                description=line.description,
                category=line.category or "uncategorized",
                quantity=line.quantity,
                unit_price=line.unit_price,
                amount=net,
                tax_rate=line.tax_rate,
                tax_amount=tax,
                gross_amount=gross,
                captured_tax_amount=q2(line.captured_tax_amount)
                if line.captured_tax_amount is not None
                else None,
                captured_gross_amount=q2(line.captured_gross_amount)
                if line.captured_gross_amount is not None
                else None,
            )
        )
        subtotal += net
        tax_total += tax
    return items, q2(subtotal), q2(tax_total)


def out(line: LineItem) -> dict:
    result = {
        key: getattr(line, key)
        for key in (
            "id",
            "description",
            "category",
            "quantity",
            "unit_price",
            "amount",
            "tax_rate",
            "tax_amount",
            "gross_amount",
            "captured_tax_amount",
            "captured_gross_amount",
        )
    }
    comparisons = [
        captured == calculated
        for captured, calculated in (
            (line.captured_tax_amount, line.tax_amount),
            (line.captured_gross_amount, line.gross_amount),
        )
        if captured is not None and calculated is not None
    ]
    result["source_amounts_match"] = all(comparisons) if comparisons else None
    return result


def source_evidence(lines: Iterable[LineItem]) -> list[dict]:
    return [
        {
            "line_index": index,
            "captured_tax_amount": str(line.captured_tax_amount)
            if line.captured_tax_amount is not None
            else None,
            "captured_gross_amount": str(line.captured_gross_amount)
            if line.captured_gross_amount is not None
            else None,
        }
        for index, line in enumerate(lines)
    ]


def vat_breakdown(invoice) -> dict:
    """Sum persisted line amounts; never reconstruct missing historical snapshots.

    Rounding each rate's aggregate base would change already saved line-rounding
    results. Captured source amounts are evidence and do not replace calculations.
    """
    groups: dict[Decimal, dict] = {}
    for line in invoice.line_items:
        rate = q2(line.tax_rate)
        group = groups.setdefault(
            rate,
            {
                "tax_rate": rate,
                "net": Decimal("0"),
                "tax": Decimal("0"),
                "gross": Decimal("0"),
                "line_count": 0,
            },
        )
        group["line_count"] += 1
        group["net"] += line.amount
        for field, value in (("tax", line.tax_amount), ("gross", line.gross_amount)):
            group[field] = None if value is None or group[field] is None else group[field] + value
    rates = [groups[rate] for rate in sorted(groups)]
    complete = bool(rates) and all(r["tax"] is not None and r["gross"] is not None for r in rates)
    matches = None
    if complete:
        matches = all(
            sum((r[field] for r in rates), Decimal("0")) == header
            for field, header in (
                ("net", invoice.subtotal),
                ("tax", invoice.tax_amount),
                ("gross", invoice.total),
            )
        )
    return {"rates": rates, "complete": complete, "matches_header": matches}
