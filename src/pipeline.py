"""
3-Layer Hybrid Neuro-Symbolic Sandwich Pipeline for Norwegian Enterprise Intelligence.
Combines 100% Deterministic Registry & Verification Guards with Bounded Agentic Web Navigation
and Extraction powered by NVIDIA NIM (meta/llama-3.3-70b-instruct with meta/llama-3.1-8b-instruct fallback).

Layer 1: Deterministic Ground-Truth Ingestion, Multi-Year Accounts & Smart Path Router
Layer 2: Bounded Agentic Discovery & Single-Shot Batched NIM Extraction (Verified Domains Only)
Layer 3: Deterministic Pre-Commit Critic (Quote Substring Lock, Negative-Entity Barrier, Financial Substring Lock)
"""

import logging
from datetime import UTC, datetime
from typing import Any

from src.collectors.brreg_client import BrregClient
from src.collectors.financials_client import FinancialsClient
from src.collectors.http_budget import BudgetedAsyncClient
from src.collectors.kunngjoringer_client import KunngjoringerClient
from src.collectors.web_discovery import WebDiscoveryCollector
from src.extraction.llm_extractor import FactExtractor
from src.models.schema import (
    CompanyProfile,
    ExecutionTelemetry,
    ExtractedFactCandidate,
    FactCategory,
    FactEvidence,
    SourceTier,
)
from src.state.profile_store import ProfileStore
from src.validators.fact_critic import FactCritic
from src.validators.fact_guard import (
    EntityAnchor,
    is_own_company_domain,
)
from src.validators.mod11 import try_normalize_orgnr

logger = logging.getLogger("signalpost.pipeline")


