"""
Hypothesis Property-Based Tests for Norwegian Modulo-11 Engine (v6.0 Fail-Proof Edition).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from src.domain.mod11 import (
    calculate_check_digit,
    extract_orgnrs_from_text,
    normalize_orgnr_candidate,
    validate_orgnr,
)


@given(first_8=st.integers(min_value=10_000_000, max_value=99_999_999).map(str))
def test_mod11_checksum_property(first_8: str) -> None:
    """Property: Any valid check digit paired with its 8-digit prefix MUST validate successfully."""
    check_res = calculate_check_digit(first_8)
    if check_res.is_ok:
        digit = check_res.unwrap()
        full_orgnr = f"{first_8}{digit}"
        val_res = validate_orgnr(full_orgnr)
        assert val_res.is_ok
        assert val_res.unwrap() == full_orgnr

        # Modifying the check digit MUST fail checksum validation
        wrong_digit = (digit + 1) % 10
        wrong_orgnr = f"{first_8}{wrong_digit}"
        wrong_res = validate_orgnr(wrong_orgnr)
        assert wrong_res.is_err
    else:
        # Check digit 10 is an invalid combination
        err = check_res.unwrap_err()
        assert err.code == "CHECK_DIGIT_TEN"


@given(first_8=st.integers(min_value=10_000_000, max_value=99_999_999).map(str))
def test_mod11_normalization_property(first_8: str) -> None:
    """Property: Normalization handles arbitrary whitespace, dashes, and MVA prefixes."""
    check_res = calculate_check_digit(first_8)
    if check_res.is_ok:
        d = check_res.unwrap()
        formatted = f" NO {first_8[:3]} - {first_8[3:6]} . {first_8[6:8]}{d} MVA "
        norm = normalize_orgnr_candidate(formatted)
        assert norm == f"{first_8}{d}"

        val_res = validate_orgnr(formatted)
        assert val_res.is_ok
        assert val_res.unwrap() == f"{first_8}{d}"


def test_known_official_norwegian_orgnrs() -> None:
    """Validates real known Norwegian enterprise numbers."""
    known_valid = [
        "923609016",  # Equinor ASA
        "984851006",  # DNB Bank ASA
        "982463718",  # Telenor ASA
        "916627939",  # Norsk Hydro ASA
        "988539473",  # Gjensidige Forsikring ASA
        "933365573",  # Schibsted ASA
        "989061593",  # Yara International ASA
        "886581432",  # Mowi ASA
        "934551435",  # Storebrand ASA
        "984661185",  # Kongsberg Gruppen ASA
    ]
    for org in known_valid:
        res = validate_orgnr(org)
        assert res.is_ok, f"Failed for {org}: {res}"
        assert res.unwrap() == org


def test_invalid_lengths_and_formats() -> None:
    """Rejects strings of incorrect length or non-digits."""
    invalid_cases = [
        "",
        "12345678",  # 8 digits
        "1234567890",  # 10 digits
        "abcdefghi",  # non-numeric
        "923609017",  # wrong check digit
        "000000000",  # trivial sequence
    ]
    for inv in invalid_cases:
        res = validate_orgnr(inv)
        assert res.is_err, f"Should have failed for {inv}"


def test_extract_orgnrs_from_unstructured_text() -> None:
    """Extracts valid orgnrs embedded in arbitrary unstructured documents."""
    doc = (
        "Equinor ASA (orgnr 923 609 016) announced a dividend.\n"
        "Contact DNB Bank at 984851006 or Telenor 982 463 718 MVA.\n"
        "Ignore invalid numbers like 12345678 or 923609017."
    )
    extracted = extract_orgnrs_from_text(doc)
    assert "923609016" in extracted
    assert "984851006" in extracted
    assert "982463718" in extracted
    assert "923609017" not in extracted
    assert len(extracted) == 3
