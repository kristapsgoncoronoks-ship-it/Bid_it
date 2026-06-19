"""
OPEN-BANKING RECONCILIATION SEAM — match BANK CREDITS against EXPECTED VAT REFUNDS.

A VAT refund (Dir. 2008/9/EC) is filed with a foreign tax authority and paid back weeks
or months later by bank transfer. This module reconciles incoming BANK TRANSACTIONS
against the EXPECTED INCOMING REFUNDS (claims submitted/approved but not yet marked
paid) by AMOUNT and DATE, so you can see at a glance which refunds have LANDED and which
are still OUTSTANDING.

ADVISORY ONLY — this module NEVER mutates a claim, status, lock, fee, payment, or any
VAT figure. It produces SUGGESTIONS over read-only data (an uploaded bank-statement CSV
+ vat_refund.recovery_report()). Marking a claim paid stays a deliberate, separate manual
action through the existing VAT lifecycle; nothing here calls record_payment.

It works TODAY via a bank-statement CSV upload. The `BankingProvider` seam allows an
automated bank-feed (account-information) integration to be plugged in LATER:

  * we are NOT a licensed AISP/PISP. The provider seam is for an AGENT-of-a-regulated-
    provider integration (Tink / TrueLayer / Yapily — account-information aggregators);
  * the default provider is NULL — no feed configured, returns no lines;
  * account-information only (read bank lines); NEVER payment initiation (PISP).

bank_recon owns NO DB — it is a STATELESS reconciliation over an uploaded/fetched CSV and
recovery_report() reads. Money EUR-compares via money.q2 (ROUND_HALF_UP). The public
functions (`parse_bank_csv`, `expected_refunds`, `reconcile`, `provider`) NEVER raise —
on bad input they degrade to empty/default values and log via applog.
"""
import csv
import datetime
import io
import os

import applog
import money

WORKDIR = os.path.dirname(os.path.abspath(__file__))

log = applog.get("bank_recon")


# ============================================================================
# Provider seam — automated bank-feed (account-information) fetch, AGENT-only
# ============================================================================
class BankingProvider:
    """Interface an account-information (AISP) integration implements to feed bank lines.

    A real provider would be an AGENT of a LICENSED account-information aggregator
    (Tink / TrueLayer / Yapily): read-only access to bank account information. It returns
    the SAME normalized line shape `parse_bank_csv` produces, so `reconcile` consumes
    either source identically.
    """
    name = "base"

    def fetch_transactions(self, account, since=None):
        """Return a list of normalized bank lines for `account` since the optional
        `since` date (ISO 'YYYY-MM-DD'). Each line is
        {date, amount (signed; + = credit/incoming), description, counterparty}."""
        raise NotImplementedError


class NullProvider(BankingProvider):
    """The DEFAULT. No bank feed is configured — returns no lines, so the CSV-upload
    path is the only source. No network call, no credentials, nothing fetched."""
    name = "none"

    def fetch_transactions(self, account, since=None):
        return []


class SandboxBankProvider(BankingProvider):
    """A DETERMINISTIC, NETWORK-FREE mock account-information feed, so the advisory
    reconciliation is exercisable end-to-end WITHOUT a live AISP partner.

    It returns mock bank lines that INCLUDE credits matching the EXPECTED refunds (so
    `reconcile` produces hits) plus some non-matching NOISE (a fee debit, an unrelated
    credit). It reads the SAME expected_refunds() recovery view to synthesise a credit per
    expected refund (amount = expected_eur, date = `since` + a few days, inside the
    reconcile day-tolerance), so a demo/test sees real matches.

    STRICT INVARIANT (CLAUDE.md): account-information ONLY — it fabricates READ lines and
    NEVER initiates a payment, marks a claim paid, or mutates any VAT figure/status. The
    feed is fully deterministic (no randomness, no clock dependence beyond the expected
    refunds' own dates) so a test is stable."""
    name = "sandbox"

    # days after a refund's `since` (filing) date that the simulated credit lands — well
    # inside reconcile()'s default 14-day tolerance, so every expected refund matches.
    _LAND_DAYS = 5

    def fetch_transactions(self, account, since=None):
        lines = []
        try:
            expected = expected_refunds()
        except Exception as e:                       # defensive — never raise
            log.warning("bank_recon: sandbox fetch could not read expected refunds: %s", e)
            expected = []
        for e in expected:
            amt = e.get("expected_eur")
            base = e.get("since")
            date = self._offset(base, self._LAND_DAYS) or base
            if amt is None or date is None:
                continue
            lines.append({
                "date": date,
                "amount": float(amt),
                "description": f"VAT refund {e.get('country') or ''} {e.get('period') or ''}".strip(),
                "counterparty": "Tax authority (sandbox)",
            })
        # deterministic NOISE — a non-matching credit + an outgoing debit. Anchored to the
        # earliest expected date (or a fixed fallback) so the output is stable.
        anchor = min((e.get("since") for e in expected if e.get("since")), default=None)
        noise_date = self._offset(anchor, 1) or "2026-01-15"
        lines.append({"date": noise_date, "amount": 42.00,
                      "description": "Interest credit (sandbox)",
                      "counterparty": "Bank (sandbox)"})
        lines.append({"date": noise_date, "amount": -17.50,
                      "description": "Account fee (sandbox)",
                      "counterparty": "Bank (sandbox)"})
        return lines

    @staticmethod
    def _offset(iso_date, days):
        try:
            return (datetime.date.fromisoformat(iso_date)
                    + datetime.timedelta(days=days)).isoformat()
        except (TypeError, ValueError):
            return None


