"""Pure Domain Layer for Signalpost Agent (v6.0 Fail-Proof Edition)."""

from src.domain.accounting_guard import (
    calculate_equity_ratio,
    calculate_operating_margin,
    calculate_yoy_growth,
    parse_norwegian_number,
    reconcile_multi_year_series,
    verify_number_digits_in_evidence,
)
from src.domain.entities import (
    AnnualAccount,
    CandidateFact,
    CompanyFact,
    CompanyProfile,
    EntityAnchor,
    ExecutiveSummary,
    JobPosting,
    SourceTier,
)
from src.domain.entity_gate import (
    EXTERNAL_POISON_ROLES,
    INTERNAL_GOVERNANCE_ROLES,
    KNOWN_POISON_ENTITIES,
    SHARED_UMBRELLA_DOMAINS,
    classify_brreg_roller,
    extract_domain_host,
    is_shared_umbrella_domain,
    split_dom_into_blocks,
    verify_dom_block_ownership,
)
from src.domain.errors import (
    CircuitBreakerOpenError,
    DomainError,
    EntityNotFoundError,
    FactCriticVerificationError,
    FinancialReconciliationError,
    HttpBudgetExceededError,
    InvalidOrgnrError,
    NetworkTimeoutError,
    ParsingError,
    PoisonEntityBleedError,
    RateLimitExceededError,
    StorageError,
)
from src.domain.fact_critic import (
    verify_all_candidates,
    verify_anti_bleed_lock,
    verify_candidate_fact,
    verify_number_lock,
    verify_verbatim_quote_substring,
)
from src.domain.mod11 import (
    calculate_check_digit,
    extract_orgnrs_from_text,
    normalize_orgnr_candidate,
    validate_orgnr,
)
from src.domain.result import Err, Ok, Result

__all__ = [
    # Result
    "Result",
    "Ok",
    "Err",
    # Errors
    "DomainError",
    "InvalidOrgnrError",
    "EntityNotFoundError",
    "PoisonEntityBleedError",
    "FinancialReconciliationError",
    "FactCriticVerificationError",
    "HttpBudgetExceededError",
    "NetworkTimeoutError",
    "RateLimitExceededError",
    "CircuitBreakerOpenError",
    "StorageError",
    "ParsingError",
    # Entities
    "SourceTier",
    "CandidateFact",
    "CompanyFact",
    "AnnualAccount",
    "JobPosting",
    "ExecutiveSummary",
    "EntityAnchor",
    "CompanyProfile",
    # Mod11
    "validate_orgnr",
    "calculate_check_digit",
    "normalize_orgnr_candidate",
    "extract_orgnrs_from_text",
    # Entity Gate
    "INTERNAL_GOVERNANCE_ROLES",
    "EXTERNAL_POISON_ROLES",
    "SHARED_UMBRELLA_DOMAINS",
    "KNOWN_POISON_ENTITIES",
    "extract_domain_host",
    "is_shared_umbrella_domain",
    "classify_brreg_roller",
    "split_dom_into_blocks",
    "verify_dom_block_ownership",
    # Accounting Guard
    "parse_norwegian_number",
    "verify_number_digits_in_evidence",
    "calculate_yoy_growth",
    "calculate_operating_margin",
    "calculate_equity_ratio",
    "reconcile_multi_year_series",
    # Fact Critic
    "verify_verbatim_quote_substring",
    "verify_number_lock",
    "verify_anti_bleed_lock",
    "verify_candidate_fact",
    "verify_all_candidates",
]
