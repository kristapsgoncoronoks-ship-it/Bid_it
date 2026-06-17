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

It is ADDITIVE ANALYTICS + an ORIGINATION LEDGER over the existing recovery data:
  * it never touches or changes a VAT figure, gate, lock, fee, payment, or the claim
    lifecycle — it ONLY reads recovery_report() and writes its OWN finance.db ledger;
  * `financeable()` REUSES vat_refund.recovery_report() — the financeable total IS the
    same `summary["outstanding"]` (submitted+approved, not yet paid) the recovery page
    already shows, so the two reconcile exactly;
  * `financeable_offers()` is the deepened, per-claim origination MODEL: for each eligible
    open receivable it derives advance / fee / net-now / net-later / expected-payout via a
    transparent, time-priced model (`offer_for`), money.f2;
  * `quote()` is a pure, deterministic advance-economics calculator (the aggregate model);
  * the state it owns is the advances ledger in finance.db (finance-owned — NOT an engine
    product DB). ORIGINATION-ONLY: `offer_advance`/`set_status` MODEL and TRACK an advance
    through {offered, accepted, funded, repaid, declined}; with the NULL provider nothing
    funds and no money moves (rows carry `provider="null"`).

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
ADVANCE_PCT_DEFAULT = 0.90       # fraction of the receivable advanced now (legacy quote())
FEE_PCT_DEFAULT = 0.02           # factoring fee as a fraction of the receivable (legacy quote())

# ----- ORIGINATION model defaults (financeable_offers / offer_advance) -------
# The richer per-claim origination model factors each open receivable individually:
#   advance = advance_rate × eligible
#   fee     = fee_rate_annual × advance × expected_days/365   (a transparent, time-priced
#             discount — the cost of advancing cash for the days until the state pays)
#   net_now = advance − fee     net_later = eligible (the full refund when the state pays)
# All three rates are app-settings configurable and clamped to sane ranges; the
# expected_days is derived from the claim's status (a submitted claim is further from
# payout than an approved one). Money via money.f2.
ADVANCE_RATE_DEFAULT = 0.80        # fraction of an eligible receivable advanced now
FEE_RATE_ANNUAL_DEFAULT = 0.08     # annualised discount/fee rate on the advance
# expected days from now to the state's payout, by recovery status (a coarse, transparent
# default timeline — a real partner would price this from the realization history).
EXPECTED_DAYS_BY_STATUS = {"submitted": 120, "approved": 45}
EXPECTED_DAYS_DEFAULT = 120

# Origination-ledger lifecycle. ORIGINATION-ONLY: these MODEL and TRACK an advance; with
# the NULL provider nothing funds/moves money. 'offered' = the platform modelled an offer
# row; 'accepted' = the client accepted the modelled terms; 'funded'/'repaid' would be a
# real partner's lifecycle (NEVER reached under NullProvider); 'declined' closes it out.
ADVANCE_STATUSES = ("offered", "accepted", "funded", "repaid", "declined")

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
    # ORIGINATION model (this session): the richer per-claim financeable model writes one
    # advances row per (entity, country, period) receivable. These columns capture the
    # modelled offer so the ledger reconciles to financeable_offers(); APPENDED at the END
    # so existing rows backfill to NULL/'EUR' and existing `request_advance` rows are
    # untouched (claim_key=subject, amount_eur=advance, fee_eur=fee). APPEND-ONLY.
    "ALTER TABLE advances ADD COLUMN period TEXT",
    "ALTER TABLE advances ADD COLUMN country TEXT",
    "ALTER TABLE advances ADD COLUMN eligible_eur REAL",
    "ALTER TABLE advances ADD COLUMN currency TEXT DEFAULT 'EUR'",
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
# Origination model — per-claim financeable offers
# ============================================================================
def _clamp_setting(key, lo, hi, default, *, lo_inclusive=True, hi_inclusive=True):
    """Read a numeric app setting and clamp it to [lo, hi]; fall back to `default` on an
    unparseable / out-of-range value. Never raises (read path)."""
    try:
        import auth
        return _clamp(auth.get_setting(key, default), lo, hi, default,
                      lo_inclusive=lo_inclusive, hi_inclusive=hi_inclusive)
    except Exception as e:
        log.warning("finance: could not read setting %s: %s", key, e)
        return default


