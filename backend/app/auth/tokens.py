from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid


def new_token() -> str:
    """At least 256 bits of cryptographic randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def source_ref(value: str) -> str:
    """Pseudonymous source identifier used in audit/rate-limit facts."""
    return hashlib.sha256(("source-v1:" + value).encode("utf-8")).hexdigest()


def account_rate_key(normalized_email: str) -> str:
    return hashlib.sha256(("login-account-v1:" + normalized_email).encode("utf-8")).hexdigest()
