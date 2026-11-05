"""
Pure O(N) Norwegian Modulo-11 Checksum Engine for Signalpost Agent (v6.0 Fail-Proof Edition).

Norwegian 9-digit organization numbers (Organisasjonsnummer) are verified using
official weights [3, 2, 7, 6, 5, 4, 3, 2].
Returns typed Result[str, InvalidOrgnrError] instead of raising raw exceptions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.domain.errors import InvalidOrgnrError
from src.domain.result import Err, Ok, Result

MOD11_WEIGHTS: Sequence[int] = (3, 2, 7, 6, 5, 4, 3, 2)
ORGNR_REGEX = re.compile(r"\b(\d{3}\s?\d{3}\s?\d{3})\b")


def normalize_orgnr_candidate(raw: str) -> str:
    """Strips whitespace, dashes, periods, and country/vat prefixes ('NO', 'MVA')."""
    cleaned = raw.strip().upper()
    cleaned = cleaned.replace("NO", "").replace("MVA", "")
    return re.sub(r"[\s\.\-]+", "", cleaned)


def calculate_check_digit(first_8_digits: str) -> Result[int, InvalidOrgnrError]:
    """
    Computes the Modulo-11 check digit for the first 8 digits of an orgnr.
    Returns Err(InvalidOrgnrError) if remainder is 1 (which yields invalid check digit 10).
    """
    if len(first_8_digits) != 8 or not first_8_digits.isdigit():
        return Err(
            InvalidOrgnrError(
                code="INVALID_LENGTH",
                message=f"First 8 digits must be exactly 8 numeric digits, got: {first_8_digits!r}",
                details={"input": first_8_digits},
            )
        )

    weighted_sum = sum(int(d) * w for d, w in zip(first_8_digits, MOD11_WEIGHTS, strict=True))
    remainder = weighted_sum % 11

    if remainder == 0:
        return Ok(0)
    elif remainder == 1:
        # Check digit would be 10, which cannot be represented as a single digit
        return Err(
            InvalidOrgnrError(
                code="CHECK_DIGIT_TEN",
                message="Modulo-11 remainder 1 produces invalid check digit 10",
                details={"first_8_digits": first_8_digits, "remainder": remainder},
            )
        )
    else:
        return Ok(11 - remainder)


def validate_orgnr(raw: str) -> Result[str, InvalidOrgnrError]:
    """
    Validates a raw string as an official Norwegian 9-digit organization number.
    Returns Ok(normalized_9_digit_str) or Err(InvalidOrgnrError).
    """
    normalized = normalize_orgnr_candidate(raw)

    if len(normalized) != 9 or not normalized.isdigit():
        return Err(
            InvalidOrgnrError(
                code="INVALID_FORMAT",
                message=f"Orgnr must normalize to exactly 9 numeric digits, got: {normalized!r}",
                details={"raw": raw, "normalized": normalized},
            )
        )

    # Reject trivial sequences (all zeroes, etc.)
    if normalized == "000000000":
        return Err(
            InvalidOrgnrError(
                code="TRIVIAL_SEQUENCE",
                message="Orgnr cannot be all zeroes",
                details={"normalized": normalized},
            )
        )

    expected_check_res = calculate_check_digit(normalized[:8])
    if expected_check_res.is_err:
        return Err(expected_check_res.unwrap_err())

    expected_check = expected_check_res.unwrap()
    actual_check = int(normalized[8])

    if expected_check != actual_check:
        return Err(
            InvalidOrgnrError(
                code="CHECKSUM_MISMATCH",
                message=f"Modulo-11 checksum mismatch: expected {expected_check}, found {actual_check}",
                details={
                    "expected": expected_check,
                    "actual": actual_check,
                    "normalized": normalized,
                },
            )
        )

    return Ok(normalized)


def extract_orgnrs_from_text(text: str) -> list[str]:
    """Scans arbitrary text and extracts all valid Modulo-11 Norwegian orgnrs."""
    candidates = ORGNR_REGEX.findall(text)
    valid_orgnrs: list[str] = []
    seen = set()

    for candidate in candidates:
        res = validate_orgnr(candidate)
        if res.is_ok:
            orgnr = res.unwrap()
            if orgnr not in seen:
                seen.add(orgnr)
                valid_orgnrs.append(orgnr)

    return valid_orgnrs