def offer_terms():
    """ORIGINATION terms for the per-claim model, each clamped to a sane range. Never
    raises. These are SEPARATE from the legacy terms()/quote() pair (which the existing
    aggregate Receivables KPI uses) so neither model perturbs the other.
      advance_rate     in (0, 1]   (default 0.80) — fraction of an eligible receivable
                                                    advanced now
      fee_rate_annual  in [0, 1)   (default 0.08) — annualised discount on the advance
    """
    return {
        "advance_rate": _clamp_setting("finance_advance_rate", 0.0, 1.0,
                                       ADVANCE_RATE_DEFAULT,
                                       lo_inclusive=False, hi_inclusive=True),
        "fee_rate_annual": _clamp_setting("finance_fee_rate_annual", 0.0, 1.0,
                                          FEE_RATE_ANNUAL_DEFAULT,
                                          lo_inclusive=True, hi_inclusive=False),
    }


def _expected_days(status):
    """Coarse, transparent days-to-payout estimate by recovery status."""
    return EXPECTED_DAYS_BY_STATUS.get(status, EXPECTED_DAYS_DEFAULT)


def _expected_payout_date(submitted, status):
    """Expected payout date = submitted_date + expected_days(status). Falls back to
    today + expected_days when there is no submitted_date. Returns an ISO date string,
    or None on a bad date (never raises)."""
    import datetime
    days = _expected_days(status)
    try:
        base = (datetime.date.fromisoformat(submitted) if submitted
                else datetime.date.today())
        return (base + datetime.timedelta(days=days)).isoformat()
    except (ValueError, TypeError) as e:
        log.debug("finance: bad submitted_date %r: %s", submitted, e)
        try:
            return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
        except Exception:
            return None


def is_eligible(status):
    """ELIGIBILITY RULE (documented): a receivable is financeable iff the claim has been
    FILED with the tax authority but NOT YET PAID — i.e. recovery status `submitted` or
    `approved`. A draft / ready / paid / withdrawn claim is NOT financeable (a draft is
    not yet a real receivable; a paid claim is already collected). This is the SAME open
    base recovery_report()/financeable() use, so the origination model never invents a
    receivable the recovery page doesn't show."""
    return status in ("submitted", "approved")


def offer_for(eligible_eur, status, submitted=None, terms=None):
    """The advance economics for ONE eligible receivable (deterministic, money.f2 EUR):

      advance   = eligible × advance_rate
      fee       = fee_rate_annual × advance × expected_days/365   (time-priced discount)
      net_now   = advance − fee          (cash the client receives now)
      net_later = eligible               (the full refund when the state pays — the
                                          alternative to financing: wait, get it all)

    Returns those figures plus expected_days, expected_payout and the terms used. Pure;
    does NOT raise on a bad amount (treats it as 0)."""
    t = terms if terms is not None else offer_terms()
    days = _expected_days(status)
    elig = money.D(eligible_eur)
    advance = money.f2(elig * money.D(t["advance_rate"]))
    fee = money.f2(money.D(t["fee_rate_annual"]) * money.D(advance)
                   * (money.D(days) / money.D(365)))
    net_now = money.f2(money.D(advance) - money.D(fee))
    net_later = money.f2(elig)
    return {"eligible_eur": net_later, "advance_eur": advance, "fee_eur": fee,
            "net_now_eur": net_now, "net_later_eur": net_later,
            "expected_days": days,
            "expected_payout": _expected_payout_date(submitted, status),
            "terms": dict(t)}


