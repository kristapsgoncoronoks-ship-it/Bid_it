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
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import q as _q
from app.models.fx import EcbRate

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_90D_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"

# --------------------------------------------------------------------------- #
# European currency registry — every European currency, valued against the EUR.
# `ecb=True` currencies are published in the ECB daily reference feed (kept live
# by refresh_from_ecb). `ecb=False` currencies are NOT in the ECB feed (Balkans,
# Eastern-Europe/Caucasus neighbours); we carry an INDICATIVE EUR rate for them so
# the module still converts every European currency — flagged approximate.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Currency:
    code: str
    name: str
    ecb: bool  # published in the ECB reference feed


EUROPEAN_CURRENCIES: tuple[Currency, ...] = (
    Currency("EUR", "Euro", True),
    # EU / EEA / EFTA + UK — all in the ECB reference feed.
    Currency("BGN", "Bulgarian lev", True),
    Currency("CZK", "Czech koruna", True),
    Currency("DKK", "Danish krone", True),
    Currency("GBP", "Pound sterling", True),
    Currency("HUF", "Hungarian forint", True),
    Currency("PLN", "Polish złoty", True),
    Currency("RON", "Romanian leu", True),
    Currency("SEK", "Swedish krona", True),
    Currency("CHF", "Swiss franc", True),
    Currency("ISK", "Icelandic króna", True),
    Currency("NOK", "Norwegian krone", True),
    Currency("TRY", "Turkish lira", True),
    # Wider Europe — NOT in the ECB feed → indicative EUR rate.
    Currency("RSD", "Serbian dinar", False),
    Currency("BAM", "Bosnia-Herzegovina convertible mark", False),
    Currency("MKD", "Macedonian denar", False),
    Currency("ALL", "Albanian lek", False),
    Currency("MDL", "Moldovan leu", False),
    Currency("UAH", "Ukrainian hryvnia", False),
    Currency("GEL", "Georgian lari", False),
    Currency("AMD", "Armenian dram", False),
    Currency("AZN", "Azerbaijani manat", False),
    Currency("BYN", "Belarusian ruble", False),
    Currency("RUB", "Russian ruble", False),
    Currency("GIP", "Gibraltar pound", False),
)
EUROPEAN_BY_CODE: dict[str, Currency] = {c.code: c for c in EUROPEAN_CURRENCIES}
# Currencies whose cached rate is indicative (never an official ECB reference).
NON_ECB_EUROPEAN: frozenset[str] = frozenset(c.code for c in EUROPEAN_CURRENCIES if not c.ecb)

# Bundled snapshot (units per 1 EUR), used when the live ECB feed is unreachable
# AND as the standing indicative rate for the non-ECB European currencies. Values
# are approximate; the ECB refresh overwrites the ecb=True ones with live rates.
FALLBACK_RATES: dict[str, Decimal] = {
    # European — ECB-published
    "GBP": Decimal("0.8550"), "CHF": Decimal("0.9500"), "PLN": Decimal("4.3000"),
    "SEK": Decimal("11.2000"), "NOK": Decimal("11.5000"), "DKK": Decimal("7.4600"),
    "CZK": Decimal("25.3000"), "RON": Decimal("4.9700"), "HUF": Decimal("395.00"),
    "BGN": Decimal("1.9558"), "ISK": Decimal("150.00"), "TRY": Decimal("40.000"),
    # European — indicative (not in the ECB feed)
    "RSD": Decimal("117.00"), "BAM": Decimal("1.9558"), "MKD": Decimal("61.500"),
    "ALL": Decimal("98.000"), "MDL": Decimal("19.500"), "UAH": Decimal("45.000"),
    "GEL": Decimal("3.0500"), "AMD": Decimal("430.00"), "AZN": Decimal("1.8500"),
    "BYN": Decimal("3.6000"), "RUB": Decimal("100.00"), "GIP": Decimal("0.8550"),
    # A few global majors, so non-EU trade still converts.
    "USD": Decimal("1.0850"), "JPY": Decimal("170.00"), "CAD": Decimal("1.4800"),
    "AUD": Decimal("1.6300"),
}
FALLBACK_START = date(2025, 1, 1)


@dataclass
class Resolved:
    rate: Decimal
    rate_date: date
    approximate: bool  # True when we had to reach outside the on-or-before window


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


