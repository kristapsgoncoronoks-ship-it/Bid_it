"""Art. 17 Dir. 2008/9/EC minimum claim amounts, enforced in the CORRECT
currency (G2.6 slice; R8; `BA_fleet_fuel.md` A3).

**Case-law-critical, verbatim boundaries:** a sub-year (quarterly) claim's
threshold is €400 (or the national fixed equivalent); a full calendar-year
claim's is €50. `SEK 3,999` is BLOCKED, `SEK 4,000` is ALLOWED; a EUR-country
claim of `€399.99` is BLOCKED, `€400.00` is ALLOWED — strict `<` comparison,
the threshold itself always passes.

WHY SOME COUNTRIES COMPARE `vat_local`, NOT `vat_eur`
----------------------------------------------------------
Some non-euro member states express Art. 17 as a FIXED STATUTORY AMOUNT in
their own currency, not a live FX conversion of the €400/€50 EUR base — a
claim just under 4000 SEK must be blocked even if its EUR-converted value
would clear €400 on a given day's exchange rate (the statute doesn't move
with the rate). `NATIONAL_MINIMUMS` harvests the only two entries
`BA_fleet_fuel.md` A3 actually gives (Sweden, Denmark) — the source text
independently confirms "euro countries and Poland are intentionally absent
... and fall back to the EUR base," but says nothing about completeness for
every OTHER non-euro member state (Hungary, Czechia, Romania, Bulgaria, ...).
Extending this table for another non-euro country is flagged as future work,
not silently assumed to already be covered by the EUR-base fallback.

WHY THE ADMIN OVERRIDE IS A BOOLEAN FLAG, NOT A PERMISSION CHECK, IN THIS
ORDER
------------------------------------------------------------------------------
R8's acceptance line: "admin override allowed and RECORDED in `status_note`."
This module (and `lock.submit_claim`, which calls it) operates below any
HTTP/route/permission layer — no `api/routes/transport/*` route exists yet
(future work) to enforce "only an admin may pass `override_minimum=True`".
The boolean parameter is the interim mechanism this order builds; a future
route wires the real role check on top of it, the same relationship
`modules.is_enabled` (a service-layer bool check) already has to
`modules.require_enabled` (the route-layer HTTP guard) elsewhere in this
codebase.
"""

from __future__ import annotations

from decimal import Decimal

# EUR base thresholds (Art. 17) — sub-year (quarterly) vs full calendar year.
MIN_QUARTER = Decimal("400.00")
MIN_ANNUAL = Decimal("50.00")

# Fixed STATUTORY local-currency amounts — see module docstring. Each value
# is (currency, quarterly_threshold, annual_threshold), harvested verbatim
# from `BA_fleet_fuel.md` A3's `NATIONAL_MINIMUMS` table.
NATIONAL_MINIMUMS: dict[str, tuple[str, Decimal, Decimal]] = {
    "SE": ("SEK", Decimal("4000"), Decimal("500")),
    "DK": ("DKK", Decimal("3000"), Decimal("400")),
}


def min_for(country: str, *, is_annual: bool) -> tuple[str, Decimal, str]:
    """`(currency, threshold, basis)` for `country`'s Art. 17 minimum.
    `basis == "local"` means compare the claim's `vat_local`; `basis ==
    "eur"` means compare `vat_eur` — every country NOT in
    `NATIONAL_MINIMUMS` (every euro member state, and Poland, per the
    harvested source text)."""
    entry = NATIONAL_MINIMUMS.get(country.upper())
    if entry is not None:
        currency, quarterly, annual = entry
        return currency, (annual if is_annual else quarterly), "local"
    return "EUR", (MIN_ANNUAL if is_annual else MIN_QUARTER), "eur"


def below_minimum(
    *,
    country: str,
    ref_period: str,
    vat_eur: Decimal,
    vat_local: Decimal | None,
) -> bool:
    """True iff the claim's VAT amount — compared in the CORRECT currency
    for `country` (never blindly `vat_eur`) — is strictly below the Art. 17
    threshold for this period's shape (quarterly vs. full-year, per
    `ref_period`'s own `"-YEAR"` suffix). The threshold itself always
    passes (`>=`, never blocked) — see module docstring's exact boundary
    values."""
    is_annual = ref_period.endswith("YEAR")
    _currency, threshold, basis = min_for(country, is_annual=is_annual)
    amount = vat_local if basis == "local" else vat_eur
    if amount is None:
        amount = Decimal("0")
    return amount < threshold
