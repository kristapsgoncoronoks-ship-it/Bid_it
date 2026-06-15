"""
EMBEDDED-FINANCE SEAM — factoring the VAT-refund receivable (origination only).

A tax-authority VAT refund (Dir. 2008/9/EC) is a high-certainty receivable: a claim
that has been FILED but not yet PAID by the foreign tax authority. That certainty is
exactly what makes it financeable — a licensed factoring partner can advance most of
the cash today against the receivable and collect from the state on settlement.

This module is the IN-REPO SEAM for that monetisation (strategy: the platform owns
ORIGINATION — our data, our UI; the licensed PARTNER carries the lending/factoring
licence and the balance-sheet risk). We do NOT hold a licence and do NOT integrate a
specific partner here. The DEFAULT provider is NULL: no financing happens, the numbers
are purely informational, and a real provider can be plugged in later behind the same
`FinanceProvider` interface.

It is ADDITIVE ANALYTICS over the existing recovery data:
  * it never touches or changes a VAT figure, gate, lock, or the claim lifecycle;
  * `financeable()` REUSES vat_refund.recovery_report() — the financeable total IS the
    same `summary["outstanding"]` (submitted+approved, not yet paid) the recovery page
    already shows, so the two reconcile exactly;
  * `quote()` is a pure, deterministic advance-economics calculator;
  * the only state it owns is a minimal advances ledger in finance.db (finance-owned —
    NOT an engine product DB), which with the NULL provider just records advance INTENT.

Money is quantized via money.f2 (ROUND_HALF_UP). HTML callers escape. The public read
functions (`financeable`, `terms`, `quote`, `list_advances`) never raise — on a broken
or missing DB they degrade to empty/default values and log via applog.
"""
import os
import sqlite3

import applog
import money

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("FINANCE_DB", f"{WORKDIR}/finance.db")

log = applog.get("finance")

# ----- terms defaults & sane clamps -----------------------------------------
ADVANCE_PCT_DEFAULT = 0.90       # fraction of the receivable advanced now
FEE_PCT_DEFAULT = 0.02           # factoring fee as a fraction of the receivable

import db_migrate
import tenancy

