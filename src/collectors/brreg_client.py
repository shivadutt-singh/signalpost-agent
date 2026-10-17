"""
Official Brønnøysund Register Centre API Client (data.brreg.no).
Fetches authoritative enterprise data, governance roles, subunits, and update logs.
All facts derived here are classified as Tier 1 (Official Registry Ground Truth).
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import httpx

from src.config import settings
from src.collectors.http_budget import BudgetedAsyncClient
from src.models.schema import ExtractedFactCandidate, FactCategory, FactEvidence, SourceTier
from src.validators.fact_guard import EntityAnchor
from src.validators.mod11 import normalize_orgnr


class BrregClient:
    """
    Client for interacting with the Norwegian Enhetsregisteret API.
    """

    def __init__(self, client: BudgetedAsyncClient):
        self.client = client
        self.base_url = settings.BRREG_BASE_URL

    async def get_enhet(self, orgnr: str) -> Optional[Dict[str, Any]]:
        """
        Fetches the primary entity record from /enhetsregisteret/api/enheter/{orgnr}.
        """
        clean_orgnr = normalize_orgnr(orgnr)
        url = f"{self.base_url}/enheter/{clean_orgnr}"
        try:
            resp = await self.client.get(url, orgnr=clean_orgnr)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                return None
            else:
                resp.raise_for_status()
                return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_roller(self, orgnr: str) -> Optional[Dict[str, Any]]:
        """
        Fetches governance roles (CEO, board of directors, auditor)
        from /enhetsregisteret/api/enheter/{orgnr}/roller.
        """
        clean_orgnr = normalize_orgnr(orgnr)
        url = f"{self.base_url}/enheter/{clean_orgnr}/roller"
        try:
            resp = await self.client.get(url, orgnr=clean_orgnr)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                return None
            return None
        except Exception:
            return None

    async def get_underenheter(self, orgnr: str, size: int = 10) -> List[Dict[str, Any]]:
        """
        Fetches subunits (branches, secondary operating establishments)
        from /enhetsregisteret/api/underenheter?overordnetEnhet={orgnr}.
        """
        clean_orgnr = normalize_orgnr(orgnr)
        url = f"{self.base_url}/underenheter?overordnetEnhet={clean_orgnr}&size={size}"
        try:
            resp = await self.client.get(url, orgnr=clean_orgnr)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("_embedded", {}).get("underenheter", [])
            return []
        except Exception:
            return []

    async def fetch_active_orgnrs(self, limit: int = 1000) -> List[str]:
        """
        Fetches a list of active Norwegian enterprise numbers for batch seeding.
        Iterates over the enheter endpoint with konkurs=false.
        """
        collected: List[str] = []
        page = 0
        page_size = min(100, limit)

        while len(collected) < limit:
            url = f"{self.base_url}/enheter?konkurs=false&page={page}&size={page_size}"
            resp = await self.client.get(url, orgnr=None)
            if resp.status_code != 200:
                break
            data = resp.json()
            entities = data.get("_embedded", {}).get("enheter", [])
            if not entities:
                break

            for entity in entities:
                orgnr = entity.get("organisasjonsnummer")
                if orgnr:
                    collected.append(orgnr)
                    if len(collected) >= limit:
                        break

            page += 1

        return collected

    def build_entity_anchor(
        self,
        enhet_data: Dict[str, Any],
        roller_data: Optional[Dict[str, Any]] = None,
    ) -> EntityAnchor:
        """
        Constructs an authoritative EntityAnchor for downstream Entity Verification Gate.
        """
        orgnr = enhet_data["organisasjonsnummer"]
        legal_name = enhet_data.get("navn", "")

        # Municipality
        municipality = None
        forr = enhet_data.get("forretningsadresse") or enhet_data.get("postadresse") or {}
        if forr.get("kommune"):
            municipality = forr["kommune"]

        # Official domains from Brreg hjemmeside
        official_domains = []
        hjemmeside = enhet_data.get("hjemmeside")
        if hjemmeside:
            # Clean protocol or leading slashes
            clean_dom = hjemmeside.strip().lower()
            clean_dom = clean_dom.replace("http://", "").replace("https://", "").split("/")[0]
            if clean_dom.startswith("www."):
                clean_dom = clean_dom[4:]
            if clean_dom:
                official_domains.append(clean_dom)

        # Parse CEO and Chair names from roller
        ceo_names: List[str] = []
        chair_names: List[str] = []

        if roller_data and "rollegrupper" in roller_data:
            for group in roller_data["rollegrupper"]:
                group_type = group.get("type", {}).get("kode")
                for role in group.get("roller", []):
                    role_type = role.get("type", {}).get("kode")
                    person = role.get("person", {})
                    name_dict = person.get("navn", {})
                    first = name_dict.get("fornavn", "")
                    last = name_dict.get("etternavn", "")
                    full_name = f"{first} {last}".strip()

                    if not full_name:
                        continue

                    if role_type == "DAGL" or group_type == "DAGL":
                        ceo_names.append(full_name)
                    elif role_type == "LEDE":
                        chair_names.append(full_name)

        return EntityAnchor(
            orgnr=orgnr,
            legal_name=legal_name,
            municipality=municipality,
            ceo_names=ceo_names,
            chair_names=chair_names,
            official_domains=official_domains,
        )

    def extract_registry_facts(
        self,
        enhet_data: Dict[str, Any],
        roller_data: Optional[Dict[str, Any]] = None,
        underenheter_data: Optional[List[Dict[str, Any]]] = None,
    ) -> List[ExtractedFactCandidate]:
        """
        Transforms raw Brreg API payloads into normalized ExtractedFactCandidate models.
        """
        orgnr = enhet_data["organisasjonsnummer"]
        source_url = f"{self.base_url}/enheter/{orgnr}"
        now_iso = datetime.now(timezone.utc).isoformat()
        facts: List[ExtractedFactCandidate] = []

        def _make_evidence(quote: Optional[str] = None, explanation: str = "") -> FactEvidence:
            return FactEvidence(
                source_url=source_url,
                source_tier=SourceTier.OFFICIAL_REGISTRY,
                source_date=enhet_data.get("registreringsdatoEnhetsregisteret"),
                retrieval_timestamp=now_iso,
                verbatim_quote=quote,
                entity_match_rule="OFFICIAL_REGISTRY_AUTHORITY",
                entity_confidence=1.0,
                raw_digits_verified=True,
                verification_explanation=explanation or (
                    f"Directly verified via official Norwegian Enhetsregisteret API for {orgnr}."
                ),
            )

        # 1. Legal Name
        if "navn" in enhet_data:
            facts.append(
                ExtractedFactCandidate(
                    fact_key="registry.legal_name",
                    value=enhet_data["navn"],
                    category=FactCategory.REGISTRY_CORE,
                    evidence=_make_evidence(
                        explanation=f"Official registered enterprise name '{enhet_data['navn']}'."
                    ),
                )
            )

        # 2. Organization Form
        if "organisasjonsform" in enhet_data:
            orgform = enhet_data["organisasjonsform"]
            facts.append(
                ExtractedFactCandidate(
                    fact_key="registry.organization_form",
                    value={
                        "kode": orgform.get("kode"),
                        "beskrivelse": orgform.get("beskrivelse"),
                    },
                    category=FactCategory.REGISTRY_CORE,
                    evidence=_make_evidence(
                        explanation=f"Official corporate form {orgform.get('kode')} ({orgform.get('beskrivelse')})."
                    ),
                )
            )

        # 3. Registration Date
        if "registreringsdatoEnhetsregisteret" in enhet_data:
            facts.append(
                ExtractedFactCandidate(
                    fact_key="registry.registration_date",
                    value=enhet_data["registreringsdatoEnhetsregisteret"],
                    category=FactCategory.REGISTRY_CORE,
                    evidence=_make_evidence(
                        explanation="Official registration date in Enhetsregisteret."
                    ),
                )
            )

        # 4. Founding Date
        if "stiftelsesdato" in enhet_data:
            facts.append(
                ExtractedFactCandidate(
                    fact_key="registry.founding_date",
                    value=enhet_data["stiftelsesdato"],
                    category=FactCategory.REGISTRY_CORE,
                    evidence=_make_evidence(
                        explanation="Official founding date (stiftelsesdato) recorded in registry."
                    ),
                )
            )

        # 5. Business Address
        if "forretningsadresse" in enhet_data:
            addr = enhet_data["forretningsadresse"]
            facts.append(
                ExtractedFactCandidate(
                    fact_key="contact.business_address",
                    value={
                        "street": " ".join(addr.get("adresse", [])),
                        "postal_code": addr.get("postnummer"),
                        "city": addr.get("poststed"),
                        "municipality": addr.get("kommune"),
                        "municipality_number": addr.get("kommunenummer"),
                        "country": addr.get("land", "Norge"),
                    },
                    category=FactCategory.CONTACT_AND_WEB,
                    evidence=_make_evidence(
                        explanation=f"Official registered business address in {addr.get('poststed', 'Norway')}."
                    ),
                )
            )

        # 6. Official Homepage
        if enhet_data.get("hjemmeside"):
            facts.append(
                ExtractedFactCandidate(
                    fact_key="contact.website",
                    value=enhet_data["hjemmeside"],
                    category=FactCategory.CONTACT_AND_WEB,
                    evidence=_make_evidence(
                        explanation=f"Official primary homepage domain declared to registry: {enhet_data['hjemmeside']}."
                    ),
                )
            )

        # 7. Industry NACE code
        if "naeringskode1" in enhet_data:
            nace = enhet_data["naeringskode1"]
            facts.append(
                ExtractedFactCandidate(
                    fact_key="operations.primary_nace",
                    value={
                        "kode": nace.get("kode"),
                        "beskrivelse": nace.get("beskrivelse"),
                    },
                    category=FactCategory.OPERATIONS,
                    evidence=_make_evidence(
                        explanation=f"Official primary economic activity code (NACE {nace.get('kode')}: {nace.get('beskrivelse')})."
                    ),
                )
            )

        # 8. Employee Count
        if "antallAnsatte" in enhet_data and enhet_data["antallAnsatte"] is not None:
            emp = int(enhet_data["antallAnsatte"])
            facts.append(
                ExtractedFactCandidate(
                    fact_key="operations.employee_count",
                    value=emp,
                    unit="count",
                    category=FactCategory.OPERATIONS,
                    evidence=_make_evidence(
                        quote=str(emp),
                        explanation=f"Official reported employee count ({emp}) from NAV Aa-registeret cross-reference.",
                    ),
                )
            )

        # 9. Latest Submitted Annual Accounts (sisteInnsendteAarsregnskap)
        if enhet_data.get("sisteInnsendteAarsregnskap"):
            yr = str(enhet_data["sisteInnsendteAarsregnskap"])
            facts.append(
                ExtractedFactCandidate(
                    fact_key="financial.latest_accounts_year",
                    value=yr,
                    category=FactCategory.FINANCIAL,
                    period_or_year=yr,
                    evidence=_make_evidence(
                        quote=yr,
                        explanation=f"Latest annual accounts year filed with Regnskapsregisteret: {yr}.",
                    ),
                )
            )

        # 10. Share Capital (kapital)
        if enhet_data.get("kapital") is not None:
            kap_raw = enhet_data["kapital"]
            if isinstance(kap_raw, dict):
                belop = kap_raw.get("belop")
                valuta = kap_raw.get("valuta", "NOK")
                aksjer = kap_raw.get("antallAksjer")
                if belop is not None:
                    try:
                        kap_float = float(belop)
                        explanation_str = f"Official registered equity share capital: {kap_float:,.2f} {valuta}."
                        if aksjer:
                            explanation_str += f" ({aksjer:,} shares)."
                        facts.append(
                            ExtractedFactCandidate(
                                fact_key="financial.share_capital",
                                value=kap_float,
                                unit=valuta,
                                category=FactCategory.FINANCIAL,
                                evidence=_make_evidence(
                                    quote=str(belop),
                                    explanation=explanation_str,
                                ),
                            )
                        )
                    except (ValueError, TypeError):
                        pass
            else:
                try:
                    kap = float(kap_raw)
                    facts.append(
                        ExtractedFactCandidate(
                            fact_key="financial.share_capital",
                            value=kap,
                            unit="NOK",
                            category=FactCategory.FINANCIAL,
                            evidence=_make_evidence(
                                quote=str(kap),
                                explanation=f"Official registered equity share capital: {kap:,.2f} NOK.",
                            ),
                        )
                    )
                except (ValueError, TypeError):
                    pass

        # 11. Statutory Purpose (vedtektsfestetFormaal)
        if enhet_data.get("vedtektsfestetFormaal"):
            purpose = " ".join(enhet_data["vedtektsfestetFormaal"])
            facts.append(
                ExtractedFactCandidate(
                    fact_key="operations.statutory_purpose",
                    value=purpose,
                    category=FactCategory.OPERATIONS,
                    evidence=_make_evidence(
                        explanation="Statutory purpose registered in articles of association."
                    ),
                )
            )

        # 12. Corporate Status (Active / Bankrupt / Liquidation)
        status = "ACTIVE"
        if enhet_data.get("konkurs", False):
            status = "BANKRUPT"
        elif enhet_data.get("underAvvikling", False):
            status = "IN_LIQUIDATION"
        elif enhet_data.get("underTvangsavviklingEllerTvangsopplosning", False):
            status = "FORCED_LIQUIDATION"

        facts.append(
            ExtractedFactCandidate(
                fact_key="registry.status",
                value=status,
                category=FactCategory.REGISTRY_CORE,
                evidence=_make_evidence(
                    explanation=f"Authoritative corporate standing: {status}."
                ),
            )
        )

        # 13. Governance Roles (CEO, Board)
        if roller_data and "rollegrupper" in roller_data:
            roles_evidence = _make_evidence(
                explanation="Official executive governance leadership registered at Brønnøysund."
            )
            for group in roller_data["rollegrupper"]:
                group_type = group.get("type", {}).get("kode")
                for role in group.get("roller", []):
                    role_type = role.get("type", {}).get("kode")
                    person = role.get("person", {})
                    name_dict = person.get("navn", {})
                    first = name_dict.get("fornavn", "")
                    last = name_dict.get("etternavn", "")
                    full_name = f"{first} {last}".strip()

                    if not full_name:
                        continue

                    if role_type == "DAGL" or group_type == "DAGL":
                        facts.append(
                            ExtractedFactCandidate(
                                fact_key="governance.ceo",
                                value=full_name,
                                category=FactCategory.GOVERNANCE,
                                evidence=roles_evidence,
                            )
                        )
                    elif role_type == "LEDE":
                        facts.append(
                            ExtractedFactCandidate(
                                fact_key="governance.board_chair",
                                value=full_name,
                                category=FactCategory.GOVERNANCE,
                                evidence=roles_evidence,
                            )
                        )

        # 14. Subunits (Underenheter)
        if underenheter_data:
            subunits = [
                {
                    "orgnr": u.get("organisasjonsnummer"),
                    "name": u.get("navn"),
                    "employees": u.get("antallAnsatte"),
                    "municipality": (u.get("forretningsadresse") or {}).get("kommune"),
                }
                for u in underenheter_data
                if u.get("organisasjonsnummer")
            ]
            facts.append(
                ExtractedFactCandidate(
                    fact_key="structure.subunits",
                    value=subunits,
                    category=FactCategory.STRUCTURE,
                    evidence=_make_evidence(
                        explanation=f"Registered active operational sub-units count: {len(subunits)}."
                    ),
                )
            )

        return facts
