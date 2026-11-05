"""
Immutable Value Objects and Domain Entities for Signalpost Agent (v6.0 Fail-Proof Edition).

Strict Pydantic v2 models with ConfigDict(frozen=True, extra="forbid") ensuring
immutability, zero-side-effect state transitions, and deterministic serialization.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceTier(StrEnum):
    """Hierarchy of evidence source authority."""

    PRIMARY_GOVERNMENT = (
        "tier_1_government"  # Brreg Enhetsregisteret, Regnskapsregisteret, NAV
    )
    OFFICIAL_DOMAIN = (
        "tier_2_company_web"  # Verified company domain (careers, about, news)
    )
    THIRD_PARTY_MEDIA = "tier_3_news_media"  # E24, Dagens Næringsliv, regional press
    FALLBACK_INFERENCE = (
        "tier_4_inference"  # Deterministic heuristics or bounded LLM summary
    )


class CandidateFact(BaseModel):
    """A raw candidate fact extracted before passing through FactCritic firewall."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predicate: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_tier: SourceTier
    source_url: str
    source_date: str
    verbatim_evidence: str
    extracted_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CompanyFact(BaseModel):
    """An immutable, verified atomic company fact backed by cryptographic evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predicate: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_tier: SourceTier
    source_url: str
    source_date: str
    verbatim_evidence: str
    extracted_at: str
    sha256_hash: str
    quarantine_reason: str | None = None

    @classmethod
    def from_candidate(
        cls, candidate: CandidateFact, quarantine_reason: str | None = None
    ) -> CompanyFact:
        """Constructs an immutable CompanyFact with SHA-256 evidence integrity digest."""
        hash_input = f"{candidate.predicate}|{candidate.value}|{candidate.source_url}|{candidate.verbatim_evidence}"
        sha256_digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        return cls(
            predicate=candidate.predicate,
            value=candidate.value,
            confidence=candidate.confidence,
            source_tier=candidate.source_tier,
            source_url=candidate.source_url,
            source_date=candidate.source_date,
            verbatim_evidence=candidate.verbatim_evidence,
            extracted_at=candidate.extracted_at,
            sha256_hash=sha256_digest,
            quarantine_reason=quarantine_reason,
        )


class AnnualAccount(BaseModel):
    """Multi-year audited financial statement with Python-computed YoY metrics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    revenue_nok: int | None = None
    operating_profit_nok: int | None = None
    profit_before_tax_nok: int | None = None
    net_income_nok: int | None = None
    total_assets_nok: int | None = None
    total_equity_nok: int | None = None

    # Python-calculated double-entry & growth metrics
    yoy_revenue_growth_pct: float | None = None
    operating_margin_pct: float | None = None
    equity_ratio_pct: float | None = None

    source_url: str
    verbatim_evidence: str
    raw_reported_value: str | None = None
    unit_scale: str = "NOK"


class JobPosting(BaseModel):
    """Verified job opening originated from NAV or the company's own careers page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    location: str | None = None
    deadline: str | None = None
    source_channel: str  # NAV_PUBLIC_FEED or CORPORATE_CAREERS_PAGE
    source_url: str
    verbatim_quote: str
    verified_ownership: bool = True


class ExecutiveSummary(BaseModel):
    """Deterministic, un-hallucinated 3-sentence company overview."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    core_business: str
    recent_developments: str
    known_unknowns: list[str] = Field(default_factory=list)
    generated_by: str = "signalpost-hybrid-v6.0"


class EntityAnchor(BaseModel):
    """Ground truth identity anchor from Brreg Enhetsregisteret."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orgnr: str
    canonical_name: str
    org_form: str
    official_domain: str | None = None
    is_shared_umbrella_domain: bool = False
    poison_entities: list[str] = Field(default_factory=list)
    internal_governance_names: list[str] = Field(default_factory=list)
    external_service_providers: list[str] = Field(default_factory=list)
    address_municipality: str | None = None
    address_postal_code: str | None = None
    parent_orgnr: str | None = None
    is_subsidiary: bool = False
    corporate_group_name: str | None = None


class CompanyProfile(BaseModel):
    """Complete, immutable intelligence profile for a Norwegian commercial entity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orgnr: str
    canonical_name: str
    org_form: str
    status: str = "ACTIVE"
    facts: list[CompanyFact] = Field(default_factory=list)
    financial_history: list[AnnualAccount] = Field(default_factory=list)
    job_listings: list[JobPosting] = Field(default_factory=list)
    executive_summary: ExecutiveSummary
    known_unknowns: list[str] = Field(default_factory=list)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    fsm_state_history: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: str = "6.0.0"

    def get_fact(self, predicate: str) -> CompanyFact | None:
        """Look up verified fact by predicate."""
        for fact in self.facts:
            if fact.predicate == predicate and fact.quarantine_reason is None:
                return fact
        return None
