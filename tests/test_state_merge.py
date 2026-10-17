"""
Unit tests for Idempotent State Merger, Temporal Conflict Resolution,
Audit History Retention, and Change Log Generation.
"""

import tempfile
from pathlib import Path
from src.models.schema import (
    ExtractedFactCandidate,
    FactCategory,
    FactEvidence,
    SourceTier,
)
from src.state.profile_store import ProfileStore


def _make_candidate(
    key: str,
    val: str,
    date_str: str,
    tier: SourceTier = SourceTier.OFFICIAL_REGISTRY,
    url: str = "https://data.brreg.no",
) -> ExtractedFactCandidate:
    return ExtractedFactCandidate(
        fact_key=key,
        value=val,
        category=FactCategory.REGISTRY_CORE,
        evidence=FactEvidence(
            source_url=url,
            source_tier=tier,
            source_date=date_str,
            verbatim_quote=str(val),
            entity_match_rule="OFFICIAL_REGISTRY_AUTHORITY",
            entity_confidence=1.0,
            raw_digits_verified=True,
            verification_explanation=f"Fact from {url}",
        ),
    )


class TestStateMerge:
    """Verifies temporal resolution, idempotency, and audit trails."""

    def test_idempotent_re_run(self):
        """Re-running with identical facts produces 0 updates and reports UNCHANGED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(profiles_dir=Path(tmpdir))

            candidate1 = _make_candidate("governance.ceo", "Anders Opedal", "2024-01-15")
            candidate2 = _make_candidate("contact.email", "post@equinor.com", "2024-01-15")

            # First run: initial creation
            profile, delta1 = store.merge_facts(
                existing_profile=None,
                orgnr="923609016",
                legal_name="EQUINOR ASA",
                candidates=[candidate1, candidate2],
            )
            assert delta1.facts_created == 2
            assert delta1.facts_updated == 0
            assert delta1.facts_unchanged == 0

            # Second run: identical candidates
            profile2, delta2 = store.merge_facts(
                existing_profile=profile,
                orgnr="923609016",
                legal_name="EQUINOR ASA",
                candidates=[candidate1, candidate2],
            )
            # Idempotency guarantees:
            assert delta2.facts_created == 0
            assert delta2.facts_updated == 0
            assert delta2.facts_unchanged == 2
            assert delta2.facts_rejected == 0
            assert profile2.facts["governance.ceo"].current_value == "Anders Opedal"
            assert len(profile2.facts["governance.ceo"].previous_values) == 0

    def test_supersede_with_newer_source_date(self):
        """A candidate with a newer source date supersedes the existing value and archives previous value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(profiles_dir=Path(tmpdir))

            # Initial 2023 fact
            cand_2023 = _make_candidate("governance.ceo", "Eldar Sætre", "2023-01-01")
            profile, _ = store.merge_facts(None, "923609016", "EQUINOR ASA", [cand_2023])
            assert profile.facts["governance.ceo"].current_value == "Eldar Sætre"

            # Newer 2024 fact arrives
            cand_2024 = _make_candidate("governance.ceo", "Anders Opedal", "2024-01-01")
            profile_updated, delta = store.merge_facts(
                profile, "923609016", "EQUINOR ASA", [cand_2024]
            )

            # Check update & archive
            assert delta.facts_updated == 1
            assert delta.facts_created == 0
            assert profile_updated.facts["governance.ceo"].current_value == "Anders Opedal"

            # Check previous_values audit trail
            history = profile_updated.facts["governance.ceo"].previous_values
            assert len(history) == 1
            assert history[0].value == "Eldar Sætre"
            assert history[0].source_date == "2023-01-01"
            assert "SUPERSEDED_BY_NEWER_DATE" in history[0].reason

    def test_reject_stale_older_source_date(self):
        """A candidate with an older date is rejected and does not overwrite current verified value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(profiles_dir=Path(tmpdir))

            # Initial 2025 fact
            cand_2025 = _make_candidate("financial.revenue", "500000000", "2025-01-01")
            profile, _ = store.merge_facts(None, "923609016", "EQUINOR ASA", [cand_2025])

            # Stale 2021 candidate
            cand_2021 = _make_candidate("financial.revenue", "200000000", "2021-01-01")
            profile_res, delta = store.merge_facts(profile, "923609016", "EQUINOR ASA", [cand_2021])

            # Must reject stale candidate
            assert delta.facts_rejected == 1
            assert delta.facts_updated == 0
            assert profile_res.facts["financial.revenue"].current_value == "500000000"
            assert len(profile_res.facts["financial.revenue"].previous_values) == 0

    def test_source_tier_priority_resolution(self):
        """When dates match, higher source tier (Tier 1 Registry) supersedes lower tier (Tier 3 Web)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(profiles_dir=Path(tmpdir))

            # Web Tier candidate
            web_cand = _make_candidate(
                "registry.legal_name",
                "Equinor ASA News",
                "2024-05-01",
                tier=SourceTier.VERIFIED_PUBLIC_WEB,
                url="https://external-news.no",
            )
            profile, _ = store.merge_facts(None, "923609016", "EQUINOR ASA", [web_cand])

            # Official Registry candidate on same date
            reg_cand = _make_candidate(
                "registry.legal_name",
                "EQUINOR ASA",
                "2024-05-01",
                tier=SourceTier.OFFICIAL_REGISTRY,
                url="https://data.brreg.no/enheter/923609016",
            )
            profile_res, delta = store.merge_facts(profile, "923609016", "EQUINOR ASA", [reg_cand])

            assert delta.facts_updated == 1
            assert profile_res.facts["registry.legal_name"].current_value == "EQUINOR ASA"
            assert len(profile_res.facts["registry.legal_name"].previous_values) == 1

    def test_explainability_narrative_generation(self):
        """Profile explanation synthesizes registered counts, tiers, and entity verification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(profiles_dir=Path(tmpdir))

            cand1 = _make_candidate("registry.legal_name", "EQUINOR ASA", "2024-01-01", SourceTier.OFFICIAL_REGISTRY)
            cand2 = _make_candidate("contact.email", "post@equinor.com", "2024-01-01", SourceTier.OFFICIAL_HOMEPAGE)

            profile, _ = store.merge_facts(None, "923609016", "EQUINOR ASA", [cand1, cand2])
            explanation = profile.profile_verification_explanation

            assert "923609016" in explanation
            assert "EQUINOR ASA" in explanation
            assert "Modulo-11" in explanation
            assert "Zero fabrication detected" in explanation

    def test_executive_summary_and_known_unknowns_persistence(self):
        """Ensures executive summary and known unknowns are correctly stored in profile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ProfileStore(profiles_dir=Path(tmpdir))
            cand = _make_candidate("registry.legal_name", "EQUINOR ASA", "2024-01-01")

            summary = "Equinor ASA is Norway's largest energy producer. Sourced from registry."
            unknowns = ["Unfiled Q4 EBITDA margins", "Detailed private commercial agreements"]

            profile, _ = store.merge_facts(
                existing_profile=None,
                orgnr="923609016",
                legal_name="EQUINOR ASA",
                candidates=[cand],
                executive_summary=summary,
                known_unknowns=unknowns,
            )

            assert profile.executive_summary == summary
            assert len(profile.known_unknowns) == 2
            assert "Unfiled Q4 EBITDA margins" in profile.known_unknowns

    def test_dynamic_http_budget_scaling(self):
        """Verifies 100-company vs 1000-company budget scaling fixing the starvation bug."""
        from src.collectors.http_budget import HttpBudgetTracker

        # 100-company mode: cap is 1950
        tracker_100 = HttpBudgetTracker.create_for_batch(batch_size=100)
        assert tracker_100.max_global == 1950
        assert tracker_100.max_per_company == 18

        # 1000-company mode: cap is dynamically scaled to 18,000 requests
        tracker_1000 = HttpBudgetTracker.create_for_batch(batch_size=1000)
        assert tracker_1000.max_global == 18000
        assert tracker_1000.max_per_company == 18
