"""Bank-identifier validation: IBAN (ISO 13616 + ISO 7064 MOD-97) and BIC (ISO 9362).

WHY THIS EXISTS
---------------
`Vendor.iban` / `Vendor.bic` become the creditor account of a real SEPA
pain.001 credit transfer (`services/sepa.py`), and `User.iban` /
`IssuerProfile.iban` feed the same file builder. A malformed IBAN in any of
those columns either bounces the payment file at the bank or — worse — pays the
wrong account. Validation is therefore applied at EVERY write path (vendor
create/update, change-request approval, employee bank details, issuer profile)
AND again inside the SEPA builder, because write-time validation can be
bypassed by a data migration or a direct DB edit; the file is the last line of
defence.

Deliberately fail-closed: an IBAN whose country prefix is not in the length
table is REJECTED, never waved through — an unknown country cannot be
structurally verified, and "unverifiable" must not mean "accepted" on a path
that moves money.

Pure functions only — no DB, no HTTP, no framework imports beyond the shared
`AppError` vocabulary.
"""

from __future__ import annotations

import re

from app.core.errors import ValidationError

# ISO 13616 IBAN length per country — every SEPA-zone country (EU-27 + EEA +
# UK/CH and the microstates that participate in SEPA). Source: the ISO 13616
# IBAN registry / EPC SEPA scheme country list.
IBAN_LENGTHS: dict[str, int] = {
    "AD": 24,  # Andorra
    "AT": 20,  # Austria
    "BE": 16,  # Belgium
    "BG": 22,  # Bulgaria
    "CH": 21,  # Switzerland
    "CY": 28,  # Cyprus
    "CZ": 24,  # Czechia
    "DE": 22,  # Germany
    "DK": 18,  # Denmark
    "EE": 20,  # Estonia
    "ES": 24,  # Spain
    "FI": 18,  # Finland
    "FR": 27,  # France
    "GB": 22,  # United Kingdom
    "GI": 23,  # Gibraltar
    "GR": 27,  # Greece
    "HR": 21,  # Croatia
    "HU": 28,  # Hungary
    "IE": 22,  # Ireland
    "IS": 26,  # Iceland
    "IT": 27,  # Italy
    "LI": 21,  # Liechtenstein
    "LT": 20,  # Lithuania
    "LU": 20,  # Luxembourg
    "LV": 21,  # Latvia
    "MC": 27,  # Monaco
    "MT": 31,  # Malta
    "NL": 18,  # Netherlands
    "NO": 15,  # Norway
    "PL": 28,  # Poland
    "PT": 25,  # Portugal
    "RO": 24,  # Romania
    "SE": 24,  # Sweden
    "SI": 19,  # Slovenia
    "SK": 24,  # Slovakia
    "SM": 27,  # San Marino
    "VA": 22,  # Vatican City State
}

# Overall IBAN shape: 2-letter country, 2 check digits, 1..30 alphanumerics.
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$")

# BIC (ISO 9362): 4-letter bank code, 2-letter country, 2-alphanumeric location,
# optional 3-alphanumeric branch — 8 or 11 characters total.
_BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$")


def normalize_iban(value: str) -> str:
    """Canonical storage/compare form: whitespace stripped, upper-cased."""
    return re.sub(r"\s+", "", value or "").upper()


def _mod97(iban: str) -> int:
    """ISO 7064 MOD-97-10 over the rearranged IBAN: move the first four
    characters to the end, map letters A→10 … Z→35, read as an integer."""
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97


def is_valid_iban(value: str) -> bool:
    """Structural ISO 13616 check: shape, country-specific length (unknown
    country ⇒ False, fail-closed) and MOD-97 check digits == 1."""
    iban = normalize_iban(value)
    if not _IBAN_RE.fullmatch(iban):
        return False
    expected_len = IBAN_LENGTHS.get(iban[:2])
    if expected_len is None or len(iban) != expected_len:
        return False
    return _mod97(iban) == 1


def is_valid_bic(value: str) -> bool:
    """ISO 9362 format check (8 or 11 chars, per-position character classes)."""
    return bool(_BIC_RE.fullmatch((value or "").strip().upper()))


def assert_iban(value: str) -> str:
    """Validate and return the normalized IBAN; raise a 422 `invalid_iban`
    AppError otherwise. The message never echoes the full input back (it may be
    a mistyped-but-nearly-real account number)."""
    iban = normalize_iban(value)
    if not is_valid_iban(iban):
        raise ValidationError(
            "Invalid IBAN: failed the ISO 13616 structure / MOD-97 check-digit test.",
            code="invalid_iban",
        )
    return iban


def assert_bic(value: str) -> str:
    """Validate and return the normalized BIC; raise a 422 `invalid_bic`
    AppError otherwise."""
    bic = (value or "").strip().upper()
    if not is_valid_bic(bic):
        raise ValidationError(
            "Invalid BIC: must be 8 or 11 characters (bank, country, location[, branch]).",
            code="invalid_bic",
        )
    return bic


def mask_iban(value: str | None) -> str | None:
    """Audit-safe representation: last four characters + total length, NEVER the
    full account number (audit rows are broadly readable and immutable)."""
    if not value:
        return None
    iban = normalize_iban(value)
    return f"…{iban[-4:]} (len {len(iban)})"
