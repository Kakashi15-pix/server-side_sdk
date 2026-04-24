"""Unit tests for key-then-user rate limiting middleware."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from auth import AuthContext
from rate_limit import KeyThenUserRateLimitMiddleware


class _Backend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def allow(self, *, scope: str, identifier: str, cost: int = 1):
        self.calls.append((scope, identifier, cost))
        return self.responses.pop(0)


async def _noop_app(scope, receive, send):
    return None


def test_allows_on_api_key_bucket():
    backend = _Backend([True])
    middleware = KeyThenUserRateLimitMiddleware(_noop_app, backend, cost=2)
    context = AuthContext(user_id=uuid4(), api_key_id=uuid4())

    decision = asyncio.run(middleware._allow_primary_then_fallback(context))

    assert decision.allowed is True
    assert decision.scope == "api_key"
    assert backend.calls[0][0] == "api_key"
    assert backend.calls[0][2] == 2


def test_falls_back_to_user_bucket_when_key_exhausted():
    backend = _Backend([False, True])
    middleware = KeyThenUserRateLimitMiddleware(_noop_app, backend)
    context = AuthContext(user_id=uuid4(), api_key_id=uuid4())

    decision = asyncio.run(middleware._allow_primary_then_fallback(context))

    assert decision.allowed is True
    assert decision.scope == "user"
    assert [c[0] for c in backend.calls] == ["api_key", "user"]


def test_denies_when_both_buckets_exhausted():
    backend = _Backend([False, False])
    middleware = KeyThenUserRateLimitMiddleware(_noop_app, backend)
    context = AuthContext(user_id=uuid4(), api_key_id=uuid4())

    decision = asyncio.run(middleware._allow_primary_then_fallback(context))

    assert decision.allowed is False
    assert decision.scope == "user"


def test_dispatch_skips_backend_without_auth_context():
    backend = _Backend([False])
    middleware = KeyThenUserRateLimitMiddleware(_noop_app, backend)
    request = SimpleNamespace(state=SimpleNamespace())

    async def _next(_request):
        return Response(status_code=204)

    response = asyncio.run(middleware.dispatch(request, _next))

    assert response.status_code == 204
    assert backend.calls == []


def test_dispatch_raises_429_when_rate_limited():
    backend = _Backend([False, False])
    middleware = KeyThenUserRateLimitMiddleware(_noop_app, backend)
    request = SimpleNamespace(
        state=SimpleNamespace(auth_context=AuthContext(user_id=uuid4(), api_key_id=uuid4()))
    )

    async def _next(_request):
        return Response(status_code=200)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(middleware.dispatch(request, _next))

    assert exc.value.status_code == 429
    assert exc.value.detail["error"] == "rate_limited"
