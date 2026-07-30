"""Art. 9 Dir. 2008/9/EC expenditure (goods) codes — Reg. (EC) 1174/2009 →
Reg. (EU) 79/2012 Annex III (G2.8; R11).

`GOODS_CODE` is harvested VERBATIM from `BA_fleet_fuel.md` A6 (the exact
table, not a paraphrase — mirrors how `claim_gates.is_synthetic()` was
harvested word-for-word in WO-49):

    Diesel        -> "1"  Fuel
    HVO           -> "1"  Fuel
    Promo adj     -> "1"  Fuel (price correction)
    AdBlue        -> "10" Other — operating fluid
    Toll/Fees     -> "4"  Road tolls and road user charges
    Parking       -> "10" Other — parking
    Service/Other -> "10" Other

**THE CRITICAL RULE (R11), verbatim from the source text:** an unknown
product group defaults to code `"10"` (Other) — **NEVER `"9"`**. Code 9 is
*"expenditure on luxuries, amusements and entertainment"*, the archetypal
NON-DEDUCTIBLE category; filing an operating fluid or parking under code 9
invites refusal from the refunding tax authority. `BA_fleet_fuel.md`'s own
historical note: Fleet Fuel once coded tolls `"3"` and AdBlue/parking `"9"`
— both were bugs, fixed before the harvest. This rebuild must not regress.

WHY EVERY `PRODUCT_GROUPS` VALUE IS EXPLICITLY LISTED, NOT LEFT TO THE
DEFAULT
------------------------------------------------------------------------
`derive_product_group()` (G1.2, WO-50) only ever returns one of the 7
`PRODUCT_GROUPS` values (its own CHECK constraint enforces this at the
database level too) — so in PRACTICE, `GOODS_CODE.get(pg, ...)`'s default
branch is never actually reached for a real line. Every one of the 7 is
still listed explicitly here (not "everything except Diesel/HVO/Promo/
Toll/Fees defaults to 10") because a future 8th `product_group` value would
otherwise SILENTLY inherit code `"10"` without a human ever having decided
that was correct for it — the same "assert the absence, not only the
presence" discipline this codebase applies to `is_synthetic()`'s consumers.
The `.get(..., ("10", "Other"))` default is defense-in-depth for that
future 8th value, not a stand-in for today's 7.

WHY THIS IS INDEPENDENT OF THE CLAIM-LINE FREEZE/SUBMISSION MACHINERY
--------------------------------------------------------------------------
`ARCH_plan.md` scopes G2.8 as depending on G1.2 ONLY (not G2.4/G2.5) — the
goods-code mapping is a pure function of `product_group`, unrelated to
whether a line is frozen, matched, or even materialized yet.
`build_claim_lines` (G2.4/WO-52) calls `derive_goods_code()` when
constructing each line, but the function itself has no claim/transaction
dependency and could be called anywhere a `product_group` string exists.
"""

from __future__ import annotations

# (code, description) — Reg. 1174/2009 & 79/2012 Annex III codes 1/4/10
# only; code "9" (luxuries/entertainment) must NEVER appear here (R11).
GOODS_CODE: dict[str, tuple[str, str]] = {
    "Diesel": ("1", "Fuel"),
    "HVO": ("1", "Fuel"),
    "Promo adj": ("1", "Fuel (price correction)"),
    "AdBlue": ("10", "Other — operating fluid"),
    "Toll/Fees": ("4", "Road tolls and road user charges"),
    "Parking": ("10", "Other — parking"),
    "Service/Other": ("10", "Other"),
}

_DEFAULT_CODE, _DEFAULT_DESC = "10", "Other"


def goods_code_and_description(product_group: str) -> tuple[str, str]:
    """The full `(code, description)` pair — `GOODS_CODE.get(pg, ("10",
    "Other"))`, verbatim per the harvested source text. An unrecognised
    `product_group` (not one of today's 7 — defense-in-depth for a future
    8th value) defaults to `("10", "Other")`, never `"9"`."""
    return GOODS_CODE.get(product_group, (_DEFAULT_CODE, _DEFAULT_DESC))


def derive_goods_code(product_group: str) -> str:
    """Just the code — the value `VatRefundClaimLine.goods_code` stores."""
    code, _desc = goods_code_and_description(product_group)
    return code
