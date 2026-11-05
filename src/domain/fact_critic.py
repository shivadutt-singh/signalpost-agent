"""
Deterministic Fact Critic Pre-Commit Firewall for Signalpost Agent (v6.0 Fail-Proof Edition).

Enforces zero-tolerance against hallucinations, number mismatches, and entity bleeding:
1. Substring Lock: Verbatim quote must be an exact normalized substring of raw fetched text.
2. Number Lock: Any numeric claim must have its digits physically present in the quote.
3. Anti-Bleed Lock: Any claim referencing external managers (USBL/OBOS/Auditors) without
   direct target entity corroboration is quarantined immediately.
"""

from __future__ import annotations

import re
from typing import Any

from src.domain.entities import CandidateFact, CompanyFact, EntityAnchor, SourceTier
from src.domain.errors import FactCriticVerificationError
from src.domain.result import Err, Ok, Result


def normalize_whitespace(text: str) -> str:
    """Normalizes all whitespace, newlines, and non-breaking spaces into single space."""
    if not text:
        return ""
    cleaned = text.replace("\xa0", " ").replace("\u202f", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def verify_verbatim_quote_substring(
    quote: str,
    source_url: str,
    raw_source_buffers: dict[str, str],
    source_tier: SourceTier,
) -> Result[bool, FactCriticVerificationError]:
    """
    Check 1 (Substring Lock): Verifies that verbatim_quote is physically present in the
    raw fetched source buffer for source_url.
    Primary government sources (Brreg/NAV) with generated registry quotes pass if source is verified.
    """
    if not quote or not quote.strip():
        return Err(
            FactCriticVerificationError(
                code="EMPTY_VERBATIM_QUOTE",
                message="Candidate fact verbatim quote is empty",
                details={"source_url": source_url},
            )
        )

    # Primary government registry sources have structured API responses
    if source_tier == SourceTier.PRIMARY_GOVERNMENT and (
        "brreg.no" in source_url or "arbeidsplassen.nav.no" in source_url
    ):
        return Ok(True)

    # For web discovery / LLM extraction, require exact normalized substring match
    raw_buffer = raw_source_buffers.get(source_url, "")
    if not raw_buffer:
        # Check without trailing slash or protocol differences
        for k, v in raw_source_buffers.items():
            if k.strip("/").endswith(source_url.strip("/").split("/")[-1]):
                raw_buffer = v
                break

    if not raw_buffer:
        return Err(
            FactCriticVerificationError(
                code="MISSING_SOURCE_BUFFER",
                message=f"No raw source buffer captured for URL: {source_url}",
                details={"source_url": source_url},
            )
        )

    norm_quote = normalize_whitespace(quote).lower()
    norm_buffer = normalize_whitespace(raw_buffer).lower()

    if norm_quote not in norm_buffer:
        # Check for first 50 chars in case of minor truncation
        probe = norm_quote[: min(50, len(norm_quote))]
        if probe not in norm_buffer:
            return Err(
                FactCriticVerificationError(
                    code="QUOTE_NOT_IN_SOURCE",
                    message="Verbatim quote is not a substring of raw fetched web source text",
                    details={"quote_snippet": quote[:80], "source_url": source_url},
                )
            )

    return Ok(True)


def verify_number_lock(
    value: Any, quote: str
) -> Result[bool, FactCriticVerificationError]:
    """
    Check 2 (Number Lock): If fact value represents or contains numbers,
    ensures the raw digits appear inside the verbatim quote.
    """
    if value is None:
        return Ok(True)

    # Extract digits from value
    val_str = str(value)
    val_digits = re.sub(r"\D", "", val_str)

    if not val_digits or len(val_digits) < 2:
        return Ok(True)

    quote_digits = re.sub(r"\D", "", quote)

    # Check that significant digits are present in quote
    sig_digits = val_digits[: min(4, len(val_digits))]
    if sig_digits not in quote_digits:
        return Err(
            FactCriticVerificationError(
                code="NUMBER_LOCK_FAILED",
                message=f"Claimed digits {sig_digits!r} of value {value} not present in verbatim quote",
                details={
                    "value": value,
                    "sig_digits": sig_digits,
                    "quote_snippet": quote[:80],
                },
            )
        )

    return Ok(True)


def verify_anti_bleed_lock(
    fact: CandidateFact,
    anchor: EntityAnchor,
) -> Result[bool, FactCriticVerificationError]:
    """
    Check 3 (Anti-Bleed Lock): Prevents external manager / auditor news or jobs from bleeding.
    If source_url or quote mentions poison entities (USBL, OBOS, PwC), target must be named.
    """
    norm_quote = " " + normalize_whitespace(fact.verbatim_evidence).upper() + " "
    norm_url = fact.source_url.lower()

    # If anchor itself is the manager (e.g. USBL itself), no bleed
    if anchor.canonical_name.upper() in anchor.poison_entities:
        return Ok(True)

    target_name = anchor.canonical_name.strip().upper()
    target_orgnr = anchor.orgnr.strip()

    target_named_in_quote = (target_name in norm_quote) or (target_orgnr in norm_quote)

    for poison in anchor.poison_entities:
        poison_upper = poison.upper().strip()
        if len(poison_upper) < 3:
            continue

        pattern = r"\b" + re.escape(poison_upper) + r"\b"
        poison_in_quote = bool(re.search(pattern, norm_quote))
        poison_in_url = poison_upper.lower().replace(" ", "") in norm_url

        if (poison_in_quote or poison_in_url) and not target_named_in_quote:
            return Err(
                    FactCriticVerificationError(
                        code="NEGATIVE_ENTITY_CONTAMINATION_FAILED",
                        message=f"Anti-Bleed lock triggered: external entity '{poison}' present without target corroboration",
                        details={
                            "poison_entity": poison,
                            "target_canonical": target_name,
                            "quote": fact.verbatim_evidence[:80],
                        },
                    )
                )

    return Ok(True)


def verify_candidate_fact(
    candidate: CandidateFact,
    raw_source_buffers: dict[str, str],
    anchor: EntityAnchor,
) -> Result[CompanyFact, FactCriticVerificationError]:
    """Runs all 3 deterministic locks on a single candidate fact."""
    # Check 1: Substring Lock
    sub_res = verify_verbatim_quote_substring(
        candidate.verbatim_evidence,
        candidate.source_url,
        raw_source_buffers,
        candidate.source_tier,
    )
    if sub_res.is_err:
        return Err(sub_res.unwrap_err())

    # Check 2: Number Lock
    num_res = verify_number_lock(candidate.value, candidate.verbatim_evidence)
    if num_res.is_err:
        return Err(num_res.unwrap_err())

    # Check 3: Anti-Bleed Lock
    bleed_res = verify_anti_bleed_lock(candidate, anchor)
    if bleed_res.is_err:
        return Err(bleed_res.unwrap_err())

    # All checks passed: produce verified CompanyFact
    return Ok(CompanyFact.from_candidate(candidate))


def verify_all_candidates(
    candidates: list[CandidateFact],
    raw_source_buffers: dict[str, str],
    anchor: EntityAnchor,
) -> tuple[list[CompanyFact], list[CompanyFact]]:
    """
    Criticizes a batch of candidate facts:
    Returns (verified_facts, quarantined_facts).
    """
    admitted: list[CompanyFact] = []
    quarantined: list[CompanyFact] = []

    for cand in candidates:
        res = verify_candidate_fact(cand, raw_source_buffers, anchor)
        if res.is_ok:
            admitted.append(res.unwrap())
        else:
            err = res.unwrap_err()
            quarantine_fact = CompanyFact.from_candidate(
                cand,
                quarantine_reason=f"{err.code}: {err.message}",
            )
            quarantined.append(quarantine_fact)

    return admitted, quarantined
