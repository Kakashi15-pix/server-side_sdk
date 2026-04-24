"""Unit tests for server-side API key lifecycle service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from api_keys import ApiKeyRecord, ApiKeyService
from auth import build_api_key_hash


class _Repo:
    def __init__(self):
        self.records = {}
        self.insert_calls = []
        self.expiry_updates = []
        self.touch_calls = []

    async def insert_api_key(self, **kwargs):
        record = ApiKeyRecord(
            id=kwargs["id"],
            user_id=kwargs["user_id"],
            key_hash=kwargs["key_hash"],
            name=kwargs["name"],
            last_used=None,
            expires_at=kwargs["expires_at"],
            created_at=kwargs["created_at"],
            revoked=False,
        )
        self.records[record.id] = record
        self.insert_calls.append(record)
        return record

    async def get_api_key_by_id(self, api_key_id):
        return self.records.get(api_key_id)

    async def revoke_api_key(self, api_key_id):
        record = self.records[api_key_id]
        revoked = ApiKeyRecord(
            id=record.id,
            user_id=record.user_id,
            key_hash=record.key_hash,
            name=record.name,
            last_used=record.last_used,
            expires_at=record.expires_at,
            created_at=record.created_at,
            revoked=True,
        )
        self.records[api_key_id] = revoked
        return revoked

    async def update_api_key_expiry(self, api_key_id, expires_at):
        record = self.records[api_key_id]
        updated = ApiKeyRecord(
            id=record.id,
            user_id=record.user_id,
            key_hash=record.key_hash,
            name=record.name,
            last_used=record.last_used,
            expires_at=expires_at,
            created_at=record.created_at,
            revoked=record.revoked,
        )
        self.records[api_key_id] = updated
        self.expiry_updates.append((api_key_id, expires_at))
        return updated

    async def touch_last_used(self, api_key_id, last_used):
        self.touch_calls.append((api_key_id, last_used))


def test_create_key_returns_raw_once_and_persists_hash():
    repo = _Repo()
    service = ApiKeyService(repo, hmac_secret="unit-secret", ttl_days=1)
    user_id = uuid4()

    created = asyncio.run(service.create_key(user_id=user_id, name="integration"))

    assert created.raw_key.startswith("ca_live_")
    assert created.record.user_id == user_id
    assert created.record.key_hash == build_api_key_hash(created.raw_key, secret="unit-secret")
    assert created.record.key_hash != created.raw_key
    assert not created.record.key_hash.startswith("ca_live_")
    assert "ca_live_" not in created.record.key_hash


def test_revoke_key_marks_record_revoked():
    repo = _Repo()
    service = ApiKeyService(repo, hmac_secret="unit-secret")
    user_id = uuid4()
    created = asyncio.run(service.create_key(user_id=user_id, name="primary"))

    revoked = asyncio.run(service.revoke_key(created.record.id))

    assert revoked.revoked is True


def test_rotate_key_creates_new_key_and_updates_old_expiry():
    repo = _Repo()
    service = ApiKeyService(repo, hmac_secret="unit-secret")
    user_id = uuid4()
    created = asyncio.run(service.create_key(user_id=user_id, name="primary"))

    result = asyncio.run(service.rotate_key(created.record.id, grace_period_seconds=120))

    assert result.rotated_from.id == created.record.id
    assert result.new_key.record.id != created.record.id
    assert result.new_key.raw_key.startswith("ca_live_")
    assert len(repo.expiry_updates) == 1
    _, updated_expiry = repo.expiry_updates[0]
    assert updated_expiry <= datetime.now(timezone.utc) + timedelta(seconds=121)


def test_rotate_key_raises_when_record_missing():
    repo = _Repo()
    service = ApiKeyService(repo, hmac_secret="unit-secret")

    with pytest.raises(KeyError):
        asyncio.run(service.rotate_key(uuid4()))


def test_touch_last_used_forwards_to_repository():
    repo = _Repo()
    service = ApiKeyService(repo, hmac_secret="unit-secret")
    key_id = uuid4()

    asyncio.run(service.touch_last_used(key_id))

    assert len(repo.touch_calls) == 1
    assert repo.touch_calls[0][0] == key_id


def test_format_for_logs_masks_key():
    assert ApiKeyService.format_for_logs("ca_live_example") == "ca_live_****"
