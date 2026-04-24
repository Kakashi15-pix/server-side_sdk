"""FastAPI API-key authentication dependency and shared security helpers.

Security checklist:
- HMAC-SHA256 with CA_KEY_HMAC_SECRET; never persist raw keys.
- Mask API keys in logs as ca_live_****.
- Allow 30-60s clock skew on expiry checks.
- Keep any read-through cache TTL at or below 60s.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol, runtime_checkable
from uuid import UUID

from fastapi import HTTPException, Request, status


API_KEY_PREFIX = "ca_live_"
AUTH_HEADER = "Authorization"
DEFAULT_CLOCK_SKEW_SECONDS = 60
DEFAULT_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class AuthContext:
    """Identity extracted from a validated API key."""

    user_id: UUID
    api_key_id: UUID


@runtime_checkable
class ApiKeyRecordLike(Protocol):
    """Minimal shape required from the key repository result."""

    id: UUID
    user_id: UUID
    revoked: bool
    expires_at: datetime


@runtime_checkable
class ApiKeyLookupService(Protocol):
    """Backend contract used by the FastAPI dependency."""

    async def get_api_key_by_hash(self, key_hash: str) -> Optional[ApiKeyRecordLike]:
        """Return a matching key record or None."""

    async def touch_last_used(self, api_key_id: UUID) -> None:
        """Persist last_used asynchronously without blocking the request path."""


@runtime_checkable
class AuthContextCache(Protocol):
    """Optional read-through cache for validated API keys."""

    def get(self, cache_key: str) -> Optional[AuthContext]:
        """Return a cached auth context when present and still valid."""

    def set(self, cache_key: str, value: AuthContext, ttl_seconds: int) -> None:
        """Store an auth context for a bounded time window."""


def _get_hmac_secret(secret: Optional[str] = None) -> bytes:
    """Load the server secret used to derive the stored key hash."""

    resolved_secret = secret or os.getenv("CA_KEY_HMAC_SECRET")
    if not resolved_secret:
        raise RuntimeError("CA_KEY_HMAC_SECRET is required")
    return resolved_secret.encode("utf-8")


def build_api_key_hash(raw_key: str, secret: Optional[str] = None) -> str:
    """Derive the stable HMAC-SHA256 hash stored in api_keys.key_hash."""

    return hmac.new(_get_hmac_secret(secret), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_api_key(raw_key: str) -> str:
    """Return the only safe log representation for a live key."""

    if raw_key.startswith(API_KEY_PREFIX):
        return f"{API_KEY_PREFIX}****"
    return "ca_live_****"


def _extract_bearer_token(request: Request) -> str:
    """Extract the raw API key from a Bearer Authorization header."""

    header_value = request.headers.get(AUTH_HEADER)
    if not header_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key"},
        )

    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key"},
        )

    return token.strip()


def _get_service(request: Request) -> ApiKeyLookupService:
    """Resolve the key lookup service from app state."""

    service = getattr(request.app.state, "api_key_service", None)
    if service is None:
        raise RuntimeError("request.app.state.api_key_service is not configured")
    return service


def _get_cache(request: Request) -> Optional[Any]:
    """Resolve the optional identity cache from app state."""

    return getattr(request.app.state, "api_key_cache", None)


async def _touch_last_used(service: ApiKeyLookupService, api_key_id: UUID) -> None:
    """Fire-and-forget last_used persistence without blocking the response path."""

    try:
        await service.touch_last_used(api_key_id)
    except Exception:
        # Security-sensitive path: never fail authentication because telemetry writes failed.
        return


async def verify_api_key(request: Request) -> AuthContext:
    """Validate an API key and return request-scoped auth context.

    The function intentionally does not persist raw keys or emit them to logs.
    """

    raw_key = _extract_bearer_token(request)
    key_hash = build_api_key_hash(raw_key)
    service = _get_service(request)
    cache = _get_cache(request)

    if cache is not None:
        cached_context = cache.get(key_hash)
        if cached_context is not None:
            request.state.auth_context = cached_context
            return cached_context

    record = await service.get_api_key_by_hash(key_hash)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_api_key"},
        )

    now = datetime.now(timezone.utc)
    if record.revoked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "key_revoked"},
        )

    if record.expires_at + timedelta(seconds=DEFAULT_CLOCK_SKEW_SECONDS) <= now:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "key_expired"},
        )

    context = AuthContext(user_id=record.user_id, api_key_id=record.id)
    request.state.auth_context = context

    if cache is not None:
        cache.set(key_hash, context, DEFAULT_CACHE_TTL_SECONDS)

    # Never block the request path on last_used persistence.
    asyncio.create_task(_touch_last_used(service, record.id))
    return context
