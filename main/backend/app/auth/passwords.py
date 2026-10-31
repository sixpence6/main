from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from app.errors import ApiError

ARGON2_TIME_COST = 2
ARGON2_MEMORY_COST_KIB = 19_456
ARGON2_PARALLELISM = 1
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16
PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128
WEAK_PASSWORD_LIST_VERSION = "r1-2026-10"
WEAK_PASSWORD_PATH = Path(__file__).with_name("weak_passwords.txt")
HASH_RUNNING_LIMIT = 2
HASH_QUEUED_LIMIT = 8

try:  # pragma: no cover - import failure is exercised through monkeypatching
    from argon2 import PasswordHasher
    from argon2.exceptions import (
        HashingError,
        InvalidHashError,
        VerificationError,
        VerifyMismatchError,
    )
    from argon2.low_level import Type

    ARGON2_AVAILABLE = True
except ImportError:  # pragma: no cover
    PasswordHasher = None  # type: ignore[assignment,misc]
    Type = None  # type: ignore[assignment,misc]
    ARGON2_AVAILABLE = False

    class HashingError(Exception):
        pass

    class InvalidHashError(Exception):
        pass

    class VerificationError(Exception):
        pass

    class VerifyMismatchError(VerificationError):
        pass


T = TypeVar("T")


class AuthUnavailable(RuntimeError):
    """Raised when the password hashing dependency is unavailable."""


class PasswordPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _load_weak_passwords() -> frozenset[str]:
    values: set[str] = set()
    try:
        for line in WEAK_PASSWORD_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                values.add(stripped.casefold())
    except OSError:
        # A missing denylist must not become a permissive fallback.
        raise AuthUnavailable("weak password list unavailable") from None
    return frozenset(values)


_weak_passwords: frozenset[str] | None = None
_weak_lock = threading.Lock()


def weak_passwords() -> frozenset[str]:
    global _weak_passwords
    if _weak_passwords is None:
        with _weak_lock:
            if _weak_passwords is None:
                _weak_passwords = _load_weak_passwords()
    return _weak_passwords


_hasher: PasswordHasher | None = None
_hasher_lock = threading.Lock()


def _get_hasher() -> PasswordHasher:
    global _hasher
    if not ARGON2_AVAILABLE or PasswordHasher is None or Type is None:
        raise AuthUnavailable("argon2-cffi unavailable")
    if _hasher is None:
        with _hasher_lock:
            if _hasher is None:
                _hasher = PasswordHasher(
                    type=Type.ID,
                    time_cost=ARGON2_TIME_COST,
                    memory_cost=ARGON2_MEMORY_COST_KIB,
                    parallelism=ARGON2_PARALLELISM,
                    hash_len=ARGON2_HASH_LEN,
                    salt_len=ARGON2_SALT_LEN,
                )
    return _hasher


_dummy_hash: str | None = None
_dummy_lock = threading.Lock()


def dummy_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        with _dummy_lock:
            if _dummy_hash is None:
                _dummy_hash = _get_hasher().hash("dummy-password-that-is-never-valid-0000")
    return _dummy_hash


def validate_password(password: str) -> None:
    if not isinstance(password, str):
        raise PasswordPolicyError("INVALID_PASSWORD", "密码不合法")
    length = len(password)
    if length < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(
            "PASSWORD_TOO_SHORT", f"密码至少需要 {PASSWORD_MIN_LENGTH} 个 Unicode 码点"
        )
    if length > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(
            "PASSWORD_TOO_LONG", f"密码不能超过 {PASSWORD_MAX_LENGTH} 个 Unicode 码点"
        )
    if password.casefold() in weak_passwords():
        raise PasswordPolicyError("WEAK_PASSWORD", "密码过于常见，请使用更难猜测的密码")


def hash_password(password: str) -> str:
    return _get_hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    hasher = _get_hasher()
    try:
        return hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        return False
    except HashingError as exc:  # pragma: no cover - defensive
        raise AuthUnavailable("password verification failed") from exc


def verify_dummy_password(password: str) -> bool:
    """Perform a real Argon2 verification against a fixed non-matching hash."""

    return verify_password(dummy_hash(), password)


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _get_hasher().check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError):
        # Defensive: an unusable stored hash must never surface as a 500.
        return True


class HashGate:
    """Bounded Argon2 work queue: exactly N running and at most M waiting."""

    def __init__(self, running: int = HASH_RUNNING_LIMIT, queued: int = HASH_QUEUED_LIMIT) -> None:
        self._max_running = running
        self._max_queued = queued
        self._running = 0
        self._waiting = 0
        self._condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._condition:
            if self._running < self._max_running and self._waiting == 0:
                self._running += 1
            elif self._waiting < self._max_queued:
                self._waiting += 1
                while self._running >= self._max_running:
                    await self._condition.wait()
                self._waiting -= 1
                self._running += 1
            else:
                raise ApiError(503, "AUTH_BUSY", "认证服务繁忙，请稍后重试")
        try:
            yield
        finally:
            async with self._condition:
                self._running -= 1
                self._condition.notify_all()

    async def run(self, function: Callable[..., T], *args: Any) -> T:
        async with self.slot():
            return await asyncio.to_thread(function, *args)
