"""
Accounting Guard & Financial Reconciler for Signalpost Agent (v6.0 Fail-Proof Edition).

Solves Builderr Organizer Mandate #2:
Parses Norwegian financial figures deterministically, verifies digit integrity against
verbatim evidence, and computes multi-year YoY growth and margins in pure Python.
"""

from __future__ import annotations

import re

from src.domain.entities import AnnualAccount
from src.domain.errors import FinancialReconciliationError
from src.domain.result import Err, Ok, Result


def parse_norwegian_number(raw: str) -> Result[int, FinancialReconciliationError]:
    """
    Parses a Norwegian-formatted numeric string into an integer NOK amount.
    Handles:
      - '1 250 400' -> 1250400
      - '1.250.400' -> 1250400
      - '-45 200' or '(45 200)' -> -45200
      - '12,5 MNOK' -> 12500000
      - '450 TNOK' -> 450000
    """
    if not raw or not isinstance(raw, str):
        return Err(
            FinancialReconciliationError(
                code="EMPTY_INPUT",
                message="Input number string is empty or invalid",
                details={"raw": str(raw)},
            )
        )

    text = raw.strip()

    # Detect negative signs including parentheses
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1].strip()
    elif text.startswith("-") or text.startswith("−") or text.startswith("–"):
        is_negative = True
        text = text[1:].strip()

    # Detect multiplier units
    multiplier = 1
    upper = text.upper()
    if "MNOK" in upper or "MKR" in upper or "MILL" in upper:
        multiplier = 1_000_000
        text = re.sub(r"(?i)\s*(MNOK|MKR|MILLIONER|MILL)\b", "", text)
    elif "TNOK" in upper or "TKR" in upper or "KNOK" in upper or "TUSEN" in upper:
        multiplier = 1_000
        text = re.sub(r"(?i)\s*(TNOK|TKR|KNOK|TUSEN)\b", "", text)

    # Clean currency labels
    text = re.sub(r"(?i)\s*(NOK|KR)\b", "", text).strip()

    # Handle comma decimals (e.g. '12,5' -> float 12.5)
    if "," in text:
        parts = text.split(",")
        if len(parts) == 2:
            int_part = re.sub(r"[\s\.]+", "", parts[0])
            dec_part = re.sub(r"[\s\.]+", "", parts[1])
            if int_part.isdigit() and dec_part.isdigit():
                val_float = float(f"{int_part}.{dec_part}") * multiplier
                result_int = int(round(val_float))
                return Ok(-result_int if is_negative else result_int)

    # Standard integer: strip internal spaces and thousand dots
    cleaned_digits = re.sub(r"[\s\.]+", "", text)
    if not cleaned_digits or not cleaned_digits.isdigit():
        return Err(
            FinancialReconciliationError(
                code="INVALID_DIGITS",
                message=f"Could not parse valid numeric digits from '{raw}'",
                details={"raw": raw, "cleaned": cleaned_digits},
            )
        )

    final_val = int(cleaned_digits) * multiplier
    return Ok(-final_val if is_negative else final_val)


def verify_number_digits_in_evidence(
    val: int, verbatim_evidence: str
) -> Result[bool, FinancialReconciliationError]:
    """
    Firewall check: verifies that the significant digits of the numeric value
    are physically present in the verbatim evidence text snippet.
    """
    if not verbatim_evidence:
        return Err(
            FinancialReconciliationError(
                code="EMPTY_EVIDENCE",
                message="Verbatim evidence snippet is empty",
                details={"value": val},
            )
        )

    evidence_digits = re.sub(r"\D", "", verbatim_evidence)
    val_digits = str(abs(val))

    # For large numbers, check at least the first 3-4 significant digits
    sig_digits = val_digits[: min(4, len(val_digits))]

    if sig_digits not in evidence_digits:
        return Err(
            FinancialReconciliationError(
                code="DIGITS_ABSENT_IN_EVIDENCE",
                message=f"Significant digits {sig_digits!r} of value {val} not found in evidence",
                details={
                    "value": val,
                    "sig_digits": sig_digits,
                    "evidence": verbatim_evidence[:100],
                },
            )
        )

    return Ok(True)


