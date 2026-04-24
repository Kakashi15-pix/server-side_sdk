"""Rate-limiting middleware skeleton with per-key primary and per-user fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

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


class KeyThenUserRateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce primary per-key limits before falling back to per-user limits.

    The middleware expects auth context to be attached earlier in the request flow.
    """

    def __init__(self, app, backend: RateLimitBackend, cost: int = 1) -> None:
        super().__init__(app)
        self.backend = backend
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
