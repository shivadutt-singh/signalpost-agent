"""
Abstract Port Interfaces (Dependency Inversion) for Signalpost Agent (v6.0 Fail-Proof Edition).

Using typing.Protocol so application logic and state machines depend strictly on
abstractions, guaranteeing 100% testability with zero network I/O in unit tests.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.domain.entities import AnnualAccount, CompanyProfile, EntityAnchor, JobPosting
from src.domain.errors import DomainError
from src.domain.result import Result


@runtime_checkable
class RegistryPort(Protocol):
    """Port for official Brreg Enhetsregisteret registry and governance lookups."""

    async def get_enhet(
        self, orgnr: str
    ) -> Result[dict[str, Any] | None, DomainError]: ...

    async def get_roller(
        self, orgnr: str
    ) -> Result[list[dict[str, Any]], DomainError]: ...

    async def get_underenheter(
        self, orgnr: str
    ) -> Result[list[dict[str, Any]], DomainError]: ...

    async def get_oppdateringer(
        self, orgnr: str
    ) -> Result[list[dict[str, Any]], DomainError]: ...


@runtime_checkable
class FinancialsPort(Protocol):
    """Port for Brreg Regnskapsregisteret multi-year financial statements."""

    async def get_multi_year_financials(
        self, orgnr: str
    ) -> Result[list[AnnualAccount], DomainError]: ...


@runtime_checkable
class JobsPort(Protocol):
    """Port for dual-source job listings (NAV public feed + official careers scraper)."""

    async def get_verified_jobs(
        self,
        orgnr: str,
        anchor: EntityAnchor,
        official_domain: str | None = None,
    ) -> Result[list[JobPosting], DomainError]: ...


@runtime_checkable
class WebScraperPort(Protocol):
    """Port for budget-locked, entity-isolated web page harvesting."""

    async def fetch_page(self, url: str) -> Result[str, DomainError]: ...

    async def harvest_verified_pages(
        self,
        base_url: str,
        anchor: EntityAnchor,
        max_subpages: int = 3,
    ) -> Result[dict[str, str], DomainError]: ...


@runtime_checkable
class LLMPort(Protocol):
    """Port for bounded NVIDIA NIM LLM structured extraction with circuit breaker."""

    async def extract_batched_profile(
        self,
        orgnr: str,
        canonical_name: str,
        source_texts: dict[str, str],
        anchor: EntityAnchor,
    ) -> Result[dict[str, Any], DomainError]: ...


@runtime_checkable
class ProfileStorePort(Protocol):
    """Port for atomic profile persistence and SQLite WAL crash-resumption."""

    def save_profile(self, profile: CompanyProfile) -> Result[None, DomainError]: ...

    def load_profile(
        self, orgnr: str
    ) -> Result[CompanyProfile | None, DomainError]: ...

    def list_all_orgnrs(self) -> Result[list[str], DomainError]: ...
