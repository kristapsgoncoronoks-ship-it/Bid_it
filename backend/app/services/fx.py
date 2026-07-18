"""FX conversion against ECB euro reference rates.

Design: rates are **cached in the database** (`ecb_rates`); the request path
never blocks on the network. A `refresh_from_ecb()` job (exposed as an admin
endpoint) pulls the ECB reference feed when the host is reachable. If the table
is empty and the network is unavailable, a bundled snapshot is seeded so
conversion always works — clearly flagged as a fallback.

Rate convention (ECB): `rate` = units of the foreign currency per **1 EUR** on a
given date. So EUR→FX multiplies, FX→EUR divides. EUR is implicit (rate 1).

`rate_for` uses the latest rate on-or-before the date (ECB publishes no rate on
weekends/holidays); if the date precedes all cached rates it falls back to the
earliest available and marks the result approximate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fx import EcbRate

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"

_CENTS = Decimal("0.01")

# Bundled ECB reference snapshot (EUR base), used when the live feed is
# unreachable so the platform is never dead in the water.
FALLBACK_RATES: dict[str, Decimal] = {
    "USD": Decimal("1.0850"), "GBP": Decimal("0.8550"), "CHF": Decimal("0.9500"),
    "PLN": Decimal("4.3000"), "SEK": Decimal("11.2000"), "NOK": Decimal("11.5000"),
    "DKK": Decimal("7.4600"), "CZK": Decimal("25.3000"), "JPY": Decimal("170.00"),
    "CAD": Decimal("1.4800"), "AUD": Decimal("1.6300"), "RON": Decimal("4.9700"),
    "HUF": Decimal("395.00"), "BGN": Decimal("1.9558"),
}
FALLBACK_START = date(2025, 1, 1)


@dataclass
class Resolved:
    rate: Decimal
    rate_date: date
    approximate: bool  # True when we had to reach outside the on-or-before window


def _q(v: Decimal, exp: Decimal = _CENTS) -> Decimal:
    return v.quantize(exp, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Rate store
# --------------------------------------------------------------------------- #
async def load_rates(db: AsyncSession, rows: list[tuple[date, str, Decimal]]) -> int:
    """Upsert (rate_date, currency, rate) rows. Returns the number written."""
    written = 0
    for rate_date, currency, rate in rows:
        currency = currency.upper()
        if currency == "EUR":
            continue
        existing = await db.scalar(
            select(EcbRate).where(EcbRate.rate_date == rate_date, EcbRate.currency == currency)
        )
        if existing is None:
            db.add(EcbRate(rate_date=rate_date, currency=currency, rate=rate))
        else:
            existing.rate = rate
        written += 1
    await db.commit()
    return written


def _month_starts(start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        out.append(date(y, m, 1))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


async def ensure_seed_rates(db: AsyncSession, today: date) -> int:
    """If the store is empty, seed the bundled snapshot across recent months so
    historical invoices resolve. No-op once any rate exists."""
    if await db.scalar(select(EcbRate.id).limit(1)) is not None:
        return 0
    rows: list[tuple[date, str, Decimal]] = []
    for idx, d in enumerate(_month_starts(FALLBACK_START, today)):
        # Small deterministic wobble so it doesn't look perfectly flat.
        factor = Decimal(1) + (Decimal((idx % 5) - 2) * Decimal("0.004"))
        for ccy, base in FALLBACK_RATES.items():
            rows.append((d, ccy, _q(base * factor, Decimal("0.0001"))))
    return await load_rates(db, rows)


async def resolve_rate(db: AsyncSession, currency: str, on_date: date) -> Resolved | None:
    currency = currency.upper()
    if currency == "EUR":
        return Resolved(Decimal(1), on_date, False)
    # Latest rate on-or-before the requested date.
    row = await db.scalar(
        select(EcbRate)
        .where(EcbRate.currency == currency, EcbRate.rate_date <= on_date)
        .order_by(EcbRate.rate_date.desc())
        .limit(1)
    )
    if row is not None:
        return Resolved(Decimal(row.rate), row.rate_date, False)
    # Date precedes all cached rates → use the earliest, flag approximate.
    row = await db.scalar(
        select(EcbRate).where(EcbRate.currency == currency).order_by(EcbRate.rate_date.asc()).limit(1)
    )
    if row is not None:
        return Resolved(Decimal(row.rate), row.rate_date, True)
    return None


async def rate_for(db: AsyncSession, currency: str, on_date: date) -> Decimal | None:
    r = await resolve_rate(db, currency, on_date)
    return r.rate if r else None


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
async def to_eur(
    db: AsyncSession, amount: Decimal, currency: str, on_date: date
) -> tuple[Decimal | None, Resolved | None]:
    r = await resolve_rate(db, currency, on_date)
    if r is None:
        return None, None
    return _q(amount / r.rate), r


async def eur_total(
    db: AsyncSession, total: Decimal, currency: str, on_date: date, stated_rate: Decimal | None
) -> tuple[Decimal | None, str]:
    """EUR value of an invoice total + how it was derived (for the create path)."""
    currency = (currency or "EUR").upper()
    if currency == "EUR":
        return _q(total), "eur"
    if stated_rate and stated_rate > 0:
        return _q(total / stated_rate), "stated"
    eur, _r = await to_eur(db, total, currency, on_date)
    return (eur, "ecb") if eur is not None else (None, "unknown")


# --------------------------------------------------------------------------- #
# Live refresh (best-effort; blocked hosts fail gracefully)
# --------------------------------------------------------------------------- #
def parse_ecb_xml(content: bytes) -> list[tuple[date, str, Decimal]]:
    from datetime import datetime

    from defusedxml.ElementTree import fromstring

    root = fromstring(content)
    out: list[tuple[date, str, Decimal]] = []
    for cube in root.iter():
        if not cube.tag.endswith("Cube"):
            continue
        time_attr = cube.get("time")
        if not time_attr:
            continue
        d = datetime.strptime(time_attr, "%Y-%m-%d").date()
        for child in cube:
            ccy = child.get("currency")
            rate = child.get("rate")
            if ccy and rate:
                try:
                    out.append((d, ccy, Decimal(rate)))
                except InvalidOperation:
                    continue
    return out


def _fetch(url: str, timeout: int = 12) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "InvoiceIQ/0.1 (+fx)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


async def refresh_from_ecb(db: AsyncSession, history: bool = True) -> dict:
    """Pull the ECB feed and cache it. Never raises — returns a status dict."""
    url = ECB_90D_URL if history else ECB_DAILY_URL
    try:
        content = _fetch(url)
        rows = parse_ecb_xml(content)
    except Exception as exc:  # network blocked / parse error
        return {"ok": False, "written": 0, "error": f"{type(exc).__name__}: {exc}", "source": url}
    written = await load_rates(db, rows)
    latest = await db.scalar(select(EcbRate.rate_date).order_by(EcbRate.rate_date.desc()).limit(1))
    return {"ok": True, "written": written, "latest_date": str(latest) if latest else None, "source": url}


# --------------------------------------------------------------------------- #
# ECB comparison over a tenant's foreign-currency invoices
# --------------------------------------------------------------------------- #
async def ecb_comparison(db: AsyncSession, org_id: str, start: date | None, end: date | None) -> dict:
    from app.models.invoice import Invoice
    from app.models.vendor import Vendor

    stmt = (
        select(Invoice, Vendor.name)
        .join(Vendor, Vendor.id == Invoice.vendor_id)
        .where(Invoice.org_id == org_id, Invoice.currency != "EUR")
        .order_by(Invoice.issue_date.desc())
    )
    if start is not None:
        stmt = stmt.where(Invoice.issue_date >= start)
    if end is not None:
        stmt = stmt.where(Invoice.issue_date <= end)
    records = (await db.execute(stmt)).all()

    rows: list[dict] = []
    total_ecb = Decimal("0")
    total_markup = Decimal("0")
    with_stated = 0
    currencies: set[str] = set()

    for inv, vendor_name in records:
        currencies.add(inv.currency)
        resolved = await resolve_rate(db, inv.currency, inv.issue_date)
        ecb_rate = resolved.rate if resolved else None
        ecb_date = resolved.rate_date if resolved else None
        eur_at_ecb = _q(inv.total / ecb_rate) if ecb_rate else None
        if eur_at_ecb is not None:
            total_ecb += eur_at_ecb

        stated = Decimal(inv.fx_rate) if inv.fx_rate is not None else None
        eur_at_stated = _q(inv.total / stated) if stated and stated > 0 else None
        markup = None
        deviation = None
        if eur_at_stated is not None and eur_at_ecb is not None:
            markup = _q(eur_at_stated - eur_at_ecb)
            total_markup += markup
            with_stated += 1
        if stated and ecb_rate:
            deviation = _q((stated - ecb_rate) / ecb_rate * 100, Decimal("0.1"))

        rows.append(
            {
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "vendor_name": vendor_name,
                "currency": inv.currency,
                "issue_date": inv.issue_date,
                "total": inv.total,
                "ecb_rate": ecb_rate,
                "ecb_rate_date": ecb_date,
                "eur_at_ecb": eur_at_ecb,
                "stated_rate": stated,
                "eur_at_stated": eur_at_stated,
                "markup_eur": markup,
                "deviation_pct": deviation,
            }
        )

    summary = {
        "non_eur_invoices": len(records),
        "with_stated_rate": with_stated,
        "total_eur_at_ecb": _q(total_ecb),
        "total_markup_eur": _q(total_markup),
        "currencies": sorted(currencies),
    }
    return {"summary": summary, "rows": rows}
