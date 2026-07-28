"""The centralized `product_group` derivation (G1.2; `BA_fleet_fuel.md`
section 4.2 row 8) — deliberately its own module, mirroring why
`claim_gates.is_synthetic()` (WO-49) is centralized: `product_group` "drives
the Art. 9 goods code" downstream, so a second, drifted implementation would
silently mis-classify a claim line the same way a drifted `is_synthetic()`
copy would silently under-block one. Every future consumer (the goods-code
mapping module, G2.8; claim-line materialization, G2.4/G2.5; fuel analytics)
must import THIS function, never re-derive the precedence.

THE PRECEDENCE ORDER — verbatim from the harvested source
--------------------------------------------------------------
    "Normalised group: Diesel, HVO, AdBlue, Parking, Toll/Fees, Promo adj,
    Service/Other. Precedence: PROMO -> HVO -> everything else -> Diesel LAST
    so 'HVO 100' is never mis-grouped as diesel. Multilingual diesel tokens
    (DIESEL, ON ACT, GASOLEO, GASOIL, GAZOLE, "ON ")."
    -- BA_fleet_fuel.md section 4.2, row 8

Read literally: PROMO tokens are checked FIRST (a promotional adjustment line
must never be miscounted as fuel spend), then HVO tokens, then the remaining
named categories (AdBlue / Parking / Toll-Fees), and Diesel's own (broad,
multilingual) token set is checked LAST among the specific categories — this
is exactly what keeps a product string like "HVO 100" from being caught by an
over-eager diesel-token match if diesel were checked first or matched via a
loose substring rule. A product matching NO token at all falls to
`"Service/Other"` — the ultimate default. This is a DELIBERATE choice, not a
literal harvested rule (the source text names the 7 categories and the
PROMO/HVO/Diesel-last order but does not spell out what happens when nothing
matches at all): defaulting to `"Service/Other"` rather than `"Diesel"` keeps
an unclassifiable product from silently implying reclaimable fuel VAT at the
default Art. 9 goods code "1" — the unrecognised default for the DOWNSTREAM
goods-code mapping is "10" (Other) per R11, and "Service/Other" is the
`product_group` value that naturally maps there. See the G1.2 work order's
Implementation guidance §3 for the full reasoning.

THE TOKEN SETS — what is verbatim vs. what is a documented best effort
---------------------------------------------------------------------------
The diesel multilingual token list is EXACT, copied verbatim from the source:
`DIESEL`, `ON ACT`, `GASOLEO`, `GASOIL`, `GAZOLE`, `"ON "` (a trailing-space
token — matches the French "ON" fuel abbreviation as a whole word, not as a
substring of an unrelated word). The PROMO/HVO/AdBlue/Parking/Toll-Fees token
sets are NOT given beyond the category names themselves in the harvested
text — this module picks a small, obviously-representative token per
category and documents that choice here rather than pretending it was
harvested. The token tuples are module-level (not a DB table) so extending
them later needs no migration; the mapping TABLE itself (goods-code lookup)
is separate future work (G2.8).
"""

from __future__ import annotations

from app.models.transport.fuel_transaction import PRODUCT_GROUPS

__all__ = ["PRODUCT_GROUPS", "derive_product_group"]

# Order within each tuple does not matter (any match in the tuple is
# sufficient); the ORDER BETWEEN THE TUPLES below (checked top to bottom) is
# what encodes the PROMO -> HVO -> {AdBlue, Parking, Toll/Fees} -> Diesel ->
# Service/Other precedence.
_PROMO_TOKENS = ("PROMO",)
_HVO_TOKENS = ("HVO",)
_ADBLUE_TOKENS = ("ADBLUE", "AD BLUE")
_PARKING_TOKENS = ("PARKING",)
_TOLL_TOKENS = ("TOLL", "ROAD TOLL", "TOLL FEE")
# Verbatim from BA_fleet_fuel.md section 4.2 row 8 — do not edit without
# re-reading the source line.
_DIESEL_TOKENS = ("DIESEL", "ON ACT", "GASOLEO", "GASOIL", "GAZOLE", "ON ")


def derive_product_group(product: str) -> str:
    """Classify a raw, as-printed `product` string into one of the exact 7
    `PRODUCT_GROUPS` values, per the precedence documented in the module
    docstring. Case-insensitive (supplier product names arrive in mixed case
    and multiple languages); matches by substring (a token anywhere in the
    string is sufficient — mirrors the harvested "ON " trailing-space
    convention, which is itself a substring match).

    Never returns a value outside `PRODUCT_GROUPS` — the DB CHECK constraint
    (`ck_fuel_transactions_product_group`) is the belt-and-braces backstop,
    not the only control.
    """
    upper = f" {product.upper()} "  # padded so a leading/trailing "ON " token still matches as a whole word

    if any(tok in upper for tok in _PROMO_TOKENS):
        return "Promo adj"
    if any(tok in upper for tok in _HVO_TOKENS):
        return "HVO"
    if any(tok in upper for tok in _ADBLUE_TOKENS):
        return "AdBlue"
    if any(tok in upper for tok in _PARKING_TOKENS):
        return "Parking"
    if any(tok in upper for tok in _TOLL_TOKENS):
        return "Toll/Fees"
    if any(tok in upper for tok in _DIESEL_TOKENS):
        return "Diesel"
    return "Service/Other"
