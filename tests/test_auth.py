"""Unit tests for server-side API-key authentication helpers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

import auth


@pytest.fixture(autouse=True)
def _set_hmac_secret(monkeypatch):
    monkeypatch.setenv("CA_KEY_HMAC_SECRET", "unit-test-secret")


@dataclass
class _Record:
    id: object
    user_id: object
    revoked: bool
    expires_at: datetime


class _Service:
    def __init__(self, record=None):
        self.record = record
        self.lookup_calls = []
        self.touched = []

    async def get_api_key_by_hash(self, key_hash: str):
        self.lookup_calls.append(key_hash)
        return self.record

    async def touch_last_used(self, api_key_id):
        self.touched.append(api_key_id)


class _Cache:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []

    def get(self, key):
        return self.cached

    def set(self, cache_key, value, ttl_seconds):
        self.set_calls.append((cache_key, value, ttl_seconds))


def _request_with(auth_header: str | None, service: _Service, cache=None):
    headers = {}
    if auth_header is not None:
        headers["Authorization"] = auth_header
    app = SimpleNamespace(state=SimpleNamespace(api_key_service=service, api_key_cache=cache))
    return SimpleNamespace(headers=headers, app=app, state=SimpleNamespace())


def test_build_api_key_hash_uses_hmac_sha256():
    key_hash = auth.build_api_key_hash("ca_live_secret", secret="unit-secret")
    assert len(key_hash) == 64
    assert key_hash == auth.build_api_key_hash("ca_live_secret", secret="unit-secret")
    assert key_hash != auth.build_api_key_hash("ca_live_secret", secret="other-secret")


def test_mask_api_key_is_safe_for_logs():
    assert auth.mask_api_key("ca_live_abcd") == "ca_live_****"
    assert auth.mask_api_key("something_else") == "ca_live_****"


def test_verify_api_key_missing_header_raises_401():
    request = _request_with(None, _Service())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.verify_api_key(request))

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_verify_api_key_uses_cache_when_present():
    context = auth.AuthContext(user_id=uuid4(), api_key_id=uuid4())
    cache = _Cache(cached=context)
    service = _Service(record=None)
    request = _request_with("Bearer ca_live_cached", service, cache=cache)

    result = asyncio.run(auth.verify_api_key(request))

    assert result == context
    assert service.lookup_calls == []
    assert request.state.auth_context == context


def test_verify_api_key_rejects_revoked_key():
    record = _Record(
        id=uuid4(),
        user_id=uuid4(),
        revoked=True,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    service = _Service(record=record)
    request = _request_with("Bearer ca_live_revoked", service)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.verify_api_key(request))

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error"] == "key_revoked"


def test_verify_api_key_rejects_expired_key_after_clock_skew():
    record = _Record(
        id=uuid4(),
        user_id=uuid4(),
        revoked=False,
        expires_at=datetime.now(timezone.utc)
        - timedelta(seconds=auth.DEFAULT_CLOCK_SKEW_SECONDS + 1),
    )
    service = _Service(record=record)
    request = _request_with("Bearer ca_live_expired", service)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.verify_api_key(request))

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error"] == "key_expired"


def test_verify_api_key_rejects_at_exact_clock_skew_boundary():
    record = _Record(
        id=uuid4(),
        user_id=uuid4(),
        revoked=False,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=auth.DEFAULT_CLOCK_SKEW_SECONDS),
    )
    service = _Service(record=record)
    request = _request_with("Bearer ca_live_boundary", service)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.verify_api_key(request))

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["error"] == "key_expired"


def test_verify_api_key_allows_just_inside_clock_skew_window(monkeypatch):
    record = _Record(
        id=uuid4(),
        user_id=uuid4(),
        revoked=False,
        expires_at=datetime.now(timezone.utc)
        - timedelta(seconds=auth.DEFAULT_CLOCK_SKEW_SECONDS)
        + timedelta(seconds=2),
    )
    service = _Service(record=record)
    cache = _Cache(cached=None)
    request = _request_with("Bearer ca_live_inside_skew", service, cache=cache)

    monkeypatch.setattr(auth.asyncio, "create_task", lambda coro: (coro.close(), None)[1])
    result = asyncio.run(auth.verify_api_key(request))

    assert result.user_id == record.user_id
    assert result.api_key_id == record.id


def test_verify_api_key_cache_hit_has_low_latency():
    context = auth.AuthContext(user_id=uuid4(), api_key_id=uuid4())
    cache = _Cache(cached=context)
    service = _Service(record=None)
    request = _request_with("Bearer ca_live_cached_latency", service, cache=cache)

    start = time.perf_counter()
    result = asyncio.run(auth.verify_api_key(request))
    elapsed = time.perf_counter() - start

    assert result == context
    assert service.lookup_calls == []
    assert elapsed < 0.05


def test_verify_api_key_success_sets_context_and_cache(monkeypatch):
    record = _Record(
        id=uuid4(),
        user_id=uuid4(),
        revoked=False,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    service = _Service(record=record)
    cache = _Cache(cached=None)
    request = _request_with("Bearer ca_live_valid", service, cache=cache)

    created = []

    def _capture_task(coro):
        created.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(auth.asyncio, "create_task", _capture_task)

    result = asyncio.run(auth.verify_api_key(request))

    assert result.user_id == record.user_id
    assert result.api_key_id == record.id
    assert request.state.auth_context == result
    assert len(cache.set_calls) == 1
    cache_key = cache.set_calls[0][0]
    assert cache_key == auth.build_api_key_hash("ca_live_valid")
    assert "ca_live_valid" not in cache_key
    assert cache.set_calls[0][2] == auth.DEFAULT_CACHE_TTL_SECONDS
    assert len(created) == 1