def financeable_offers(report=None):
    """The per-claim FINANCEABLE OFFERS modelled from the recovery data — the deepened
    origination view. For each ELIGIBLE open receivable (see is_eligible: submitted or
    approved, filed-but-unpaid), models the advance / fee / net-now / net-later /
    expected-payout via offer_for(). Read-only; never raises (→ empty on any failure).

    `report` may be a pre-fetched recovery_report() result `(out, summary)` (so a caller
    can reuse one fetch); when None it calls vat_refund.recovery_report() itself.

    Returns {"offers": [ {subject, entity, country, period, status, eligible_eur,
                          advance_eur, fee_eur, net_now_eur, net_later_eur,
                          expected_days, expected_payout, age_days}, ... ],
             "totals": {eligible_eur, advance_eur, fee_eur, net_now_eur, net_later_eur},
             "terms": offer_terms()}.
    INELIGIBLE claims (draft/paid/…) are EXCLUDED — never modelled."""
    t = offer_terms()
    try:
        out, _summary = report if report is not None else _recovery()
    except Exception as e:
        log.warning("finance: financeable_offers() could not read recovery: %s", e)
        return {"offers": [], "totals": _zero_totals(), "terms": t}
    offers = []
    for o in out:
        status = o.get("status")
        if not is_eligible(status):
            continue
        entity = o.get("entity"); country = o.get("country"); period = o.get("period")
        econ = offer_for(o.get("vat_eur") or 0, status, o.get("submitted"), t)
        offers.append({
            "subject": _subject(entity, country, period),
            "entity": entity, "country": country, "period": period, "status": status,
            "age_days": o.get("age_days"),
            **{k: econ[k] for k in ("eligible_eur", "advance_eur", "fee_eur",
                                    "net_now_eur", "net_later_eur",
                                    "expected_days", "expected_payout")},
        })
    totals = {
        "eligible_eur": money.fsum(of["eligible_eur"] for of in offers),
        "advance_eur": money.fsum(of["advance_eur"] for of in offers),
        "fee_eur": money.fsum(of["fee_eur"] for of in offers),
        "net_now_eur": money.fsum(of["net_now_eur"] for of in offers),
        "net_later_eur": money.fsum(of["net_later_eur"] for of in offers),
    }
    return {"offers": offers, "totals": totals, "terms": t}


def _recovery():
    import vat_refund
    return vat_refund.recovery_report()


def _zero_totals():
    return {k: 0.0 for k in ("eligible_eur", "advance_eur", "fee_eur",
                             "net_now_eur", "net_later_eur")}


def _subject(entity, country, period):
    """The stable receivable reference used as the advances-ledger subject/claim_key."""
    return f"{entity or ''}|{country or ''}|{period or ''}"


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


def list_advances(subject=None):
    """Recorded advances/intent rows, newest first. Optionally filtered to one `subject`
    (the receivable ref / claim_key). Never raises (→ [] on a missing or broken DB).

    Exposes the ORIGINATION columns (period/country/eligible_eur/currency) alongside the
    legacy fields; legacy `request_advance` rows have them NULL/'EUR'. Tenant-scoped via
    scope_clause()."""
    try:
        con = connect()
        frag, tp = tenancy.scope_clause()
        params = list(tp)
        sub_frag = ""
        if subject is not None:
            sub_frag = " AND claim_key = ?"
            params.append(subject)
        rows = con.execute(
            "SELECT id, claim_key, amount_eur, fee_eur, eligible_eur, period, country, "
            "currency, provider, status, ref, message, created_by, created_at "
            "FROM advances WHERE 1=1" + frag + sub_frag
            + " ORDER BY id DESC", params).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("finance: list_advances() failed, returning empty: %s", e)
        return []


def advance(subject):
    """The most recent advance/intent row for `subject` (the receivable ref), or None.
    Never raises."""
    rows = list_advances(subject)
    return rows[0] if rows else None


# ============================================================================
# Origination lifecycle — offer / status transitions (ORIGINATION-ONLY)
# ============================================================================
def _audit(action, key, detail):
    """Best-effort explicit audit of a finance-ledger origination event into finance.db's
    own audit_log (attributed to the current thread actor). Never raises — the ledger
    write is the source of truth; a missing audit row must not fail the origination."""
    try:
        import audit
        con = connect()
        try:
            audit.bind(con)
            audit.record_event(con, "advances", key, action, detail)
        finally:
            con.close()
    except Exception as e:
        log.warning("finance: audit of %s %r skipped: %s", action, key, e)


