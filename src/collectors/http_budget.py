"""
Budgeted Asynchronous HTTP Client with strict per-company and dynamic global request limits.
Guarantees enforcement of the 18-requests-per-company and fixes the 1,000-company starvation bug
by dynamically scaling global web budget = batch_size * 18 when batch_size > 100.
Tracks web outbound scraping requests separately from LLM API calls.
"""

import asyncio
from typing import Dict, List, Optional
import httpx
from src.config import settings


class CompanyHttpBudgetExceededError(Exception):
    """Raised when a single company reaches its strict 18 HTTP request budget limit."""
    pass


class GlobalHttpBudgetExceededError(Exception):
    """Raised when the aggregate batch HTTP budget is exhausted."""
    pass


class HttpBudgetTracker:
    """
    Thread- and coroutine-safe HTTP budget manager.
    Tracks outbound web scraping calls globally and on a per-company basis,
    while separating LLM API calls so they do not exhaust the web scraping budget.
    """

    def __init__(
        self,
        batch_size: Optional[int] = None,
        max_per_company: int = settings.MAX_HTTP_REQUESTS_PER_COMPANY,
        max_global: Optional[int] = None,
    ):
        self.batch_size = batch_size
        self.max_per_company = max_per_company

        # Dynamic budget scaling:
        # For daily 100-company evaluation mode (batch_size <= 100): enforce MAX_GLOBAL_WEB_REQUESTS = 1950
        # For 1,000-company generator mode (batch_size > 100): dynamically scale MAX_GLOBAL_WEB_REQUESTS = batch_size * 18
        if max_global is not None:
            self.max_global = max_global
        elif batch_size is not None:
            if batch_size <= 100:
                self.max_global = settings.MAX_GLOBAL_WEB_REQUESTS_100  # 1950 (safety buffer below 2000)
            else:
                self.max_global = batch_size * self.max_per_company     # 1000 * 18 = 18,000 max
        else:
            self.max_global = settings.GLOBAL_MAX_HTTP_REQUESTS

        self._lock = asyncio.Lock()
        self._global_web_request_count: int = 0
        self._llm_api_call_count: int = 0
        self._company_request_counts: Dict[str, int] = {}
        self._company_urls: Dict[str, List[str]] = {}
        self._cache: Dict[str, httpx.Response] = {}

    @classmethod
    def create_for_batch(cls, batch_size: int) -> "HttpBudgetTracker":
        """Factory constructor that sets optimal dynamic budget scaling for the batch size."""
        return cls(batch_size=batch_size)

    async def check_and_increment(self, orgnr: Optional[str], url: str) -> bool:
        """
        Validates whether an outbound web request can be executed without exceeding budget limits.
        If the URL was already fetched for this orgnr in the current session, it is served
        from cache without consuming a request token.
        Returns:
            bool: True if new request allocated, False if cached.
        """
        cache_key = f"{orgnr or 'global'}::{url}"
        async with self._lock:
            if cache_key in self._cache:
                return False

            if self._global_web_request_count >= self.max_global:
                raise GlobalHttpBudgetExceededError(
                    f"Global HTTP request budget reached: {self._global_web_request_count}/{self.max_global} calls."
                )

            if orgnr:
                current_count = self._company_request_counts.get(orgnr, 0)
                if current_count >= self.max_per_company:
                    raise CompanyHttpBudgetExceededError(
                        f"Company '{orgnr}' reached strict HTTP request budget: "
                        f"{current_count}/{self.max_per_company} calls made."
                    )
                self._company_request_counts[orgnr] = current_count + 1
                self._company_urls.setdefault(orgnr, []).append(url)

            self._global_web_request_count += 1
            return True

    async def record_llm_call(self) -> None:
        """Records an LLM API call separately from outbound web scraping requests."""
        async with self._lock:
            self._llm_api_call_count += 1

    async def store_cache(self, orgnr: Optional[str], url: str, response: httpx.Response) -> None:
        cache_key = f"{orgnr or 'global'}::{url}"
        async with self._lock:
            self._cache[cache_key] = response

    async def get_cached(self, orgnr: Optional[str], url: str) -> Optional[httpx.Response]:
        cache_key = f"{orgnr or 'global'}::{url}"
        async with self._lock:
            return self._cache.get(cache_key)

    @property
    def total_requests(self) -> int:
        """Total outbound web requests."""
        return self._global_web_request_count

    @property
    def web_outbound_requests(self) -> int:
        return self._global_web_request_count

    @property
    def llm_api_calls(self) -> int:
        return self._llm_api_call_count

    @property
    def requests_per_company(self) -> Dict[str, int]:
        return dict(self._company_request_counts)

    def get_company_count(self, orgnr: str) -> int:
        return self._company_request_counts.get(orgnr, 0)


class BudgetedAsyncClient:
    """
    Wrapper around httpx.AsyncClient that strictly passes all network I/O
    through the HttpBudgetTracker.
    """

    def __init__(
        self,
        budget_tracker: Optional[HttpBudgetTracker] = None,
        timeout: float = settings.HTTP_TIMEOUT_SECONDS,
        user_agent: str = settings.USER_AGENT,
    ):
        self.budget = budget_tracker or HttpBudgetTracker()
        self.timeout = timeout
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/html, application/xhtml+xml, */*",
            "Accept-Language": "no, nb, nn, en;q=0.9",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=5.0),
            headers=self.headers,
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        orgnr: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        retries: int = settings.HTTP_MAX_RETRIES,
    ) -> httpx.Response:
        """
        Executes a budgeted GET request.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                headers=self.headers,
                follow_redirects=True,
            )

        # Check cache
        cached = await self.budget.get_cached(orgnr, url)
        if cached is not None:
            return cached

        # Check budget before sending wire request
        await self.budget.check_and_increment(orgnr, url)

        last_error = None
        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)

        for attempt in range(retries):
            try:
                response = await self._client.get(url, headers=merged_headers, params=params)
                # Store in session cache
                await self.budget.store_cache(orgnr, url, response)
                return response
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as err:
                last_error = err
                if attempt < retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                else:
                    break

        raise last_error or httpx.HTTPError(f"Failed to fetch {url}")
