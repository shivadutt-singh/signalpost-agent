"""
Unit & Integration Tests for Negative-Entity Exclusion & Strict Page-Level Gate.
Proves 100% elimination of Umbrella/Manager Entity Bleed (The 'USBL vs. Borettslag' Bug).
"""

import pytest
from src.validators.fact_guard import (
    EntityAnchor,
    EntityVerificationGate,
    is_own_company_domain,
    verify_page_belongs_to_company,
    SHARED_UMBRELLA_DOMAINS,
)


class TestUsblBorettslagGuard:
    """Tests isolating managed housing associations from umbrella managers (USBL, OBOS, Bate, NBBO)."""

    def setup_method(self):
        # Target: Aksiseboden Borettslag, managed by USBL
        self.borettslag_anchor = EntityAnchor(
            orgnr="950639822",
            legal_name="AKSISEBODEN BORETTSLAG",
            municipality="OSLO",
            ceo_names=[],
            chair_names=["Kari Nordmann"],
            official_domains=[],  # Filtered out because Brreg hjemmeside pointed to usbl.no
            external_role_entities=[
                {"orgnr": "950285680", "navn": "Boligbyggelaget Usbl", "role_type": "FFØR"},
                {"orgnr": "972412112", "navn": "SLM REVISJON AS", "role_type": "REVI"},
            ],
            hjemmeside_is_shared_manager=True,
        )

        # Target: USBL itself
        self.usbl_anchor = EntityAnchor(
            orgnr="950285680",
            legal_name="BOLIGBYGGELAGET USBL",
            municipality="OSLO",
            ceo_names=["Hallvard Bratberg"],
            chair_names=["Tor Morten"],
            official_domains=["usbl.no"],
            external_role_entities=[],
            hjemmeside_is_shared_manager=False,
        )

    def test_shared_umbrella_domain_detection(self):
        """Verifies shared property manager domains are rejected for managed housing associations."""
        # For Borettslag, usbl.no is a shared manager domain -> must return False
        assert not is_own_company_domain("https://www.usbl.no", "AKSISEBODEN BORETTSLAG", "950639822")
        assert not is_own_company_domain("https://obos.no/nyheter", "AKERSBAKKEN BORETTSLAG", "948817705")
        assert not is_own_company_domain("https://bate.no/jobb", "SOLVANG BORETTSLAG", "950000000")

        # For USBL itself, usbl.no IS its own company domain -> must return True
        assert is_own_company_domain("https://www.usbl.no", "BOLIGBYGGELAGET USBL", "950285680")
        assert is_own_company_domain("https://obos.no", "OBOS BBL", "981440811")

    def test_reject_usbl_general_news_from_borettslag(self):
        """Verifies news about USBL is 100% rejected when profiling the managed Borettslag."""
        usbl_news_page = (
            "Boligbyggelaget Usbl feirer 75 år som boligutvikler i Viken og Oslo. "
            "Konsernsjefen uttaler at USBL har hatt en omsetningsvekst på 15 prosent i 2024. "
            "Kontakt USBL kundesenter på telefon for spørsmål om borettslag og sameier."
        )

        # Function-level verification check
        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr="950639822",
            canonical_name="AKSISEBODEN BORETTSLAG",
            page_text=usbl_news_page,
            external_role_entities=self.borettslag_anchor.external_role_entities,
        )
        assert not belongs
        assert rule == "REJECTED_MANAGER_OR_UMBRELLA_ENTITY_BLEED"
        assert conf == 0.0

        # Gate-level verification
        res = EntityVerificationGate.verify_source(
            anchor=self.borettslag_anchor,
            source_url="https://www.usbl.no/nyheter/usbl-75-ar",
            raw_source_text=usbl_news_page,
        )
        assert not res.is_verified
        assert res.rule == "REJECTED_MANAGER_OR_UMBRELLA_ENTITY_BLEED"

    def test_reject_usbl_job_posting_from_borettslag(self):
        """Verifies job openings at USBL are never attached to a managed Borettslag."""
        usbl_job_page = (
            "Stilling ledig: Eiendomsforvalter / Rådgiver forvaltningsavdelingen. "
            "Arbeidsgiver: Boligbyggelaget Usbl (org.nr 950285680). "
            "Vi søker en dyktig rådgiver til å bistå styreledere i borettslag med økonomisk rådgivning."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr="950639822",
            canonical_name="AKSISEBODEN BORETTSLAG",
            page_text=usbl_job_page,
            external_role_entities=self.borettslag_anchor.external_role_entities,
        )
        assert not belongs
        assert rule == "REJECTED_MANAGER_OR_UMBRELLA_ENTITY_BLEED"

        res = EntityVerificationGate.verify_source(
            anchor=self.borettslag_anchor,
            source_url="https://www.usbl.no/karriere/eiendomsforvalter",
            raw_source_text=usbl_job_page,
        )
        assert not res.is_verified

    def test_accept_page_explicitly_naming_borettslag_and_orgnr(self):
        """Verifies pages that explicitly name the Borettslag with its orgnr are accepted."""
        borettslag_page = (
            "Protokoll fra ordinær generalforsamling i Aksiseboden Borettslag (org.nr 950639822). "
            "Møtet ble holdt i Oslo. Forretningsfører Boligbyggelaget Usbl bisto som møtesekretær. "
            "Årsberetningen og regnskapet for 2024 ble enstemmig godkjent av andelseierne."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr="950639822",
            canonical_name="AKSISEBODEN BORETTSLAG",
            page_text=borettslag_page,
            external_role_entities=self.borettslag_anchor.external_role_entities,
        )
        assert belongs
        assert rule == "EXACT_ORGNR_MATCH"
        assert conf >= 0.95

    def test_accept_page_with_exact_canonical_name_preserving_legal_form(self):
        """Verifies pages matching the full legal canonical name (including 'Borettslag') are accepted."""
        borettslag_page_without_orgnr = (
            "Bystyret har vedtatt støtte til oppgradering av bakgården i Aksiseboden Borettslag. "
            "Styreleder Kari Nordmann opplyser at arbeidet starter våren 2025."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr="950639822",
            canonical_name="AKSISEBODEN BORETTSLAG",
            page_text=borettslag_page_without_orgnr,
            external_role_entities=self.borettslag_anchor.external_role_entities,
        )
        assert belongs
        assert rule == "EXACT_COMPANY_NAME_MATCH"

    def test_usbl_itself_accepts_its_own_pages(self):
        """Verifies that when profiling USBL itself, its own pages are accepted."""
        usbl_page = (
            "Boligbyggelaget Usbl (orgnr 950285680) er et av Norges største boligbyggelag. "
            "Vi tilbyr forretningsførsel og boligutvikling."
        )

        belongs, rule, conf = verify_page_belongs_to_company(
            target_orgnr="950285680",
            canonical_name="BOLIGBYGGELAGET USBL",
            page_text=usbl_page,
            external_role_entities=[],
        )
        assert belongs
        assert rule == "EXACT_ORGNR_MATCH"
