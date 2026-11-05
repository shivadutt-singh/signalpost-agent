"""
Unit & Integration Tests for Multi-Year Financial Accounts Engine (2021-2025).
Verifies Brreg Regnskapsregisteret parsing, Norwegian number normalization,
multi-year table parsing, and anti-fabrication constraints.
"""

import pytest
from src.collectors.financials_client import (
    FinancialsClient,
    parse_norwegian_financial_number,
)
from src.models.schema import AnnualFinancialStatement
from src.validators.fact_guard import verify_financial_fact


class TestNorwegianNumberNormalization:
    """Tests Norwegian currency and number normalizer across spaces, periods, commas, and TNOK/MNOK."""

    def test_standard_space_separated_integers(self):
        assert parse_norwegian_financial_number("1 250 000") == 1250000.0
        assert parse_norwegian_financial_number("95 400 000 NOK") == 95400000.0

    def test_period_separated_integers(self):
        assert parse_norwegian_financial_number("1.250.000") == 1250000.0
        assert parse_norwegian_financial_number("45.000 kr") == 45000.0

    def test_parenthesized_and_signed_negatives(self):
        assert parse_norwegian_financial_number("(150 000)") == -150000.0
        assert parse_norwegian_financial_number("-45,5") == -45.5
        assert parse_norwegian_financial_number("(32.500)") == -32500.0

    def test_comma_decimals(self):
        assert parse_norwegian_financial_number("1 250 000,50") == 1250000.50
        assert parse_norwegian_financial_number("12,8") == 12.8

    def test_tnok_and_mnok_multipliers(self):
        assert parse_norwegian_financial_number("3 450 TNOK") == 3450000.0
        assert parse_norwegian_financial_number("12,5 MNOK") == 12500000.0
        assert parse_norwegian_financial_number("1 200", multiplier=1000.0) == 1200000.0