# >>> AISP AGENT SEAM <<<
# To wire an automated bank feed, add a `BankingProvider` subclass whose
# `fetch_transactions` calls a LICENSED account-information aggregator (Tink / TrueLayer /
# Yapily) over an authenticated, out-of-band, audited channel and returns normalized bank
# lines. STRICT CONSTRAINTS:
#   * we integrate as an AGENT of a REGULATED provider — we do NOT self-licence as
#     an AISP/PISP;
#   * ACCOUNT INFORMATION ONLY (read bank lines). NEVER payment initiation (PISP) — no
#     payment is ever moved from this repo;
#   * credentials/tokens use envelope encryption (KEK->DEK, KMS/HSM, per-tenant/BYOK),
#     prefer scoped OAuth tokens, least-privilege, full audit (see CLAUDE.md);
#   * fetching runs OUT-OF-BAND on the intake worker tier, never inline in a web request.
# Register the subclass in `_PROVIDERS` and select it via the `bank_provider` app setting.
# Do NOT implement a real bank API call in this repo — the `sandbox` provider above is a
# DETERMINISTIC mock feed (no network, account-information only) for end-to-end exercise.
_PROVIDERS = {
    "none": NullProvider,
    "null": NullProvider,         # explicit alias for the default
    "sandbox": SandboxBankProvider,
}


def provider():
    """The configured BankingProvider. Defaults to NullProvider; an unknown/garbled
    `bank_provider` setting also falls back to NullProvider (never raises)."""
    key = "none"
    try:
        import auth
        key = (auth.get_setting("bank_provider", "none") or "none").strip().lower()
    except Exception as e:
        log.warning("bank_recon: could not read bank_provider setting: %s", e)
    cls = _PROVIDERS.get(key, NullProvider)
    return cls()


# ============================================================================
# Bank-statement CSV parsing
# ============================================================================
# Accepted header aliases (matched case-insensitively, whitespace-trimmed). A
# bank-statement export typically carries a booking/value date, a signed amount (or a
# pair of credit/debit columns), a description/reference and a counterparty name.
_DATE_ALIASES = ("date", "booking date", "bookingdate", "value date", "valuedate",
                 "transaction date", "posted")
_AMOUNT_ALIASES = ("amount", "value", "transaction amount")
_CREDIT_ALIASES = ("credit", "credit amount", "paid in", "paid-in", "money in")
_DEBIT_ALIASES = ("debit", "debit amount", "paid out", "paid-out", "money out")
_DESC_ALIASES = ("description", "reference", "details", "narrative", "remittance",
                 "memo")
_PARTY_ALIASES = ("counterparty", "name", "payer", "payee", "creditor", "debtor",
                  "beneficiary")


def _norm_header(h):
    return (h or "").strip().lower().lstrip("﻿")


def _find_col(fieldnames, aliases):
    """First field whose normalized name matches one of `aliases` (None if none)."""
    norm = {_norm_header(f): f for f in (fieldnames or [])}
    for a in aliases:
        if a in norm:
            return norm[a]
    return None


