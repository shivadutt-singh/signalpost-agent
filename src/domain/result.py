"""
Railway-Oriented Result Monad for Signalpost Agent (v6.0 Fail-Proof Edition).

Provides algebraic Ok[T] and Err[E] types to guarantee that pipeline workers
never terminate with uncaught exceptions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, NoReturn, TypeVar

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    """Represents a successful outcome carrying value T."""

    value: T
    is_ok: bool = True
    is_err: bool = False

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self.value

    def unwrap_err(self) -> NoReturn:
        raise ValueError(f"Called unwrap_err on Ok: {self.value!r}")

    def map(self, fn: Callable[[T], U]) -> Result[U, E]:
        return Ok(fn(self.value))

    def map_err(self, fn: Callable[[E], F]) -> Result[T, F]:
        # Ok ignores error mapping
        return Ok(self.value)

    def and_then(self, fn: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return fn(self.value)

    def match(self, ok_fn: Callable[[T], U], err_fn: Callable[[E], U]) -> U:
        return ok_fn(self.value)


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    """Represents a failed outcome carrying error E."""

    error: E
    is_ok: bool = False
    is_err: bool = True

    def unwrap(self) -> NoReturn:
        raise ValueError(f"Called unwrap on Err: {self.error!r}")

    def unwrap_or(self, default: T) -> T:
        return default

    def unwrap_err(self) -> E:
        return self.error

    def map(self, fn: Callable[[T], U]) -> Result[U, E]:
        # Err ignores success mapping
        return Err(self.error)

    def map_err(self, fn: Callable[[E], F]) -> Result[T, F]:
        return Err(fn(self.error))

    def and_then(self, fn: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return Err(self.error)

    def match(self, ok_fn: Callable[[T], U], err_fn: Callable[[E], U]) -> U:
        return err_fn(self.error)


Result = Ok[T] | Err[E]
