"""Rate-limiting middleware skeleton with per-key primary and per-user fallback."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple, runtime_checkable

from fastapi import HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from auth import AuthContext


@dataclass(frozen=True)
class RateLimitDecision:
    """Simple decision object for debugging and tests."""

    allowed: bool
    scope: str
    identifier: str


@runtime_checkable
class RateLimitBackend(Protocol):
    """Abstract storage engine for quota checks."""

    async def allow(self, *, scope: str, identifier: str, cost: int = 1) -> bool:
        """Consume quota for a given identity bucket."""


@dataclass
class _TokenBucket:
    capacity: int
    refill_rate: float
    tokens: float
    last_refill: float

    def consume(self, cost: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


class InMemoryRateLimitBackend:
    """Pure FastAPI-friendly token bucket backend with no external dependencies."""

    def __init__(self, capacity: int = 60, refill_rate: float = 1.0) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._buckets: Dict[Tuple[str, str], _TokenBucket] = {}
        self._lock = threading.RLock()

    async def allow(self, *, scope: str, identifier: str, cost: int = 1) -> bool:
        with self._lock:
            bucket = self._buckets.get((scope, identifier))
            if bucket is None:
                bucket = _TokenBucket(
                    capacity=self.capacity,
                    refill_rate=self.refill_rate,
                    tokens=float(self.capacity),
                    last_refill=time.monotonic(),
                )
                self._buckets[(scope, identifier)] = bucket

            return bucket.consume(cost=cost)


class KeyThenUserRateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce primary per-key limits before falling back to per-user limits.

    The middleware expects auth context to be attached earlier in the request flow.
    """

    def __init__(
        self,
        app,
        backend: Optional[RateLimitBackend] = None,
        cost: int = 1,
    ) -> None:
        super().__init__(app)
        self.backend = backend or InMemoryRateLimitBackend()
        self.cost = cost

    async def dispatch(self, request: Request, call_next) -> Response:
        """Check rate limits before the route handler runs."""

        context = self._resolve_context(request)
        if context is not None:
            decision = await self._allow_primary_then_fallback(context)
            if not decision.allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"error": "rate_limited"},
                )

        return await call_next(request)

    async def _allow_primary_then_fallback(self, context: AuthContext) -> RateLimitDecision:
        """Check the API key bucket first, then the user bucket."""

        key_identifier = str(context.api_key_id)
        if await self.backend.allow(scope="api_key", identifier=key_identifier, cost=self.cost):
            return RateLimitDecision(True, "api_key", key_identifier)

        user_identifier = str(context.user_id)
        if await self.backend.allow(scope="user", identifier=user_identifier, cost=self.cost):
            return RateLimitDecision(True, "user", user_identifier)

        return RateLimitDecision(False, "user", user_identifier)

    @staticmethod
    def _resolve_context(request: Request) -> Optional[AuthContext]:
        """Read auth context from request state if authentication already resolved it."""

        context = getattr(request.state, "auth_context", None)
        if isinstance(context, AuthContext):
            return context
        return None