def _parse_date(raw):
    """Normalize a date cell to ISO 'YYYY-MM-DD' (None if unparseable). Accepts ISO and
    the common European/US separators (dd.mm.yyyy, dd/mm/yyyy, yyyy/mm/dd)."""
    s = (raw or "").strip()
    if not s:
        return None
    # ISO first (handles 'YYYY-MM-DD' and 'YYYY-MM-DDTHH:MM:SS')
    try:
        return datetime.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_amount(raw):
    """Parse a possibly-formatted amount cell to a float (None if unparseable). Tolerates
    thousands separators, a trailing/leading currency symbol, and a '1.234,56'-style
    decimal comma."""
    s = (raw or "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")     # (123.45) accountancy negative
    s = s.strip("()")
    # strip everything except digits, separators and sign
    s = "".join(ch for ch in s if ch.isdigit() or ch in ".,-+")
    if not s or s in ("+", "-", ".", ","):
        return None
    # decide the decimal separator: if both present, the LAST one is the decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # lone comma -> decimal comma (European)
        s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def parse_bank_csv(data):
    """Parse a bank-statement CSV (bytes) into normalized lines. Never raises (-> []).

    Accepted columns (case-insensitive header aliases; utf-8-sig BOM tolerated):
      date         : date | booking date | value date | transaction date | posted
      amount       : amount | value | transaction amount        (signed; + = credit)
                     OR a credit/debit pair: credit | paid in  AND  debit | paid out
      description  : description | reference | details | narrative | memo
      counterparty : counterparty | name | payer | payee | creditor | beneficiary

    Returns a list of {date: 'YYYY-MM-DD', amount: float (signed; + = credit/incoming),
    description: str, counterparty: str}. A row missing a usable date or amount is SKIPPED
    (logged at debug); a credit column is read as a positive amount and a debit column as
    negative when no signed `amount` column exists.
    """
    out = []
    try:
        text = (data.decode("utf-8-sig", "replace") if isinstance(data, (bytes, bytearray))
                else str(data))
    except Exception as e:
        log.warning("bank_recon: could not decode CSV bytes: %s", e)
        return out
    try:
        reader = csv.DictReader(io.StringIO(text))
        fields = reader.fieldnames
        if not fields:
            return out
        date_col = _find_col(fields, _DATE_ALIASES)
        amt_col = _find_col(fields, _AMOUNT_ALIASES)
        credit_col = _find_col(fields, _CREDIT_ALIASES)
        debit_col = _find_col(fields, _DEBIT_ALIASES)
        desc_col = _find_col(fields, _DESC_ALIASES)
        party_col = _find_col(fields, _PARTY_ALIASES)
        for row in reader:
            try:
                date = _parse_date(row.get(date_col) if date_col else None)
                amount = None
                if amt_col:
                    amount = _parse_amount(row.get(amt_col))
                if amount is None and (credit_col or debit_col):
                    cr = _parse_amount(row.get(credit_col)) if credit_col else None
                    dr = _parse_amount(row.get(debit_col)) if debit_col else None
                    if cr:
                        amount = abs(cr)
                    elif dr:
                        amount = -abs(dr)
                if date is None or amount is None:
                    log.debug("bank_recon: skipping unparseable row %r", row)
                    continue
                out.append({
                    "date": date,
                    "amount": float(amount),
                    "description": (row.get(desc_col) or "").strip() if desc_col else "",
                    "counterparty": (row.get(party_col) or "").strip() if party_col else "",
                })
            except Exception as e:
                log.debug("bank_recon: skipping bad row %r: %s", row, e)
                continue
    except Exception as e:
        log.warning("bank_recon: parse_bank_csv failed: %s", e)
    return out


# ============================================================================
# Expected refunds — REUSE recovery_report (read-only)
# ============================================================================
def expected_refunds(year=None):
    """The EXPECTED incoming refunds — claims FILED but NOT YET PAID (submitted/approved),
    as expected bank CREDITS. REUSES vat_refund.recovery_report(year) so it reconciles
    exactly with the recovery/receivables surfaces. Read-only; never raises (-> []).

    Returns [{key, entity, country, period, expected_eur (=vat_eur), since (=submitted)}].
    """
    try:
        import vat_refund
        out, _summary = vat_refund.recovery_report(year)
        rows = []
        for o in out:
            if o.get("status") not in ("submitted", "approved"):
                continue
            entity = o.get("entity") or ""
            country = o.get("country") or ""
            period = o.get("period") or ""
            rows.append({
                "key": f"{entity}|{country}|{period}",
                "entity": entity,
                "country": country,
                "period": period,
                "expected_eur": o.get("vat_eur"),
                "since": o.get("submitted"),
            })
        return rows
    except Exception as e:
        log.warning("bank_recon: expected_refunds() failed, returning empty: %s", e)
        return []


