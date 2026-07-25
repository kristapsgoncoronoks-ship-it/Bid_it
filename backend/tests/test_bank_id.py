"""`app/core/bank_id.py` — IBAN ISO 13616 structure + ISO 7064 MOD-97 check
digits, and BIC ISO 9362 format. Pure unit tests; every value is the published
ISO/registry EXAMPLE iban for its country (synthetic, not a real account)."""

from __future__ import annotations

import pytest

from app.core import bank_id
from app.core.errors import AppError

# (country, example IBAN with correct length + check digits) — 15 SEPA countries.
VALID_IBANS = [
    ("DE", "DE89370400440532013000"),
    ("NL", "NL91ABNA0417164300"),
    ("FR", "FR1420041010050500013M02606"),
    ("GB", "GB29NWBK60161331926819"),
    ("ES", "ES9121000418450200051332"),
    ("IT", "IT60X0542811101000000123456"),
    ("BE", "BE68539007547034"),
    ("AT", "AT611904300234573201"),
    ("PL", "PL61109010140000071219812874"),
    ("PT", "PT50000201231234567890154"),
    ("FI", "FI2112345600000785"),
    ("IE", "IE29AIBK93115212345678"),
    ("LV", "LV80BANK0000435195001"),
    ("LT", "LT121000011101001000"),
    ("EE", "EE382200221020145685"),
]


@pytest.mark.parametrize(("country", "iban"), VALID_IBANS)
def test_valid_ibans_per_country(country, iban):
    assert iban.startswith(country)
    assert len(iban) == bank_id.IBAN_LENGTHS[country]
    assert bank_id.is_valid_iban(iban)
    # Spacing / lower-case never changes the verdict; assert_iban normalizes.
    spaced = " ".join(iban[i : i + 4] for i in range(0, len(iban), 4)).lower()
    assert bank_id.is_valid_iban(spaced)
    assert bank_id.assert_iban(spaced) == iban


@pytest.mark.parametrize(("country", "iban"), VALID_IBANS)
def test_invalid_check_digits_rejected(country, iban):
    """A structurally correct IBAN with mutated check digits must fail MOD-97."""
    good = int(iban[2:4])
    mutated = iban[:2] + f"{(good + 1) % 100:02d}" + iban[4:]
    assert not bank_id.is_valid_iban(mutated)
    with pytest.raises(AppError) as exc:
        bank_id.assert_iban(mutated)
    assert exc.value.code == "invalid_iban"
    assert exc.value.status == 422


def test_unknown_country_prefix_rejected():
    """Fail-closed: a country outside the length table is REJECTED, never waved
    through — 'unverifiable' must not mean 'accepted' on a payment path."""
    assert "XX" not in bank_id.IBAN_LENGTHS
    assert not bank_id.is_valid_iban("XX89370400440532013000")
    # Real ISO example for a NON-SEPA country not in our table (Brazil).
    assert not bank_id.is_valid_iban("BR1800360305000010009795493C1")


def test_iban_length_mismatch_rejected():
    """Right country prefix, valid characters, wrong length."""
    assert not bank_id.is_valid_iban("DE8937040044053201300")  # 21, DE needs 22
    assert not bank_id.is_valid_iban("DE893704004405320130000")  # 23
    assert not bank_id.is_valid_iban("NL91ABNA041716430")  # 17, NL needs 18
    assert not bank_id.is_valid_iban("")
    assert not bank_id.is_valid_iban("DE89")


def test_bic_format_8_and_11_accepted_others_rejected():
    assert bank_id.is_valid_bic("ABNANL2A")  # 8
    assert bank_id.is_valid_bic("COBADEFFXXX")  # 11
    assert bank_id.is_valid_bic("cobadeffxxx")  # normalized before checking
    assert not bank_id.is_valid_bic("COBADEFF9")  # 9 chars
    assert not bank_id.is_valid_bic("COBADEFFXX")  # 10 chars
    assert not bank_id.is_valid_bic("C0BADEFF")  # digit in the 4-letter bank code
    assert not bank_id.is_valid_bic("COBA12FF")  # digits in the country code
    assert not bank_id.is_valid_bic("")
    with pytest.raises(AppError) as exc:
        bank_id.assert_bic("NOPE")
    assert exc.value.code == "invalid_bic"
    assert bank_id.assert_bic(" abnanl2a ") == "ABNANL2A"


def test_mask_iban_never_reveals_more_than_last_four():
    masked = bank_id.mask_iban("DE89370400440532013000")
    assert masked == "…3000 (len 22)"
    assert "DE89370400440532013000" not in masked
    assert bank_id.mask_iban(None) is None
