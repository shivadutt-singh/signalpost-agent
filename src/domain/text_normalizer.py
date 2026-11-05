"""
Cross-Platform UTF-8 & Norwegian Character Integrity Engine (v7.0 Red-Team Edition).

Handles NFC normalization, repairs Mojibake (e.g. 'Ã¸' -> 'ø', 'Ã¥' -> 'å'),
provides POSIX path normalization for Linux harness compatibility, and guarantees
robust matching of Norwegian company names containing 'æ', 'ø', 'å'.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Canonical Mojibake patterns resulting from UTF-8 bytes read as Windows-1252 / ISO-8859-1
MOJIBAKE_MAP = {
    "Ã¸": "ø",
    "Ã¥": "å",
    "Ã¦": "æ",
    "Ã˜": "Ø",
    "Ã…": "Å",
    "Ã†": "Æ",
    "Ã©": "é",
    "Ã¨": "è",
    "Ã‰": "É",
    "Ã¢": "â",
    "Ã¶": "ö",
    "Ã¤": "ä",
    "Ã¼": "ü",
    "â€“": "–",  # En-dash
    "â€”": "—",  # Em-dash
    "â€™": "’",  # Right single quote
    "â€˜": "‘",  # Left single quote
    "â€œ": "“",  # Left double quote
    "â€\x9d": "”", # Right double quote
    "â€": "”",
    "Â ": " ",   # Non-breaking space artifact
    "Â": "",
    "": "",     # Unicode replacement character
}

MOJIBAKE_SIGNATURES = [
    "Ã¸", "Ã¥", "Ã¦", "Ã˜", "Ã…", "Ã†", "Ã©", "Ã¨", "â€“", "â€”", "â€™", "â€œ", "Â "
]


def normalize_norwegian_text(text: str) -> str:
    """
    Normalizes Norwegian text across platforms:
    1. Converts Unicode to NFC composition.
    2. Detects and repairs known UTF-8 / Windows-1252 Mojibake sequences.
    3. Cleans whitespace while preserving Norwegian characters (æ, ø, å).
    """
    if not text:
        return ""

    # Step 1: NFC normalization
    normalized = unicodedata.normalize("NFC", text)

    # Step 2: Attempt standard Latin-1 / UTF-8 re-decode if double-encoded
    if any(sig in normalized for sig in MOJIBAKE_SIGNATURES):
        try:
            re_decoded = normalized.encode("latin1").decode("utf-8")
            normalized = unicodedata.normalize("NFC", re_decoded)
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Fall back to dictionary replacement
            for bad, good in MOJIBAKE_MAP.items():
                if bad in normalized:
                    normalized = normalized.replace(bad, good)

    # Final pass dictionary replacement for residual artifacts
    for bad, good in MOJIBAKE_MAP.items():
        if bad in normalized:
            normalized = normalized.replace(bad, good)

    return normalized


def has_mojibake(text: str) -> bool:
    """Returns True if text contains corrupted UTF-8 / Latin-1 Mojibake artifacts."""
    if not text:
        return False
    return any(sig in text for sig in MOJIBAKE_SIGNATURES)


def to_posix_path(path: str | Path) -> str:
    """
    Normalizes any file path string or Path object to use forward slashes (POSIX format).
    Guarantees Linux evaluators do not fail when reading generated JSONs.
    """
    if isinstance(path, Path):
        return path.as_posix()
    return str(path).replace("\\", "/")


def clean_company_name_match(haystack: str, canonical_name: str) -> bool:
    """
    Checks if a canonical Norwegian company name is present in text,
    normalizing Norwegian characters (æ, ø, å) and respecting word boundaries.
    """
    norm_haystack = " " + normalize_norwegian_text(haystack).upper() + " "
    norm_target = normalize_norwegian_text(canonical_name).strip().upper()

    if not norm_target:
        return False

    # Exact string match with word boundary
    pattern = r"\b" + re.escape(norm_target) + r"\b"
    if re.search(pattern, norm_haystack):
        return True

    # Fallback to direct substring match if legal suffix varies slightly
    return f" {norm_target} " in norm_haystack
