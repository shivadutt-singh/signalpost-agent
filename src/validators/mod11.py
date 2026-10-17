"""
Norwegian 9-Digit Enterprise Number (organisasjonsnummer) Modulo-11 Checksum & Normalizer.
Standardized according to Brønnøysund Register Centre official specification.
"""

import re
from typing import List, Optional

# Official Modulo 11 weights for Norwegian 9-digit enterprise numbers
MOD11_WEIGHTS = [3, 2, 7, 6, 5, 4, 3, 2]

# Regex pattern for matching 9 digits, possibly separated by spaces or dots/dashes
ORGN_REGEX = re.compile(
    r"(?:NO\s*)?(?:\b|\D)(\d{3}[\s.-]?\d{3}[\s.-]?\d{3})(?:\s*MVA)?(?:\b|\D)",
    re.IGNORECASE
)


def calculate_check_digit(first_8_digits: str) -> Optional[int]:
    """
    Computes the 9th Modulo-11 check digit from the first 8 digits.
    Returns:
        int: check digit (0-9)
        None: if the remainder is 1, indicating an invalid combination that cannot be used.
    """
    if len(first_8_digits) != 8 or not first_8_digits.isdigit():
        return None

    digits = [int(c) for c in first_8_digits]
    weighted_sum = sum(d * w for d, w in zip(digits, MOD11_WEIGHTS))
    remainder = weighted_sum % 11

    if remainder == 0:
        return 0
    elif remainder == 1:
        # Remainder 1 yields check digit 10 (not representable as single digit). Invalid orgnr.
        return None
    else:
        return 11 - remainder


def validate_mod11(orgnr: str) -> bool:
    """
    Verifies if a 9-digit string is a mathematically valid Norwegian organisasjonsnummer
    using the official Modulo-11 checksum.
    In Norway, enterprise numbers never start with 0 (official numbers start with 8 or 9).
    """
    if not isinstance(orgnr, str):
        return False

    clean = "".join(filter(str.isdigit, orgnr))
    if len(clean) != 9 or clean.startswith("0"):
        return False

    expected_check = calculate_check_digit(clean[:8])
    if expected_check is None:
        return False

    actual_check = int(clean[8])
    return actual_check == expected_check


def normalize_orgnr(raw: str) -> str:
    """
    Strips noise, whitespace, MVA/NO prefixes/suffixes, and returns the canonical 9-digit string.
    Raises ValueError if input does not contain a valid Modulo-11 orgnr.
    """
    if not raw or not isinstance(raw, str):
        raise ValueError(f"Orgnr must be a non-empty string, got: {raw!r}")

    # Remove standard Norwegian prefixes/suffixes
    cleaned = raw.strip()
    cleaned = re.sub(r"(?i)\bNO\b", "", cleaned)
    cleaned = re.sub(r"(?i)\bMVA\b", "", cleaned)
    cleaned = re.sub(r"(?i)\borg\.?\s*nr\.?", "", cleaned)

    # Extract only digits
    digits = "".join(filter(str.isdigit, cleaned))

    if len(digits) != 9:
        raise ValueError(
            f"Input '{raw}' normalized to '{digits}', which does not contain exactly 9 digits."
        )

    if not validate_mod11(digits):
        expected = calculate_check_digit(digits[:8])
        raise ValueError(
            f"Modulo-11 checksum verification failed for '{digits}'. "
            f"Expected check digit: {expected}, actual: {digits[8]}."
        )

    return digits


def try_normalize_orgnr(raw: str) -> Optional[str]:
    """Safe normalizer that returns None instead of raising on failure."""
    try:
        return normalize_orgnr(raw)
    except (ValueError, TypeError):
        return None


def extract_orgnrs_from_text(text: str) -> List[str]:
    """
    Scans free-form unstructured text (web pages, PDFs, documents)
    and extracts all unique, valid 9-digit Norwegian enterprise numbers.
    """
    if not text or not isinstance(text, str):
        return []

    found = []
    seen = set()

    for match in ORGN_REGEX.finditer(text):
        raw_match = match.group(1)
        normalized = try_normalize_orgnr(raw_match)
        if normalized and normalized not in seen:
            seen.add(normalized)
            found.append(normalized)

    # Secondary scan for unseparated 9-digit blocks
    for match in re.finditer(r"\b\d{9}\b", text):
        raw_match = match.group(0)
        normalized = try_normalize_orgnr(raw_match)
        if normalized and normalized not in seen:
            seen.add(normalized)
            found.append(normalized)

    return found
