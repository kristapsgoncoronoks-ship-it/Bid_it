"""Supplier bank instructions captured as document evidence, never payment authority."""

from collections.abc import Iterable

from app.core import bank_id

# Qualified names only: a generic `iban` could be the buyer's account.
SOURCE_KEYS = ("supplier_iban", "supplier_bank_iban", "payee_iban")


def from_candidates(values: Iterable[str | None], warnings: list[str]) -> str | None:
    candidates = {bank_id.normalize_iban(value) for value in values if value and value.strip()}
    if not candidates:
        return None
    if len(candidates) != 1:
        warnings.append("Multiple supplier payment accounts: no IBAN selected; review the source.")
        return None
    value = candidates.pop()
    if not bank_id.is_valid_iban(value):
        warnings.append("Supplier IBAN is invalid or unsupported; left missing for source review.")
        return None
    return value


def from_mapping(source: dict, warnings: list[str]) -> str | None:
    return from_rows([source], warnings)


def from_rows(rows: list[dict], warnings: list[str]) -> str | None:
    normalized_rows = [{str(k).strip().lower(): v for k, v in row.items()} for row in rows]
    return from_candidates(
        (
            str(row[key])
            for row in normalized_rows
            for key in SOURCE_KEYS
            if row.get(key) not in (None, "")
        ),
        warnings,
    )
