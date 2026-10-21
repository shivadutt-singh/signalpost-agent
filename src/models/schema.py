"""
Strict Pydantic v2 Models for Facts, Evidence, Profiles, Delta Logs, and Telemetry.
Enforces verifiable data integrity, temporal history, and auditability.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class SourceTier(str, Enum):
    """
    Hierarchical authority tier for information sources.
    Higher tier wins on timestamp collision.
    """
    OFFICIAL_REGISTRY = "official_registry"      # Brønnøysund Register Centre API (Tier 1 - Ground Truth)
    OFFICIAL_HOMEPAGE = "official_homepage"      # Primary company website verified via registry (Tier 2)
    VERIFIED_PUBLIC_WEB = "verified_public_web"  # Targeted public records (Tier 3)

    @property
    def priority(self) -> int:
        tiers = {
            SourceTier.OFFICIAL_REGISTRY: 3,
            SourceTier.OFFICIAL_HOMEPAGE: 2,
            SourceTier.VERIFIED_PUBLIC_WEB: 1,
        }
        return tiers[self]


class FactCategory(str, Enum):
    """Category classification for company facts."""
    REGISTRY_CORE = "registry_core"
    GOVERNANCE = "governance"
    FINANCIAL = "financial"
    OPERATIONS = "operations"
    CONTACT_AND_WEB = "contact_and_web"
    STRUCTURE = "structure"


class FactEvidence(BaseModel):
    """Verifiable cryptographic and textual evidence backing an extracted fact."""
    source_url: str = Field(..., description="Canonical source URL where fact was observed")
    source_tier: SourceTier = Field(..., description="Authority tier of the source")
    source_date: Optional[str] = Field(
        None,
        description="ISO-8601 date when source fact was published or reported (YYYY-MM-DD or ISO datetime)"
    )
    retrieval_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 timestamp when the agent fetched this evidence"
    )
    verbatim_quote: Optional[str] = Field(
        None,
        description="Verbatim exact quote extracted from raw page content (mandatory for numeric/financial facts)"
    )
    entity_match_rule: Optional[str] = Field(
        None,
        description="Rule used to verify entity match (e.g., ORGNR_MATCH, OFFICIAL_DOMAIN_MATCH, NAME_AND_LOCATION_MATCH)"
    )
    entity_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Deterministic confidence score from Entity Verification Gate"
    )
    raw_digits_verified: bool = Field(
        default=False,
        description="True if digits were deterministically verified inside verbatim_quote and raw source text"
    )
    verification_explanation: str = Field(
        ...,
        description="High-signal explanation detailing source authority and entity resolution method"
    )
    source_content_sha256: Optional[str] = Field(
        None,
        description="SHA-256 hash of the normalized source text snippet ensuring cryptographic provenance"
    )


class ExtractedFactCandidate(BaseModel):
    """Raw candidate fact produced by collectors/extractors before profile merging."""
    fact_key: str = Field(..., description="Canonical dot-delimited fact key, e.g., 'financial.revenue_2023'")
    value: Any = Field(..., description="Extracted structured value")
    category: FactCategory = Field(default=FactCategory.REGISTRY_CORE)
    unit: Optional[str] = Field(None, description="Optional measurement unit (e.g., NOK, USD, count)")
    period_or_year: Optional[str] = Field(None, description="Target accounting period or year, if applicable")
    evidence: FactEvidence = Field(..., description="Evidence supporting this candidate fact")


class PreviousValue(BaseModel):
    """Historical value superseded during profile updates, retained for auditability."""
    value: Any
    source_url: str
    source_date: Optional[str] = None
    replaced_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    reason: str = Field(..., description="Why this value was superseded (e.g., SUPERSEDED_BY_NEWER_DATE)")


class FactAuditHistory(BaseModel):
    """Active fact state with temporal audit history and backward lineage."""
    fact_key: str
    current_value: Any
    category: FactCategory = FactCategory.REGISTRY_CORE
    unit: Optional[str] = None
    period_or_year: Optional[str] = None
    current_evidence: FactEvidence
    previous_values: List[PreviousValue] = Field(default_factory=list)


class ChangeLogEntry(BaseModel):
    """Granular delta record for each fact evaluated during an update."""
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    fact_key: str
    action: str = Field(
        ...,
        description="CREATED | UNCHANGED | SUPERSEDED | REJECTED_STALE | REJECTED_FABRICATION | REJECTED_ENTITY_MISMATCH"
    )
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    reason: str


class ProfileDeltaLog(BaseModel):
    """Execution delta log produced when updating a company profile."""
    orgnr: str
    run_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    changes: List[ChangeLogEntry] = Field(default_factory=list)
    facts_created: int = 0
    facts_updated: int = 0
    facts_unchanged: int = 0
    facts_rejected: int = 0


class CompanyProfile(BaseModel):
    """Complete, verifiable JSON profile of an enterprise."""
    orgnr: str = Field(..., description="9-digit Norwegian Company Number (Modulo-11 validated)")
    legal_name: str = Field(..., description="Canonical legal name from official registry")
    canonical_status: str = Field(default="ACTIVE", description="Corporate status: ACTIVE, BANKRUPT, LIQUIDATED, etc.")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = "1.0.0"
    profile_verification_explanation: str = Field(
        ...,
        description="Synthesized summary of source verification, official cross-referencing, and entity integrity"
    )
    executive_summary: Optional[str] = Field(
        None,
        description="Crisp 3-sentence synthesis of products/services, recent changes, and known unknowns"
    )
    known_unknowns: List[str] = Field(
        default_factory=list,
        description="Explicit list of verifiable data points that were sought but remain unconfirmed"
    )
    facts: Dict[str, FactAuditHistory] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("orgnr")
    @classmethod
    def validate_orgnr_format(cls, v: str) -> str:
        clean = "".join(filter(str.isdigit, str(v)))
        if len(clean) != 9:
            raise ValueError(f"Orgnr must contain exactly 9 digits, got '{v}'")
        return clean


class ExecutionTelemetry(BaseModel):
    """Comprehensive performance and resource telemetry manifest."""
    session_id: str
    start_time: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    end_time: Optional[str] = None
    total_duration_seconds: float = 0.0
    companies_processed: int = 0
    companies_succeeded: int = 0
    companies_failed: int = 0

    # HTTP request accounting (Web Outbound Requests strictly capped at 18 per company)
    total_http_requests: int = 0
    web_outbound_requests: int = 0
    http_requests_per_company: Dict[str, int] = Field(default_factory=dict)

    # LLM API Calls (Tracked separately from web scraping calls)
    llm_api_calls: int = 0
    nvidia_primary_calls: int = 0
    nvidia_fallback_calls: int = 0

    # LLM Token & Cost Accounting (Budget cap: max $9.50 per 100 companies, target < $0.015)
    total_llm_input_tokens: int = 0
    total_llm_output_tokens: int = 0
    total_llm_cost_usd: float = 0.0
    llm_cost_per_company: Dict[str, float] = Field(default_factory=dict)

    # Guardrail metrics (Disqualification prevention)
    entity_gate_rejections: int = 0
    anti_fabrication_rejections: int = 0
    budget_violations: List[str] = Field(default_factory=list)
