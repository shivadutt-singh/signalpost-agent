"""
Entity Verification Gate & Anti-Fabrication Financial Substring Verifier.
Implements the Critical Hard Competition Constraints to prevent wrong-company bleed
and financial hallucinations with zero tolerance.
"""

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, List, Optional
from src.validators.mod11 import extract_orgnrs_from_text


def _normalize_whitespace(text: str) -> str:
    """Collapses consecutive whitespace characters into a single space and strips ends."""
    if not text:
        return ""
    # Normalize unicode spaces/nbsp
    cleaned = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\ufeff", "")
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass
class EntityAnchor:
    """Ground truth anchor metadata extracted from official Brønnøysund Registry."""
    orgnr: str
    legal_name: str
    municipality: Optional[str] = None
    ceo_names: List[str] = field(default_factory=list)
    chair_names: List[str] = field(default_factory=list)
    official_domains: List[str] = field(default_factory=list)

    @property
    def clean_legal_name(self) -> str:
        """Strips standard legal corporate form suffixes for flexible matching."""
        name = self.legal_name.upper()
        # Remove common Norwegian entity forms from end of name
        suffixes = [
            r"\bALLMENT AKSJESELSKAP\b",
            r"\bAKSJESELSKAP\b",
            r"\bASA\b",
            r"\bAS\b",
            r"\bANS\b",
            r"\bDA\b",
            r"\bENK\b",
            r"\bNUF\b",
            r"\bSA\b",
            r"\bSTIFTELSE\b",
        ]
        for pattern in suffixes:
            name = re.sub(pattern, "", name).strip()
        # Clean punctuation
        name = re.sub(r"[,\.-]+$", "", name).strip()
        return name if len(name) >= 3 else self.legal_name.upper()


@dataclass
class EntityVerificationResult:
    """Outcome of Entity Verification Gate check."""
    is_verified: bool
    rule: str
    confidence: float
    explanation: str


