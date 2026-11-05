"""
Unit & Integration Tests for Enhanced Negative-Entity Filter.
Tests isolation of target companies against:
1. Property managers (FFØR: USBL, OBOS, NBBO, BATE, TOBB, BOB)
2. Big-4 & major auditors (REVI: PwC, EY, Deloitte, KPMG, BDO, RSM)
3. Accounting firms (REGN: Azets, Accountor, SpareBank 1 Regnskapshuset, Aider)
and enforces the 250-character paragraph/card locality gate.
"""

import pytest
from src.validators.fact_guard import (
    EntityAnchor,
    EntityVerificationGate,
    is_own_company_domain,
    verify_page_belongs_to_company,
)


class TestEnhancedNegativeEntityFilter:
    """Verifies enhanced negative entity isolation across managers, auditors, and accountants."""

    def setup_method(self):
        # Target company: Kongsberg Gruppen ASA
        self.kongsberg_anchor = EntityAnchor(
            orgnr="943753709",
            legal_name="KONGSBERG GRUPPEN ASA",
            municipality="KONGSBERG",
            ceo_names=["Geir Håøy"],
            chair_names=["Eivind Reiten"],
            official_domains=["kongsberg.com"],
            external_role_entities=[
                {"orgnr": "987009713", "navn": "PRICEWATERHOUSECOOPERS AS", "role_type": "REVI"},
            ],
            hjemmeside_is_shared_manager=False,
        )

        # Target company: Aksiseboden Borettslag
        self.borettslag_anchor = EntityAnchor(
            orgnr="950639822",
            legal_name="AKSISEBODEN BORETTSLAG",
            municipality="OSLO",
            ceo_names=[],
            chair_names=["Kari Nordmann"],
            official_domains=[],
            external_role_entities=[
                {"orgnr": "950285680", "navn": "Boligbyggelaget Usbl", "role_type": "FFØR"},
                {"orgnr": "972412112", "navn": "SLM REVISJON AS", "role_type": "REVI"},
                {"orgnr": "984242111", "navn": "AZETS INSIGHT AS", "role_type": "REGN"},
            ],
            hjemmeside_is_shared_manager=True,
        )

    def test_reject_auditor_hiring_news_bleeding_into_client(self):
        """Verifies that PwC / Deloitte / KPMG hiring news is never attributed to their audit client."""
        pwc_careers_snippet = (
            "PwC Norge opplever sterk vekst innen cybersikkerhet og finansiell rådgivning. "
            "PwC søker nå 50 nyutdannede revisorer og konsulenter til våre kontorer i Oslo, Bergen og Trondheim. "
            "Søk på karriere i PwC innen 1. oktober."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr=self.kongsberg_anchor.orgnr,
            canonical_name=self.kongsberg_anchor.legal_name,
            page_text=pwc_careers_snippet,
            external_role_entities=self.kongsberg_anchor.external_role_entities,
        )
        assert not belongs
        assert rule == "REJECTED_MANAGER_OR_UMBRELLA_ENTITY_BLEED"
        assert conf == 0.0

    def test_reject_accountant_general_press_bleeding_into_client(self):
        """Verifies that Azets / Accountor press releases are never attributed to their accounting client."""
        azets_press_snippet = (
            "Azets Insight styrker sin posisjon i Norden ved oppkjøp av regionalt regnskapskontor. "
            "Administrerende direktør i Azets uttaler at digital automatisering gir raskere regnskapsførsel."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr=self.borettslag_anchor.orgnr,
            canonical_name=self.borettslag_anchor.legal_name,
            page_text=azets_press_snippet,
            external_role_entities=self.borettslag_anchor.external_role_entities,
        )
        assert not belongs
        assert rule == "REJECTED_MANAGER_OR_UMBRELLA_ENTITY_BLEED"
        assert conf == 0.0

    def test_accept_auditor_statement_when_target_named_within_250_chars(self):
        """Verifies that an auditor's report mentioning the client within 250 chars is accepted."""
        audit_opinion_snippet = (
            "Uavhengig revisors beretning for Kongsberg Gruppen ASA (org.nr 943753709). "
            "Vi har revidert årsregnskapet for 2024. Etter vår mening gir regnskapet et rettvisende bilde. "
            "Oslo, 15. mars 2025. PricewaterhouseCoopers AS, statsautorisert revisor."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr=self.kongsberg_anchor.orgnr,
            canonical_name=self.kongsberg_anchor.legal_name,
            page_text=audit_opinion_snippet,
            external_role_entities=self.kongsberg_anchor.external_role_entities,
        )
        assert belongs
        assert rule == "EXACT_ORGNR_MATCH"
        assert conf >= 0.95

    def test_reject_distant_footer_orgnr_on_manager_page(self):
        """
        Verifies that if a manager (e.g. USBL) page mentions USBL news at the top,
        and the target borettslag orgnr is mentioned far away in a directory list (>250 chars away),
        the local claim is rejected.
        """
        long_page = (
            "USBL feirer nye boligprosjekter i Lillestrøm. Konsernsjefen i Boligbyggelaget Usbl inviterer til åpning. "
            + (" Andre nyheter og irrelevante artikler om eiendom. " * 20)  # > 800 characters
            + " Liste over forvaltede enheter: Aksiseboden Borettslag (orgnr 950639822)."
        )

        # Claim at the top about USBL news
        claim_about_usbl = "USBL feirer nye boligprosjekter"

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr=self.borettslag_anchor.orgnr,
            canonical_name=self.borettslag_anchor.legal_name,
            page_text=long_page,
            external_role_entities=self.borettslag_anchor.external_role_entities,
            claim_text=claim_about_usbl,
        )
        # Because USBL is > 250 characters away from the borettslag orgnr, it must be rejected!
        assert not belongs
        assert rule == "REJECTED_MANAGER_OR_UMBRELLA_ENTITY_BLEED"
