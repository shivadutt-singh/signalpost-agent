"""
Unit Tests for Deterministic Fact Critic Pre-Commit Firewall (v6.0 Fail-Proof Edition).

Tests zero-hallucination guarantees: Substring Lock, Number Lock, and Anti-Bleed Lock.
"""

from __future__ import annotations

from src.domain.entities import CandidateFact, EntityAnchor, SourceTier
from src.domain.fact_critic import (
    verify_all_candidates,
    verify_anti_bleed_lock,
    verify_number_lock,
    verify_verbatim_quote_substring,
)


def test_substring_lock_validation() -> None:
    """Verifies verbatim quote must be a substring of raw source buffer."""
    source_url = "https://example.no/about"
    raw_buffer = "ACME AS was established in 1998 in Bergen and produces high-grade industrial valves."
    buffers = {source_url: raw_buffer}

    # Valid substring
    valid_quote = "ACME AS was established in 1998 in Bergen"
    assert verify_verbatim_quote_substring(
        valid_quote, source_url, buffers, SourceTier.OFFICIAL_DOMAIN
    ).is_ok

    # Valid with minor whitespace / newline variation
    varied_quote = "ACME AS  was established\nin 1998 in Bergen"
    assert verify_verbatim_quote_substring(
        varied_quote, source_url, buffers, SourceTier.OFFICIAL_DOMAIN
    ).is_ok

    # Hallucinated quote
    hallucinated_quote = "ACME AS is the largest exporter of timber to Sweden"
    res = verify_verbatim_quote_substring(
        hallucinated_quote, source_url, buffers, SourceTier.OFFICIAL_DOMAIN
    )
    assert res.is_err
    assert res.unwrap_err().code == "QUOTE_NOT_IN_SOURCE"

    # Missing buffer
    res_missing = verify_verbatim_quote_substring(
        valid_quote, "https://unknown.no", buffers, SourceTier.OFFICIAL_DOMAIN
    )
    assert res_missing.is_err
    assert res_missing.unwrap_err().code == "MISSING_SOURCE_BUFFER"


def test_number_lock_validation() -> None:
    """Verifies that numeric claims must have their digits inside the quote."""
    quote = "Revenue grew to 45 000 000 NOK in 2024 with 35 employees."

    # Admitted
    assert verify_number_lock(45_000_000, quote).is_ok
    assert verify_number_lock(35, quote).is_ok

    # Rejected numeric claim
    res = verify_number_lock(99_000_000, quote)
    assert res.is_err
    assert res.unwrap_err().code == "NUMBER_LOCK_FAILED"


def test_anti_bleed_lock_quarantines_poison_entities() -> None:
    """Verifies quarantine when poison manager appears without target corroboration."""
    anchor = EntityAnchor(
        orgnr="950049596",
        canonical_name="A/L F.L.S. BORETTSLAG",
        org_form="BRL",
        poison_entities=["USBL", "BOLIGBYGGELAGET USBL", "OBOS", "PWC"],
    )

    # Contaminated claim: talks about USBL without mentioning F.L.S. Borettslag
    bad_cand = CandidateFact(
        predicate="news.announcement",
        value="USBL launched a new digital resident portal for all its members.",
        confidence=0.9,
        source_tier=SourceTier.OFFICIAL_DOMAIN,
        source_url="https://usbl.no/nyheter",
        source_date="2026-03-01",
        verbatim_evidence="USBL launched a new digital resident portal for all its members.",
    )
    bad_res = verify_anti_bleed_lock(bad_cand, anchor)
    assert bad_res.is_err
    assert bad_res.unwrap_err().code == "NEGATIVE_ENTITY_CONTAMINATION_FAILED"

    # Permitted claim: explicitly names target entity
    good_cand = CandidateFact(
        predicate="governance.manager",
        value="A/L F.L.S. BORETTSLAG has engaged USBL as business manager.",
        confidence=0.9,
        source_tier=SourceTier.OFFICIAL_DOMAIN,
        source_url="https://usbl.no/fls",
        source_date="2026-03-01",
        verbatim_evidence="A/L F.L.S. BORETTSLAG has engaged USBL as business manager.",
    )
    assert verify_anti_bleed_lock(good_cand, anchor).is_ok


def test_verify_all_candidates_splits_admitted_and_quarantined() -> None:
    """Verifies batch pre-commit criticism splits facts accurately."""
    anchor = EntityAnchor(
        orgnr="923609016",
        canonical_name="EQUINOR ASA",
        org_form="ASA",
        poison_entities=["PWC"],
    )
    source_url = "https://equinor.com/about"
    raw_text = (
        "Equinor ASA operates offshore energy fields with 22 000 employees globally."
    )
    buffers = {source_url: raw_text}

    cand1 = CandidateFact(
        predicate="headcount",
        value=22000,
        confidence=1.0,
        source_tier=SourceTier.OFFICIAL_DOMAIN,
        source_url=source_url,
        source_date="2026-01-01",
        verbatim_evidence="offshore energy fields with 22 000 employees globally.",
    )
    cand2_hallucinated = CandidateFact(
        predicate="products",
        value="Electric Aircraft",
        confidence=0.9,
        source_tier=SourceTier.OFFICIAL_DOMAIN,
        source_url=source_url,
        source_date="2026-01-01",
        verbatim_evidence="Equinor produces electric supersonic aircraft for European airlines.",
    )

    admitted, quarantined = verify_all_candidates(
        [cand1, cand2_hallucinated], buffers, anchor
    )

    assert len(admitted) == 1
    assert admitted[0].predicate == "headcount"
    assert admitted[0].quarantine_reason is None

    assert len(quarantined) == 1
    assert quarantined[0].predicate == "products"
    assert "QUOTE_NOT_IN_SOURCE" in str(quarantined[0].quarantine_reason)
