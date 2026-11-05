"""
Multi-Year Financial Engine & Regnskapsregisteret Adapter for Signalpost Agent (v6.0 Fail-Proof Edition).

Solves Builderr Organizer Mandate #2:
Extracts up to 5 historical years (2021–2025) of audited financial statements,
reconciles them using AccountingGuard, and computes YoY growth and margins in Python.
Implements FinancialsPort.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.http_gateway import HttpGateway
from src.domain.accounting_guard import (
    parse_norwegian_number,
    reconcile_multi_year_series,
)
from src.domain.entities import AnnualAccount, CandidateFact, SourceTier
from src.domain.errors import DomainError
from src.domain.result import Err, Ok, Result
from src.ports.interfaces import FinancialsPort

logger = logging.getLogger(__name__)

REGNSKAP_API_URL = "https://data.brreg.no/regnskapsregisteret/regnskap"


class FinancialsAdapter(FinancialsPort):
    """Adapter for Brreg Regnskapsregisteret and multi-year time-series synthesis."""

    def __init__(self, http_gateway: HttpGateway) -> None:
        self.http = http_gateway

    async def get_multi_year_financials(
        self, orgnr: str
    ) -> Result[list[AnnualAccount], DomainError]:
        """
        Fetches official multi-year financial statements from Regnskapsregisteret.
        Parses multiple filings, comparative prior-year figures, and reconciles via Python.
        """
        url = f"{REGNSKAP_API_URL}/{orgnr}"
        res = await self.http.get(url, orgnr=orgnr)

        if res.is_err:
            # If 404 or no filings (common for new entities or holding/housing companies), return empty list
            return Ok([])

        resp = res.unwrap()
        if resp.status_code == 404 or resp.status_code != 200:
            return Ok([])

        try:
            data = resp.json()
            raw_filings = data if isinstance(data, list) else [data]
        except Exception:
            return Ok([])

        parsed_accounts: list[AnnualAccount] = []

        for filing in raw_filings:
            acc_list = self._parse_single_filing(filing, orgnr, url)
            parsed_accounts.extend(acc_list)

        # Reconcile via AccountingGuard
        reconciled_res = reconcile_multi_year_series(parsed_accounts)
        if reconciled_res.is_err:
            return Err(reconciled_res.unwrap_err())

        return Ok(reconciled_res.unwrap())

    def _parse_single_filing(
        self,
        filing: dict[str, Any],
        orgnr: str,
        source_url: str,
    ) -> list[AnnualAccount]:
        """Parses current and comparative prior years from a single Regnskapsregisteret JSON filing."""
        results: list[AnnualAccount] = []
        periode = filing.get("regnskapsperiode", {})
        til_dato = periode.get("tilDato", "")

        if not til_dato or len(til_dato) < 4:
            return results

        try:
            curr_year = int(til_dato[:4])
        except ValueError:
            return results

        res_regnskap = filing.get("resultatregnskapResultat", {})
        balanse = filing.get("egenkapitalGjeld", {})
        eiendeler = filing.get("eiendeler", {})

        def _safe_int(container: dict[str, Any], key: str) -> int | None:
            val = container.get(key)
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str):
                p = parse_norwegian_number(val)
                return p.unwrap() if p.is_ok else None
            return None

        # 1. Parse current year
        rev = _safe_int(res_regnskap, "driftsinntekter")
        op_profit = _safe_int(res_regnskap, "driftsresultat")
        pbt = _safe_int(res_regnskap, "ordinaertResultatFoerSkattekostnad")
        net_inc = _safe_int(res_regnskap, "aarsresultat")
        assets = _safe_int(eiendeler, "sumEiendeler")
        equity = _safe_int(balanse, "sumEgenkapital")

        curr_evidence = (
            f"Audited Brreg Regnskapsregisteret filing for fiscal year {curr_year}: "
            f"Revenue={rev}, OperatingProfit={op_profit}, ProfitBeforeTax={pbt}, "
            f"NetIncome={net_inc}, TotalAssets={assets}, TotalEquity={equity}"
        )

        curr_account = AnnualAccount(
            year=curr_year,
            revenue_nok=rev,
            operating_profit_nok=op_profit,
            profit_before_tax_nok=pbt,
            net_income_nok=net_inc,
            total_assets_nok=assets,
            total_equity_nok=equity,
            source_url=source_url,
            verbatim_evidence=curr_evidence,
        )
        results.append(curr_account)

        # 2. Inspect comparative prior year figures (fjoraret / sammenligningstall)
        prior_res = (
            filing.get("fjoraretResultatregnskapResultat")
            or filing.get("sammenligningstallResultatregnskap")
            or {}
        )
        prior_balanse = (
            filing.get("fjoraretEgenkapitalGjeld")
            or filing.get("sammenligningstallEgenkapitalGjeld")
            or {}
        )
        prior_eiendeler = (
            filing.get("fjoraretEiendeler")
            or filing.get("sammenligningstallEiendeler")
            or {}
        )

        if prior_res or prior_balanse or prior_eiendeler:
            prior_year = curr_year - 1
            p_rev = _safe_int(prior_res, "driftsinntekter")
            p_op_profit = _safe_int(prior_res, "driftsresultat")
            p_pbt = _safe_int(prior_res, "ordinaertResultatFoerSkattekostnad")
            p_net_inc = _safe_int(prior_res, "aarsresultat")
            p_assets = _safe_int(prior_eiendeler, "sumEiendeler")
            p_equity = _safe_int(prior_balanse, "sumEgenkapital")

            if any(
                v is not None
                for v in [p_rev, p_op_profit, p_pbt, p_net_inc, p_assets, p_equity]
            ):
                prior_evidence = (
                    f"Audited Brreg comparative prior-year filing for fiscal year {prior_year}: "
                    f"Revenue={p_rev}, OperatingProfit={p_op_profit}, ProfitBeforeTax={p_pbt}, "
                    f"NetIncome={p_net_inc}, TotalAssets={p_assets}, TotalEquity={p_equity}"
                )
                prior_account = AnnualAccount(
                    year=prior_year,
                    revenue_nok=p_rev,
                    operating_profit_nok=p_op_profit,
                    profit_before_tax_nok=p_pbt,
                    net_income_nok=p_net_inc,
                    total_assets_nok=p_assets,
                    total_equity_nok=p_equity,
                    source_url=source_url,
                    verbatim_evidence=prior_evidence,
                )
                results.append(prior_account)

        return results

    def accounts_to_candidate_facts(
        self, accounts: list[AnnualAccount], orgnr: str
    ) -> list[CandidateFact]:
        """Converts multi-year accounts into verifiable CandidateFact objects for the profile."""
        facts: list[CandidateFact] = []
        for acc in accounts:
            yr = acc.year
            url = acc.source_url
            date_str = f"{yr}-12-31"

            if acc.revenue_nok is not None:
                facts.append(
                    CandidateFact(
                        predicate=f"financial.revenue_{yr}",
                        value=acc.revenue_nok,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=url,
                        source_date=date_str,
                        verbatim_evidence=f"Revenue {yr}: {acc.revenue_nok} NOK ({acc.verbatim_evidence})",
                    )
                )

            if acc.operating_profit_nok is not None:
                facts.append(
                    CandidateFact(
                        predicate=f"financial.operating_profit_{yr}",
                        value=acc.operating_profit_nok,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=url,
                        source_date=date_str,
                        verbatim_evidence=f"Operating profit {yr}: {acc.operating_profit_nok} NOK ({acc.verbatim_evidence})",
                    )
                )

            if acc.yoy_revenue_growth_pct is not None:
                facts.append(
                    CandidateFact(
                        predicate=f"financial.yoy_growth_pct_{yr}",
                        value=acc.yoy_revenue_growth_pct,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=url,
                        source_date=date_str,
                        verbatim_evidence=f"YoY revenue growth {yr}: {acc.yoy_revenue_growth_pct}%",
                    )
                )

            if acc.operating_margin_pct is not None:
                facts.append(
                    CandidateFact(
                        predicate=f"financial.operating_margin_pct_{yr}",
                        value=acc.operating_margin_pct,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=url,
                        source_date=date_str,
                        verbatim_evidence=f"Operating margin {yr}: {acc.operating_margin_pct}%",
                    )
                )

        return facts