class TestRegnskapsregisteretParsing:
    """Tests parsing JSON payloads from Brønnøysund Regnskapsregisteret API."""

    def setup_method(self):
        self.mock_brreg_payload = [
            {
                "id": 1001,
                "journalnr": "2024001",
                "regnskapstype": "SELSKAP",
                "virksomhet": {"organisasjonsnummer": "984851006"},
                "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
                "valuta": "NOK",
                "resultatregnskapResultat": {
                    "driftsresultat": {
                        "driftsinntekter": {"sumDriftsinntekter": 75000000.0},
                        "driftsresultat": 18000000.0,
                    },
                    "ordinaertResultatFoerSkattekostnad": 17500000.0,
                    "aarsresultat": 13650000.0,
                },
                "eiendeler": {"sumEiendeler": 120000000.0},
                "egenkapitalGjeld": {
                    "egenkapital": {"sumEgenkapital": 65000000.0},
                    "gjeldOversikt": {"sumGjeld": 55000000.0},
                },
            },
            {
                "id": 1002,
                "journalnr": "2023001",
                "regnskapstype": "SELSKAP",
                "virksomhet": {"organisasjonsnummer": "984851006"},
                "regnskapsperiode": {"fraDato": "2023-01-01", "tilDato": "2023-12-31"},
                "valuta": "NOK",
                "resultatregnskapResultat": {
                    "driftsresultat": {
                        "driftsinntekter": {"sumDriftsinntekter": 68000000.0},
                        "driftsresultat": 15000000.0,
                    },
                    "ordinaertResultatFoerSkattekostnad": 14500000.0,
                    "aarsresultat": 11310000.0,
                },
                "eiendeler": {"sumEiendeler": 110000000.0},
                "egenkapitalGjeld": {
                    "egenkapital": {"sumEgenkapital": 58000000.0},
                    "gjeldOversikt": {"sumGjeld": 52000000.0},
                },
            },
            {
                "id": 1003,
                "journalnr": "2022001",
                "regnskapstype": "SELSKAP",
                "virksomhet": {"organisasjonsnummer": "984851006"},
                "regnskapsperiode": {"fraDato": "2022-01-01", "tilDato": "2022-12-31"},
                "valuta": "NOK",
                "resultatregnskapResultat": {
                    "driftsresultat": {
                        "driftsinntekter": {"sumDriftsinntekter": 61000000.0},
                        "driftsresultat": 12000000.0,
                    },
                    "ordinaertResultatFoerSkattekostnad": 11800000.0,
                    "aarsresultat": 9200000.0,
                },
                "eiendeler": {"sumEiendeler": 98000000.0},
                "egenkapitalGjeld": {
                    "egenkapital": {"sumEgenkapital": 51000000.0},
                    "gjeldOversikt": {"sumGjeld": 47000000.0},
                },
            },
        ]

    def test_parse_multi_year_json_series(self):
        client = FinancialsClient(client=None)
        statements = [
            client._parse_regnskap_item(item, "984851006", "https://data.brreg.no/regnskap/984851006")
            for item in self.mock_brreg_payload
        ]
        sorted_stmts = sorted(statements, key=lambda s: s.fiscal_year, reverse=True)

        assert len(sorted_stmts) == 3
        assert sorted_stmts[0].fiscal_year == 2024
        assert sorted_stmts[1].fiscal_year == 2023
        assert sorted_stmts[2].fiscal_year == 2022

        fy24 = sorted_stmts[0]
        assert fy24.operating_revenue_nok == 75000000.0
        assert fy24.operating_profit_nok == 18000000.0
        assert fy24.profit_before_tax_nok == 17500000.0
        assert fy24.net_income_nok == 13650000.0
        assert fy24.total_assets_nok == 120000000.0
        assert fy24.total_equity_nok == 65000000.0
        assert fy24.total_liabilities_nok == 55000000.0

    def test_html_table_multi_year_parser(self):
        client = FinancialsClient(client=None)
        html_content = """
        <html>
        <body>
            <p>Hovedtall fra konsernregnskapet (alle tall i TNOK):</p>
            <table>
                <tr>
                    <th>Resultatregnskap</th>
                    <th>2024</th>
                    <th>2023</th>
                    <th>2022</th>
                    <th>2021</th>
                </tr>
                <tr>
                    <td>Driftsinntekter</td>
                    <td>85 000</td>
                    <td>78 000</td>
                    <td>71 000</td>
                    <td>65 000</td>
                </tr>
                <tr>
                    <td>Driftsresultat (EBIT)</td>
                    <td>12 500</td>
                    <td>10 800</td>
                    <td>9 200</td>
                    <td>8 100</td>
                </tr>
                <tr>
                    <td>Resultat før skatt</td>
                    <td>11 900</td>
                    <td>10 100</td>
                    <td>8 700</td>
                    <td>7 600</td>
                </tr>
                <tr>
                    <td>Årsresultat</td>
                    <td>9 280</td>
                    <td>7 880</td>
                    <td>6 790</td>
                    <td>5 930</td>
                </tr>
                <tr>
                    <td>Sum eiendeler</td>
                    <td>145 000</td>
                    <td>132 000</td>
                    <td>120 000</td>
                    <td>110 000</td>
                </tr>
                <tr>
                    <td>Sum egenkapital</td>
                    <td>78 000</td>
                    <td>71 000</td>
                    <td>65 000</td>
                    <td>60 000</td>
                </tr>
            </table>
        </body>
        </html>
        """

        stmts = client._parse_html_financial_tables(
            html=html_content,
            source_url="https://company.no/investor/financial-tables",
            clean_orgnr="984851006",
        )
        by_year = {s.fiscal_year: s for s in stmts}

        assert 2024 in by_year
        assert 2023 in by_year
        assert 2022 in by_year
        assert 2021 in by_year

        # Multiplied by TNOK (1000)
        assert by_year[2024].operating_revenue_nok == 85000000.0
        assert by_year[2024].operating_profit_nok == 12500000.0
        assert by_year[2024].net_income_nok == 9280000.0
        assert by_year[2021].operating_revenue_nok == 65000000.0

    def test_anti_fabrication_verification_on_financial_statements(self):
        """Verifies that extracted figures match verbatim source quotes and reject hallucinated amounts."""
        raw_text = "I 2024 oppnådde selskapet driftsinntekter på 75 000 000 kr og et driftsresultat på 18 000 000 kr."
        quote = "driftsinntekter på 75 000 000 kr"

        # Valid extraction matches exact digits and substring
        assert verify_financial_fact(75000000.0, quote, raw_text)

        # Mismatched/hallucinated number is rejected
        assert not verify_financial_fact(99900000.0, quote, raw_text)

        # Hallucinated quote not in raw text is rejected
        assert not verify_financial_fact(75000000.0, "omsetning på 75 000 000 kr", raw_text)
