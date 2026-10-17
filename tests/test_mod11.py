"""
Unit tests for Norwegian Modulo-11 enterprise number checksum verification and normalization.
"""

import pytest
from src.validators.mod11 import (
    calculate_check_digit,
    extract_orgnrs_from_text,
    normalize_orgnr,
    try_normalize_orgnr,
    validate_mod11,
)


class TestMod11Validation:
    """Tests for Modulo-11 validation algorithm."""

    def test_valid_official_orgnrs(self):
        """Validates real active Norwegian enterprise numbers."""
        valid_orgnrs = [
            "923609016",  # EQUINOR ASA
            "982463718",  # TELENOR ASA
            "984851006",  # DNB BANK ASA
            "984661185",  # POSTEN BRING AS
            "886581432",  # AKER ASA
        ]
        for orgnr in valid_orgnrs:
            assert validate_mod11(orgnr) is True, f"Failed for valid orgnr: {orgnr}"

    def test_invalid_check_digits(self):
        """Ensures mutated check digits fail validation."""
        invalid_orgnrs = [
            "923609017",  # Correct is 6, altered to 7
            "923609010",  # Correct is 6, altered to 0
            "123456789",  # Arbitrary numbers
            "000000000",  # Zeroes
            "987654321",  # Invalid check digit (expected 5, got 1)
        ]
        for orgnr in invalid_orgnrs:
            assert validate_mod11(orgnr) is False, f"Expected failure for: {orgnr}"

    def test_invalid_lengths_and_formats(self):
        """Ensures wrong string lengths or non-digits fail."""
        assert validate_mod11("92360901") is False  # 8 digits
        assert validate_mod11("9236090160") is False  # 10 digits
        assert validate_mod11("abcdefghi") is False
        assert validate_mod11("") is False
        assert validate_mod11(None) is False

    def test_calculate_check_digit(self):
        """Verifies calculation of 9th check digit from first 8 digits."""
        # 92360901 -> 6 (Equinor)
        assert calculate_check_digit("92360901") == 6
        # 98246371 -> 8 (Telenor)
        assert calculate_check_digit("98246371") == 8
        # 98485100 -> 6 (DNB)
        assert calculate_check_digit("98485100") == 6
        # Invalid input length
        assert calculate_check_digit("12345") is None

    def test_remainder_1_invalid_combination(self):
        """
        Remainder 1 requires check digit 10, which cannot be represented in a single digit.
        Find an 8-digit sequence yielding remainder 1 and verify calculate_check_digit returns None.
        """
        found_remainder_1 = None
        for i in range(10000000, 10000200):
            s = str(i)
            weights = [3, 2, 7, 6, 5, 4, 3, 2]
            w_sum = sum(int(d) * w for d, w in zip(s, weights))
            if w_sum % 11 == 1:
                found_remainder_1 = s
                break

        assert found_remainder_1 is not None
        assert calculate_check_digit(found_remainder_1) is None


class TestMod11Normalizer:
    """Tests for regex-based and formatting normalizers."""

    def test_normalize_clean(self):
        assert normalize_orgnr("923609016") == "923609016"

    def test_normalize_with_spaces_and_mva(self):
        assert normalize_orgnr("NO 923 609 016 MVA") == "923609016"
        assert normalize_orgnr("923 609 016") == "923609016"
        assert normalize_orgnr("923-609-016") == "923609016"
        assert normalize_orgnr("Org.nr. 923.609.016") == "923609016"

    def test_normalize_raises_on_invalid(self):
        with pytest.raises(ValueError):
            normalize_orgnr("123456789")

        with pytest.raises(ValueError):
            normalize_orgnr("not-an-orgnr")

    def test_try_normalize_orgnr(self):
        assert try_normalize_orgnr("NO 923 609 016 MVA") == "923609016"
        assert try_normalize_orgnr("invalid") is None


class TestTextExtraction:
    """Tests for extracting valid enterprise numbers from arbitrary text."""

    def test_extract_orgnrs_from_unstructured_text(self):
        text = """
        Velkommen til vår nettside. Vår juridiske enhet er Equinor ASA med orgnr NO 923 609 016 MVA.
        Vår samarbeidspartner er Telenor ASA (org.nr 982463718).
        Telefonnummeret vårt er 51 99 00 00 og tilfeldig tall 123456789 (ugyldig orgnr).
        Gjentakelse av 923609016 bør dedupliseres.
        """
        extracted = extract_orgnrs_from_text(text)
        assert "923609016" in extracted
        assert "982463718" in extracted
        assert "123456789" not in extracted
        assert len(extracted) == 2