async def ensure_european_coverage(db: AsyncSession, today: date) -> int:
    """Guarantee every European currency has a cached rate. Idempotent and
    ADDITIVE — seeds only currencies with no row at all (so it never clobbers
    live ECB data), across recent months so historical invoices resolve. Runs on
    every boot, so installs that already hold ECB rates still gain the non-ECB
    European currencies."""
    existing = set(await db.scalars(select(EcbRate.currency).distinct()))
    missing = [
        c.code for c in EUROPEAN_CURRENCIES
        if c.code != "EUR" and c.code not in existing and c.code in FALLBACK_RATES
    ]
    if not missing:
        return 0
    rows: list[tuple[date, str, Decimal]] = []
    for d in _month_starts(FALLBACK_START, today):
        for ccy in missing:
            rows.append((d, ccy, FALLBACK_RATES[ccy]))
    return await load_rates(db, rows)


async def resolve_rate(db: AsyncSession, currency: str, on_date: date) -> Resolved | None:
    currency = currency.upper()
    if currency == "EUR":
        return Resolved(Decimal(1), on_date, False)
    # A non-ECB European currency is ALWAYS indicative, never an official rate.
    indicative = currency in NON_ECB_EUROPEAN
    # Latest rate on-or-before the requested date.
    row = await db.scalar(
        select(EcbRate)
        .where(EcbRate.currency == currency, EcbRate.rate_date <= on_date)
        .order_by(EcbRate.rate_date.desc())
        .limit(1)
    )
    if row is not None:
        return Resolved(Decimal(row.rate), row.rate_date, indicative)
    # Date precedes all cached rates → use the earliest, flag approximate.
    row = await db.scalar(
        select(EcbRate).where(EcbRate.currency == currency).order_by(EcbRate.rate_date.asc()).limit(1)
    )
    if row is not None:
        return Resolved(Decimal(row.rate), row.rate_date, True)
    return None


async def load_rate_series(db: AsyncSession, currencies: set[str]) -> dict[str, list[tuple[date, Decimal]]]:
    """All cached rates for `currencies` in ONE query, newest-first per currency.

    Feeds `resolve_from` so callers that resolve many (currency, date) pairs make
    a single round-trip instead of one query per pair (kills the N+1)."""
    codes = {c.upper() for c in currencies if c and c.upper() != "EUR"}
    if not codes:
        return {}
    rows = await db.execute(
        select(EcbRate.currency, EcbRate.rate_date, EcbRate.rate)
        .where(EcbRate.currency.in_(codes))
        .order_by(EcbRate.currency, EcbRate.rate_date.desc())
    )
    out: dict[str, list[tuple[date, Decimal]]] = {}
    for ccy, rate_date, rate in rows:
        out.setdefault(ccy, []).append((rate_date, Decimal(rate)))
    return out


def resolve_from(series_by_ccy: dict[str, list[tuple[date, Decimal]]], currency: str, on_date: date) -> Resolved | None:
    """Pure equivalent of `resolve_rate` over rows preloaded by `load_rate_series`.

    Same rule: latest rate on-or-before the date; else the earliest (approximate).
    Non-ECB European currencies are always flagged indicative."""
    currency = currency.upper()
    if currency == "EUR":
        return Resolved(Decimal(1), on_date, False)
    series = series_by_ccy.get(currency)  # newest-first
    if not series:
        return None
    indicative = currency in NON_ECB_EUROPEAN
    for rate_date, rate in series:
        if rate_date <= on_date:
            return Resolved(rate, rate_date, indicative)
    rate_date, rate = series[-1]  # earliest cached → outside the window
    return Resolved(rate, rate_date, True)


async def supported_currencies(db: AsyncSession, as_of: date) -> list[dict]:
    """Every European currency vs EUR, with its current cached rate (if any)."""
    series = await load_rate_series(db, {c.code for c in EUROPEAN_CURRENCIES})
    out: list[dict] = []
    for c in EUROPEAN_CURRENCIES:
        r = resolve_from(series, c.code, as_of)
        out.append({
            "code": c.code,
            "name": c.name,
            "ecb": c.ecb,
            "rate": (r.rate if r else None),
            "rate_date": (r.rate_date if r else None),
            "indicative": (r.approximate if r else not c.ecb),
        })
    return out


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

    # Preload every needed currency's rates in one query (no per-invoice N+1).
    currencies: set[str] = {inv.currency for inv, _ in records}
    series = await load_rate_series(db, currencies)

    rows: list[dict] = []
    total_ecb = Decimal("0")
    total_markup = Decimal("0")
    with_stated = 0

    for inv, vendor_name in records:
        resolved = resolve_from(series, inv.currency, inv.issue_date)
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