# ============================================================================
# Reconciliation — pure, deterministic matching (SUGGESTIONS ONLY)
# ============================================================================
def _days_between(since_iso, bank_iso):
    """bank_date - since_date in days, or None if either is unparseable."""
    try:
        a = datetime.date.fromisoformat(since_iso)
        b = datetime.date.fromisoformat(bank_iso)
        return (b - a).days
    except (TypeError, ValueError):
        return None


def reconcile(bank_lines, expected, eur_tol=0.01, day_tol=14):
    """Match each EXPECTED refund to an incoming BANK CREDIT, greedily one-to-one.

    A bank line is a candidate for an expected refund when:
      * it is a CREDIT (amount > 0 — money coming in);
      * |bank.amount - expected.expected_eur| <= eur_tol (compared via money.q2, so a
        EUR boundary never flips on binary-float noise);
      * the booking date is on/after the expected `since` (a refund arrives AFTER filing)
        and within `day_tol` days of it (0 <= day_gap <= day_tol).

    Greedy one-to-one: every (expected, candidate-bank) pair is scored and the pairs are
    consumed best-first, each expected and each bank line used at most once. The ordering
    is DETERMINISTIC — sort key (amount_delta asc, day_gap asc, expected.key asc,
    bank index asc) — so ties (e.g. two identical amounts) resolve stably by claim key
    then statement order, never randomly.

    Returns {matched: [{expected, bank, amount_delta, day_gap}],
             unmatched_expected: [...expected rows...],
             unmatched_bank: [...incoming (credit) lines that matched nothing...]}.

    PURE and ADVISORY: it changes NOTHING — it only suggests pairings. Never raises.
    """
    result = {"matched": [], "unmatched_expected": list(expected or []),
              "unmatched_bank": []}
    try:
        exp = list(expected or [])
        lines = list(bank_lines or [])
        # candidate incoming credits, tagged with their original index for stable ordering.
        # money.q2 tolerates a non-numeric amount (→ 0), so a garbled cell is simply not a
        # credit rather than an error.
        credits = [(i, ln) for i, ln in enumerate(lines)
                   if isinstance(ln, dict) and money.q2(ln.get("amount")) > 0]

        candidates = []
        for ei, e in enumerate(exp):
            exp_amt = money.q2(e.get("expected_eur"))
            since = e.get("since")
            for bi, ln in credits:
                delta = abs(money.q2(ln.get("amount")) - exp_amt)
                if delta > money.q2(eur_tol):
                    continue
                gap = _days_between(since, ln.get("date"))
                if gap is None or gap < 0 or gap > day_tol:
                    continue
                candidates.append((float(delta), gap, str(e.get("key") or ""), bi, ei))

        # best-first: smallest amount delta, then nearest date, then stable tie-breaks
        candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))

        used_exp, used_bank = set(), set()
        matched = []
        for delta, gap, _key, bi, ei in candidates:
            if ei in used_exp or bi in used_bank:
                continue
            used_exp.add(ei)
            used_bank.add(bi)
            matched.append({
                "expected": exp[ei],
                "bank": lines[bi],
                "amount_delta": money.f2(delta),
                "day_gap": gap,
            })

        result["matched"] = matched
        result["unmatched_expected"] = [e for i, e in enumerate(exp) if i not in used_exp]
        result["unmatched_bank"] = [ln for i, ln in credits if i not in used_bank]
    except Exception as e:
        log.warning("bank_recon: reconcile() failed, returning empty match: %s", e)
        result = {"matched": [], "unmatched_expected": list(expected or []), "unmatched_bank": []}
    return result


if __name__ == "__main__":
    # inline smoke test (the project convention)
    exp = [{"key": "ACME|DE|2026-Q1", "expected_eur": 1234.56, "since": "2026-03-01"}]
    bank = [{"date": "2026-03-10", "amount": 1234.56, "description": "VAT refund",
             "counterparty": "Bundeszentralamt"}]
    r = reconcile(bank, exp)
    assert len(r["matched"]) == 1, r
    assert r["matched"][0]["day_gap"] == 9
    assert r["matched"][0]["amount_delta"] == 0.0
    assert not r["unmatched_expected"] and not r["unmatched_bank"]
    csvdata = b"\xef\xbb\xbfBooking Date,Amount,Reference,Name\n2026-03-10,1234.56,VAT,Tax\n"
    lines = parse_bank_csv(csvdata)
    assert lines and lines[0]["amount"] == 1234.56 and lines[0]["counterparty"] == "Tax"
    assert parse_bank_csv(b"not,a,bank,statement\n\x00\x01") == []
    assert isinstance(provider(), NullProvider)
    print("bank_recon.py: all smoke tests PASS")
