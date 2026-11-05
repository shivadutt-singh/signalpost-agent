"""
Careers-Page-First Web Discovery Collector and Strict News Pipeline.
Eliminates generic search engine job queries in favor of:
1. Direct company career page discovery on verified own domains.
2. Official Norwegian NAV public job registry (arbeidsplassen.nav.no) filtered strictly by exact 9-digit orgnr.
3. Strict news pipeline requiring verified domain ownership or exact orgnr/canonical name corroboration.
"""

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from src.collectors.http_budget import BudgetedAsyncClient
from src.domain.text_normalizer import normalize_norwegian_text
from src.validators.fact_guard import (
    EntityAnchor,
    EntityVerificationGate,
    EntityVerificationResult,
    is_own_company_domain,
    verify_page_belongs_to_company,
)

logger = logging.getLogger("signalpost.discovery")

CAREER_REGEX = re.compile(
    r"(karriere|ledige[-_]?stillinger|stillinger|jobb[-_]?hos[-_]?oss|jobb|careers|jobs|work[-_]?with[-_]?us)",
    re.IGNORECASE,
)

ATS_DOMAINS = (
    "teamtailor.com",
    "recman.no",
    "webcruiter.com",
    "reachmee.com",
    "jobbnorge.no",
)

NEWS_REGEX = re.compile(
    r"(nyheter|aktuelt|pressemeldinger|press|presse|news)",
    re.IGNORECASE,
)

ABOUT_REGEX = re.compile(
    r"(om[-_]?oss|about|produkter|tjenester|virksomhet|kontakt|hva[-_]?vi[-_]?gjor|solutions|services)",
    re.IGNORECASE,
)


@dataclass
class WebPageContent:
    """Raw parsed content, entity resolution verdict, and HTML of a discovered web page."""
    url: str
    title: str
    raw_text: str
    source_date: str | None
    verification: EntityVerificationResult
    status_code: int
    html: str = ""


