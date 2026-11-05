"""
Unit Tests for DOM-Subtree & Poison-Entity Gate (v6.0 Fail-Proof Edition).

Tests Soham's #1 evaluation requirement:
Strict isolation of external managers (USBL, OBOS, NBBO) and auditors from housing associations.
"""

from __future__ import annotations

from src.domain.entity_gate import (
    classify_brreg_roller,
    is_shared_umbrella_domain,
    split_dom_into_blocks,
    verify_dom_block_ownership,
)


def test_shared_umbrella_domain_detection() -> None:
    """Verifies that shared property manager domains are detected for housing associations."""
    # Borettslag with USBL homepage
    assert is_shared_umbrella_domain(
        "https://usbl.no/borettslag/fls", "A/L F.L.S. BORETTSLAG"
    )
    assert is_shared_umbrella_domain("obos.no", "AKERSBAKKEN BORETTSLAG")
    assert is_shared_umbrella_domain(
        "https://www.nbbo.no/sameier/12", "STRANDVEIEN SAMEIE"
    )

    # Independent corporate domains are NOT umbrellas
    assert not is_shared_umbrella_domain("equinor.com", "EQUINOR ASA")
    assert not is_shared_umbrella_domain("dnb.no", "DNB BANK ASA")
    assert not is_shared_umbrella_domain("telenor.no", "TELENOR ASA")

    # USBL itself owning usbl.no is NOT considered an external umbrella
    assert not is_shared_umbrella_domain("usbl.no", "BOLIGBYGGELAGET USBL")
    assert not is_shared_umbrella_domain("obos.no", "OBOS BBL")


def test_brreg_roller_classification() -> None:
    """Verifies classification of internal officers vs external poison service providers."""
    mock_roller = [
        {
            "type": {"kode": "DAGL"},
            "person": {"navn": {"fornavn": "Anders", "etternavn": "Opedal"}},
        },
        {
            "type": {"kode": "LEDE"},
            "person": {"navn": {"fornavn": "Jon", "etternavn": "Reinertsen"}},
        },
        {
            "type": {"kode": "MEDL"},
            "person": {"navn": {"fornavn": "Kari", "etternavn": "Nordmann"}},
        },
        {"type": {"kode": "FFØR"}, "enhet": {"navn": "BOLIGBYGGELAGET USBL"}},
        {"type": {"kode": "REVI"}, "enhet": {"navn": "PRICEWATERHOUSECOOPERS AS"}},
        {"type": {"kode": "REGN"}, "enhet": {"navn": "AZETS INSIGHT AS"}},
    ]

    internal, poison, external = classify_brreg_roller(mock_roller)

    assert "Anders Opedal" in internal
    assert "Jon Reinertsen" in internal
    assert "Kari Nordmann" in internal
    assert "BOLIGBYGGELAGET USBL" in external
    assert "PRICEWATERHOUSECOOPERS AS" in external
    assert "AZETS INSIGHT AS" in external

    # Poison list must include managers and auditors
    assert any("USBL" in p for p in poison)
    assert any("PRICEWATERHOUSECOOPERS" in p or "PWC" in p for p in poison)


def test_dom_block_splitting_and_ownership() -> None:
    """Tests isolating individual DOM blocks and verifying block-level ownership."""
    html_page = """
    <html>
        <body>
            <header>Welcome to USBL Portal</header>
            <article id="news-1">
                <h2>USBL arranges autumn cleaning across all regional associations</h2>
                <p>USBL is proud to announce new recycling bins across 500 managed properties in Oslo.</p>
            </article>
            <article id="borettslag-specific">
                <h2>A/L F.L.S. BORETTSLAG (950049596) Annual Meeting</h2>
                <p>A/L F.L.S. BORETTSLAG will hold its annual general assembly on May 15th.</p>
            </article>
            <footer>Managed by Boligbyggelaget USBL</footer>
        </body>
    </html>
    """
    blocks = split_dom_into_blocks(html_page)
    target_orgnr = "950049596"
    canonical_name = "A/L F.L.S. BORETTSLAG"
    poison_entities = ["USBL", "BOLIGBYGGELAGET USBL", "OBOS"]

    admitted_count = 0
    rejected_count = 0

    for b in blocks:
        res = verify_dom_block_ownership(
            b, target_orgnr, canonical_name, poison_entities
        )
        if res.is_ok:
            admitted_count += 1
            # Admitted block MUST contain target name or orgnr
            assert (target_orgnr in b) or (canonical_name in b.upper())
        else:
            rejected_count += 1

    # Specific borettslag article is admitted
    assert admitted_count >= 1
    # Generic USBL article and header are rejected
    assert rejected_count >= 1
