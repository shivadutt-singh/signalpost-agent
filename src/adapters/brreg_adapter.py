"""
Brreg Enhetsregisteret Adapter for Signalpost Agent (v6.0 Fail-Proof Edition).

Fetches official government registry records, extracts positive internal governance
(CEO, Chair, Board Members) and isolates external poison service providers (USBL/OBOS/Auditors).
Implements RegistryPort.
"""

from __future__ import annotations

import logging
from typing import Any

from src.adapters.http_gateway import HttpGateway
from src.domain.entities import CandidateFact, EntityAnchor, SourceTier
from src.domain.entity_gate import classify_brreg_roller, is_shared_umbrella_domain
from src.domain.errors import DomainError
from src.domain.result import Err, Ok, Result
from src.ports.interfaces import RegistryPort

logger = logging.getLogger(__name__)

BRREG_ENHETER_URL = "https://data.brreg.no/enhetsregisteret/api/enheter"
BRREG_UNDERENHETER_URL = "https://data.brreg.no/enhetsregisteret/api/underenheter"


class BrregAdapter(RegistryPort):
    """Adapter for official Brreg Enhetsregisteret API."""

    def __init__(self, http_gateway: HttpGateway) -> None:
        self.http = http_gateway

    async def get_enhet(self, orgnr: str) -> Result[dict[str, Any] | None, DomainError]:
        """Fetches primary entity record from Brreg Enhetsregisteret."""
        url = f"{BRREG_ENHETER_URL}/{orgnr}"
        res = await self.http.get(url, orgnr=orgnr)

        if res.is_err:
            err = res.unwrap_err()
            return Err(err)

        resp = res.unwrap()
        if resp.status_code == 200:
            return Ok(resp.json())
        elif resp.status_code == 404:
            # Fallback to check if it is registered as an underenhet
            under_url = f"{BRREG_UNDERENHETER_URL}/{orgnr}"
            under_res = await self.http.get(under_url, orgnr=orgnr)
            if under_res.is_ok and under_res.unwrap().status_code == 200:
                return Ok(under_res.unwrap().json())
            return Ok(None)
        else:
            return Err(
                DomainError(
                    code="BRREG_API_ERROR",
                    message=f"Brreg Enhetsregisteret returned status {resp.status_code}",
                    details={"orgnr": orgnr, "status_code": resp.status_code},
                )
            )

    async def get_roller(self, orgnr: str) -> Result[list[dict[str, Any]], DomainError]:
        """Fetches governance roles from Brreg /roller endpoint."""
        url = f"{BRREG_ENHETER_URL}/{orgnr}/roller"
        res = await self.http.get(url, orgnr=orgnr)

        if res.is_err:
            return Ok([])  # Non-fatal if roles return error or 404

        resp = res.unwrap()
        if resp.status_code == 200:
            data = resp.json()
            # Brreg returns list of role groups in 'rollegrupper'
            rollegrupper = data.get("rollegrupper", [])
            flat_roles: list[dict[str, Any]] = []
            for grp in rollegrupper:
                roles = grp.get("roller", [])
                flat_roles.extend(roles)
            return Ok(flat_roles)
        return Ok([])

    async def get_underenheter(
        self, orgnr: str
    ) -> Result[list[dict[str, Any]], DomainError]:
        """Fetches operating subunits from Brreg."""
        url = f"{BRREG_UNDERENHETER_URL}?overordnetEnhet={orgnr}"
        res = await self.http.get(url, orgnr=orgnr)

        if res.is_err:
            return Ok([])

        resp = res.unwrap()
        if resp.status_code == 200:
            data = resp.json()
            embedded = data.get("_embedded", {})
            subunits = embedded.get("underenheter", [])
            return Ok(subunits)
        return Ok([])

    async def get_oppdateringer(
        self, orgnr: str
    ) -> Result[list[dict[str, Any]], DomainError]:
        """Fetches official registry updates changelog from Brreg oppdateringer API."""
        url = f"https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer={orgnr}&size=10"
        res = await self.http.get(url, orgnr=orgnr)

        if res.is_err:
            return Ok([])

        resp = res.unwrap()
        if resp.status_code == 200:
            try:
                data = resp.json()
                embedded = data.get("_embedded", {})
                updates = embedded.get("oppdaterteEnheter", [])
                return Ok(updates)
            except Exception:
                return Ok([])
        return Ok([])

    def build_anchor(
        self,
        enhet: dict[str, Any],
        roller: list[dict[str, Any]],
    ) -> EntityAnchor:
        """Constructs the canonical ground truth EntityAnchor."""
        orgnr = str(enhet.get("organisasjonsnummer", ""))
        canonical_name = enhet.get("navn", f"Entity {orgnr}")
        org_form = enhet.get("organisasjonsform", {}).get("kode", "ANNET")
        hjemmeside = enhet.get("hjemmeside")

        # Classify internal governance vs external poison roles
        internal_names, poison_entities, external_providers = classify_brreg_roller(
            roller
        )

        # Check shared umbrella domain
        is_umbrella = False
        if hjemmeside:
            is_umbrella = is_shared_umbrella_domain(hjemmeside, canonical_name)

        post_adr = enhet.get("postadresse", {}) or enhet.get("forretningsadresse", {})
        municipality = post_adr.get("kommune")
        postal_code = post_adr.get("postnummer")
        overordnet = enhet.get("overordnetEnhet")
        parent_orgnr = str(overordnet) if overordnet else None
        is_subsidiary = bool(parent_orgnr)

        return EntityAnchor(
            orgnr=orgnr,
            canonical_name=canonical_name,
            org_form=org_form,
            official_domain=hjemmeside,
            is_shared_umbrella_domain=is_umbrella,
            poison_entities=poison_entities,
            internal_governance_names=internal_names,
            external_service_providers=external_providers,
            address_municipality=municipality,
            address_postal_code=postal_code,
            parent_orgnr=parent_orgnr,
            is_subsidiary=is_subsidiary,
        )

    def extract_registry_candidate_facts(
        self,
        enhet: dict[str, Any],
        roller: list[dict[str, Any]],
        underenheter: list[dict[str, Any]],
        oppdateringer: list[dict[str, Any]] | None = None,
    ) -> list[CandidateFact]:
        """Extracts deterministic candidate facts from official registry data."""
        orgnr = str(enhet.get("organisasjonsnummer", ""))
        source_url = f"{BRREG_ENHETER_URL}/{orgnr}"
        source_date = enhet.get("registreringsdatoEnhetsregisteret", "2026-01-01")
        facts: list[CandidateFact] = []

        # Canonical Name
        name = enhet.get("navn")
        if name:
            facts.append(
                CandidateFact(
                    predicate="canonical_name",
                    value=name,
                    confidence=1.0,
                    source_tier=SourceTier.PRIMARY_GOVERNMENT,
                    source_url=source_url,
                    source_date=source_date,
                    verbatim_evidence=f"Official Brreg registration name: {name}",
                )
            )

        # Organization Form
        org_form = enhet.get("organisasjonsform", {}).get("kode")
        if org_form:
            facts.append(
                CandidateFact(
                    predicate="legal_form",
                    value=org_form,
                    confidence=1.0,
                    source_tier=SourceTier.PRIMARY_GOVERNMENT,
                    source_url=source_url,
                    source_date=source_date,
                    verbatim_evidence=f"Organisasjonsform kode: {org_form}",
                )
            )

        # Address & Municipality
        forr_adr = enhet.get("forretningsadresse") or enhet.get("postadresse") or {}
        kommune = forr_adr.get("kommune")
        if kommune:
            facts.append(
                CandidateFact(
                    predicate="municipality",
                    value=kommune,
                    confidence=1.0,
                    source_tier=SourceTier.PRIMARY_GOVERNMENT,
                    source_url=source_url,
                    source_date=source_date,
                    verbatim_evidence=f"Forretningsadresse kommune: {kommune}",
                )
            )

        # Governance: CEO (DAGL), Chair (LEDE), Board Members (MEDL)
        medl_idx = 0
        for r in roller:
            type_obj = r.get("type", {})
            code = (
                type_obj.get("kode", "")
                if isinstance(type_obj, dict)
                else str(type_obj)
            )
            person = r.get("person") or {}
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

            if not p_name:
                continue

            if code == "DAGL":
                facts.append(
                    CandidateFact(
                        predicate="governance.ceo",
                        value=p_name,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=f"{source_url}/roller",
                        source_date=source_date,
                        verbatim_evidence=f"Daglig leder (DAGL): {p_name}",
                    )
                )
            elif code == "LEDE":
                facts.append(
                    CandidateFact(
                        predicate="governance.board_chair",
                        value=p_name,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=f"{source_url}/roller",
                        source_date=source_date,
                        verbatim_evidence=f"Styrets leder (LEDE): {p_name}",
                    )
                )
            elif code == "MEDL":
                medl_idx += 1
                facts.append(
                    CandidateFact(
                        predicate=f"governance.board_member.{medl_idx}",
                        value=p_name,
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=f"{source_url}/roller",
                        source_date=source_date,
                        verbatim_evidence=f"Styremedlem (MEDL): {p_name}",
                    )
                )

        # Subunit branch footprint
        if underenheter:
            facts.append(
                CandidateFact(
                    predicate="geographic_footprint.subunit_count",
                    value=len(underenheter),
                    confidence=1.0,
                    source_tier=SourceTier.PRIMARY_GOVERNMENT,
                    source_url=f"{BRREG_UNDERENHETER_URL}?overordnetEnhet={orgnr}",
                    source_date=source_date,
                    verbatim_evidence=f"Registered operating subunits: {len(underenheter)}",
                )
            )

        # Official Brreg Oppdateringer events changelog
        if oppdateringer:
            for idx, upd in enumerate(oppdateringer[:5], start=1):
                upd_id = upd.get("oppdateringsid", "")
                upd_type = upd.get("endringstype", "ENDRING")
                upd_date = upd.get("dato", source_date)
                date_str = str(upd_date)[:10] if upd_date else source_date
                facts.append(
                    CandidateFact(
                        predicate=f"public_activity.registration_update.{idx}",
                        value=f"{upd_type} (Update ID: {upd_id})",
                        confidence=1.0,
                        source_tier=SourceTier.PRIMARY_GOVERNMENT,
                        source_url=f"https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer={orgnr}",
                        source_date=date_str,
                        verbatim_evidence=f"Official Brreg registration update: id={upd_id}, type={upd_type}, date={upd_date}",
                    )
                )

        return facts
