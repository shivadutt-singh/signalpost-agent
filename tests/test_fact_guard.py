"""
Unit tests for Critical Competition Guardrails:
1. Entity Verification Gate (Zero Wrong-Company Bleed)
2. Anti-Fabrication Financial Substring Verifier (Zero Financial Fabrication)
"""

from src.validators.fact_guard import (
    AntiFabricationGuard,
    EntityAnchor,
    EntityVerificationGate,
)


class TestEntityVerificationGate:
    """Verifies that non-registry sources are strictly gated against wrong-company bleed."""

    anchor = EntityAnchor(
        orgnr="923609016",
        legal_name="EQUINOR ASA",
        municipality="STAVANGER",
        ceo_names=["Anders Opedal"],
        chair_names=["Jon Erik Reinhardsen"],
        official_domains=["equinor.com"],
    )

    def test_gate_pass_on_orgnr_match(self):
        raw_text = "Firmaet med organisasjonsnummer 923609016 rapporterer sterk vekst i fjerde kvartal."
        result = EntityVerificationGate.verify_source(
            anchor=self.anchor,
            source_url="https://external-news.no/article123",
            raw_source_text=raw_text,
        )
        assert result.is_verified is True
        assert result.rule == "ORGNR_MATCH"
        assert result.confidence == 1.0
        assert "923609016" in result.explanation

    def test_gate_pass_on_formatted_orgnr_match(self):
        raw_text = "For mer informasjon, se Brønnøysundoppføringen under NO 923 609 016 MVA."
        result = EntityVerificationGate.verify_source(
            anchor=self.anchor,
            source_url="https://news.org/report",
            raw_source_text=raw_text,
        )
        assert result.is_verified is True
        assert result.rule == "ORGNR_MATCH"

    def test_gate_pass_on_official_domain_match(self):
        raw_text = "Våre prinsipper for fornybar energi og bærekraftig utvikling."
        result = EntityVerificationGate.verify_source(
            anchor=self.anchor,
            source_url="https://investor.equinor.com/reports/2024",
            raw_source_text=raw_text,
        )
        assert result.is_verified is True
        assert result.rule == "OFFICIAL_DOMAIN_MATCH"
        assert result.confidence >= 0.95

    def test_gate_pass_on_name_and_municipality_match(self):
        raw_text = "Energiselskapet Equinor ASA har sitt hovedkontor plassert i Stavanger kommune."
        result = EntityVerificationGate.verify_source(
            anchor=self.anchor,
            source_url="https://energiwatch.no/nyheter",
            raw_source_text=raw_text,
        )
        assert result.is_verified is True
        assert result.rule == "NAME_AND_LOCATION_MATCH"
        assert result.confidence >= 0.90

    def test_gate_pass_on_name_and_ceo_match(self):
        raw_text = "Konsernsjef Anders Opedal i Equinor uttalte seg om de nye havvindprosjektene."
        result = EntityVerificationGate.verify_source(
            anchor=self.anchor,
            source_url="https://e24.no/bors/nyheter",
            raw_source_text=raw_text,
        )
        assert result.is_verified is True
        assert result.rule == "NAME_AND_KEY_PERSON_MATCH"
        assert result.confidence >= 0.90

    def test_gate_reject_wrong_company_bleed(self):
        """Must reject unrelated company page to eliminate wrong-company bleed risk."""
        raw_text = "Statkraft AS produserer ren fornybar energi i Oslo. Konsernsjef Christian Rynning-Tønnesen."
        result = EntityVerificationGate.verify_source(
            anchor=self.anchor,
            source_url="https://statkraft.com/om-oss",
            raw_source_text=raw_text,
        )
        assert result.is_verified is False
        assert result.rule == "FAILED_ENTITY_GATE"
        assert result.confidence == 0.0
        assert "Disqualified for Wrong-Company Bleed" in result.explanation


class TestAntiFabricationGuard:
    """Verifies that numeric/financial facts are strictly verified against exact verbatim quotes."""

    sample_page = """
    Årsrapport for regnskapsåret 2024:
    Totale driftsinntekter utgjorde 35 840 000 NOK i perioden.
    Selskapets driftsresultat endte på 4 250 000 NOK.
    Ved utgangen av året hadde konsernet 21 231 fast ansatte på verdensbasis.
    Aksjekapitalen er oppgitt til 1 200 000 NOK.
    """

    def test_valid_exact_verbatim_quote(self):
        res = AntiFabricationGuard.verify_numeric_fact(
            fact_key="financial.revenue",
            value=35840000,
            verbatim_quote="Totale driftsinntekter utgjorde 35 840 000 NOK i perioden.",
            raw_source_text=self.sample_page,
        )
        assert res.is_valid is True
        assert res.raw_digits_verified is True

    def test_valid_with_whitespace_and_newline_variation(self):
        # Quote with multi-space and newline normalized
        res = AntiFabricationGuard.verify_numeric_fact(
            fact_key="financial.operating_profit",
            value=4250000,
            verbatim_quote="driftsresultat   endte på  4 250 000 NOK",
            raw_source_text=self.sample_page,
        )
        assert res.is_valid is True
        assert res.raw_digits_verified is True

    def test_reject_missing_or_empty_quote(self):
        res = AntiFabricationGuard.verify_numeric_fact(
            fact_key="financial.revenue",
            value=35840000,
            verbatim_quote=None,
            raw_source_text=self.sample_page,
        )
        assert res.is_valid is False
        assert "missing required verbatim_quote" in res.rejection_reason

    def test_reject_hallucinated_quote(self):
        res = AntiFabricationGuard.verify_numeric_fact(
            fact_key="financial.revenue",
            value=99999999,
            verbatim_quote="Totale driftsinntekter var rekordhøye på 99 999 999 NOK.",
            raw_source_text=self.sample_page,
        )
        assert res.is_valid is False
        assert "not found in raw source text" in res.rejection_reason

    def test_reject_mismatched_digits_in_quote(self):
        # Quote exists in text, but extracted value has different digits
        res = AntiFabricationGuard.verify_numeric_fact(
            fact_key="operations.employee_count",
            value=88888,  # Does not match 21231
            verbatim_quote="hadde konsernet 21 231 fast ansatte på verdensbasis.",
            raw_source_text=self.sample_page,
        )
        assert res.is_valid is False
        assert "digits not found in verbatim_quote" in res.rejection_reason

    def test_verify_financial_fact_convenience_function(self):
        """Tests top-level verify_financial_fact helper."""
        from src.validators.fact_guard import verify_financial_fact

        # Valid fact
        valid = verify_financial_fact(
            value=35840000,
            verbatim_quote="Totale driftsinntekter utgjorde 35 840 000 NOK i perioden.",
            raw_page_text=self.sample_page,
        )
        assert valid is True

        # Invalid fact
        invalid = verify_financial_fact(
            value=99999999,
            verbatim_quote="Totale driftsinntekter var rekordhøye på 99 999 999 NOK.",
            raw_page_text=self.sample_page,
        )
        assert invalid is False
