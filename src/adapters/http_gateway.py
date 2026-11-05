"""
Hardened HTTP Gateway Adapter for Signalpost Agent (v6.0 Fail-Proof Edition).

Features:
- Strict per-company budget enforcement (Max 18 HTTP calls per entity).
- SSRF prevention (blocks private subnets, localhost, link-local metadata).
- 2MB response size limit to prevent memory exhaustion.
- Railway-oriented Result[httpx.Response, DomainError] wrapping so no network error crashes a worker.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

import httpx

from src.domain.errors import (
    DomainError,
    HttpBudgetExceededError,
    NetworkTimeoutError,
    RateLimitExceededError,
)
from src.domain.result import Err, Ok, Result

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2MB
MAX_REQUESTS_PER_COMPANY = 18

DISALLOWED_HOSTS: set[str] = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "169.254.169.254",  # Cloud metadata
    "::1",
}


def is_ssrf_safe_url(url: str) -> bool:
    """Verifies that the target URL does not resolve to private/local/metadata IPs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        host = parsed.hostname
        if not host:
            return False

        host_lower = host.lower()
        if host_lower in DISALLOWED_HOSTS:
            return False

        # Attempt IP check
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            # Domain name, check for standard local suffixes
            if host_lower.endswith(".local") or host_lower.endswith(".internal"):
                return False

        return True
    except Exception:
        return False


class HttpGateway:
    """Budget-locked, SSRF-safe HTTP client adapter."""

    def __init__(
        self,
        max_requests_per_company: int = MAX_REQUESTS_PER_COMPANY,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = "Signalpost-Agent/6.0 (Norwegian-Intelligence-Pipeline; Evaluation-Runner)",
    ) -> None:
        self.max_requests_per_company = max_requests_per_company
        self.timeout = timeout
        self.user_agent = user_agent
        self._request_counts: dict[str, int] = {}
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        """Returns or creates the underlying shared httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5",
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=40),
            )
        return self._client

    async def close(self) -> None:
        """Closes the underlying client session."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def get_requests_used(self, orgnr: str) -> int:
        """Returns HTTP calls consumed so far for the company."""
        return self._request_counts.get(orgnr, 0)

    async def get(
        self,
        url: str,
        orgnr: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Result[httpx.Response, DomainError]:
        """
        Executes an HTTP GET request with budget enforcement and error wrapping.
        """
        # 1. Budget enforcement
        current_calls = self._request_counts.get(orgnr, 0)
        if current_calls >= self.max_requests_per_company:
            return Err(
                HttpBudgetExceededError(
                    code="HTTP_BUDGET_EXCEEDED",
                    message=f"Company {orgnr} reached strict HTTP limit of {self.max_requests_per_company} calls",
                    details={
                        "orgnr": orgnr,
                        "calls_made": current_calls,
                        "target_url": url,
                    },
                )
            )

        # 2. SSRF Guard
        if not is_ssrf_safe_url(url):
            return Err(
                DomainError(
                    code="SSRF_FORBIDDEN",
                    message=f"SSRF check rejected URL: {url}",
                    details={"url": url},
                )
            )

        # Increment call count
        self._request_counts[orgnr] = current_calls + 1
        client = await self.get_client()
        req_timeout = timeout or self.timeout

        try:
            resp = await client.get(url, headers=headers, timeout=req_timeout)

            # Check 429 Too Many Requests
            if resp.status_code == 429:
                return Err(
                    RateLimitExceededError(
                        code="RATE_LIMIT_429",
                        message=f"Rate limit 429 encountered at {url}",
                        details={"status_code": 429, "url": url},
                    )
                )

            # Check content length
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                return Err(
                    DomainError(
                        code="RESPONSE_TOO_LARGE",
                        message=f"Response size {content_length} exceeds 2MB limit",
                        details={"url": url, "size": content_length},
                    )
                )

            return Ok(resp)

        except httpx.TimeoutException as exc:
            return Err(
                NetworkTimeoutError(
                    code="NETWORK_TIMEOUT",
                    message=f"HTTP request timed out after {req_timeout}s: {url}",
                    details={"url": url, "timeout": req_timeout, "error": str(exc)},
                )
            )
        except httpx.HTTPError as exc:
            return Err(
                DomainError(
                    code="HTTP_CLIENT_ERROR",
                    message=f"HTTP transport failure: {exc}",
                    details={"url": url, "error": str(exc)},
                )
            )
        except Exception as exc:
            return Err(
                DomainError(
                    code="UNEXPECTED_NETWORK_ERROR",
                    message=f"Unexpected error contacting {url}: {exc}",
                    details={"url": url, "error": str(exc)},
                )
            )
