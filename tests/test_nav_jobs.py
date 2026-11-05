"""
Unit and Integration Tests for Dual-Source Jobs & Careers Engine (NAV + Careers Pages).
Tests Channel A (NAV Public Job Feed) and Channel B (Corporate Domain Careers Scraper)
with strict entity verification and negative manager domain guards.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from bs4 import BeautifulSoup

from src.collectors.jobs_client import JobsClient
from src.validators.fact_guard import EntityAnchor, verify_page_belongs_to_company


class TestNavAndCareersJobsEngine:
    """Verifies NAV public job feed integration and official corporate careers scraping."""

    def setup_method(self):
        self.equinor_anchor = EntityAnchor(
            orgnr="923609016",
            legal_name="EQUINOR ASA",
            municipality="STAVANGER",
            ceo_names=["Anders Opedal"],
            chair_names=["Jon Erik Reinhardsen"],
            official_domains=["equinor.com"],
            external_role_entities=[],
            hjemmeside_is_shared_manager=False,
        )

        self.usbl_managed_borettslag = EntityAnchor(
            orgnr="950639822",
            legal_name="AKSISEBODEN BORETTSLAG",
            municipality="OSLO",
            ceo_names=[],
            chair_names=["Kari Nordmann"],
            official_domains=[],
            external_role_entities=[
                {"orgnr": "950285680", "navn": "Boligbyggelaget Usbl", "role_type": "FFØR"}
            ],
            hjemmeside_is_shared_manager=True,
        )

    @pytest.mark.asyncio
    async def test_nav_public_feed_success_exact_orgnr(self):
        """Proves NAV public feed API returns verified jobs legally registered under exact orgnr."""
        mock_client = MagicMock()
        mock_budget = MagicMock()
        mock_budget.get_company_count.return_value = 2
        mock_client.budget = mock_budget

        nav_payload = {
            "content": [
                {
                    "uuid": "abc-123-equinor",
                    "title": "Principal Subsea Pipeline Engineer",
                    "published": "2025-02-10T10:00:00",
                    "employer": {
                        "name": "EQUINOR ASA",
                        "orgnr": "923609016",
                    },
                },
                {
                    "uuid": "def-456-equinor",
                    "title": "Renewable Energy Project Manager",
                    "published": "2025-02-12T14:30:00",
                    "employer": {
                        "name": "EQUINOR ASA",
                        "orgnr": "923609016",
                    },
                },
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nav_payload
        mock_client.get = AsyncMock(return_value=mock_resp)

        jobs_client = JobsClient(client=mock_client)
        nav_jobs = await jobs_client.fetch_nav_jobs("923609016", self.equinor_anchor)

        assert len(nav_jobs) == 2
        assert nav_jobs[0]["title"] == "Principal Subsea Pipeline Engineer"
        assert nav_jobs[0]["verification_rule"] == "EXACT_ORGNR_MATCH"
        assert "923609016" in nav_jobs[0]["verbatim_quote"]
        assert "https://arbeidsplassen.nav.no/stillinger/stilling/abc-123-equinor" in nav_jobs[0]["url"]

    @pytest.mark.asyncio
    async def test_nav_public_feed_rejects_mismatched_employer(self):
        """Proves NAV public feed rejects jobs where employer orgnr does not match target."""
        mock_client = MagicMock()
        mock_budget = MagicMock()
        mock_budget.get_company_count.return_value = 2
        mock_client.budget = mock_budget

        nav_payload = {
            "content": [
                {
                    "uuid": "xyz-999-other",
                    "title": "Unrelated Job at Third Party",
                    "published": "2025-01-20T10:00:00",
                    "employer": {
                        "name": "OTHER UNRELATED AS",
                        "orgnr": "999888777",
                    },
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nav_payload
        mock_client.get = AsyncMock(return_value=mock_resp)

        jobs_client = JobsClient(client=mock_client)
        nav_jobs = await jobs_client.fetch_nav_jobs("923609016", self.equinor_anchor)

        assert len(nav_jobs) == 0

    @pytest.mark.asyncio
    async def test_corporate_career_page_with_json_ld(self):
        """Proves corporate career page with Schema.org JobPosting is parsed when entity is on page."""
        mock_client = MagicMock()
        mock_budget = MagicMock()
        mock_budget.get_company_count.return_value = 3
        mock_client.budget = mock_budget

        homepage_html = """
        <html>
        <head><title>Equinor</title></head>
        <body>
            <a href="/careers">Careers & Jobs</a>
        </body>
        </html>
        """

        career_html = """
        <html>
        <head>
            <title>Careers at Equinor</title>
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "title": "Senior AI Systems Architect",
                "datePosted": "2025-02-01",
                "hiringOrganization": {
                    "@type": "Organization",
                    "name": "EQUINOR ASA"
                }
            }
            </script>
        </head>
        <body>
            <h1>Karriere i EQUINOR ASA</h1>
            <p>Orgnr: 923609016</p>
        </body>
        </html>
        """

        async def _mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "equinor.com/careers" in url or "karriere" in url:
                resp.text = career_html
            else:
                resp.text = homepage_html
            return resp

        mock_client.get = AsyncMock(side_effect=_mock_get)

        jobs_client = JobsClient(client=mock_client)
        career_jobs, portal_url = await jobs_client.fetch_career_page_jobs(self.equinor_anchor)

        assert len(career_jobs) >= 1
        assert career_jobs[0]["title"] == "Senior AI Systems Architect"
        assert "EQUINOR ASA" in career_jobs[0]["verbatim_quote"]

    @pytest.mark.asyncio
    async def test_corporate_career_page_rejects_without_entity_on_page(self):
        """Proves career page is discarded if target orgnr / canonical name is missing."""
        mock_client = MagicMock()
        mock_budget = MagicMock()
        mock_budget.get_company_count.return_value = 3
        mock_client.budget = mock_budget

        homepage_html = "<html><body><a href='/careers'>Jobb</a></body></html>"
        generic_career_html = """
        <html>
        <body>
            <h1>Ledige stillinger i IT-sektoren</h1>
            <h2>Senior Utvikler</h2>
        </body>
        </html>
        """

        async def _mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = generic_career_html if "careers" in url else homepage_html
            return resp

        mock_client.get = AsyncMock(side_effect=_mock_get)

        jobs_client = JobsClient(client=mock_client)
        career_jobs, portal_url = await jobs_client.fetch_career_page_jobs(self.equinor_anchor)

        assert len(career_jobs) == 0

    @pytest.mark.asyncio
    async def test_shared_umbrella_domain_skips_career_crawl(self):
        """Proves housing associations managed by USBL/OBOS skip corporate career crawling."""
        mock_client = MagicMock()
        mock_budget = MagicMock()
        mock_client.budget = mock_budget

        jobs_client = JobsClient(client=mock_client)
        career_jobs, portal_url = await jobs_client.fetch_career_page_jobs(self.usbl_managed_borettslag)

        assert len(career_jobs) == 0
        assert portal_url is None
        # Budget count must be 0: no requests fired
        assert mock_client.get.call_count == 0

    @pytest.mark.asyncio
    async def test_zero_jobs_generates_explicit_known_unknowns(self):
        """Proves 0 verified jobs generates transparent known_unknowns statement."""
        mock_client = MagicMock()
        mock_budget = MagicMock()
        mock_budget.get_company_count.return_value = 2
        mock_client.budget = mock_budget

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"content": []}
        mock_resp.text = "<html><body>No jobs</body></html>"
        mock_client.get = AsyncMock(return_value=mock_resp)

        jobs_client = JobsClient(client=mock_client)
        all_jobs, portal, unknowns, candidates = await jobs_client.discover_jobs(self.usbl_managed_borettslag)

        assert len(all_jobs) == 0
        assert len(candidates) == 0
        assert len(unknowns) == 1
        assert "0 active openings verified" in unknowns[0]
