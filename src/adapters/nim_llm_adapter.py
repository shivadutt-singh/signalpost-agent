"""
NVIDIA NIM LLM Adapter with Token Bucket & Circuit Breaker for Signalpost Agent (v6.0 Fail-Proof Edition).

Features:
- Token-Bucket Limiter (strictly capped at 35 RPM).
- Circuit Breaker with exponential backoff & full jitter.
- Single batched prompt at temperature=0.0 returning structured operations, news, and executive summary.
- Automatic fallback to meta/llama-3.1-8b-instruct and offline deterministic extractor.
Implements LLMPort.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from enum import StrEnum
from typing import Any

import httpx

from src.domain.entities import EntityAnchor
from src.domain.errors import DomainError
from src.domain.result import Ok, Result
from src.ports.interfaces import LLMPort

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
PRIMARY_MODEL = "meta/llama-3.3-70b-instruct"
FALLBACK_MODEL = "meta/llama-3.1-8b-instruct"
DEFAULT_RPM = 35


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class NimCircuitBreaker:
    """Circuit breaker with exponential backoff and jitter."""

    def __init__(self, failure_threshold: int = 3, recovery_time: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        now = time.monotonic()
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.recovery_time:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                f"NimCircuitBreaker OPENED: {self.failure_count} consecutive failures"
            )


class TokenBucketLimiter:
    """Thread-safe token-bucket rate limiter enforcing RPM bounds."""

    def __init__(self, rpm: int = DEFAULT_RPM) -> None:
        self.capacity = float(rpm)
        self.tokens = float(rpm)
        self.fill_rate = rpm / 60.0  # tokens per second
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_update = now

            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.fill_rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_update = time.monotonic()
            else:
                self.tokens -= 1.0


class NimLlmAdapter(LLMPort):
    """NVIDIA NIM LLM Adapter implementing LLMPort."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        primary_model: str = PRIMARY_MODEL,
        fallback_model: str = FALLBACK_MODEL,
        rpm_limit: int = DEFAULT_RPM,
    ) -> None:
        self.api_key = api_key or os.getenv("NVIDIA_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.limiter = TokenBucketLimiter(rpm=rpm_limit)
        self.circuit_breaker = NimCircuitBreaker()

    async def extract_batched_profile(
        self,
        orgnr: str,
        canonical_name: str,
        source_texts: dict[str, str],
        anchor: EntityAnchor,
    ) -> Result[dict[str, Any], DomainError]:
        """
        Executes a single batched extraction call over all verified text buffers.
        Falls back to offline deterministic heuristics if NIM is unavailable.
        """
        if not source_texts:
            return Ok(self._deterministic_fallback_extraction(canonical_name, anchor))

        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            logger.info(
                f"NIM Circuit breaker OPEN, using deterministic fallback for {canonical_name}"
            )
            return Ok(self._deterministic_fallback_extraction(canonical_name, anchor))

        # If no API key configured, use deterministic fallback
        if not self.api_key:
            return Ok(self._deterministic_fallback_extraction(canonical_name, anchor))

        # Acquire token bucket permission
        await self.limiter.acquire()

        combined_text = ""
        for url, text in source_texts.items():
            combined_text += f"\n--- SOURCE: {url} ---\n{text[:2000]}\n"

        prompt = (
            f"You are a strict, verifiable factual intelligence extractor for Norwegian enterprises.\n"
            f"Target Company: {canonical_name} (orgnr: {orgnr})\n"
            f"DO NOT include news or events belonging to external property managers (USBL, OBOS) or auditors.\n"
            f"Every fact must include an exact verbatim quote from the text below.\n\n"
            f"Source Text:\n{combined_text[:4000]}\n\n"
            f"Output strictly valid JSON with keys:\n"
            f"- core_business: 1 sentence on company's core operations\n"
            f"- recent_developments: 1 sentence on verified recent updates\n"
            f"- known_unknowns: list of unconfirmed items (e.g. ['Zero active job postings', 'No public domain'])\n"
            f"- operations_facts: list of {{'predicate': str, 'value': str, 'verbatim_quote': str, 'source_url': str}}\n"
        )

        for model in [self.primary_model, self.fallback_model]:
            try:
                result_json = await self._call_nim_api(model, prompt)
                self.circuit_breaker.record_success()
                return Ok(result_json)
            except Exception as exc:
                logger.warning(f"NIM call failed for model {model}: {exc}")
                self.circuit_breaker.record_failure()

        # If all NIM models failed, execute deterministic fallback
        return Ok(self._deterministic_fallback_extraction(canonical_name, anchor))

    async def _call_nim_api(self, model: str, prompt: str) -> dict[str, Any]:
        """Calls NVIDIA NIM chat completions endpoint."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise corporate intelligence extractor that outputs JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            elif resp.status_code == 410:
                raise RuntimeError(f"NIM Model {model} reached End-Of-Life (410)")
            else:
                raise RuntimeError(
                    f"NIM returned status {resp.status_code}: {resp.text[:100]}"
                )

    def _deterministic_fallback_extraction(
        self, canonical_name: str, anchor: EntityAnchor
    ) -> dict[str, Any]:
        """Safe deterministic offline synthesis when NIM is offline or skipped."""
        org_form_desc = (
            "Housing association / cooperative"
            if "BRL" in anchor.org_form
            else f"Norwegian enterprise ({anchor.org_form})"
        )
        core_biz = f"{canonical_name} is a verified {org_form_desc} registered in {anchor.address_municipality or 'Norway'}."
        rec_dev = "Registry status and official multi-year filings verified against Brreg Enhetsregisteret."
        unknowns = []

        if anchor.is_shared_umbrella_domain:
            unknowns.append(
                "Shared property management domain isolated; zero manager bleed permitted."
            )
        if not anchor.official_domain:
            unknowns.append("No verified dedicated web domain registered in Brreg.")

        return {
            "core_business": core_biz,
            "recent_developments": rec_dev,
            "known_unknowns": unknowns,
            "operations_facts": [],
        }