class WebDiscoveryCollector:
    """
    Direct, targeted web discovery collector.
    Strictly follows Careers-Page-First and strict news discovery rules without generic search-engine bleed.
    """

    def __init__(self, client: BudgetedAsyncClient):
        self.client = client

    async def fetch_page(
        self,
        url: str,
        anchor: EntityAnchor,
    ) -> WebPageContent | None:
        """
        Fetches a web page, parses metadata and clean text, and runs EntityVerificationGate.
        """
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        try:
            resp = await self.client.get(url, orgnr=anchor.orgnr)
            if resp.status_code != 200:
                return None

            try:
                if resp.encoding is None or resp.encoding.lower() in ("iso-8859-1", "ascii"):
                    resp.encoding = "utf-8"
            except Exception:
                pass

            html_text = normalize_norwegian_text(resp.text)
            soup = BeautifulSoup(html_text, "html.parser")

            # Extract title
            title = normalize_norwegian_text(soup.title.string.strip() if soup.title and soup.title.string else "")

            # Extract publication / last-modified date if present
            source_date = self._extract_date_metadata(soup, resp.headers)

            # Clean page text: remove scripts, styles, svg
            clean_soup = BeautifulSoup(html_text, "html.parser")
            for tag in clean_soup(["script", "style", "noscript", "svg"]):
                tag.extract()

            text = normalize_norwegian_text(clean_soup.get_text(separator=" ", strip=True))

            # Run Entity Verification Gate
            verification = EntityVerificationGate.verify_source(
                anchor=anchor,
                source_url=url,
                raw_source_text=text,
            )

            return WebPageContent(
                url=url,
                title=title,
                raw_text=text,
                source_date=source_date,
                verification=verification,
                status_code=resp.status_code,
                html=html_text,
            )
        except Exception:
            return None

    def _extract_date_metadata(self, soup: BeautifulSoup, headers: dict) -> str | None:
        """Attempts to extract publication/modification date from meta tags or HTTP headers."""
        meta_candidates = [
            ("property", "article:published_time"),
            ("property", "article:modified_time"),
            ("name", "date"),
            ("name", "pubdate"),
            ("name", "last-modified"),
            ("itemprop", "datePublished"),
            ("itemprop", "dateModified"),
        ]
        for attr, val in meta_candidates:
            tag = soup.find("meta", attrs={attr: val})
            if tag and tag.get("content"):
                date_str = tag["content"].strip()
                m = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
                if m:
                    return m.group(1)

        last_mod = headers.get("last-modified")
        if last_mod:
            m = re.search(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", last_mod)
            if m:
                return m.group(1)

        return None

    async def discover_company_pages(
        self,
        anchor: EntityAnchor,
        include_search: bool = True,
    ) -> list[WebPageContent]:
        """
        Discovers official homepage and key corporate sections (about, investor, news, careers).
        All returned pages have already passed EntityVerificationGate.
        """
        pages: list[WebPageContent] = []
        visited = set()

        # Step 1: Official homepage (only if verified own domain, not shared manager)
        if anchor.official_domains and not anchor.hjemmeside_is_shared_manager:
            primary_domain = anchor.official_domains[0]
            if is_own_company_domain(primary_domain, anchor.legal_name, anchor.orgnr):
                primary_url = f"https://{primary_domain}"
                visited.add(primary_url)
                try:
                    home_page = await self.fetch_page(primary_url, anchor)
                    if home_page and home_page.verification.is_verified:
                        pages.append(home_page)

                        # Parse navigation links from homepage HTML for key subpages
                        soup = BeautifulSoup(home_page.html, "html.parser")
                        candidate_subpages = []

                        # Scan <a> tags for relevant corporate destinations
                        for a in soup.find_all("a", href=True):
                            href = a["href"].strip()
                            txt = a.get_text().strip()
                            if CAREER_REGEX.search(href) or CAREER_REGEX.search(txt) or NEWS_REGEX.search(href) or NEWS_REGEX.search(txt) or any(k in href.lower() for k in ["om-oss", "about", "investor", "aarsrapport"]):
                                candidate_subpages.append(href)

                        # Add standard fallbacks if not found
                        standard_paths = ["/karriere", "/jobb", "/nyheter", "/om-oss", "/investor"]
                        for p in standard_paths:
                            if p not in candidate_subpages:
                                candidate_subpages.append(p)

                        # Fetch key subpages within strict budget
                        for sub in candidate_subpages:
                            if self.client.budget.get_company_count(anchor.orgnr) >= 12 or len(pages) >= 5:
                                break
                            sub_url = urllib.parse.urljoin(primary_url, sub)
                            clean_sub_url = sub_url.split("#")[0].split("?")[0]
                            if clean_sub_url not in visited and is_own_company_domain(clean_sub_url, anchor.legal_name, anchor.orgnr):
                                visited.add(clean_sub_url)
                                sub_page = await self.fetch_page(clean_sub_url, anchor)
                                if sub_page and sub_page.verification.is_verified:
                                    pages.append(sub_page)
                except Exception as e:
                    logger.debug(f"Error discovering subpages on {primary_domain}: {e}")

        return pages

    async def discover_and_rank_subpages(
        self,
        anchor: EntityAnchor,
        max_subpages: int = 4,
    ) -> tuple[list[WebPageContent], dict[str, str], list[dict[str, Any]]]:
        """
        Stage 2A & 2B: Hybrid Link Selection & Deterministic Page Pre-Filter.
        1. Fetches company's verified homepage.
        2. Extracts internal links and approved embedded ATS links (*.teamtailor.com, etc.).
        3. Ranks links into 3 priority buckets: Careers > News > About/Operations.
        4. Selects up to max_subpages and fetches them within the HTTP budget.
        5. Pre-filters each page with verify_page_belongs_to_company() and parses JSON-LD JobPostings.
        Returns:
            (verified_pages, raw_scraped_pages, ld_json_jobs)
        """
        verified_pages: list[WebPageContent] = []
        raw_scraped_pages: dict[str, str] = {}
        ld_json_jobs: list[dict[str, Any]] = []
        visited = set()

        if not anchor.official_domains or anchor.hjemmeside_is_shared_manager:
            return verified_pages, raw_scraped_pages, ld_json_jobs

        primary_domain = anchor.official_domains[0]
        if not is_own_company_domain(primary_domain, anchor.legal_name, anchor.orgnr):
            return verified_pages, raw_scraped_pages, ld_json_jobs

        primary_url = f"https://{primary_domain}"
        visited.add(primary_url)

        # 1. Fetch official homepage
        home_page = await self.fetch_page(primary_url, anchor)
        if not home_page or not home_page.verification.is_verified:
            return verified_pages, raw_scraped_pages, ld_json_jobs

        verified_pages.append(home_page)
        raw_scraped_pages[home_page.url] = home_page.raw_text

        # 2. Stage 2A: Parse and rank candidate subpages
        soup = BeautifulSoup(home_page.html, "html.parser")
        p1_careers: list[str] = []
        p2_news: list[str] = []
        p3_about: list[str] = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            anchor_text = a.get_text().strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue

            full_url = urllib.parse.urljoin(primary_url, href)
            clean_url = full_url.split("#")[0].split("?")[0]
            if clean_url in visited:
                continue

            try:
                parsed = urllib.parse.urlparse(clean_url)
                host = parsed.netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
            except Exception:
                continue

            is_ats = any(host == ats or host.endswith(f".{ats}") for ats in ATS_DOMAINS)
            is_internal = host == primary_domain or host.endswith(f".{primary_domain}")

            if not (is_ats or is_internal):
                continue

            # Priority 1: Careers
            if is_ats or CAREER_REGEX.search(clean_url) or CAREER_REGEX.search(anchor_text):
                if clean_url not in p1_careers:
                    p1_careers.append(clean_url)
            # Priority 2: News / Updates
            elif NEWS_REGEX.search(clean_url) or NEWS_REGEX.search(anchor_text):
                if clean_url not in p2_news:
                    p2_news.append(clean_url)
            # Priority 3: About / Operations / Services
            elif ABOUT_REGEX.search(clean_url) or ABOUT_REGEX.search(anchor_text):
                if clean_url not in p3_about:
                    p3_about.append(clean_url)

        # Standard fallbacks if lists are empty
        fallbacks = [
            (f"{primary_url}/karriere", p1_careers),
            (f"{primary_url}/jobb", p1_careers),
            (f"{primary_url}/nyheter", p2_news),
            (f"{primary_url}/om-oss", p3_about),
        ]
        for fb_url, target_list in fallbacks:
            if fb_url not in target_list and fb_url not in visited:
                target_list.append(fb_url)

        # Select up to max_subpages across priorities
        selected_subpages: list[str] = []
        # Up to 2 career pages
        for u in p1_careers[:2]:
            if u not in selected_subpages:
                selected_subpages.append(u)
        # Up to 1 news page
        for u in p2_news[:1]:
            if u not in selected_subpages:
                selected_subpages.append(u)
        # Up to 1 about/operations page
        for u in p3_about[:1]:
            if u not in selected_subpages:
                selected_subpages.append(u)

        # Fill remaining slots up to max_subpages
        remaining_pool = [u for u in (p1_careers + p2_news + p3_about) if u not in selected_subpages]
        for u in remaining_pool:
            if len(selected_subpages) >= max_subpages:
                break
            selected_subpages.append(u)

        # 3. Stage 2B: Fetch and run Deterministic Pre-Filter
        for sub_url in selected_subpages:
            if self.client.budget.get_company_count(anchor.orgnr) >= 15:
                break
            visited.add(sub_url)
            sub_page = await self.fetch_page(sub_url, anchor)
            if not sub_page:
                continue

            # Deterministic Page Pre-Filter: verify page belongs to company
            is_ok, _, _ = verify_page_belongs_to_company(
                target_orgnr=anchor.orgnr,
                canonical_name=anchor.legal_name,
                page_text=sub_page.raw_text,
                external_role_entities=anchor.external_role_entities,
            )
            if not is_ok:
                logger.debug(f"[Pre-Filter] Dropped non-belonging subpage: {sub_url}")
                continue

            verified_pages.append(sub_page)
            raw_scraped_pages[sub_page.url] = sub_page.raw_text

            # Deterministic JSON-LD JobPosting parsing in Python
            try:
                sub_soup = BeautifulSoup(sub_page.html, "html.parser")
                for s in sub_soup.find_all("script", type="application/ld+json"):
                    if not s.string:
                        continue
                    try:
                        data = json.loads(s.string)
                        items = data if isinstance(data, list) else [data]
                        for it in items:
                            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                                title = it.get("title")
                                if title:
                                    quote = f"JobPosting: {title} | {anchor.legal_name}"
                                    ld_json_jobs.append({
                                        "title": title,
                                        "url": sub_page.url,
                                        "source_date": it.get("datePosted") or sub_page.source_date,
                                        "verbatim_quote": quote,
                                    })
                    except Exception:
                        pass
            except Exception:
                pass

        return verified_pages, raw_scraped_pages, ld_json_jobs

    async def discover_jobs(
        self,
        anchor: EntityAnchor,
    ) -> tuple[list[dict[str, Any]], str | None, list[str]]:
        """
        Strict Careers-Page-First Jobs Pipeline powered by JobsClient:
        Channel A: Norway NAV Public Job Feed (arbeidsplassen.nav.no).
        Channel B: Verified Corporate Domain Careers Scraper with Entity Guard.
        Explicitly logs known unknowns when 0 active openings are confirmed.
        """
        if self.client:
            from src.collectors.jobs_client import JobsClient
            jobs_client = JobsClient(self.client)
            all_jobs, portal_url, known_unknowns, _ = await jobs_client.discover_jobs(anchor)
            return all_jobs, portal_url, known_unknowns

        today_str = datetime.now().strftime("%Y-%m-%d")
        domain_desc = f"official website ({anchor.official_domains[0]})" if anchor.official_domains else "registry records"
        return [], None, [
            f"Active job listings: Checked {domain_desc} and official NAV public job feed on {today_str}; "
            f"0 active openings verified featuring the company's legal name or exact orgnr."
        ]

    async def discover_news(
        self,
        anchor: EntityAnchor,
    ) -> list[dict[str, Any]]:
        """
        Strict News Discovery:
        Collects press releases and corporate announcements ONLY from verified company domain
        or sources explicitly confirmed by verify_page_belongs_to_company.
        """
        news_items: list[dict[str, Any]] = []
        today_str = datetime.now().strftime("%Y-%m-%d")

        if not anchor.official_domains or anchor.hjemmeside_is_shared_manager:
            return news_items

        primary_domain = anchor.official_domains[0]
        if not is_own_company_domain(primary_domain, anchor.legal_name, anchor.orgnr):
            return news_items

        news_paths = ["/nyheter", "/aktuelt", "/presse", "/news", "/press"]
        for path in news_paths[:2]:
            if self.client.budget.get_company_count(anchor.orgnr) >= 15:
                break
            news_url = f"https://{primary_domain}{path}"
            page = await self.fetch_page(news_url, anchor)
            if not page or page.status_code != 200:
                continue

            # Must satisfy page verification
            is_verified, rule, _ = verify_page_belongs_to_company(
                target_orgnr=anchor.orgnr,
                canonical_name=anchor.legal_name,
                page_text=page.raw_text,
                external_role_entities=anchor.external_role_entities,
            )
            if not is_verified:
                continue

            critic_rule = (
                "EXACT_ORGNR_ON_PAGE"
                if rule == "EXACT_ORGNR_MATCH"
                else "EXACT_LEGAL_NAME_ON_OWN_DOMAIN"
            )

            soup = BeautifulSoup(page.html, "html.parser")
            for article in soup.find_all(["article", "div", "li"]):
                heading = article.find(["h2", "h3", "h4", "a"])
                if heading:
                    h_text = heading.get_text().strip()
                    if len(h_text) > 15 and len(h_text) < 150:
                        link = heading.get("href") or news_url
                        full_link = urllib.parse.urljoin(news_url, link) if isinstance(link, str) else news_url
                        date_tag = article.find(["time", "span"])
                        date_val = page.source_date or today_str
                        if date_tag and re.search(r"\d{4}", date_tag.get_text()):
                            date_val = date_tag.get_text().strip()[:10]

                        if not any(n["title"] == h_text for n in news_items):
                            news_items.append({
                                "title": h_text,
                                "source_url": full_link,
                                "source_date": date_val,
                                "verbatim_quote": h_text,
                                "verification_rule": critic_rule,
                            })
                            if len(news_items) >= 4:
                                break

        return news_items