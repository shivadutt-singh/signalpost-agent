"""
Web Discovery Collector for targeted company homepage and permitted public sources.
Parses raw HTML, extracts dates, and applies Entity Verification Gate before emitting content.
"""

import re
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional
from bs4 import BeautifulSoup

from src.collectors.http_budget import BudgetedAsyncClient
from src.validators.fact_guard import EntityAnchor, EntityVerificationGate, EntityVerificationResult


@dataclass
class WebPageContent:
    """Raw parsed content and entity resolution verdict of a discovered web page."""
    url: str
    title: str
    raw_text: str
    source_date: Optional[str]
    verification: EntityVerificationResult
    status_code: int


class WebDiscoveryCollector:
    """
    Finds and collects relevant official and public web pages for a company
    while strictly abiding by the per-company HTTP budget.
    """

    def __init__(self, client: BudgetedAsyncClient):
        self.client = client

    async def fetch_page(
        self,
        url: str,
        anchor: EntityAnchor,
    ) -> Optional[WebPageContent]:
        """
        Fetches a web page, parses metadata and clean text, and runs the Entity Verification Gate.
        """
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        try:
            resp = await self.client.get(url, orgnr=anchor.orgnr)
            if resp.status_code != 200:
                return None

            html_text = resp.text
            soup = BeautifulSoup(html_text, "html.parser")

            # Extract title
            title = soup.title.string.strip() if soup.title and soup.title.string else ""

            # Extract publication / last-modified date if present
            source_date = self._extract_date_metadata(soup, resp.headers)

            # Clean page text: remove scripts, styles, svg
            for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
                tag.extract()

            text = soup.get_text(separator=" ", strip=True)

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
            )
        except Exception:
            return None

    def _extract_date_metadata(self, soup: BeautifulSoup, headers: dict) -> Optional[str]:
        """Attempts to extract publication/modification date from meta tags or HTTP headers."""
        # 1. Meta tags
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
                # Basic ISO validation or first 10 chars (YYYY-MM-DD)
                m = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
                if m:
                    return m.group(1)

        # 2. HTTP Last-Modified header
        last_mod = headers.get("last-modified")
        if last_mod:
            # e.g., "Wed, 21 Oct 2025 07:28:00 GMT"
            m = re.search(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})", last_mod)
            if m:
                return m.group(1)

        return None

    async def search_public_web(
        self,
        anchor: EntityAnchor,
        max_results: int = 2,
    ) -> List[str]:
        """
        Queries public search engine (DuckDuckGo HTML) for targeted authoritative company records.
        Yields up to max_results distinct external URLs.
        """
        query = f'"{anchor.orgnr}" OR ("{anchor.legal_name}" {anchor.municipality or ""})'
        search_url = "https://html.duckduckgo.com/html/"
        params = {"q": query}

        discovered_urls: List[str] = []
        try:
            resp = await self.client.get(search_url, orgnr=anchor.orgnr, params=params)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", class_="result__url", href=True):
                    raw_href = a["href"]
                    # Extract target URL from DuckDuckGo redirect if needed
                    parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                    target = parsed_qs.get("uddg", [raw_href])[0]
                    if target.startswith("http") and "duckduckgo.com" not in target:
                        if target not in discovered_urls:
                            discovered_urls.append(target)
                            if len(discovered_urls) >= max_results:
                                break
        except Exception:
            pass

        return discovered_urls

    async def discover_company_pages(
        self,
        anchor: EntityAnchor,
        include_search: bool = True,
    ) -> List[WebPageContent]:
        """
        Discovers and retrieves official homepage and verified public pages.
        All returned pages have already passed EntityVerificationGate.
        """
        pages: List[WebPageContent] = []
        visited = set()

        # 1. Official homepage (Tier 2)
        if anchor.official_domains:
            primary_url = f"https://{anchor.official_domains[0]}"
            visited.add(primary_url)
            try:
                page = await self.fetch_page(primary_url, anchor)
                if page and page.verification.is_verified:
                    pages.append(page)

                    # Try key subpages (om-oss, about, karriere, nyheter, investor) within company budget
                    subpages = ["/om-oss", "/about", "/karriere", "/nyheter", "/investor"]
                    for sub in subpages:
                        if self.client.budget.get_company_count(anchor.orgnr) >= 14:
                            break
                        sub_url = urllib.parse.urljoin(primary_url, sub)
                        if sub_url not in visited:
                            visited.add(sub_url)
                            sub_page = await self.fetch_page(sub_url, anchor)
                            if sub_page and sub_page.verification.is_verified:
                                pages.append(sub_page)
                                if len(pages) >= 3:
                                    break
            except Exception:
                pass

        # 2. Targeted search if enabled and within budget
        if include_search and len(pages) < 2 and self.client.budget.get_company_count(anchor.orgnr) < 14:
            try:
                search_links = await self.search_public_web(anchor, max_results=1)
                for link in search_links:
                    if link not in visited:
                        visited.add(link)
                        ext_page = await self.fetch_page(link, anchor)
                        if ext_page and ext_page.verification.is_verified:
                            pages.append(ext_page)
                            break
            except Exception:
                pass

        return pages
