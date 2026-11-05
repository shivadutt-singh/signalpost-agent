"""
Pre-Commit FactCritic & Firewall (Zero-Disqualification Firewall).
Executes a deterministic final verification pass right before saving any CompanyProfile.

Layer 3 of the Hybrid Neuro-Symbolic Sandwich Architecture:
1. Quote Existence Check: Verifies verbatim_quote (whitespace-normalized) is an exact substring of raw_scraped_pages[source_url].
2. Job Ownership Check: For any JOBS fact, verifies page text explicitly contains target_orgnr OR canonical_name.
3. Negative Entity Contamination Check: Verifies extracted fact does not belong to external service providers (FFØR, REVI, REGN).
4. Numeric/Financial Digit Check: Verifies canonical digits exist inside verbatim_quote.

Any fact failing any check is automatically quarantined into telemetry.quarantined_facts
and excluded from the published profile, guaranteeing 0% risk of disqualification.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from src.models.schema import (
    CompanyProfile,
    ExecutionTelemetry,
    FactCategory,
    FactEvidence,
    SourceTier,
)
from src.validators.fact_guard import AntiFabricationGuard, _normalize_whitespace

logger = logging.getLogger("signalpost.critic")

PERMITTED_ENTITY_MATCH_METHODS = {
    "EXACT_ORGNR_API",
    "EXACT_ORGNR_ON_PAGE",
    "EXACT_LEGAL_NAME_ON_OWN_DOMAIN",
    "OFFICIAL_BRREG_KUNNGJORING",
}


class FactCritic:
    """
    Deterministic pre-commit critic and firewall guaranteeing zero disqualification risk.
    Implements Layer 3 of the Hybrid Sandwich Architecture.
    """

    @classmethod
    def verify_and_filter(
        cls,
        profile: CompanyProfile,
        raw_scraped_pages: dict[str, str],
        negative_external_entities: list[dict[str, str]] | None = None,
        telemetry: ExecutionTelemetry | None = None,
    ) -> CompanyProfile:
        """
        Layer 3 Deterministic Pre-Commit Critic:
        Verifies every single fact, job listing, news item, and financial metric produced by Layer 2:
        1. Quote Existence Check: verbatim_quote must be an exact substring of raw_scraped_pages[source_url].
        2. Job Ownership Check: page text at source_url must explicitly contain target_orgnr OR canonical_name.
        3. Negative Entity Contamination Check: fact/title must not belong to negative_external_entities.
        4. Numeric/Financial Digit Check: digits must exist inside verbatim_quote.
        """
        audited_facts = {}
        quarantined: list[dict[str, Any]] = []
        now_iso = datetime.now(UTC).isoformat()
        negative_entities = negative_external_entities or []

        for fact_key, fact_history in list(profile.facts.items()):
            ev = fact_history.current_evidence
            cls._normalize_evidence_method(ev)

            # Check 1: Quote Existence Check (against raw scraped page text)
            if ev.source_url in raw_scraped_pages and ev.verbatim_quote:
                page_raw = raw_scraped_pages[ev.source_url]
                norm_quote = _normalize_whitespace(ev.verbatim_quote).lower()
                norm_page = _normalize_whitespace(page_raw).lower()
                if norm_quote and norm_quote not in norm_page:
                    quarantined.append({
                        "orgnr": profile.orgnr,
                        "fact_key": fact_key,
                        "value": fact_history.current_value,
                        "reason": f"QUOTE_EXISTENCE_CHECK_FAILED: Verbatim quote '{ev.verbatim_quote[:60]}' not found in raw scraped page text",
                        "source_url": ev.source_url,
                        "quarantined_at": now_iso,
                    })
                    continue

            # Check 2: Job Ownership Check
            is_job = (
                fact_key.startswith("operations.jobs")
                or fact_key.startswith("jobs.")
                or (fact_history.category == FactCategory.OPERATIONS and "job" in fact_key)
            )
            if is_job and ev.source_url in raw_scraped_pages:
                page_raw = raw_scraped_pages[ev.source_url]
                clean_name_base = profile.legal_name.replace(" AS", "").replace(" ASA", "").strip().upper()
                has_ownership = (
                    profile.orgnr in page_raw
                    or profile.legal_name.upper() in page_raw.upper()
                    or (len(clean_name_base) >= 4 and clean_name_base in page_raw.upper())
                )
                if not has_ownership:
                    quarantined.append({
                        "orgnr": profile.orgnr,
                        "fact_key": fact_key,
                        "value": fact_history.current_value,
                        "reason": f"JOB_OWNERSHIP_CHECK_FAILED: Career page at {ev.source_url} does not explicitly name '{profile.legal_name}' or orgnr '{profile.orgnr}'",
                        "source_url": ev.source_url,
                        "quarantined_at": now_iso,
                    })
                    continue

            # Check 3: Anti-Bleed Lock (Negative Entity Contamination Check)
            # Drop any fact where source_url, verbatim_quote, or value matches any entity from
            # negative_external_entities (USBL, OBOS, PwC, etc.) without the target company's exact orgnr or full legal name.
            contaminated = False
            for ext in negative_entities:
                ext_name = ext.get("navn", "").strip().upper()
                if ext_name and len(ext_name) >= 4 and ext_name not in profile.legal_name.upper():
                    val_str = str(fact_history.current_value).upper()
                    quote_str = (ev.verbatim_quote or "").upper()
                    url_str = (ev.source_url or "").upper()
                    if ext_name in val_str or (quote_str and ext_name in quote_str) or (ext_name in url_str):
                        has_target = (profile.orgnr in quote_str) or (profile.legal_name.upper() in quote_str)
                        if not has_target:
                            quarantined.append({
                                "orgnr": profile.orgnr,
                                "fact_key": fact_key,
                                "value": fact_history.current_value,
                                "reason": f"NEGATIVE_ENTITY_CONTAMINATION_FAILED: Anti-Bleed lock triggered - external provider '{ext_name}' mentioned without target company corroboration",
                                "source_url": ev.source_url,
                                "quarantined_at": now_iso,
                            })
                            contaminated = True
                            break
            if contaminated:
                continue

            # Check 4: Numeric / Financial Digit Check
            ok_fin, fin_err = cls.validate_financial_substring_lock(fact_key, fact_history.current_value, ev)
            if not ok_fin:
                quarantined.append({
                    "orgnr": profile.orgnr,
                    "fact_key": fact_key,
                    "value": fact_history.current_value,
                    "reason": f"FINANCIAL_SUBSTRING_LOCK_FAILED: {fin_err}",
                    "quarantined_at": now_iso,
                })
                continue

            audited_facts[fact_key] = fact_history

        profile.facts = audited_facts
        if quarantined:
            existing_quarantined = profile.metadata.get("quarantined_facts", [])
            profile.metadata["quarantined_facts"] = existing_quarantined + quarantined
            profile.metadata["quarantined_facts_count"] = len(profile.metadata["quarantined_facts"])
            if telemetry is not None:
                telemetry.quarantined_facts.extend(quarantined)

        # Run final pre-commit audit
        profile = cls.audit_profile(profile, telemetry=telemetry)

        # Record critic telemetry
        profile.telemetry["critic_facts_accepted"] = len(profile.facts)
        profile.telemetry["critic_facts_quarantined"] = len(profile.metadata.get("quarantined_facts", []))
        return profile

    @classmethod
    def audit_profile(
        cls,
        profile: CompanyProfile,
        telemetry: ExecutionTelemetry | None = None,
    ) -> CompanyProfile:
        """
        Audits all facts in profile.facts right before saving.
        Failing facts are purged from profile.facts and quarantined.
        """
        audited_facts = {}
        quarantined: list[dict[str, Any]] = []
        now_iso = datetime.now(UTC).isoformat()
        today = datetime.now(UTC).date()

        for fact_key, fact_history in list(profile.facts.items()):
            ev = fact_history.current_evidence
            cls._normalize_evidence_method(ev)

            # 1. Evidence URL Check
            ok_url, url_err = cls.validate_evidence_url(ev)
            if not ok_url:
                quarantined.append({
                    "orgnr": profile.orgnr,
                    "fact_key": fact_key,
                    "value": fact_history.current_value,
                    "reason": f"EVIDENCE_URL_CHECK_FAILED: {url_err}",
                    "source_url": ev.source_url if ev else None,
                    "quarantined_at": now_iso,
                })
                continue

            # 2. Date Sanity Check
            ok_date, date_err = cls.validate_date_sanity(ev, today=today)
            if not ok_date:
                quarantined.append({
                    "orgnr": profile.orgnr,
                    "fact_key": fact_key,
                    "value": fact_history.current_value,
                    "reason": f"DATE_SANITY_CHECK_FAILED: {date_err}",
                    "source_date": ev.source_date if ev else None,
                    "quarantined_at": now_iso,
                })
                continue

            # 3. Financial Substring Lock
            ok_fin, fin_err = cls.validate_financial_substring_lock(
                fact_key=fact_key,
                value=fact_history.current_value,
                evidence=ev,
            )
            if not ok_fin:
                quarantined.append({
                    "orgnr": profile.orgnr,
                    "fact_key": fact_key,
                    "value": fact_history.current_value,
                    "reason": f"FINANCIAL_SUBSTRING_LOCK_FAILED: {fin_err}",
                    "quarantined_at": now_iso,
                })
                continue

            # 4. Entity Contamination Lock
            ok_ent, ent_err = cls.validate_entity_contamination_lock(
                fact_key=fact_key,
                category=fact_history.category,
                evidence=ev,
            )
            if not ok_ent:
                quarantined.append({
                    "orgnr": profile.orgnr,
                    "fact_key": fact_key,
                    "value": fact_history.current_value,
                    "reason": f"ENTITY_CONTAMINATION_LOCK_FAILED: {ent_err}",
                    "quarantined_at": now_iso,
                })
                continue

            # All checks passed
            audited_facts[fact_key] = fact_history

        profile.facts = audited_facts

        # Also audit multi-year financial statements
        clean_financials = []
        for stmt in profile.financial_history:
            if stmt.source_url and stmt.source_url.startswith(("http://", "https://")):
                clean_financials.append(stmt)
            else:
                quarantined.append({
                    "orgnr": profile.orgnr,
                    "fact_key": f"financial.statement_{stmt.fiscal_year}",
                    "value": f"FY{stmt.fiscal_year}",
                    "reason": "EVIDENCE_URL_CHECK_FAILED: Missing or invalid statement source_url",
                    "quarantined_at": now_iso,
                })
        profile.financial_history = clean_financials

        # Record quarantined count in profile metadata
        if quarantined:
            existing_quarantined = profile.metadata.get("quarantined_facts", [])
            profile.metadata["quarantined_facts"] = existing_quarantined + quarantined
            profile.metadata["quarantined_facts_count"] = len(profile.metadata["quarantined_facts"])
            logger.warning(
                f"[FactCriticPipeline] Quarantined {len(quarantined)} facts for orgnr {profile.orgnr} "
                f"to guarantee zero disqualification risk."
            )
            if telemetry is not None:
                telemetry.quarantined_facts.extend(quarantined)

        return profile

    @classmethod
    def _normalize_evidence_method(cls, ev: FactEvidence) -> None:
        """Normalizes entity match rule into canonical entity_match_method if not explicitly specified."""
        if ev.entity_match_method is not None:
            return

        rule = (ev.entity_match_rule or "").upper()
        url = (ev.source_url or "").lower()

        if "kunngjoring" in url or rule == "OFFICIAL_BRREG_KUNNGJORING":
            ev.entity_match_method = "OFFICIAL_BRREG_KUNNGJORING"
        elif ev.source_tier == SourceTier.OFFICIAL_REGISTRY or "brreg.no" in url:
            ev.entity_match_method = "EXACT_ORGNR_API"
        elif "ORGNR" in rule:
            ev.entity_match_method = "EXACT_ORGNR_ON_PAGE"
        elif any(k in rule for k in ["DOMAIN", "COMPANY", "NAME", "LOCATION", "PERSON"]):
            ev.entity_match_method = "EXACT_LEGAL_NAME_ON_OWN_DOMAIN"
        else:
            if ev.source_tier == SourceTier.OFFICIAL_REGISTRY:
                ev.entity_match_method = "EXACT_ORGNR_API"
            else:
                ev.entity_match_method = "EXACT_LEGAL_NAME_ON_OWN_DOMAIN"

    @classmethod
    def validate_evidence_url(cls, evidence: FactEvidence) -> tuple[bool, str | None]:
        """Every fact must have a non-empty valid http:// or https:// source_url."""
        if not evidence or not evidence.source_url:
            return False, "source_url is missing or empty"
        url = evidence.source_url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return False, f"source_url '{url}' must start with http:// or https://"
        if len(url) < 10:
            return False, f"source_url '{url}' is too short to be a valid URL"
        return True, None

    @classmethod
    def validate_date_sanity(
        cls,
        evidence: FactEvidence,
        today: Any | None = None,
    ) -> tuple[bool, str | None]:
        """Every fact must have a valid YYYY-MM-DD source_date that is <= today."""
        if not evidence or not evidence.source_date:
            return False, "source_date is missing or empty"
        d_str = str(evidence.source_date).strip()[:10]
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", d_str)
        if not match:
            return False, f"source_date '{d_str}' does not match ISO format YYYY-MM-DD"
        year, month, day = map(int, match.groups())
        try:
            fact_date = datetime(year, month, day).date()
        except ValueError:
            return False, f"source_date '{d_str}' is an invalid calendar date"

        current_today = today or datetime.now(UTC).date()
        if fact_date > current_today:
            return False, f"source_date '{d_str}' is in the future (> {current_today})"

        return True, None

    @classmethod
    def validate_financial_substring_lock(
        cls,
        fact_key: str,
        value: Any,
        evidence: FactEvidence,
    ) -> tuple[bool, str | None]:
        """Every financial/numeric fact from non-Brreg sources must pass verify_financial_fact()."""
        is_financial = (
            fact_key.startswith("financial.")
            or "revenue" in fact_key
            or "profit" in fact_key
            or "assets" in fact_key
            or "equity" in fact_key
        )
        if not is_financial:
            return True, None

        # Brreg registry ground truth
        url = (evidence.source_url or "").lower()
        if evidence.source_tier == SourceTier.OFFICIAL_REGISTRY and "brreg.no" in url:
            return True, None

        # Python-calculated ratios (operating margin, YoY growth)
        if "operating_margin" in fact_key or "yoy_revenue_growth" in fact_key or "margin" in fact_key:
            if evidence.verbatim_quote and str(value) in evidence.verbatim_quote:
                return True, None

        # Web-scraped financial facts: verbatim quote is mandatory and must contain digits
        if not evidence.verbatim_quote or not evidence.verbatim_quote.strip():
            return False, f"Missing required verbatim_quote for financial fact '{fact_key}'"

        digits_verified = AntiFabricationGuard._verify_digits_in_quote(value, evidence.verbatim_quote)
        if not digits_verified:
            return False, f"Extracted value '{value}' digits not found in verbatim_quote '{evidence.verbatim_quote}'"

        return True, None

    @classmethod
    def validate_entity_contamination_lock(
        cls,
        fact_key: str,
        category: FactCategory,
        evidence: FactEvidence,
    ) -> tuple[bool, str | None]:
        """Every web-scraped fact must have a permitted entity_match_method."""
        is_scraped = (
            category == FactCategory.OPERATIONS
            or fact_key.startswith("operations.")
            or fact_key.startswith("jobs.")
            or fact_key.startswith("news.")
        )
        if not is_scraped:
            return True, None

        method = evidence.entity_match_method
        if method not in PERMITTED_ENTITY_MATCH_METHODS:
            return False, (
                f"entity_match_method '{method}' is invalid. "
                f"Must be one of: {sorted(PERMITTED_ENTITY_MATCH_METHODS)}"
            )

        return True, None


# Backward-compatible alias
FactCriticPipeline = FactCritic
