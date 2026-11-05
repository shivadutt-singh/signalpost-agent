"""
DOM-Subtree & Poison-Entity Gate for Signalpost Agent (v6.0 Fail-Proof Edition).

Solves Builderr Organizer Mandate #1:
Strict isolation of external property managers (USBL, OBOS, NBBO), auditors (PwC, EY),
and accountants (Azets) from housing associations (Borettslag/Sameie) and client companies.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.domain.errors import PoisonEntityBleedError
from src.domain.result import Err, Ok, Result

# Governance roles permitted to represent the target entity
INTERNAL_GOVERNANCE_ROLES: frozenset[str] = frozenset(
    {
        "DAGL",  # Daglig leder / CEO
        "LEDE",  # Styreleder / Board Chair
        "NEST",  # Nestleder / Deputy Chair
        "MEDL",  # Styremedlem / Board Member
        "VARA",  # Varamedlem / Alternate Board Member
        "INNH",  # Innehaver / Proprietor
        "DTPR",  # Deltaker med proratarisk ansvar
        "DTSO",  # Deltaker med solidarisk ansvar
    }
)

# External service providers that MUST NEVER bleed publications or jobs into client
EXTERNAL_POISON_ROLES: frozenset[str] = frozenset(
    {
        "FFØR",  # Forretningsfører (e.g. USBL, OBOS, BORI, TOBB)
        "REVI",  # Revisor (e.g. PwC, EY, KPMG, BDO)
        "REGN",  # Regnskapsfører (e.g. Azets, Accountor, SpareBank 1 Regnskap)
        "KONT",  # Kontaktperson / External contact
    }
)

# Umbrella portals and shared property manager domains
SHARED_UMBRELLA_DOMAINS: frozenset[str] = frozenset(
    {
        "usbl.no",
        "obos.no",
        "nbbo.no",
        "bate.no",
        "tobb.no",
        "bob.no",
        "vestbo.no",
        "bonord.no",
        "boalliansen.no",
        "garanti.no",
        "proff.no",
        "regnskapstall.no",
        "purehelp.no",
        "1881.no",
        "gulesider.no",
        "finn.no",
        "linkedin.com",
        "facebook.com",
    }
)

# Canonical known poison entity names
KNOWN_POISON_ENTITIES: frozenset[str] = frozenset(
    {
        "USBL",
        "BOLIGBYGGELAGET USBL",
        "OBOS",
        "OBOS EIENDOMSFORVALTNING",
        "NBBO",
        "TOBB",
        "BATE",
        "BOB",
        "BORI",
        "VESTBO",
        "BONORD",
        "PWC",
        "PRICEWATERHOUSECOOPERS",
        "ERNST & YOUNG",
        "EY",
        "KPMG",
        "BDO",
        "AZETS",
        "ACCOUNTOR",
    }
)


def extract_domain_host(url_or_domain: str) -> str:
    """Extracts lowercase hostname without www or port."""
    if not url_or_domain:
        return ""
    cand = url_or_domain.strip().lower()
    if not cand.startswith("http://") and not cand.startswith("https://"):
        cand = "https://" + cand
    try:
        parsed = urlparse(cand)
        host = parsed.netloc or parsed.path
        host = host.split(":")[0]
        host = host.removeprefix("www.")
        return host
    except Exception:
        return ""


def is_shared_umbrella_domain(domain_or_url: str, canonical_name: str) -> bool:
    """
    Returns True if the domain is a known shared umbrella/portal and NOT
    the dedicated domain of the target entity itself.
    """
    host = extract_domain_host(domain_or_url)
    if not host:
        return False

    name_clean = canonical_name.lower().replace(" ", "").replace("-", "")

    for umbrella in SHARED_UMBRELLA_DOMAINS:
        if host == umbrella or host.endswith("." + umbrella):
            umbrella_root = umbrella.split(".")[0]
            # If the company's own legal name is the manager (e.g. "USBL AS"), it is not shared
            return umbrella_root not in name_clean

    return False


def classify_brreg_roller(
    roller_data: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """
    Classifies roles from Brreg /enheter/{orgnr}/roller:
    Returns (internal_names, poison_entities, external_provider_names).
    """
    internal_names: list[str] = []
    poison_entities: set[str] = set()
    external_provider_names: list[str] = []

    for item in roller_data:
        type_obj = item.get("type", {})
        role_code = (
            type_obj.get("kode", "") if isinstance(type_obj, dict) else str(type_obj)
        )
        person = item.get("person") or {}
        enhet = item.get("enhet") or {}

        # Person name
        p_name = ""
        if isinstance(person, dict) and person.get("navn"):
            n = person["navn"]
            if isinstance(n, dict):
                parts = [
                    n.get("fornavn", ""),
                    n.get("mellomnavn", ""),
                    n.get("etternavn", ""),
                ]
                p_name = " ".join(p for p in parts if p).strip()
            elif isinstance(n, str):
                p_name = n.strip()

        # Entity name (e.g. Forretningsfører USBL or Auditor PwC)
        e_name = ""
        if isinstance(enhet, dict) and enhet.get("navn"):
            e_name = enhet["navn"].strip()

        rep_name = p_name or e_name
        if not rep_name:
            continue

        if role_code in INTERNAL_GOVERNANCE_ROLES:
            if rep_name not in internal_names:
                internal_names.append(rep_name)
        elif role_code in EXTERNAL_POISON_ROLES:
            if rep_name not in external_provider_names:
                external_provider_names.append(rep_name)
            # Add to poison entities
            poison_entities.add(rep_name.upper())

    # Ensure standard poison entities are also present
    combined_poison = list(poison_entities | KNOWN_POISON_ENTITIES)
    return internal_names, combined_poison, external_provider_names


def split_dom_into_blocks(html_or_text: str) -> list[str]:
    """
    Deconstructs HTML or Markdown into semantic blocks:
    <article>, <section>, <li>, <tr>, <div class="card">, or double-newline paragraphs.
    """
    if "<" in html_or_text and ">" in html_or_text:
        try:
            soup = BeautifulSoup(html_or_text, "html.parser")
            # Remove scripts and styles
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()

            blocks: list[str] = []
            semantic_tags = soup.find_all(
                ["article", "section", "li", "tr", "div", "p"]
            )
            for tag in semantic_tags:
                txt = tag.get_text(" ", strip=True)
                if len(txt) >= 20 and txt not in blocks:
                    blocks.append(txt)

            if blocks:
                return blocks
        except Exception:
            pass

    # Fallback to double newline paragraphs
    paras = [
        p.strip() for p in re.split(r"\n\s*\n", html_or_text) if len(p.strip()) >= 20
    ]
    return paras or [html_or_text.strip()]


def verify_dom_block_ownership(
    block_text: str,
    target_orgnr: str,
    canonical_name: str,
    poison_entities: list[str],
    parent_orgnr: str | None = None,
    group_name: str | None = None,
) -> Result[bool, PoisonEntityBleedError]:
    """
    DOM-Block Level Verification (v7.0 Red-Team Edition):
    (Condition 1) Block or parent must match target_orgnr or full canonical_name.
    (Condition 2) Block must NOT exclusively refer to a poison entity (USBL, OBOS, PwC)
                  without target entity presence.
    (Condition 3) If parent_orgnr/group_name is present (Konsern / Corporate Group),
                  allow group-level evidence provided target or corporate group context is confirmed.
    """
    normalized_block = " " + re.sub(r"\s+", " ", block_text.upper()) + " "
    norm_orgnr = target_orgnr.strip()
    norm_name = " " + canonical_name.strip().upper() + " "

    # Check Condition 1: Target presence
    has_orgnr = norm_orgnr in normalized_block
    has_name = norm_name in normalized_block
    has_parent_orgnr = bool(parent_orgnr and parent_orgnr.strip() in normalized_block)
    has_group = bool(group_name and group_name.strip().upper() in normalized_block)

    # If neither target orgnr nor target canonical name nor confirmed corporate group is mentioned, reject block
    if not has_orgnr and not has_name and not (has_parent_orgnr or has_group):
        return Err(
            PoisonEntityBleedError(
                code="TARGET_ABSENT_IN_BLOCK",
                message=f"Block does not name target company '{canonical_name}' or orgnr {target_orgnr}",
                details={"block_preview": block_text[:120]},
            )
        )

    # Check Condition 2: Poison entity isolation (USBL / OBOS / PwC / Azets)
    detected_poison: list[str] = []
    for poison in poison_entities:
        poison_upper = poison.upper().strip()
        if len(poison_upper) < 3:
            continue
        # Use regex word boundary to avoid false substring matches
        pattern = r"\b" + re.escape(poison_upper) + r"\b"
        if re.search(pattern, normalized_block):
            detected_poison.append(poison)

    if detected_poison:
        # If target orgnr is present, ownership is verified
        if has_orgnr:
            return Ok(True)

        # If canonical name is present in the block, verify it's corroborated
        if has_name:
            return Ok(True)

        # External manager cannot be bypassed by group rules
        return Err(
            PoisonEntityBleedError(
                code="POISON_MANAGER_BLEED",
                message=f"Block contaminated by external manager {detected_poison} without target presence",
                details={"detected_poison": detected_poison, "target": canonical_name},
            )
        )

    return Ok(True)
