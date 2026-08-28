"""The refund country -> national tax authority map (`BA_fleet_fuel.md` §3.F
F5, `customer_master.TAX_AUTHORITY`).

WHY A MAP AND NOT A LOOKUP
---------------------------
A power of attorney is addressed to a NAMED authority. The harvested system
carried the mapping as data because the name is not derivable from the country
code and is not something an operator should have to remember per filing.

WHY AN UNKNOWN COUNTRY YIELDS `""` AND NEVER A GUESS
-----------------------------------------------------
F5 states it verbatim: *"an unknown country yields `""` — the merge never
substitutes a guess."* A PoA naming the wrong authority is not a cosmetic
defect; it is a document the member state can refuse. An empty string is
visibly missing. A plausible-looking wrong name is not, which is exactly the
failure mode `excise.py` refuses for rates and `doc_templates.render` refuses
for tokens — an unresolved token stays VISIBLY in place rather than resolving
to something invented.

WHY THE KEY SET IS PINNED TO THE COHERENCE TABLE
-------------------------------------------------
`capture_review.COUNTRIES` (the `VAT_RATES` key set) is this codebase's ONE
country list. A second list here would drift the first time a country is added
in one place only, so `test_wo_ab_tax_authority.py` asserts the two key sets
are equal rather than this module importing a table it has no other use for.
"""

from __future__ import annotations

# Country -> the authority a refund claim / PoA is addressed to, in that
# state's own official naming. 23 entries — the coherence table's key set.
TAX_AUTHORITY: dict[str, str] = {
    "AT": "Finanzamt Österreich",
    "BE": "Federale Overheidsdienst Financiën / Service Public Fédéral Finances",
    "BG": "Национална агенция за приходите",
    "CZ": "Finanční správa České republiky",
    "DE": "Bundeszentralamt für Steuern",
    "DK": "Skattestyrelsen",
    "EE": "Maksu- ja Tolliamet",
    "ES": "Agencia Estatal de Administración Tributaria",
    "FI": "Verohallinto",
    "FR": "Direction générale des Finances publiques",
    "HR": "Ministarstvo financija — Porezna uprava",
    "HU": "Nemzeti Adó- és Vámhivatal",
    "IT": "Agenzia delle Entrate",
    "LT": "Valstybinė mokesčių inspekcija",
    "LU": "Administration de l'enregistrement, des domaines et de la TVA",
    "LV": "Valsts ieņēmumu dienests",
    "NL": "Belastingdienst",
    "PL": "Krajowa Administracja Skarbowa",
    "PT": "Autoridade Tributária e Aduaneira",
    "RO": "Agenția Națională de Administrare Fiscală",
    "SE": "Skatteverket",
    "SI": "Finančna uprava Republike Slovenije",
    "SK": "Finančná správa Slovenskej republiky",
}


def authority_for(country: str | None) -> str:
    """The authority name for `country`, or `""` when there is none — F5's own
    contract. Case-insensitive on the ISO-2 code; `None`/blank is an unknown
    country like any other, not an error, because the caller is a LABEL, not
    a gate."""
    if not country or not country.strip():
        return ""
    return TAX_AUTHORITY.get(country.strip().upper(), "")