def calculate_yoy_growth(current_val: int, previous_val: int) -> float | None:
    """Computes YoY percentage growth deterministically in Python."""
    if previous_val == 0:
        return None
    growth = ((current_val - previous_val) / abs(previous_val)) * 100.0
    return round(growth, 2)


def calculate_operating_margin(operating_profit: int, revenue: int) -> float | None:
    """Computes operating margin % (Driftsmargin) deterministically in Python."""
    if revenue == 0:
        return None
    margin = (operating_profit / revenue) * 100.0
    return round(margin, 2)


def calculate_equity_ratio(total_equity: int, total_assets: int) -> float | None:
    """Computes equity ratio % (Egenkapitalandel) deterministically in Python."""
    if total_assets <= 0:
        return None
    ratio = (total_equity / total_assets) * 100.0
    return round(ratio, 2)


def reconcile_multi_year_series(
    accounts: list[AnnualAccount],
) -> Result[list[AnnualAccount], FinancialReconciliationError]:
    """
    Takes annual accounts, sorts them chronologically, and calculates
    YoY revenue growth, operating margin, and equity ratio for each year.
    Returns the audited series sorted newest-first.
    """
    if not accounts:
        return Ok([])

    # Deduplicate by year, keeping the one with higher data completeness
    year_map: dict[int, AnnualAccount] = {}
    for acc in accounts:
        existing = year_map.get(acc.year)
        if not existing:
            year_map[acc.year] = acc
        else:
            # Prefer account with more populated fields
            ex_fields = sum(
                1
                for v in [
                    existing.revenue_nok,
                    existing.operating_profit_nok,
                    existing.total_assets_nok,
                ]
                if v is not None
            )
            new_fields = sum(
                1
                for v in [
                    acc.revenue_nok,
                    acc.operating_profit_nok,
                    acc.total_assets_nok,
                ]
                if v is not None
            )
            if new_fields > ex_fields:
                year_map[acc.year] = acc

    sorted_years = sorted(year_map.keys())  # Oldest to newest
    reconciled: list[AnnualAccount] = []

    for idx, yr in enumerate(sorted_years):
        curr = year_map[yr]

        # Calculate YoY revenue growth
        yoy_growth = None
        if idx > 0 and curr.revenue_nok is not None:
            prev_year = sorted_years[idx - 1]
            prev = year_map[prev_year]
            if prev.revenue_nok is not None and prev.revenue_nok != 0:
                yoy_growth = calculate_yoy_growth(curr.revenue_nok, prev.revenue_nok)

        # Calculate operating margin
        op_margin = None
        if curr.operating_profit_nok is not None and curr.revenue_nok is not None:
            op_margin = calculate_operating_margin(
                curr.operating_profit_nok, curr.revenue_nok
            )

        # Calculate equity ratio
        eq_ratio = None
        if curr.total_equity_nok is not None and curr.total_assets_nok is not None:
            eq_ratio = calculate_equity_ratio(
                curr.total_equity_nok, curr.total_assets_nok
            )

        # Create updated immutable AnnualAccount
        updated = AnnualAccount(
            year=curr.year,
            revenue_nok=curr.revenue_nok,
            operating_profit_nok=curr.operating_profit_nok,
            profit_before_tax_nok=curr.profit_before_tax_nok,
            net_income_nok=curr.net_income_nok,
            total_assets_nok=curr.total_assets_nok,
            total_equity_nok=curr.total_equity_nok,
            yoy_revenue_growth_pct=yoy_growth,
            operating_margin_pct=op_margin,
            equity_ratio_pct=eq_ratio,
            source_url=curr.source_url,
            verbatim_evidence=curr.verbatim_evidence,
        )
        reconciled.append(updated)

    # Sort newest-first for presentation
    reconciled.sort(key=lambda a: a.year, reverse=True)
    return Ok(reconciled)
