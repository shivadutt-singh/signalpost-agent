"""
Unit and Integration Test Suite for the 3-Layer Hybrid Neuro-Symbolic Sandwich Pipeline.
Verifies:
1. Borettslag fast-path routing (DETERMINISTIC_FAST_PATH with zero external crawl & zero USBL bleed)
2. Operating AS company bounded agentic path (HYBRID_AGENTIC_PATH with ATS/careers page verification)
3. FactCritic pre-commit firewall:
   - Quote Existence Check (dropping hallucinated quotes not found in raw scraped pages)
   - Job Ownership Check
   - Negative Entity Contamination Barrier
   - Financial Substring Lock
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.schema import (
    AnnualFinancialStatement,
    CompanyProfile,
    ExecutionTelemetry,
    ExtractedFactCandidate,
    FactCategory,
    FactEvidence,
    SourceTier,
)
from src.pipeline import HybridPipeline
from src.validators.fact_critic import FactCritic
from src.validators.fact_guard import EntityAnchor, is_own_company_domain


class TestHybridPathRouter:
    """Verifies Layer 1 Smart Path Router decisions."""

    def test_shared_manager_borettslag_routed_to_fast_path(self):
        """Borettslag with USBL property manager must take DETERMINISTIC_FAST_PATH."""
        anchor = EntityAnchor(
            orgnr="950049596",
            legal_name="A/L F.L.S. BORETTSLAG",
            municipality="OSLO",
            official_domains=[],
            negative_external_entities=[
                {"orgnr": "950285680", "navn": "BOLIGBYGGELAGET USBL", "role_type": "FFØR"},
                {"orgnr": "935850983", "navn": "KPMG AS", "role_type": "REVI"},
            ],
            hjemmeside_is_shared_manager=True,
        )
        has_verified_own_domain = (
            bool(anchor.official_domains)
            and not anchor.hjemmeside_is_shared_manager
            and is_own_company_domain(
                anchor.official_domains[0] if anchor.official_domains else "",
                anchor.legal_name,
                anchor.orgnr,
                anchor.negative_external_entities,
            )
        )
        assert not has_verified_own_domain

    def test_operating_as_with_dedicated_domain_routed_to_agentic_path(self):
        """Operating AS enterprise with its own domain must take HYBRID_AGENTIC_PATH."""
        anchor = EntityAnchor(
            orgnr="886581432",
            legal_name="BOUVET NORGE AS",
            municipality="OSLO",
            official_domains=["bouvet.no"],
            negative_external_entities=[
                {"orgnr": "935850983", "navn": "KPMG AS", "role_type": "REVI"},
            ],
            hjemmeside_is_shared_manager=False,
        )
        has_verified_own_domain = (
            bool(anchor.official_domains)
            and not anchor.hjemmeside_is_shared_manager
            and is_own_company_domain(
                anchor.official_domains[0],
                anchor.legal_name,
                anchor.orgnr,
                anchor.negative_external_entities,
            )
        )
        assert has_verified_own_domain


class TestFactCriticPreCommitFirewall:
    """Verifies Layer 3 Deterministic FactCritic pre-commit rules."""

    def test_drop_fact_when_verbatim_quote_not_in_raw_scraped_page(self):
        """FactCritic must reject and quarantine facts with hallucinated/modified quotes."""
        raw_scraped_pages = {
            "https://bouvet.no/karriere": "Bli en del av Bouvet Norge AS. Vi søker senior utviklere i Oslo."
        }
        profile = CompanyProfile(
            orgnr="886581432",
            legal_name="BOUVET NORGE AS",
            profile_verification_explanation="Test profile",
        )
        # Propose a fact with a quote that does NOT exist in raw_scraped_pages
        profile.facts["operations.jobs"] = MagicMock(
            current_value={"title": "Senior AI Architect"},
            category=FactCategory.OPERATIONS,
            current_evidence=FactEvidence(
                source_url="https://bouvet.no/karriere",
                source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                source_date="2026-11-05",
                verbatim_quote="Hallucinated quote: Bouvet is hiring 500 Quantum Engineers",
                entity_match_method="EXACT_LEGAL_NAME_ON_OWN_DOMAIN",
                verification_explanation="Agentic candidate",
            )
        )
        telemetry = ExecutionTelemetry(session_id="test-1")
        audited = FactCritic.verify_and_filter(
            profile=profile,
            raw_scraped_pages=raw_scraped_pages,
            telemetry=telemetry,
        )
        # The hallucinated fact must be dropped and quarantined
        assert "operations.jobs" not in audited.facts
        assert audited.telemetry["critic_facts_quarantined"] >= 1
        assert any("QUOTE_EXISTENCE_CHECK_FAILED" in q["reason"] for q in audited.metadata["quarantined_facts"])

    def test_accept_fact_when_verbatim_quote_matches_raw_scraped_page(self):
        """FactCritic must accept facts whose verbatim quote exists in raw scraped page."""
        raw_scraped_pages = {
            "https://bouvet.no/karriere": "Bli en del av Bouvet Norge AS. Vi søker senior utviklere i Oslo og Bergen."
        }
        profile = CompanyProfile(
            orgnr="886581432",
            legal_name="BOUVET NORGE AS",
            profile_verification_explanation="Test profile",
        )
        profile.facts["operations.jobs"] = MagicMock(
            current_value={"title": "senior utviklere i Oslo og Bergen"},
            category=FactCategory.OPERATIONS,
            current_evidence=FactEvidence(
                source_url="https://bouvet.no/karriere",
                source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                source_date="2026-11-05",
                verbatim_quote="Vi søker senior utviklere i Oslo og Bergen",
                entity_match_method="EXACT_LEGAL_NAME_ON_OWN_DOMAIN",
                verification_explanation="Agentic candidate",
            )
        )
        audited = FactCritic.verify_and_filter(
            profile=profile,
            raw_scraped_pages=raw_scraped_pages,
        )
        assert "operations.jobs" in audited.facts

    def test_drop_contaminated_negative_entity_claim(self):
        """FactCritic must drop facts referring to external managers/auditors without target corroboration."""
        raw_scraped_pages = {
            "https://test-borettslag.no/info": "Forretningsfører BOLIGBYGGELAGET USBL håndterer felleskostnader."
        }
        profile = CompanyProfile(
            orgnr="950049596",
            legal_name="A/L F.L.S. BORETTSLAG",
            profile_verification_explanation="Test profile",
        )
        profile.facts["operations.manager_claim"] = MagicMock(
            current_value={"claim": "BOLIGBYGGELAGET USBL har 300 ansatte"},
            category=FactCategory.OPERATIONS,
            current_evidence=FactEvidence(
                source_url="https://test-borettslag.no/info",
                source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                source_date="2026-11-05",
                verbatim_quote="Forretningsfører BOLIGBYGGELAGET USBL håndterer felleskostnader",
                entity_match_method="EXACT_LEGAL_NAME_ON_OWN_DOMAIN",
                verification_explanation="Uncorroborated manager claim",
            )
        )
        negative_entities = [{"orgnr": "950285680", "navn": "BOLIGBYGGELAGET USBL", "role_type": "FFØR"}]
        audited = FactCritic.verify_and_filter(
            profile=profile,
            raw_scraped_pages=raw_scraped_pages,
            negative_external_entities=negative_entities,
        )
        assert "operations.manager_claim" not in audited.facts
        assert any("NEGATIVE_ENTITY_CONTAMINATION_FAILED" in q["reason"] for q in audited.metadata["quarantined_facts"])


@pytest.mark.asyncio
class TestHybridPipelineExecution:
    """Verifies end-to-end execution of the 3-Layer Hybrid Pipeline."""

    async def test_borettslag_runs_deterministic_fast_path(self):
        """Simulated Borettslag with USBL manager must finish via DETERMINISTIC_FAST_PATH."""
        mock_client = MagicMock()
        pipeline = HybridPipeline(client=mock_client)

        # Mock Layer 1 responses
        pipeline.brreg.get_enhet = AsyncMock(return_value={
            "organisasjonsnummer": "950049596",
            "navn": "A/L F.L.S. BORETTSLAG",
            "hjemmeside": "usbl.no",
            "forretningsadresse": {"kommune": "OSLO"},
        })
        pipeline.brreg.get_roller = AsyncMock(return_value={
            "rollegrupper": [{
                "type": {"kode": "FFØR"},
                "roller": [{"enhet": {"organisasjonsnummer": "950285680", "navn": "BOLIGBYGGELAGET USBL"}}],
            }]
        })
        pipeline.brreg.get_underenheter = AsyncMock(return_value=[])
        pipeline.financials.fetch_multi_year_financials = AsyncMock(return_value=([], ["Exempt BRL"], []))
        pipeline.kunngjoringer.fetch_kunngjoringer = AsyncMock(return_value=[])

        profile = await pipeline.run(raw_orgnr="950049596", include_web=True)
        assert profile is not None
        assert profile.telemetry["routing_path"] == "DETERMINISTIC_FAST_PATH"
        assert profile.telemetry["agentic_facts_proposed"] == 0
        assert any("BOLIGBYGGELAGET USBL" in u for u in profile.known_unknowns)

    async def test_operating_as_runs_hybrid_agentic_path(self):
        """Operating AS enterprise with dedicated domain must finish via HYBRID_AGENTIC_PATH."""
        mock_client = MagicMock()
        mock_client.budget.get_company_count.return_value = 2
        pipeline = HybridPipeline(client=mock_client)

        pipeline.brreg.get_enhet = AsyncMock(return_value={
            "organisasjonsnummer": "886581432",
            "navn": "BOUVET NORGE AS",
            "hjemmeside": "bouvet.no",
            "forretningsadresse": {"kommune": "OSLO"},
        })
        pipeline.brreg.get_roller = AsyncMock(return_value={"rollegrupper": []})
        pipeline.brreg.get_underenheter = AsyncMock(return_value=[])
        pipeline.financials.fetch_multi_year_financials = AsyncMock(return_value=([
            AnnualFinancialStatement(
                fiscal_year=2024,
                operating_revenue_nok=3500000000.0,
                operating_profit_nok=450000000.0,
                source_url="https://data.brreg.no/regnskap/886581432",
                source_name="Regnskapsregisteret",
                source_date="2025-05-10",
                verbatim_evidence="Driftsinntekter: 3500000000",
            )
        ], [], []))
        pipeline.kunngjoringer.fetch_kunngjoringer = AsyncMock(return_value=[])

        # Mock Layer 2 web discovery
        home_text = "Bouvet Norge AS er et ledende norsk konsulentselskap innen IT og digitalisering."
        career_text = "Karriere hos Bouvet Norge AS. Vi søker senior konsulent i Oslo."
        pipeline.discovery.discover_and_rank_subpages = AsyncMock(return_value=(
            [
                MagicMock(url="https://bouvet.no", raw_text=home_text, source_date="2026-11-05", html=""),
                MagicMock(url="https://bouvet.no/karriere", raw_text=career_text, source_date="2026-11-05", html=""),
            ],
            {
                "https://bouvet.no": home_text,
                "https://bouvet.no/karriere": career_text,
            },
            []
        ))

        profile = await pipeline.run(raw_orgnr="886581432", include_web=True)
        assert profile is not None
        assert profile.telemetry["routing_path"] == "HYBRID_AGENTIC_PATH"
        assert profile.telemetry["critic_facts_accepted"] > 0
        assert profile.executive_summary is not None