class HybridPipeline:
    """
    Production Hybrid Neuro-Symbolic Pipeline orchestrating Layers 1, 2, and 3.
    """

    def __init__(
        self,
        client: BudgetedAsyncClient,
        store: ProfileStore | None = None,
        extractor: FactExtractor | None = None,
    ):
        self.client = client
        self.store = store or ProfileStore()
        self.extractor = extractor or FactExtractor()
        self.brreg = BrregClient(client)
        self.financials = FinancialsClient(client)
        self.kunngjoringer = KunngjoringerClient(client)
        self.discovery = WebDiscoveryCollector(client)

    async def run(
        self,
        raw_orgnr: str,
        include_web: bool = True,
        use_nim_full: bool = True,
        telemetry: ExecutionTelemetry | None = None,
    ) -> CompanyProfile | None:
        """
        Executes the 3-Layer Hybrid Pipeline for a single Norwegian organization number.
        """
        # ======================================================================
        # LAYER 1: DETERMINISTIC GROUND-TRUTH & SMART PATH ROUTER
        # ======================================================================
        clean_orgnr = try_normalize_orgnr(raw_orgnr)
        if not clean_orgnr:
            logger.error(f"Modulo-11 Validation Failure: '{raw_orgnr}' is invalid.")
            return None

        logger.info(f"[Layer 1] Ingesting official registry ground truth for orgnr {clean_orgnr}...")

        # 1. Fetch Enhet & Subunits
        enhet_data = None
        try:
            enhet_data = await self.brreg.get_enhet(clean_orgnr)
            if not enhet_data:
                enhet_data = await self.brreg.get_underenhet(clean_orgnr)
        except Exception as e:
            logger.warning(f"Brreg enhet retrieval error for {clean_orgnr}: {e}")

        legal_name = (enhet_data or {}).get("navn", f"Enterprise {clean_orgnr}")
        canonical_status = "ACTIVE"
        if enhet_data:
            if enhet_data.get("konkurs", False):
                canonical_status = "BANKRUPT"
            elif enhet_data.get("underAvvikling", False):
                canonical_status = "IN_LIQUIDATION"

        # 2. Fetch Governance Roles & Subunits
        roller_data = None
        underenheter_data: list[dict[str, Any]] = []
        try:
            roller_data = await self.brreg.get_roller(clean_orgnr)
        except Exception:
            pass
        try:
            underenheter_data = await self.brreg.get_underenheter(clean_orgnr)
        except Exception:
            pass

        # Build Anchor with positive_officers & negative_external_entities
        anchor: EntityAnchor = self.brreg.build_entity_anchor(
            enhet_data=enhet_data or {"organisasjonsnummer": clean_orgnr, "navn": legal_name},
            roller_data=roller_data,
        )
        registry_candidates = self.brreg.extract_registry_facts(
            enhet_data=enhet_data or {"organisasjonsnummer": clean_orgnr, "navn": legal_name},
            roller_data=roller_data,
            underenheter_data=underenheter_data,
        )

        # 3. Multi-Year Financial Accounts (Regnskapsregisteret & Python YoY / Margins)
        financial_history = []
        fin_unknowns: list[str] = []
        fin_facts: list[ExtractedFactCandidate] = []
        multi_year_trajectory: str | None = None
        try:
            financial_history, fin_unknowns, fin_facts = await self.financials.fetch_multi_year_financials(
                orgnr=clean_orgnr,
                anchor=anchor,
            )
            if financial_history:
                _, multi_year_trajectory = self.financials.compute_multi_year_insights(financial_history)
        except Exception as fin_err:
            logger.warning(f"Financials error for {clean_orgnr}: {fin_err}")

        # 4. Official Brreg Kunngjøringer Feed (100% Entity Exact)
        kj_facts: list[ExtractedFactCandidate] = []
        try:
            kj_items = await self.kunngjoringer.fetch_kunngjoringer(clean_orgnr, limit=8)
            if kj_items:
                kj_facts = self.kunngjoringer.to_fact_candidates(kj_items, clean_orgnr)
        except Exception as kj_err:
            logger.debug(f"Kunngjøringer error for {clean_orgnr}: {kj_err}")

        # Initial deterministic candidate pool
        all_candidates: list[ExtractedFactCandidate] = list(registry_candidates + fin_facts + kj_facts)
        known_unknowns: list[str] = list(fin_unknowns)
        executive_summary: str | None = None

        # 5. Smart Path Router
        hjemmeside = (enhet_data or {}).get("hjemmeside")
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

        agentic_facts_proposed = 0
        raw_scraped_pages: dict[str, str] = {}
        routing_path = "DETERMINISTIC_FAST_PATH"

        if not include_web or not has_verified_own_domain:
            # ------------------------------------------------------------------
            # ROUTE 1: DETERMINISTIC FAST-PATH
            # ------------------------------------------------------------------
            logger.info(
                f"[Router] Routing {clean_orgnr} to DETERMINISTIC_FAST_PATH "
                f"(Domain: {hjemmeside or 'None'}, SharedManager: {anchor.hjemmeside_is_shared_manager})"
            )
            routing_path = "DETERMINISTIC_FAST_PATH"

            if anchor.hjemmeside_is_shared_manager:
                manager_names = [e.get("navn", "") for e in anchor.negative_external_entities if e.get("navn")]
                mgr_str = f" managed by {', '.join(manager_names)}" if manager_names else ""
                known_unknowns.append(
                    f"Entity isolation active: Property manager domain ({hjemmeside}){mgr_str} was strictly quarantined "
                    f"to prevent wrong-company job, news, and executive bleed."
                )

            # Deterministic executive summary
            s1 = (
                f"{anchor.legal_name} is a registered Norwegian enterprise located in {anchor.municipality or 'Norway'} "
                f"governed under enterprise number {clean_orgnr}."
            )
            s2 = (
                multi_year_trajectory
                or f"Registration records confirm corporate standing with active leadership under {', '.join(anchor.ceo_names + anchor.chair_names) or 'Executive Board'}."
            )
            s3 = "Unfiled interim quarterly results, private partner contracts, and internal headcount fluctuations remain unconfirmed from public records."
            executive_summary = f"{s1} {s2} {s3}"

        else:
            # ------------------------------------------------------------------
            # ROUTE 2: BOUNDED AGENTIC PATH (Verified Dedicated Domain)
            # ------------------------------------------------------------------
            logger.info(
                f"[Router] Routing {clean_orgnr} to HYBRID_AGENTIC_PATH "
                f"(Verified Domain: {anchor.official_domains[0]})"
            )
            routing_path = "HYBRID_AGENTIC_PATH"

            try:
                # Stage 2A & 2B: Hybrid Link Selection & Deterministic Pre-Filter
                verified_pages, raw_scraped_pages, ld_json_jobs = await self.discovery.discover_and_rank_subpages(
                    anchor=anchor,
                    max_subpages=4,
                )

                # Add deterministic JSON-LD jobs if discovered
                now_iso = datetime.now(UTC).isoformat()
                for job in ld_json_jobs:
                    job_ev = FactEvidence(
                        source_url=job["url"],
                        source_tier=SourceTier.OFFICIAL_HOMEPAGE,
                        source_date=job.get("source_date") or datetime.now().strftime("%Y-%m-%d"),
                        retrieval_timestamp=now_iso,
                        verbatim_quote=job["verbatim_quote"],
                        entity_match_rule="EXACT_COMPANY_NAME_MATCH",
                        entity_match_method="EXACT_LEGAL_NAME_ON_OWN_DOMAIN",
                        entity_confidence=1.0,
                        raw_digits_verified=False,
                        verification_explanation="Deterministic application/ld+json JobPosting verified on company career page.",
                    )
                    all_candidates.append(
                        ExtractedFactCandidate(
                            fact_key="operations.jobs.ld_json",
                            value={"title": job["title"], "url": job["url"]},
                            category=FactCategory.OPERATIONS,
                            evidence=job_ev,
                        )
                    )

                # Stage 2C: Single-Shot Batched NVIDIA NIM Agentic Extraction
                if verified_pages:
                    agentic_results = await self.extractor.extract_batched_from_verified_pages(
                        anchor=anchor,
                        verified_pages=verified_pages,
                        multi_year_trajectory=multi_year_trajectory,
                    )
                    proposed_candidates = agentic_results.get("candidates", [])
                    agentic_facts_proposed = len(proposed_candidates)
                    all_candidates.extend(proposed_candidates)

                    if agentic_results.get("executive_summary"):
                        executive_summary = agentic_results["executive_summary"]
                    if agentic_results.get("known_unknowns"):
                        known_unknowns.extend(agentic_results["known_unknowns"])
            except Exception as agentic_err:
                logger.warning(f"Layer 2 Agentic Discovery encountered non-fatal error: {agentic_err}")

        # ======================================================================
        # MERGE CANDIDATES INTO PROFILE
        # ======================================================================
        profile, delta = self.store.merge_facts(
            existing_profile=self.store.load_profile(clean_orgnr),
            orgnr=clean_orgnr,
            legal_name=legal_name,
            candidates=all_candidates,
            canonical_status=canonical_status,
            executive_summary=executive_summary,
            known_unknowns=known_unknowns,
            financial_history=financial_history,
        )

        # Set initial routing telemetry
        profile.telemetry = {
            "routing_path": routing_path,
            "agentic_facts_proposed": agentic_facts_proposed,
            "critic_facts_accepted": len(profile.facts),
            "critic_facts_quarantined": 0,
        }

        # ======================================================================
        # LAYER 3: DETERMINISTIC PRE-COMMIT CRITIC
        # ======================================================================
        logger.info(f"[Layer 3] Running FactCritic pre-commit verification on {clean_orgnr}...")
        profile = FactCritic.verify_and_filter(
            profile=profile,
            raw_scraped_pages=raw_scraped_pages,
            negative_external_entities=anchor.negative_external_entities,
            telemetry=telemetry,
        )

        # Final store commit
        self.store.save_profile(profile)
        self.store.save_delta(delta)

        logger.info(
            f"Successfully saved profile for {legal_name} ({clean_orgnr}) | "
            f"Path: {profile.telemetry.get('routing_path')} | "
            f"Facts Accepted: {profile.telemetry.get('critic_facts_accepted')} | "
            f"Quarantined: {profile.telemetry.get('critic_facts_quarantined')}"
        )
        return profile
