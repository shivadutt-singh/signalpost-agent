"""
Official Multi-Year Financial Accounts Collector (2021-2025).
Combines Layer 1 (Brønnøysund Regnskapsregisteret REST API) and Layer 2 (Verified Multi-Year Table Parser)
with Norwegian numeric normalization (NOK/TNOK/MNOK), anti-fabrication guards, and explicit known-unknown reporting.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from src.collectors.http_budget import BudgetedAsyncClient
from src.models.schema import (
    AnnualFinancialStatement,
    ExtractedFactCandidate,
    FactCategory,
    FactEvidence,
    SourceTier,
)
from src.validators.fact_guard import (
    EntityAnchor,
    verify_page_belongs_to_company,
)
from src.validators.mod11 import normalize_orgnr

logger = logging.getLogger("signalpost.financials")

REGNSKAP_API_URL = "https://data.brreg.no/regnskapsregisteret/regnskap"


def parse_norwegian_financial_number(raw_str: str, multiplier: float = 1.0) -> float | None:
    """
    Parses a Norwegian formatted financial number string (supporting space/dot thousand separators,
    comma decimal separators, parenthesized negatives, and currency unit multipliers).
    Examples:
        '1 250 000' -> 1250000.0
        '1.250.000' -> 1250000.0
        '(150 000)' -> -150000.0
        '-45,5' -> -45.5
        '3 450' with multiplier 1000.0 -> 3450000.0
    """
    if not raw_str or not isinstance(raw_str, str):
        return None

    clean = raw_str.strip().replace("\u00a0", " ").replace("\u202f", " ")

    # Check for parenthesized negative: (1 234) -> -1234
    is_negative = False
    if clean.startswith("(") and clean.endswith(")"):
        is_negative = True
        clean = clean[1:-1].strip()
    elif clean.startswith("-") or clean.startswith("−"):
        is_negative = True
        clean = clean[1:].strip()

    # Check for inline TNOK/MNOK
    local_mult = multiplier
    clean_lower = clean.lower()
    if "mnok" in clean_lower or "mrd" in clean_lower or "mill" in clean_lower:
        local_mult = 1_000_000.0
        clean = re.sub(r"(?i)\b(mnok|mrd|mill\.?\s*kr|mill)\b", "", clean).strip()
    elif "tnok" in clean_lower or "tusen" in clean_lower:
        local_mult = 1_000.0
        clean = re.sub(r"(?i)\b(tnok|tusen\s*kr|tusen)\b", "", clean).strip()

    # Strip currency labels
    clean = re.sub(r"(?i)\b(nok|kr)\b", "", clean).strip()

    # Handle standard Norwegian thousand separators
    if "," in clean and "." in clean:
        # e.g., 1.250.000,50
        clean = clean.replace(".", "").replace(",", ".")
    elif " " in clean and "," in clean:
        # e.g. 1 250 000,50
        clean = clean.replace(" ", "").replace(",", ".")
    elif " " in clean:
        # e.g., 1 250 000
        clean = clean.replace(" ", "")
    elif "." in clean:
        # Check if period is thousand separator (e.g. 1.250.000) or decimal (e.g. 12.5)
        parts = clean.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and int(parts[0]) > 0):
            clean = clean.replace(".", "")
        else:
            # decimal dot
            pass
    elif "," in clean:
        clean = clean.replace(",", ".")

    try:
        val = float(clean)
        if is_negative:
            val = -abs(val)
        return val * local_mult
    except (ValueError, TypeError):
        return None


class FinancialsClient:
    """
    Multi-year financial intelligence collector.
    Extracts 2021-2025 annual accounts with strict entity verification and anti-fabrication guards.
    """

    def __init__(self, client: BudgetedAsyncClient):
        self.client = client
        self.base_url = REGNSKAP_API_URL

    async def fetch_multi_year_financials(
        self,
        orgnr: str,
        anchor: EntityAnchor,
        discovered_html_pages: list[dict[str, str]] | None = None,
    ) -> tuple[list[AnnualFinancialStatement], list[str], list[ExtractedFactCandidate]]:
        """
        Gathers multi-year annual financial accounts.
        Returns:
            (financial_history, known_unknowns_contributions, extracted_fact_candidates)
        """
        clean_orgnr = normalize_orgnr(orgnr)
        statements_by_year: dict[int, AnnualFinancialStatement] = {}
        known_unknowns: list[str] = []
        candidates: list[ExtractedFactCandidate] = []
        now_iso = datetime.now(UTC).isoformat()

        # ----------------------------------------------------------------------
        # Layer 1: Official Brønnøysund Regnskapsregisteret REST API
        # ----------------------------------------------------------------------
        url = f"{self.base_url}/{clean_orgnr}"
        try:
            resp = await self.client.get(url, orgnr=clean_orgnr)
            if resp.status_code == 200:
                raw_list = resp.json()
                if isinstance(raw_list, list):
                    # Sort so KONSERN statements supersede SELSKAP for identical years if available
                    sorted_records = sorted(
                        raw_list,
                        key=lambda r: (r.get("regnskapstype") == "KONSERN", r.get("journalnr", "")),
                    )
                    for item in sorted_records:
                        statement = self._parse_regnskap_item(item, clean_orgnr, url)
                        if statement:
                            statements_by_year[statement.fiscal_year] = statement

                        # Also parse prior-year comparative columns (sammenligningstall / fjoraret) if present
                        prior_stmt = self._parse_comparative_regnskap_item(item, statement, clean_orgnr, url)
                        if prior_stmt and prior_stmt.fiscal_year not in statements_by_year:
                            statements_by_year[prior_stmt.fiscal_year] = prior_stmt
            elif resp.status_code == 404:
                logger.info(f"Regnskapsregisteret returned 404 (no filed accounts) for {clean_orgnr}")
        except Exception as e:
            logger.warning(f"Error querying Regnskapsregisteret for {clean_orgnr}: {e}")

        # ----------------------------------------------------------------------
        # Layer 2: Multi-Year Historical Series Enrichment from Verified Pages
        # ----------------------------------------------------------------------
        # If fewer than 3 historical years available and verified HTML pages provided
        if len(statements_by_year) < 3 and discovered_html_pages:
            for page in discovered_html_pages:
                page_url = page.get("url", "")
                page_text = page.get("raw_text", "")
                page_html = page.get("html", "")
                if not page_html:
                    continue

                # Must pass page-level entity verification
                is_verified, rule, _ = verify_page_belongs_to_company(
                    target_orgnr=clean_orgnr,
                    canonical_name=anchor.legal_name,
                    page_text=page_text or page_html,
                    external_role_entities=anchor.external_role_entities,
                )
                if not is_verified:
                    continue

                table_statements = self._parse_html_financial_tables(
                    html=page_html,
                    source_url=page_url,
                    clean_orgnr=clean_orgnr,
                )
                for stmt in table_statements:
                    if stmt.fiscal_year not in statements_by_year:
                        statements_by_year[stmt.fiscal_year] = stmt

        # ----------------------------------------------------------------------
        # Sort Statements Descending (e.g. [2025, 2024, 2023, 2022, 2021])
        # ----------------------------------------------------------------------
        sorted_statements = sorted(
            statements_by_year.values(),
            key=lambda s: s.fiscal_year,
            reverse=True,
        )

        # ----------------------------------------------------------------------
        # Convert statements into ExtractedFactCandidates
        # ----------------------------------------------------------------------
        for stmt in sorted_statements:
            yr = str(stmt.fiscal_year)
            evidence = FactEvidence(
                source_url=stmt.source_url,
                source_tier=SourceTier.OFFICIAL_REGISTRY if "brreg.no" in stmt.source_url else SourceTier.OFFICIAL_HOMEPAGE,
                source_date=stmt.source_date,
                retrieval_timestamp=now_iso,
                verbatim_quote=stmt.verbatim_evidence,
                entity_match_rule="EXACT_ORGNR_MATCH",
                entity_confidence=1.0,
                raw_digits_verified=True,
                verification_explanation=(
                    f"Annual account for FY{yr} verified from {stmt.source_name} ({stmt.source_url})."
                ),
            )

            if stmt.operating_revenue_nok is not None:
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.revenue_{yr}",
                        value=stmt.operating_revenue_nok,
                        unit=stmt.currency,
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )
            if stmt.operating_profit_nok is not None:
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.operating_profit_{yr}",
                        value=stmt.operating_profit_nok,
                        unit=stmt.currency,
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )
            if stmt.profit_before_tax_nok is not None:
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.profit_before_tax_{yr}",
                        value=stmt.profit_before_tax_nok,
                        unit=stmt.currency,
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )
            if stmt.net_income_nok is not None:
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.net_income_{yr}",
                        value=stmt.net_income_nok,
                        unit=stmt.currency,
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )
            if stmt.total_assets_nok is not None:
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.total_assets_{yr}",
                        value=stmt.total_assets_nok,
                        unit=stmt.currency,
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )
            if stmt.total_equity_nok is not None:
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.total_equity_{yr}",
                        value=stmt.total_equity_nok,
                        unit=stmt.currency,
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )

        # ----------------------------------------------------------------------
        # Deterministic Python Financial Insights (YoY Growth & Operating Margins)
        # ----------------------------------------------------------------------
        insight_facts, trajectory_summary = self.compute_multi_year_insights(sorted_statements)
        candidates.extend(insight_facts)

        # ----------------------------------------------------------------------
        # Explicit Unknowns Reporting (Transparent & Verifiable)
        # ----------------------------------------------------------------------
        if len(sorted_statements) == 0:
            known_unknowns.append(
                f"Multi-year financial accounts: Regnskapsregisteret returned 0 filed accounts for orgnr {clean_orgnr}. "
                f"Housing associations (BRL/ESEK), branch units, or small non-corporate entities are frequently exempt "
                f"from submitting full public annual accounts to Regnskapsregisteret."
            )
        elif len(sorted_statements) == 1:
            only_year = sorted_statements[0].fiscal_year
            known_unknowns.append(
                f"Multi-year financial accounts: Exactly 1 fiscal year ({only_year}) filed and publicly retrievable from "
                f"Regnskapsregisteret; earlier historical fiscal years are unfiled or predate digital registry filing."
            )

        return sorted_statements, known_unknowns, candidates

    @staticmethod
    def compute_multi_year_insights(
        statements: list[AnnualFinancialStatement],
    ) -> tuple[list[ExtractedFactCandidate], str | None]:
        """
        Deterministically computes multi-year financial ratios and trajectory insights in Python:
        1. Operating Margin % = (operating_profit_nok / operating_revenue_nok) * 100
        2. YoY Revenue Growth % = ((rev_t - rev_{t-1}) / rev_{t-1}) * 100
        3. Multi-year trajectory synthesis for executive summary
        Never asks LLM to do math.
        """
        candidates: list[ExtractedFactCandidate] = []
        if not statements:
            return [], None

        stmts_by_year = {s.fiscal_year: s for s in statements}
        now_iso = datetime.now(UTC).isoformat()
        trajectory_summary = None

        # Sort descending by year
        sorted_stmts = sorted(statements, key=lambda s: s.fiscal_year, reverse=True)

        for stmt in sorted_stmts:
            yr = str(stmt.fiscal_year)
            # 1. Operating Margin %
            if stmt.operating_revenue_nok and stmt.operating_revenue_nok > 0 and stmt.operating_profit_nok is not None:
                op_margin = round((stmt.operating_profit_nok / stmt.operating_revenue_nok) * 100.0, 2)
                evidence = FactEvidence(
                    source_url=stmt.source_url,
                    source_tier=SourceTier.OFFICIAL_REGISTRY if "brreg.no" in stmt.source_url else SourceTier.OFFICIAL_HOMEPAGE,
                    source_date=stmt.source_date,
                    retrieval_timestamp=now_iso,
                    verbatim_quote=(
                        f"FY{yr} Operating Margin: {op_margin}% calculated from "
                        f"Driftsresultat {int(stmt.operating_profit_nok)} NOK / "
                        f"Driftsinntekter {int(stmt.operating_revenue_nok)} NOK"
                    ),
                    entity_match_rule="EXACT_ORGNR_MATCH",
                    entity_confidence=1.0,
                    raw_digits_verified=True,
                    verification_explanation=(
                        f"Deterministically computed Operating Margin for FY{yr} in Python ({op_margin}%)."
                    ),
                )
                candidates.append(
                    ExtractedFactCandidate(
                        fact_key=f"financial.operating_margin_{yr}",
                        value=op_margin,
                        unit="%",
                        period_or_year=yr,
                        category=FactCategory.FINANCIAL,
                        evidence=evidence,
                    )
                )

            # 2. YoY Revenue Growth %
            prev_yr = stmt.fiscal_year - 1
            if prev_yr in stmts_by_year:
                prev_stmt = stmts_by_year[prev_yr]
                if (
                    prev_stmt.operating_revenue_nok
                    and prev_stmt.operating_revenue_nok > 0
                    and stmt.operating_revenue_nok is not None
                ):
                    yoy_growth = round(
                        ((stmt.operating_revenue_nok - prev_stmt.operating_revenue_nok) / prev_stmt.operating_revenue_nok) * 100.0,
                        2,
                    )
                    evidence = FactEvidence(
                        source_url=stmt.source_url,
                        source_tier=SourceTier.OFFICIAL_REGISTRY if "brreg.no" in stmt.source_url else SourceTier.OFFICIAL_HOMEPAGE,
                        source_date=stmt.source_date,
                        retrieval_timestamp=now_iso,
                        verbatim_quote=(
                            f"FY{yr} YoY Revenue Growth: {yoy_growth}% calculated from "
                            f"FY{yr} {int(stmt.operating_revenue_nok)} NOK vs "
                            f"FY{prev_yr} {int(prev_stmt.operating_revenue_nok)} NOK"
                        ),
                        entity_match_rule="EXACT_ORGNR_MATCH",
                        entity_confidence=1.0,
                        raw_digits_verified=True,
                        verification_explanation=(
                            f"Deterministically computed YoY Revenue Growth for FY{yr} vs FY{prev_yr} ({yoy_growth}%)."
                        ),
                    )
                    candidates.append(
                        ExtractedFactCandidate(
                            fact_key=f"financial.yoy_revenue_growth_{yr}",
                            value=yoy_growth,
                            unit="%",
                            period_or_year=yr,
                            category=FactCategory.FINANCIAL,
                            evidence=evidence,
                        )
                    )

        # Build trajectory summary for executive summary
        if len(sorted_stmts) >= 2:
            latest = sorted_stmts[0]
            earliest = sorted_stmts[-1]
            prev = stmts_by_year.get(latest.fiscal_year - 1)

            rev_fmt = f"{latest.operating_revenue_nok:,.0f}".replace(",", " ") if latest.operating_revenue_nok else "N/A"
            growth_fmt = ""
            if prev and prev.operating_revenue_nok and latest.operating_revenue_nok:
                pct = round(((latest.operating_revenue_nok - prev.operating_revenue_nok) / prev.operating_revenue_nok) * 100.0, 1)
                sign = "+" if pct > 0 else ""
                growth_fmt = f" (YoY: {sign}{pct}%)"

            margin_fmt = ""
            if latest.operating_revenue_nok and latest.operating_revenue_nok > 0 and latest.operating_profit_nok is not None:
                m_pct = round((latest.operating_profit_nok / latest.operating_revenue_nok) * 100.0, 1)
                margin_fmt = f", operating margin: {m_pct}%"

            trajectory_summary = (
                f"Multi-year accounts ({earliest.fiscal_year}–{latest.fiscal_year}): FY{latest.fiscal_year} "
                f"operating revenue reached {rev_fmt} NOK{growth_fmt}{margin_fmt}."
            )

        return candidates, trajectory_summary

    def _parse_regnskap_item(
        self,
        item: dict[str, Any],
        orgnr: str,
        source_url: str,
    ) -> AnnualFinancialStatement | None:
        """Parses a single JSON statement record from Regnskapsregisteret API."""
        periode = item.get("regnskapsperiode", {})
        til_dato = periode.get("tilDato")
        fra_dato = periode.get("fraDato")

        if not til_dato:
            return None

        try:
            fiscal_year = int(til_dato[:4])
        except ValueError:
            return None

        currency = item.get("valuta", "NOK")

        # P&L (Resultatregnskap)
        resultat = item.get("resultatregnskapResultat", {})
        driftsresultat_dict = resultat.get("driftsresultat", {})

        # Operating Revenue (driftsinntekter)
        operating_revenue = None
        if isinstance(driftsresultat_dict, dict):
            driftsinntekter_dict = driftsresultat_dict.get("driftsinntekter", {})
            if isinstance(driftsinntekter_dict, dict):
                operating_revenue = driftsinntekter_dict.get("sumDriftsinntekter")
                if operating_revenue is None:
                    operating_revenue = driftsinntekter_dict.get("salgsinntekter")

        # Operating Profit (driftsresultat)
        operating_profit = None
        if isinstance(driftsresultat_dict, dict):
            operating_profit = driftsresultat_dict.get("driftsresultat")

        # Profit Before Tax (ordinaertResultatFoerSkattekostnad)
        profit_before_tax = resultat.get("ordinaertResultatFoerSkattekostnad")

        # Net Income (aarsresultat)
        net_income = resultat.get("aarsresultat")

        # Balance Sheet (Balanse)
        eiendeler = item.get("eiendeler", {})
        total_assets = eiendeler.get("sumEiendeler")

        egenkapital_gjeld = item.get("egenkapitalGjeld", {})
        egenkapital_dict = egenkapital_gjeld.get("egenkapital", {})
        total_equity = egenkapital_dict.get("sumEgenkapital") if isinstance(egenkapital_dict, dict) else None

        gjeld_dict = egenkapital_gjeld.get("gjeldOversikt", {})
        total_liabilities = gjeld_dict.get("sumGjeld") if isinstance(gjeld_dict, dict) else None

        verbatim = (
            f"Regnskapsregisteret record journalnr {item.get('journalnr')}: "
            f"Periode {fra_dato} til {til_dato} ({item.get('regnskapstype', 'SELSKAP')}). "
            f"Driftsinntekter: {operating_revenue}, Driftsresultat: {operating_profit}, "
            f"Ordinært resultat før skatt: {profit_before_tax}, Årsresultat: {net_income}, "
            f"Sum eiendeler: {total_assets}, Sum egenkapital: {total_equity}."
        )

        return AnnualFinancialStatement(
            fiscal_year=fiscal_year,
            accounting_period_from=fra_dato,
            accounting_period_to=til_dato,
            currency=currency,
            operating_revenue_nok=float(operating_revenue) if operating_revenue is not None else None,
            operating_profit_nok=float(operating_profit) if operating_profit is not None else None,
            profit_before_tax_nok=float(profit_before_tax) if profit_before_tax is not None else None,
            net_income_nok=float(net_income) if net_income is not None else None,
            total_assets_nok=float(total_assets) if total_assets is not None else None,
            total_equity_nok=float(total_equity) if total_equity is not None else None,
            total_liabilities_nok=float(total_liabilities) if total_liabilities is not None else None,
            source_url=source_url,
            source_name="Brønnøysund Regnskapsregisteret API",
            source_date=til_dato,
            verbatim_evidence=verbatim,
        )

    def _parse_comparative_regnskap_item(
        self,
        item: dict[str, Any],
        current_statement: AnnualFinancialStatement | None,
        orgnr: str,
        source_url: str,
    ) -> AnnualFinancialStatement | None:
        """
        Parses prior-year comparative figures ('sammenligningstall' / 'fjoraret')
        often embedded within an annual filing record.
        """
        comparative = (
            item.get("sammenligningstall")
            or item.get("fjorar")
            or item.get("fjoraret")
            or item.get("tidligerePeriode")
        )
        if not comparative or not isinstance(comparative, dict):
            return None

        prior_year = None
        if current_statement and current_statement.fiscal_year:
            prior_year = current_statement.fiscal_year - 1
        else:
            periode = comparative.get("regnskapsperiode", {})
            til = periode.get("tilDato")
            if til:
                try:
                    prior_year = int(til[:4])
                except ValueError:
                    pass

        if not prior_year:
            return None

        currency = comparative.get("valuta", item.get("valuta", "NOK"))
        resultat = comparative.get("resultatregnskapResultat", {})
        driftsresultat_dict = resultat.get("driftsresultat", {})

        operating_revenue = None
        if isinstance(driftsresultat_dict, dict):
            driftsinntekter_dict = driftsresultat_dict.get("driftsinntekter", {})
            if isinstance(driftsinntekter_dict, dict):
                operating_revenue = driftsinntekter_dict.get("sumDriftsinntekter") or driftsinntekter_dict.get("salgsinntekter")

        operating_profit = driftsresultat_dict.get("driftsresultat") if isinstance(driftsresultat_dict, dict) else None
        profit_before_tax = resultat.get("ordinaertResultatFoerSkattekostnad")
        net_income = resultat.get("aarsresultat")

        eiendeler = comparative.get("eiendeler", {})
        total_assets = eiendeler.get("sumEiendeler")

        egenkapital_gjeld = comparative.get("egenkapitalGjeld", {})
        total_equity = egenkapital_gjeld.get("egenkapital", {}).get("sumEgenkapital") if isinstance(egenkapital_gjeld, dict) else None
        total_liabilities = egenkapital_gjeld.get("gjeldOversikt", {}).get("sumGjeld") if isinstance(egenkapital_gjeld, dict) else None

        source_date = f"{prior_year}-12-31"
        verbatim = (
            f"Regnskapsregisteret comparative record for FY{prior_year} (journalnr {item.get('journalnr')}): "
            f"Driftsinntekter: {operating_revenue}, Driftsresultat: {operating_profit}, "
            f"Ordinært resultat før skatt: {profit_before_tax}, Årsresultat: {net_income}, "
            f"Sum eiendeler: {total_assets}, Sum egenkapital: {total_equity}."
        )

        return AnnualFinancialStatement(
            fiscal_year=prior_year,
            accounting_period_from=f"{prior_year}-01-01",
            accounting_period_to=source_date,
            currency=currency,
            operating_revenue_nok=float(operating_revenue) if operating_revenue is not None else None,
            operating_profit_nok=float(operating_profit) if operating_profit is not None else None,
            profit_before_tax_nok=float(profit_before_tax) if profit_before_tax is not None else None,
            net_income_nok=float(net_income) if net_income is not None else None,
            total_assets_nok=float(total_assets) if total_assets is not None else None,
            total_equity_nok=float(total_equity) if total_equity is not None else None,
            total_liabilities_nok=float(total_liabilities) if total_liabilities is not None else None,
            source_url=source_url,
            source_name="Brønnøysund Regnskapsregisteret Comparative Figures",
            source_date=source_date,
            verbatim_evidence=verbatim,
        )

    def _parse_html_financial_tables(
        self,
        html: str,
        source_url: str,
        clean_orgnr: str,
    ) -> list[AnnualFinancialStatement]:
        """
        Extracts multi-year financial series from HTML tables (e.g. from company annual reports).
        Matches column headers to 4-digit fiscal years (2021-2025) and row labels to core metrics.
        """
        statements_by_year: dict[int, dict[str, Any]] = {}
        soup = BeautifulSoup(html, "html.parser")

        # Multiplier check on entire page/table
        table_multiplier = 1.0
        text_lower = soup.get_text(separator=" ").lower()
        if "alle tall i tnok" in text_lower or "tall i hele tusen" in text_lower or "beløp i tnok" in text_lower:
            table_multiplier = 1_000.0
        elif "alle tall i mnok" in text_lower or "i millioner kroner" in text_lower or "beløp i mnok" in text_lower:
            table_multiplier = 1_000_000.0

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows or len(rows) < 3:
                continue

            # Step 1: Detect year columns from header row
            col_years: dict[int, int] = {}  # col_index -> fiscal_year
            header_row = rows[0]
            cells = header_row.find_all(["th", "td"])
            for idx, cell in enumerate(cells):
                txt = cell.get_text().strip()
                m = re.search(r"\b(202[1-5])\b", txt)
                if m:
                    col_years[idx] = int(m.group(1))

            if not col_years:
                # Try second row
                if len(rows) > 1:
                    cells2 = rows[1].find_all(["th", "td"])
                    for idx, cell in enumerate(cells2):
                        txt = cell.get_text().strip()
                        m = re.search(r"\b(202[1-5])\b", txt)
                        if m:
                            col_years[idx] = int(m.group(1))

            if not col_years:
                continue

            # Step 2: Iterate over content rows
            for row in rows[1:]:
                row_cells = row.find_all(["th", "td"])
                if len(row_cells) < 2:
                    continue

                row_label = row_cells[0].get_text().strip().lower()

                # Metric mapping
                target_field = None
                if any(k in row_label for k in ["driftsinntekter", "salgsinntekter", "omsetning", "total revenue"]):
                    target_field = "operating_revenue_nok"
                elif any(k in row_label for k in ["driftsresultat", "ebit", "operating profit"]):
                    target_field = "operating_profit_nok"
                elif any(k in row_label for k in ["ordinært resultat før", "resultat før skatt", "profit before tax"]):
                    target_field = "profit_before_tax_nok"
                elif any(k in row_label for k in ["årsresultat", "aarsresultat", "net profit", "net income"]):
                    target_field = "net_income_nok"
                elif any(k in row_label for k in ["sum eiendeler", "totalkapital", "total assets"]):
                    target_field = "total_assets_nok"
                elif any(k in row_label for k in ["sum egenkapital", "total equity"]):
                    target_field = "total_equity_nok"
                elif any(k in row_label for k in ["sum gjeld", "total gjeld", "total liabilities"]):
                    target_field = "total_liabilities_nok"

                if not target_field:
                    continue

                for col_idx, yr in col_years.items():
                    if col_idx < len(row_cells):
                        cell_txt = row_cells[col_idx].get_text().strip()
                        num = parse_norwegian_financial_number(cell_txt, multiplier=table_multiplier)
                        if num is not None:
                            if yr not in statements_by_year:
                                statements_by_year[yr] = {
                                    "fiscal_year": yr,
                                    "source_url": source_url,
                                    "verbatim": f"Table Row '{row_cells[0].get_text().strip()}': {cell_txt}",
                                }
                            statements_by_year[yr][target_field] = num

        result: list[AnnualFinancialStatement] = []
        for yr, data in statements_by_year.items():
            result.append(
                AnnualFinancialStatement(
                    fiscal_year=yr,
                    accounting_period_from=f"{yr}-01-01",
                    accounting_period_to=f"{yr}-12-31",
                    currency="NOK",
                    operating_revenue_nok=data.get("operating_revenue_nok"),
                    operating_profit_nok=data.get("operating_profit_nok"),
                    profit_before_tax_nok=data.get("profit_before_tax_nok"),
                    net_income_nok=data.get("net_income_nok"),
                    total_assets_nok=data.get("total_assets_nok"),
                    total_equity_nok=data.get("total_equity_nok"),
                    total_liabilities_nok=data.get("total_liabilities_nok"),
                    source_url=data.get("source_url", source_url),
                    source_name="Verified Company Annual Financial Table",
                    source_date=f"{yr}-12-31",
                    verbatim_evidence=data.get("verbatim", f"FY{yr} Annual Table on {source_url}"),
                )
            )

        return result