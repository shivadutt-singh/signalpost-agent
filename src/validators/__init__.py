"""
Validation modules for Norwegian company identifiers and anti-hallucination guardrails.
"""

from src.validators.fact_critic import FactCritic, FactCriticPipeline
from src.validators.fact_guard import (
    AntiFabricationGuard,
    EntityAnchor,
    EntityVerificationGate,
    EntityVerificationResult,
    FabricationCheckResult,
    verify_financial_fact,
)
from src.validators.mod11 import (
    calculate_check_digit,
    extract_orgnrs_from_text,
    normalize_orgnr,
    try_normalize_orgnr,
    validate_mod11,
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
    "FactCriticPipeline",
]