# advances ledger — captures every advance request and its provider outcome. With the
# NULL provider these rows record INTENT (status 'no_provider'); a real provider records
# the partner's accept/reject. APPEND new columns at the END of this list (db_migrate).
_MIGRATIONS = [
    """CREATE TABLE IF NOT EXISTS advances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_key TEXT,
        amount_eur REAL,
        fee_eur REAL,
        provider TEXT,
        status TEXT,
        ref TEXT,
        message TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    # P1 multi-tenancy (schema plumbing only): stamp the finance-owned advances ledger
    # with a tenant_id; existing rows backfill to DEFAULT_TENANT_ID via the column
    # DEFAULT, new rows default too. NO query reads this column yet (the `multitenant`
    # switch is OFF and scope_clause is unwired until P2), so this is a pure
    # no-behavior-change addition. TEXT is audit-safe. APPEND-ONLY — keep at END.
    *tenancy.tenant_column_ddls(["advances"]),
]
_READY = set()


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    if DB == ":memory:" or DB not in _READY:
        db_migrate.apply(con, "finance", _MIGRATIONS)
        if DB != ":memory:":
            _READY.add(DB)
    return con


# ============================================================================
# Provider seam
# ============================================================================
class FinanceProvider:
    """Interface a licensed factoring partner implements to receive advance requests.

    A real provider (e.g. a Factris-style partner: OUR data + UI, the PARTNER's API and
    licence) would, in `submit_advance`, call out to the partner over an authenticated,
    out-of-band channel and return the partner's decision. NOTHING in this repo makes
    such a call — that integration is the licensed partner's plug-in point.
    """
    name = "base"

    def submit_advance(self, claim_key, amount_eur, meta):
        """Request an advance against `claim_key` for `amount_eur` (EUR). `meta` carries
        context (receivable, fee, terms, actor). Returns a dict with at least
        {ok, ref, status, message}."""
        raise NotImplementedError


class NullProvider(FinanceProvider):
    """The DEFAULT. No financing partner is configured, so no money moves — an advance
    request is recorded as INTENT only and reports `no_provider`."""
    name = "none"

    def submit_advance(self, claim_key, amount_eur, meta):
        return {"ok": False, "ref": None, "status": "no_provider",
                "message": "No financing partner configured — informational only."}


# >>> LICENSED-PARTNER SEAM <<<
# To wire a real factoring partner, add a `FinanceProvider` subclass whose
# `submit_advance` calls the partner's API (out-of-band, authenticated, audited — never
# inline secrets), register it in `_PROVIDERS` below, and select it via the
# `finance_provider` app setting. The platform stays origination-only: the partner holds
# the lending/factoring licence and the balance-sheet risk. Do NOT implement a real
# partner call in this repo.
_PROVIDERS = {
    "none": NullProvider,
}


def provider():
    """The configured FinanceProvider. Defaults to NullProvider; an unknown/garbled
    setting also falls back to NullProvider (never raises)."""
    key = "none"
    try:
        import auth
        key = (auth.get_setting("finance_provider", "none") or "none").strip().lower()
    except Exception as e:
        log.warning("finance: could not read finance_provider setting: %s", e)
    cls = _PROVIDERS.get(key, NullProvider)
    return cls()


# ============================================================================
# Terms
# ============================================================================
def _clamp(value, lo, hi, default, *, lo_inclusive=True, hi_inclusive=True):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                                          # NaN
        return default
    if (v < lo) or (not lo_inclusive and v == lo):
        return default
    if (v > hi) or (not hi_inclusive and v == hi):
        return default
    return v


def terms():
    """Advance/fee terms from app settings, each clamped to a sane range. Never raises.
      advance_pct in (0, 1]   (default 0.90) — fraction of the receivable advanced now
      fee_pct     in [0, 0.5) (default 0.02) — factoring fee as a fraction of the receivable
    Out-of-range or unparseable settings fall back to the default."""
    adv, fee = ADVANCE_PCT_DEFAULT, FEE_PCT_DEFAULT
    try:
        import auth
        adv = _clamp(auth.get_setting("finance_advance_pct", ADVANCE_PCT_DEFAULT),
                     0.0, 1.0, ADVANCE_PCT_DEFAULT, lo_inclusive=False, hi_inclusive=True)
        fee = _clamp(auth.get_setting("finance_fee_pct", FEE_PCT_DEFAULT),
                     0.0, 0.5, FEE_PCT_DEFAULT, lo_inclusive=True, hi_inclusive=False)
    except Exception as e:
        log.warning("finance: could not read terms settings: %s", e)
    return {"advance_pct": adv, "fee_pct": fee}


# ============================================================================
# Financeable base & advance economics
# ============================================================================
def financeable(year=None):
    """The financeable receivable base — claims FILED but NOT YET PAID by the tax
    authority (submitted/approved). REUSES vat_refund.recovery_report(year): `total`
    IS the SAME `summary["outstanding"]` the recovery page shows, so the two reconcile
    exactly (never re-summed differently). Read-only; never raises.

    Returns {"rows": [{entity, country, period, vat_eur, status, age_days}, ...],
             "total": <outstanding EUR>}.
    """
    try:
        import vat_refund
        out, summary = vat_refund.recovery_report(year)
        rows = [{"entity": o.get("entity"), "country": o.get("country"),
                 "period": o.get("period"), "vat_eur": o.get("vat_eur"),
                 "status": o.get("status"), "age_days": o.get("age_days")}
                for o in out if o.get("status") in ("submitted", "approved")]
        return {"rows": rows, "total": summary.get("outstanding", 0.0)}
    except Exception as e:
        log.warning("finance: financeable() failed, returning empty: %s", e)
        return {"rows": [], "total": 0.0}


def quote(receivable_eur, terms=None):
    """Advance economics for a receivable, deterministic and pure (money.f2 EUR).

      advance                   = receivable * advance_pct
      fee                       = receivable * fee_pct
      net_now                   = advance - fee            (cash the client receives now)
      remainder_on_settlement   = receivable - advance     (released when the state pays)

    `terms` defaults to the configured terms(). Returns all four figures plus the terms
    used. Does NOT raise on a bad amount (treats it as 0)."""
    t = terms if terms is not None else globals()["terms"]()
    adv_pct = t["advance_pct"]
    fee_pct = t["fee_pct"]
    r = money.D(receivable_eur)
    advance = money.f2(r * money.D(adv_pct))
    fee = money.f2(r * money.D(fee_pct))
    net_now = money.f2(money.D(advance) - money.D(fee))
    remainder = money.f2(r - money.D(advance))
    return {"receivable_eur": money.f2(r), "advance_eur": advance, "fee_eur": fee,
            "net_now_eur": net_now, "remainder_on_settlement_eur": remainder,
            "terms": {"advance_pct": adv_pct, "fee_pct": fee_pct}}


# ============================================================================
# Advances ledger
# ============================================================================
def request_advance(claim_key, amount_eur, fee_eur, actor="system"):
    """Record an advance request against `claim_key` and forward it to the configured
    provider. With the NULL provider this captures INTENT and returns a `no_provider`
    result (no money moves). Records one ledger row with the provider outcome and
    returns the provider result dict {ok, ref, status, message}.

    Never moves a VAT figure or touches a claim — it only writes the finance-owned
    advances ledger. On a ledger-write failure the provider result is still returned
    (the failure is logged), so the seam degrades safely."""
    p = provider()
    meta = {"receivable_eur": money.f2(amount_eur), "fee_eur": money.f2(fee_eur),
            "actor": actor}
    try:
        result = p.submit_advance(claim_key, money.f2(amount_eur), meta)
    except Exception as e:
        log.warning("finance: provider %s.submit_advance failed: %s", p.name, e)
        result = {"ok": False, "ref": None, "status": "error",
                  "message": f"Provider error: {e}"}
    # USER-FACING origination write (Receivables financing page): resolve the bound
    # tenant via write_tenant() BEFORE the degrade-safely try/except — it fails LOUD
    # (require_tenant) if the switch is ON and no concrete tenant is bound, and that
    # loud failure MUST propagate (a tenant-less/owner-scope write is a bug, not an
    # incidental ledger glitch). OFF -> DEFAULT_TENANT_ID (byte-identical to omitting it).
    tid = tenancy.write_tenant()
    try:
        con = connect()
        con.execute(
            """INSERT INTO advances
               (claim_key, amount_eur, fee_eur, provider, status, ref, message, created_by,
                tenant_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (claim_key, money.f2(amount_eur), money.f2(fee_eur), p.name,
             result.get("status"), result.get("ref"),
             (result.get("message") or "")[:500], actor, tid))
        con.commit()
        con.close()
    except Exception as e:
        log.warning("finance: could not record advance for %r: %s", claim_key, e)
    return result


def list_advances():
    """Every recorded advance request, newest first. Never raises (→ [] on a missing or
    broken DB)."""
    try:
        con = connect()
        frag, tp = tenancy.scope_clause()
        rows = con.execute(
            "SELECT id, claim_key, amount_eur, fee_eur, provider, status, ref, message, "
            "created_by, created_at FROM advances WHERE 1=1" + frag
            + " ORDER BY id DESC", tp).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("finance: list_advances() failed, returning empty: %s", e)
        return []
