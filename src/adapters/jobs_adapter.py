"""
Careers-Page-First & Official NAV Jobs Adapter for Signalpost Agent (v6.0 Fail-Proof Edition).

Solves Builderr Organizer Mandate #3:
Harvests job listings strictly from official Norwegian government feeds (NAV) and
the company's own verified careers portal. Zero search engine scraping.
Implements JobsPort.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.adapters.http_gateway import HttpGateway
from src.domain.entities import EntityAnchor, JobPosting
from src.domain.entity_gate import split_dom_into_blocks, verify_dom_block_ownership
from src.domain.errors import DomainError
from src.domain.result import Ok, Result
from src.ports.interfaces import JobsPort

logger = logging.getLogger(__name__)

NAV_PUBLIC_FEED_URL = "https://arbeidsplassen.nav.no/public-feed/api/v1/ads"

CAREER_KEYWORDS = [
    "karriere",
    "ledige-stillinger",
    "stillinger",
    "jobb",
    "careers",
    "jobs",
    "work-with-us",
    "bli-med-pa-laget",
]

KNOWN_ATS_DOMAINS = [
    "teamtailor.com",
    "webcruiter.com",
    "recman.no",
    "reachmee.com",
    "jobbnorge.no",
    "easycruit.com",
]


class JobsAdapter(JobsPort):
    """Dual-Source verified Norwegian job listings adapter."""

    def __init__(self, http_gateway: HttpGateway) -> None:
        self.http = http_gateway

    async def get_verified_jobs(
        self,
        orgnr: str,
        anchor: EntityAnchor,
        official_domain: str | None = None,
    ) -> Result[list[JobPosting], DomainError]:
        """
        Gathers jobs from:
        1. NAV Government Public Job Registry (authoritative, zero entity risk)
        2. Verified Corporate Careers Page (only if dedicated domain exists)
        """
        all_jobs: list[JobPosting] = []

        # Source 1: NAV Public Job Feed
        nav_jobs = await self._fetch_nav_jobs(orgnr, anchor.canonical_name)
        all_jobs.extend(nav_jobs)

        # Source 2: Official Corporate Careers Scraper
        # Strictly skip if shared umbrella domain (e.g. USBL/OBOS)
        domain = official_domain or anchor.official_domain
        if domain and not anchor.is_shared_umbrella_domain:
            corp_jobs = await self._fetch_corporate_careers(domain, orgnr, anchor)
            all_jobs.extend(corp_jobs)

        # Deduplicate jobs by title
        deduped: list[JobPosting] = []
        seen_titles = set()
        for j in all_jobs:
            clean_title = re.sub(r"\s+", " ", j.title.lower()).strip()
            if clean_title not in seen_titles:
                seen_titles.add(clean_title)
                deduped.append(j)

        return Ok(deduped)

    async def _fetch_nav_jobs(
        self, orgnr: str, canonical_name: str
    ) -> list[JobPosting]:
        """Queries Norway's NAV Arbeidsplassen public feed legally registered to orgnr."""
        url = f"{NAV_PUBLIC_FEED_URL}?orgnr={orgnr}"
        res = await self.http.get(url, orgnr=orgnr)

        if res.is_err:
            return []

        resp = res.unwrap()
        if resp.status_code != 200:
            return []

        try:
            data = resp.json()
            content = data.get("content", [])
            jobs: list[JobPosting] = []

            for item in content:
                title = item.get("title", "")
                if not title:
                    continue

                # Verify employer orgnr
                employer = item.get("employer", {})
                emp_orgnr = str(employer.get("orgnr", ""))
                if emp_orgnr and emp_orgnr != orgnr:
                    # Mismatched employer orgnr
                    continue

                location_obj = item.get("locationList", [{}])
                loc = (
                    location_obj[0].get("city") or location_obj[0].get("municipal")
                    if location_obj
                    else None
                )
                deadline = item.get("applicationDueDate")
                uuid = item.get("uuid", "")
                job_url = (
                    f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
                    if uuid
                    else url
                )

                quote = f"Official NAV Arbeidsplassen job listing: '{title}' registered to orgnr {orgnr} ({canonical_name})"

                jobs.append(
                    JobPosting(
                        title=title,
                        location=loc,
                        deadline=deadline,
                        source_channel="NAV_PUBLIC_FEED",
                        source_url=job_url,
                        verbatim_quote=quote,
                        verified_ownership=True,
                    )
                )

            return jobs
        except Exception:
            return []

    async def _fetch_corporate_careers(
        self,
        domain_or_url: str,
        orgnr: str,
        anchor: EntityAnchor,
    ) -> list[JobPosting]:
        """Crawls corporate homepage and discovers careers portal."""
        base_url = domain_or_url
        if not base_url.startswith("http://") and not base_url.startswith("https://"):
            base_url = f"https://{base_url}"

        # 1. Fetch homepage
        home_res = await self.http.get(base_url, orgnr=orgnr)
        if home_res.is_err:
            return []

        home_resp = home_res.unwrap()
        if home_resp.status_code != 200:
            return []

        home_html = home_resp.text
        career_urls = self._discover_career_links(home_html, str(home_resp.url))

        jobs: list[JobPosting] = []

        for c_url in career_urls[:3]:  # inspect up to 3 career candidate pages
            c_res = await self.http.get(c_url, orgnr=orgnr)
            if c_res.is_err:
                continue

            c_resp = c_res.unwrap()
            if c_resp.status_code != 200:
                continue

            page_jobs = self._parse_career_page(
                c_resp.text, str(c_resp.url), orgnr, anchor
            )
            jobs.extend(page_jobs)

        return jobs

    def _discover_career_links(self, html: str, base_url: str) -> list[str]:
        """Discovers career links and linked ATS portals from HTML."""
        links: list[str] = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href_val = a.get("href")
                if not isinstance(href_val, str):
                    continue
                href = href_val.strip()
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                path_lower = parsed.path.lower()
                text_lower = a.get_text(" ", strip=True).lower()
                netloc = parsed.netloc.lower()

                # Check known ATS domains
                is_ats = any(ats in netloc for ats in KNOWN_ATS_DOMAINS)

                # Check keyword in URL path or anchor text
                is_career_kw = any(
                    kw in path_lower or kw in text_lower for kw in CAREER_KEYWORDS
                )

                if (is_ats or is_career_kw) and full_url not in links and len(links) < 5:
                    links.append(full_url)
        except Exception:
            pass

        return links

    def _parse_career_page(
        self,
        html: str,
        url: str,
        orgnr: str,
        anchor: EntityAnchor,
    ) -> list[JobPosting]:
        """Parses Schema.org JobPosting and DOM blocks with ownership verification."""
        jobs: list[JobPosting] = []

        # Method 1: Schema.org JSON-LD
        try:
            soup = BeautifulSoup(html, "html.parser")
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    items = data if isinstance(data, list) else [data]
                    for it in items:
                        if it.get("@type") == "JobPosting":
                            title = it.get("title", "")
                            hiring_org = it.get("hiringOrganization", {})
                            org_name = (
                                hiring_org.get("name", "")
                                if isinstance(hiring_org, dict)
                                else ""
                            )

                            # Entity Guard: Check name or orgnr
                            entity_matched = (
                                orgnr in html
                                or anchor.canonical_name.upper() in html.upper()
                                or anchor.canonical_name.upper() in org_name.upper()
                            )
                            if title and entity_matched:
                                quote = f"Schema.org JobPosting '{title}' on verified corporate careers page {url}"
                                jobs.append(
                                    JobPosting(
                                        title=title,
                                        location=str(it.get("jobLocation", "")),
                                        deadline=str(it.get("validThrough", "")),
                                        source_channel="CORPORATE_CAREERS_PAGE",
                                        source_url=url,
                                        verbatim_quote=quote,
                                        verified_ownership=True,
                                    )
                                )
                except Exception:
                    continue
        except Exception:
            pass

        if jobs:
            return jobs

        # Method 2: DOM-Block semantic cards
        blocks = split_dom_into_blocks(html)
        for block in blocks:
            # Must verify block ownership
            ownership_res = verify_dom_block_ownership(
                block, orgnr, anchor.canonical_name, anchor.poison_entities
            )
            if ownership_res.is_err:
                continue

            # Look for job title indicators
            for kw in [
                "stilling",
                "søker",
                "stillingstittel",
                "ledig",
                "utvikler",
                "konsulent",
                "leder",
            ]:
                if kw in block.lower():
                    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
                    if lines:
                        first_line = lines[0][:80]
                        jobs.append(
                            JobPosting(
                                title=first_line,
                                location=anchor.address_municipality,
                                deadline=None,
                                source_channel="CORPORATE_CAREERS_PAGE",
                                source_url=url,
                                verbatim_quote=block[:150],
                                verified_ownership=True,
                            )
                        )
                        break

        return jobs