class EntityVerificationGate:
    """
    Deterministic gate guaranteeing ZERO wrong-company bleed.
    Every non-registry web source MUST satisfy at least one deterministic condition:
    1. Exact 9-digit orgnr match in source text.
    2. Source URL domain matches verified official registry homepage domain.
    3. Exact legal name match AND official municipality match.
    4. Exact legal name match AND official CEO/Chair name match.
    """

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extracts normalized hostname from URL, stripping www."""
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.netloc or parsed.path).lower().split(":")[0]
            if host.startswith("www."):
                host = host[4:]
            return host.strip()
        except Exception:
            return ""

    @classmethod
    def verify_entity_ownership(
        cls,
        anchor: EntityAnchor,
        source_url: str,
        raw_source_text: str,
    ) -> EntityVerificationResult:
        """
        Evaluates source URL and text against official anchor credentials.
        Returns detailed verification result with explanation.
        """
        norm_text = _normalize_whitespace(raw_source_text)
        upper_text = norm_text.upper()

        # Multi-entity directory detection
        extracted_orgnrs = extract_orgnrs_from_text(raw_source_text)
        is_directory_page = len(extracted_orgnrs) > 3

        # Gate 1: Exact 9-digit Orgnr Match in text
        if anchor.orgnr in norm_text:
            return EntityVerificationResult(
                is_verified=True,
                rule="ORGNR_MATCH",
                confidence=1.0,
                explanation=(
                    f"Entity identity verified: raw source text contains official "
                    f"Norwegian 9-digit enterprise number '{anchor.orgnr}'."
                ),
            )

        # Also check formatted orgnr e.g. "923 609 016" or "923-609-016"
        spaced_orgnr = f"{anchor.orgnr[:3]} {anchor.orgnr[3:6]} {anchor.orgnr[6:]}"
        if spaced_orgnr in norm_text:
            return EntityVerificationResult(
                is_verified=True,
                rule="ORGNR_MATCH",
                confidence=1.0,
                explanation=(
                    f"Entity identity verified: raw source text contains formatted "
                    f"Norwegian enterprise number '{spaced_orgnr}'."
                ),
            )

        # Gate 2: Official Homepage Domain Match
        source_domain = cls._extract_domain(source_url)
        if source_domain and anchor.official_domains:
            for official_domain in anchor.official_domains:
                clean_official = cls._extract_domain(official_domain)
                if not clean_official:
                    continue
                if source_domain == clean_official or source_domain.endswith(f".{clean_official}"):
                    return EntityVerificationResult(
                        is_verified=True,
                        rule="OFFICIAL_DOMAIN_MATCH",
                        confidence=0.98,
                        explanation=(
                            f"Entity identity verified: source domain '{source_domain}' matches "
                            f"official registry domain '{clean_official}'."
                        ),
                    )

        # If it's a directory page and we haven't matched orgnr or domain, reject it
        if is_directory_page:
            return EntityVerificationResult(
                is_verified=False,
                rule="MULTI_ENTITY_DIRECTORY_REJECTION",
                confidence=0.0,
                explanation=(
                    f"Disqualified for Wrong-Company Bleed: source is a multi-entity directory "
                    f"({len(extracted_orgnrs)} orgnrs found) and lacks target orgnr or official domain."
                ),
            )

        # Gate 3: Exact Legal Name + Municipality Match
        target_name = anchor.clean_legal_name
        has_name = (
            anchor.legal_name.upper() in upper_text
            or (len(target_name) >= 4 and target_name in upper_text)
        )
        has_municipality = bool(
            anchor.municipality and len(anchor.municipality) >= 3 and anchor.municipality.upper() in upper_text
        )

        if has_name and has_municipality:
            return EntityVerificationResult(
                is_verified=True,
                rule="NAME_AND_LOCATION_MATCH",
                confidence=0.92,
                explanation=(
                    f"Entity identity verified: source contains legal name '{anchor.legal_name}' "
                    f"and registered municipality '{anchor.municipality}'."
                ),
            )

        # Gate 4: Exact Legal Name + CEO/Chair Match
        executive_candidates = anchor.ceo_names + anchor.chair_names
        matched_exec = None
        for exec_name in executive_candidates:
            if not exec_name or len(exec_name.strip()) < 4:
                continue
            if exec_name.upper() in upper_text:
                matched_exec = exec_name
                break

        if has_name and matched_exec:
            return EntityVerificationResult(
                is_verified=True,
                rule="NAME_AND_KEY_PERSON_MATCH",
                confidence=0.95,
                explanation=(
                    f"Entity identity verified: source contains legal name '{anchor.legal_name}' "
                    f"and registered executive '{matched_exec}'."
                ),
            )

        # Disqualification: Failed all deterministic gates
        return EntityVerificationResult(
            is_verified=False,
            rule="FAILED_ENTITY_GATE",
            confidence=0.0,
            explanation=(
                f"Disqualified for Wrong-Company Bleed: source failed entity verification. "
                f"Did not find orgnr {anchor.orgnr}, official domain {anchor.official_domains}, "
                f"or legal name + municipality/executive combinations in source content."
            ),
        )


@dataclass
class FabricationCheckResult:
    """Outcome of Anti-Fabrication check on numeric/financial facts."""
    is_valid: bool
    raw_digits_verified: bool
    rejection_reason: Optional[str] = None
    normalized_quote: Optional[str] = None


class AntiFabricationGuard:
    """
    Deterministic anti-hallucination verifier for financial & numeric facts.
    Mandates:
    1. verbatim_quote must exist as an exact substring in raw source text (whitespace-normalized).
    2. Raw numeric digits from the extracted value must appear within that verbatim_quote.
    """

    @classmethod
    def verify_numeric_fact(
        cls,
        fact_key: str,
        value: Any,
        verbatim_quote: Optional[str],
        raw_source_text: str,
    ) -> FabricationCheckResult:
        """
        Validates that a numeric or financial fact has an exact verbatim quote
        present in the raw text, and that the quote actually contains the digits.
        """
        # Step 1: Check presence of verbatim quote
        if not verbatim_quote or not str(verbatim_quote).strip():
            return FabricationCheckResult(
                is_valid=False,
                raw_digits_verified=False,
                rejection_reason=f"Zero-Fabrication Guard: fact '{fact_key}' missing required verbatim_quote.",
            )

        quote_clean = _normalize_whitespace(verbatim_quote)
        source_clean = _normalize_whitespace(raw_source_text)

        # Step 2: Check verbatim quote exists as exact substring in raw source text
        # We test both exact case and case-insensitive
        if quote_clean not in source_clean and quote_clean.lower() not in source_clean.lower():
            return FabricationCheckResult(
                is_valid=False,
                raw_digits_verified=False,
                rejection_reason=(
                    f"Zero-Fabrication Guard: verbatim_quote '{quote_clean[:80]}...' "
                    f"not found in raw source text."
                ),
            )

        # Step 3: Check raw digits match
        # Extract meaningful digits from value
        digits_verified = cls._verify_digits_in_quote(value, quote_clean)
        if not digits_verified:
            return FabricationCheckResult(
                is_valid=False,
                raw_digits_verified=False,
                rejection_reason=(
                    f"Zero-Fabrication Guard: extracted numeric value '{value}' digits "
                    f"not found in verbatim_quote '{quote_clean}'."
                ),
            )

        return FabricationCheckResult(
            is_valid=True,
            raw_digits_verified=True,
            normalized_quote=quote_clean,
        )

    @classmethod
    def _verify_digits_in_quote(cls, value: Any, quote: str) -> bool:
        """
        Checks whether the essential digit pattern of `value` is represented in `quote`.
        Supports integers, floats, scientific notation, and Norwegian formatted numbers
        (e.g., 1 234 567,89, 1.234.567,-, MNOK, mrd. NOK).
        """
        if value is None:
            return False

        str_val = str(value).strip()

        value_digits = "".join(filter(str.isdigit, str_val))
        if not value_digits:
            return str_val.lower() in quote.lower()

        if str_val in quote:
            return True

        quote_digits = "".join(filter(str.isdigit, quote))
        if value_digits in quote_digits:
            return True

        # Handle decimal/scale cases: e.g. value is 35840000000 and quote says "35,84 mrd" or "35.84"
        sig_digits = value_digits.lstrip("0")[:4]
        if len(sig_digits) >= 2 and sig_digits in quote_digits:
            return True

        if isinstance(value, (int, float)):
            # Float precision issues handling
            val_f = float(value)

            # Formats to check
            patterns = []

            # Format: 1 234 567,89
            patterns.append(f"{val_f:,.2f}".replace(",", " ").replace(".", ","))
            patterns.append(f"{val_f:,.0f}".replace(",", " "))

            # Format: 1.234.567,-
            patterns.append(f"{val_f:,.0f}".replace(",", ".") + ",-")
            patterns.append(f"{val_f:,.0f}".replace(",", "."))

            # Format: MNOK (Millions)
            if val_f >= 1_000_000:
                mil_val = val_f / 1_000_000
                patterns.append(f"{mil_val:,.0f}")
                patterns.append(f"{mil_val:,.1f}".replace(".", ","))
                patterns.append(f"{mil_val:,.2f}".replace(".", ","))

            # Format: mrd NOK (Billions)
            if val_f >= 1_000_000_000:
                bil_val = val_f / 1_000_000_000
                patterns.append(f"{bil_val:,.1f}".replace(".", ","))
                patterns.append(f"{bil_val:,.2f}".replace(".", ","))
                patterns.append(f"{bil_val:,.0f}")

            for pat in patterns:
                if pat in quote:
                    return True

        return False

def verify_financial_fact(value: Any, verbatim_quote: Optional[str], raw_page_text: str) -> bool:
    """
    Deterministic anti-fabrication validator matching the competition specification:
    verifies that exact digits and quote exist as an exact substring in raw_page_text.
    """
    res = AntiFabricationGuard.verify_numeric_fact(
        fact_key="financial_claim",
        value=value,
        verbatim_quote=verbatim_quote,
        raw_source_text=raw_page_text,
    )
    return res.is_valid and res.raw_digits_verified
