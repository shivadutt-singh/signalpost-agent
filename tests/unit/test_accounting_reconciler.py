"""
Unit Tests for Accounting Guard & Double-Entry Reconciler (v6.0 Fail-Proof Edition).

Tests Soham's #2 evaluation requirement:
Multi-year accounts (2021–2025), Norwegian number format parsing, and Python-calculated YoY math.
"""

from __future__ import annotations

from src.domain.accounting_guard import (
    calculate_equity_ratio,
    calculate_operating_margin,
    calculate_yoy_growth,
    parse_norwegian_number,
    reconcile_multi_year_series,
    verify_number_digits_in_evidence,
)
from src.domain.entities import AnnualAccount


def test_norwegian_number_parsing_formats() -> None:
    """Verifies deterministic parsing of various Norwegian currency notations."""
    # Standard space-separated integers
    assert parse_norwegian_number("1 250 400").unwrap() == 1250400
    assert parse_norwegian_number("45 000 NOK").unwrap() == 45000

    # Period-separated thousands
    assert parse_norwegian_number("1.250.400").unwrap() == 1250400

    # Negative formats
    assert parse_norwegian_number("-45 200").unwrap() == -45200
    assert parse_norwegian_number("(45 200)").unwrap() == -45200
    assert parse_norwegian_number("−10 000").unwrap() == -10000

    # Multiplier units: TNOK (thousand NOK) & MNOK (million NOK)
    assert parse_norwegian_number("500 TNOK").unwrap() == 500000
    assert parse_norwegian_number("12,5 MNOK").unwrap() == 12500000
    assert parse_norwegian_number("2,75 Mkr").unwrap() == 2750000


def test_number_digits_in_evidence_verification() -> None:
    """Verifies that claimed figures must have raw digits present in evidence."""
    evidence = "In fiscal year 2024, driftsinntekter amounted to 1 250 400 NOK with driftsresultat of 320 000 NOK."

    # Admitted
    assert verify_number_digits_in_evidence(1250400, evidence).is_ok
    assert verify_number_digits_in_evidence(320000, evidence).is_ok

    # Hallucinated number not in evidence
    hallucinated_res = verify_number_digits_in_evidence(9999999, evidence)
    assert hallucinated_res.is_err
    assert hallucinated_res.unwrap_err().code == "DIGITS_ABSENT_IN_EVIDENCE"


def test_deterministic_python_growth_and_margins() -> None:
    """Tests YoY growth and operating margin formulas."""
    # YoY Revenue Growth: (120 - 100) / 100 * 100 = +20%
    assert calculate_yoy_growth(120_000, 100_000) == 20.0
    # YoY Revenue Decline: (80 - 100) / 100 * 100 = -20%
    assert calculate_yoy_growth(80_000, 100_000) == -20.0

    # Operating Margin: 25 / 100 * 100 = 25%
    assert calculate_operating_margin(25_000, 100_000) == 25.0

    # Equity Ratio: 40 / 100 * 100 = 40%
    assert calculate_equity_ratio(40_000, 100_000) == 40.0


def test_reconcile_multi_year_series() -> None:
    """Verifies chronological multi-year accounting reconciliation."""
    acc_2023 = AnnualAccount(
        year=2023,
        revenue_nok=100_000,
        operating_profit_nok=20_000,
        total_assets_nok=200_000,
        total_equity_nok=80_000,
        source_url="https://data.brreg.no/regnskap/test",
        verbatim_evidence="2023 statement: 100000",
    )
    acc_2024 = AnnualAccount(
        year=2024,
        revenue_nok=130_000,
        operating_profit_nok=32_500,
        total_assets_nok=250_000,
        total_equity_nok=100_000,
        source_url="https://data.brreg.no/regnskap/test",
        verbatim_evidence="2024 statement: 130000",
    )

    series_res = reconcile_multi_year_series([acc_2024, acc_2023])
    assert series_res.is_ok
    reconciled = series_res.unwrap()

    # Sorted newest-first: [2024, 2023]
    assert len(reconciled) == 2
    assert reconciled[0].year == 2024
    assert reconciled[1].year == 2023

    # 2024 YoY Growth: (130k - 100k) / 100k = +30.0%
    assert reconciled[0].yoy_revenue_growth_pct == 30.0
    # 2024 Operating Margin: 32.5k / 130k = 25.0%
    assert reconciled[0].operating_margin_pct == 25.0
    # 2024 Equity Ratio: 100k / 250k = 40.0%
    assert reconciled[0].equity_ratio_pct == 40.0

    # 2023 (first year) has no prior YoY growth
    assert reconciled[1].yoy_revenue_growth_pct is None
    assert reconciled[1].operating_margin_pct == 20.0
