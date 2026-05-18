"""API key lifecycle service for the FastAPI backend."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID, uuid4

from auth import build_api_key_hash, mask_api_key


API_KEY_PREFIX = "ca_live_"
DEFAULT_TTL_DAYS = 30


@dataclass(frozen=True)
class ApiKeyRecord:
    id: UUID
    user_id: UUID
    key_hash: str
    name: str
    last_used: Optional[datetime]
    expires_at: datetime
    created_at: datetime
    revoked: bool


@dataclass(frozen=True)
class CreatedApiKey:
    """Return value that exposes the raw key exactly once."""

    raw_key: str
    record: ApiKeyRecord


@dataclass(frozen=True)
class RotationResult:
    new_key: CreatedApiKey
    rotated_from: ApiKeyRecord


@runtime_checkable
class ApiKeyRepository(Protocol):
    """Backend repository contract used by the service layer."""

    async def insert_api_key(
        self,
        *,
        id: UUID,
        user_id: UUID,
        key_hash: str,
        name: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> ApiKeyRecord:
        """Persist a new key and return the stored record."""

async def get_api_key_by_id(self, api_key_id: UUID) -> Optional[ApiKeyRecord]:

 async def revoke_api_key(self, api_key_id: UUID) -> ApiKeyRecord:

  async def update_api_key_expiry(self, api_key_id: UUID, expires_at: datetime) -> ApiKeyRecord:

   async def touch_last_used(self, api_key_id: UUID, last_used: datetime) -> None:



    class ApiKeyService:
    

     def __init__(
        self,
        repository: ApiKeyRepository,
        *,
        hmac_secret: Optional[str] = None,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        self.repository = repository
        self.hmac_secret = hmac_secret
        self.ttl_days = ttl_days

    def _generate_raw_key(self) -> str:
        """Generate a key with a fixed prefix and a cryptographically random suffix."""

        return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"

    async def create_key(
        self,
        *,
        user_id: UUID,
        name: str,
        expires_at: Optional[datetime] = None,
    ) -> CreatedApiKey:
        """Create a brand-new key and return the raw secret once."""

        now = datetime.now(timezone.utc)
        effective_expires_at = expires_at or (now + timedelta(days=self.ttl_days))
        raw_key = self._generate_raw_key()
        key_hash = build_api_key_hash(raw_key, secret=self.hmac_secret)

        record = await self.repository.insert_api_key(
            id=uuid4(),
            user_id=user_id,
            key_hash=key_hash,
            name=name,
            expires_at=effective_expires_at,
            created_at=now,
        )
        return CreatedApiKey(raw_key=raw_key, record=record)

    async def revoke_key(self, api_key_id: UUID) -> ApiKeyRecord:
        """Revoke a key immediately."""

        return await self.repository.revoke_api_key(api_key_id)

    async def rotate_key(self, api_key_id: UUID, grace_period_seconds: int = 600) -> RotationResult:
        """Rotate a key while keeping the old one alive for a bounded grace period."""

        existing = await self.repository.get_api_key_by_id(api_key_id)
        if existing is None:
            raise KeyError(f"API key {api_key_id} was not found")

        now = datetime.now(timezone.utc)
        new_key = await self.create_key(user_id=existing.user_id, name=existing.name)

        grace_deadline = now + timedelta(seconds=grace_period_seconds)
        await self.repository.update_api_key_expiry(api_key_id, expires_at=grace_deadline)

        return RotationResult(new_key=new_key, rotated_from=existing)

    async def touch_last_used(self, api_key_id: UUID) -> None:
        """Forward the last-used update to the repository."""

        await self.repository.touch_last_used(api_key_id, datetime.now(timezone.utc))

    @staticmethod
    def format_for_logs(raw_key: str) -> str:
        """Return the only safe representation for logs and audit trails."""

        return mask_api_key(raw_key)