def offer_advance(subject, eligible_eur, advance_eur, fee_eur, actor="system",
                  period=None, country=None, currency="EUR"):
    """ORIGINATE an advance OFFER against receivable `subject` — record an `offered` row in
    the finance-owned ledger. ORIGINATION-ONLY: this MODELS and TRACKS the advance; with
    the NULL provider (the default) NOTHING funds and no money moves — `provider="null"`.
    A real licensed partner, when configured, carries the lending; this row stays the
    platform's origination record either way.

    NEVER touches a VAT figure, status, lock, fee, or payment — it only writes finance.db.
    Returns the new advance row dict (or None on a ledger-write failure, which is logged).

    Tenant-stamped via write_tenant() (fails LOUD if the switch is ON and no tenant is
    bound — a tenant-less origination write is a bug) and audited."""
    p = provider()
    prov_name = "null" if p.name == "none" else p.name
    tid = tenancy.write_tenant()
    elig = money.f2(eligible_eur); adv = money.f2(advance_eur); fee = money.f2(fee_eur)
    try:
        con = connect()
        try:
            cur = con.execute(
                """INSERT INTO advances
                   (claim_key, amount_eur, fee_eur, eligible_eur, period, country, currency,
                    provider, status, message, created_by, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (subject, adv, fee, elig, period, country, currency or "EUR",
                 prov_name, "offered",
                 "Origination-only offer (no funds move).", actor, tid))
            new_id = cur.lastrowid
            con.commit()
        finally:
            con.close()
    except Exception as e:
        log.warning("finance: could not record offer for %r: %s", subject, e)
        return None
    _audit("offer_advance", new_id,
           {"subject": subject, "eligible_eur": elig, "advance_eur": adv, "fee_eur": fee,
            "provider": prov_name, "status": "offered", "actor": actor})
    return advance_by_id(new_id)


def advance_by_id(advance_id):
    """One advance row by id (tenant-scoped), or None. Never raises."""
    try:
        con = connect()
        frag, tp = tenancy.scope_clause()
        row = con.execute(
            "SELECT id, claim_key, amount_eur, fee_eur, eligible_eur, period, country, "
            "currency, provider, status, ref, message, created_by, created_at "
            "FROM advances WHERE id = ?" + frag, [advance_id, *tp]).fetchone()
        con.close()
        return dict(row) if row else None
    except Exception as e:
        log.warning("finance: advance_by_id(%r) failed: %s", advance_id, e)
        return None


def set_status(advance_id, status, actor="system"):
    """Transition an advance to `status` (one of ADVANCE_STATUSES). ORIGINATION-ONLY: this
    only updates the finance-owned ledger row — it NEVER moves money, a VAT figure, a claim
    status, a lock, or a fee. With the NULL provider 'funded'/'repaid' are not reachable
    through any platform action (no partner funds), but the column accepts the full
    lifecycle for a future real provider's bookkeeping.

    Returns the updated row dict, or None (unknown id / bad status / write failure — all
    logged). Tenant-scoped + audited."""
    if status not in ADVANCE_STATUSES:
        log.warning("finance: refusing unknown advance status %r (allowed: %s)",
                    status, ADVANCE_STATUSES)
        return None
    tid = tenancy.write_tenant()   # FAIL LOUD on a tenant-less write under the switch
    try:
        con = connect()
        frag, tp = tenancy.scope_clause()
        try:
            cur = con.execute(
                "UPDATE advances SET status = ? WHERE id = ?" + frag,
                [status, advance_id, *tp])
            con.commit()
            changed = cur.rowcount
        finally:
            con.close()
    except Exception as e:
        log.warning("finance: set_status(%r,%r) failed: %s", advance_id, status, e)
        return None
    if not changed:
        log.warning("finance: set_status: no advance %r in scope (tenant=%s)",
                    advance_id, tid)
        return None
    _audit("set_status", advance_id, {"status": status, "actor": actor})
    return advance_by_id(advance_id)
