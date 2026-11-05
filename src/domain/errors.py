"""
Structured Domain Error Taxonomy for Signalpost Agent (v6.0 Fail-Proof Edition).

All domain and adapter errors inherit from DomainError, allowing exhaustive matching
and zero unhandled crashes across the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainError:
    """Base class for all domain errors."""

    code: str = "DOMAIN_ERROR"
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.details:
            return f"[{self.code}] {self.message} (details: {self.details})"
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True, slots=True, kw_only=True)
class InvalidOrgnrError(DomainError):
    """Raised when an orgnr fails Modulo-11 checksum or formatting."""

    code: str = "INVALID_ORGNR"


@dataclass(frozen=True, slots=True, kw_only=True)
class EntityNotFoundError(DomainError):
    """Raised when an organization is not found in Brreg Enhetsregisteret."""

    code: str = "ENTITY_NOT_FOUND"


@dataclass(frozen=True, slots=True, kw_only=True)
class PoisonEntityBleedError(DomainError):
    """Raised when content from external managers (USBL/OBOS/Auditor) bleeds into target entity."""

    code: str = "POISON_ENTITY_BLEED"


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialReconciliationError(DomainError):
    """Raised when accounting numbers fail parsing, digit verification, or double-entry rules."""

    code: str = "FINANCIAL_RECONCILIATION_FAILED"


@dataclass(frozen=True, slots=True, kw_only=True)
class FactCriticVerificationError(DomainError):
    """Raised when a candidate fact fails verbatim quote, number lock, or anti-bleed check."""

    code: str = "FACT_CRITIC_VERIFICATION_FAILED"


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpBudgetExceededError(DomainError):
    """Raised when the maximum HTTP requests per company (18) is exhausted."""

    code: str = "HTTP_BUDGET_EXCEEDED"


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkTimeoutError(DomainError):
    """Raised when an outbound HTTP request exceeds its deadline."""

    code: str = "NETWORK_TIMEOUT"


@dataclass(frozen=True, slots=True, kw_only=True)
class RateLimitExceededError(DomainError):
    """Raised when an external API rate limit (e.g. 429) is hit and cannot be retried."""

    code: str = "RATE_LIMIT_EXCEEDED"


@dataclass(frozen=True, slots=True, kw_only=True)
class CircuitBreakerOpenError(DomainError):
    """Raised when NIM LLM circuit breaker is in OPEN state."""

    code: str = "CIRCUIT_BREAKER_OPEN"


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageError(DomainError):
    """Raised when atomic profile persistence or SQLite WAL logging fails."""

    code: str = "STORAGE_ERROR"


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsingError(DomainError):
    """Raised when HTML, JSON, or schema parsing fails unexpectedly."""

    code: str = "PARSING_ERROR"
