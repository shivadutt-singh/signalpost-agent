"""
Unit & Integration Tests for Pre-Commit FactCriticPipeline (Zero-Disqualification Firewall).
Verifies the 4 critical gates:
1. Evidence URL Check
2. Date Sanity Check
3. Financial Substring Lock
4. Entity Contamination Lock
and confirms that failing facts are properly quarantined without raising exceptions.
"""

from datetime import datetime, timezone, timedelta
import pytest

from src.models.schema import (
    CompanyProfile,
    ExecutionTelemetry,
    FactAuditHistory,
    FactCategory,
    FactEvidence,
    SourceTier,
)
from src.validators.fact_critic import FactCriticPipeline


class TestFactCriticPipeline:
    """Verifies that invalid or hallucinated facts are strictly quarantined before profile persistence."""

    def _create_base_profile(self) -> CompanyProfile:
        return CompanyProfile(
            orgnr="923609016",
            legal_name="EQUINOR ASA",
            canonical_status="ACTIVE",
            profile_verification_explanation="Verified from official Brønnøysund registry.",
            facts={},
        )

    def test_pass_valid_registry_fact(self):
        profile = self._create_base_profile()
        profile.facts["registry_core.orgnr"] = FactAuditHistory(
            fact_key="registry_core.orgnr",
            current_value="923609016",
            category=FactCategory.REGISTRY_CORE,
            current_evidence=FactEvidence(
                source_url="https://data.brreg.no/enhetsregisteret/api/enheter/923609016",
                source_tier=SourceTier.OFFICIAL_REGISTRY,
                source_date="2025-01-01",
                entity_match_method="EXACT_ORGNR_API",
                verification_explanation="Official ground truth",
            ),
        )

        telemetry = ExecutionTelemetry(session_id="test_sess")
        audited = FactCriticPipeline.audit_profile(profile, telemetry=telemetry)

        assert "registry_core.orgnr" in audited.facts
        assert len(telemetry.quarantined_facts) == 0

    def test_quarantine_missing_or_invalid_evidence_url(self):
        profile = self._create_base_profile()
        # Invalid URL: not starting with http/https
        profile.facts["operations.bad_url"] = FactAuditHistory(
            fact_key="operations.bad_url",
            current_value="Some claim",
            category=FactCategory.OPERATIONS,
            current_evidence=FactEvidence(
                source_url="ftp://example.com/file",
                source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                source_date="2025-01-01",
                entity_match_method="EXACT_ORGNR_ON_PAGE",
                verification_explanation="Bad scheme",
            ),
        )

        telemetry = ExecutionTelemetry(session_id="test_sess")
        audited = FactCriticPipeline.audit_profile(profile, telemetry=telemetry)

        assert "operations.bad_url" not in audited.facts
        assert len(telemetry.quarantined_facts) == 1
        assert "EVIDENCE_URL_CHECK_FAILED" in telemetry.quarantined_facts[0]["reason"]

    def test_quarantine_future_source_date(self):
        profile = self._create_base_profile()
        future_date = (datetime.now(timezone.utc) + timedelta(days=400)).strftime("%Y-%m-%d")

        profile.facts["operations.future_news"] = FactAuditHistory(
            fact_key="operations.future_news",
            current_value="Future announcement",
            category=FactCategory.OPERATIONS,
            current_evidence=FactEvidence(
                source_url="https://equinor.com/news/future",
                source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                source_date=future_date,
                entity_match_method="EXACT_LEGAL_NAME_ON_OWN_DOMAIN",
                verification_explanation="Future timestamp",
            ),
        )

        telemetry = ExecutionTelemetry(session_id="test_sess")
        audited = FactCriticPipeline.audit_profile(profile, telemetry=telemetry)

        assert "operations.future_news" not in audited.facts
        assert len(telemetry.quarantined_facts) == 1
        assert "DATE_SANITY_CHECK_FAILED" in telemetry.quarantined_facts[0]["reason"]

    def test_quarantine_financial_fact_without_digits_in_quote(self):
        profile = self._create_base_profile()
        # Non-Brreg financial fact with mismatched quote
        profile.facts["financial.revenue_2024"] = FactAuditHistory(
            fact_key="financial.revenue_2024",
            current_value=85000000.0,
            category=FactCategory.FINANCIAL,
            current_evidence=FactEvidence(
                source_url="https://example.com/reports",
                source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                source_date="2024-12-31",
                verbatim_quote="Total revenue for the year reached twelve million dollars.",
                entity_match_method="EXACT_ORGNR_ON_PAGE",
                verification_explanation="Mismatched digits",
            ),
        )

        telemetry = ExecutionTelemetry(session_id="test_sess")
        audited = FactCriticPipeline.audit_profile(profile, telemetry=telemetry)

        assert "financial.revenue_2024" not in audited.facts
        assert len(telemetry.quarantined_facts) == 1
        assert "FINANCIAL_SUBSTRING_LOCK_FAILED" in telemetry.quarantined_facts[0]["reason"]

    def test_quarantine_web_fact_with_unpermitted_entity_method(self):
        profile = self._create_base_profile()
        profile.facts["operations.job_opening"] = FactAuditHistory(
            fact_key="operations.job_opening",
            current_value="Software Engineer",
            category=FactCategory.OPERATIONS,
            current_evidence=FactEvidence(
                source_url="https://random-blog.com/jobs",
                source_tier=SourceTier.VERIFIED_PUBLIC_WEB,
                source_date="2025-01-01",
                entity_match_method="UNVERIFIED_FUZZY_MATCH",  # Not in permitted set
                verification_explanation="Fuzzy guess",
            ),
        )

        telemetry = ExecutionTelemetry(session_id="test_sess")
        audited = FactCriticPipeline.audit_profile(profile, telemetry=telemetry)

        assert "operations.job_opening" not in audited.facts
        assert len(telemetry.quarantined_facts) == 1
        assert "ENTITY_CONTAMINATION_LOCK_FAILED" in telemetry.quarantined_facts[0]["reason"]
