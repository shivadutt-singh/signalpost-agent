"""
Validation modules for Norwegian company identifiers and anti-hallucination guardrails.
"""

from src.validators.mod11 import (
    validate_mod11,
    calculate_check_digit,
    normalize_orgnr,
    try_normalize_orgnr,
    extract_orgnrs_from_text,
)
from src.validators.fact_guard import (
    EntityAnchor,
    EntityVerificationResult,
    EntityVerificationGate,
    AntiFabricationGuard,
    FabricationCheckResult,
    verify_financial_fact,
)

__all__ = [
    "validate_mod11",
    "calculate_check_digit",
    "normalize_orgnr",
    "try_normalize_orgnr",
    "extract_orgnrs_from_text",
    "EntityAnchor",
    "EntityVerificationResult",
    "EntityVerificationGate",
    "AntiFabricationGuard",
    "FabricationCheckResult",
    "verify_financial_fact",
]
