"""Route tests for SDK-facing backend endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import CustomPricingPayload, TelemetryFlushPayload, app, custom_pricing, telemetry_flush
from auth import AuthContext
from routes import AUTH_VERIFY_PATH, CUSTOM_PRICING_PATH, TELEMETRY_FLUSH_PATH


@dataclass
class _Record:
    id: object
    user_id: object
    revoked: bool
    expires_at: datetime


class _ApiKeyService:
    def __init__(self):
        self.record = _Record(
            id=uuid4(),
            user_id=uuid4(),
            revoked=False,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    async def get_api_key_by_hash(self, key_hash: str):
        return self.record

    async def touch_last_used(self, api_key_id):
        return None


class _Orchestrator:
    def __init__(self):
        self.received = []

    def process_batch(self, batch):
        self.received.extend(batch)
        return [
            {
                "request_id": item.request_id,
                "provider": item.provider,
                "model": item.model,
                "usage": {
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "cache_creation_tokens": item.cache_creation_tokens,
                    "cache_read_tokens": item.cache_read_tokens,
                },
                "cost": {"total_cost": 0.001},
                "metadata": item.metadata,
            }
            for item in batch
        ]


class _PricingManager:
    def __init__(self):
        self.calls = []

    def set_custom_pricing(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "model": kwargs["model"],
            "provider": kwargs["provider"],
            "pricing": {
                "input_cost_per_1m_tokens": kwargs["input_cost_per_1m_tokens"],
                "output_cost_per_1m_tokens": kwargs["output_cost_per_1m_tokens"],
                "currency": kwargs["currency"],
            },
        }


@pytest.fixture(autouse=True)
def _configure_app(monkeypatch):
    monkeypatch.setenv("CA_KEY_HMAC_SECRET", "unit-test-secret")
    app.state.api_key_service = _ApiKeyService()
    app.state.api_key_cache = None
    app.state.pricing_orchestrator = _Orchestrator()
    app.state.pricing_manager = _PricingManager()
    yield
    for name in [
        "api_key_service",
        "api_key_cache",
        "pricing_orchestrator",
        "pricing_manager",
    ]:
        if hasattr(app.state, name):
            delattr(app.state, name)


def test_auth_verify_route_returns_sdk_identity():
    paths = {route.path for route in app.routes}

    assert AUTH_VERIFY_PATH in paths


@pytest.mark.anyio
async def test_telemetry_flush_route_processes_sdk_batch():
    payload = TelemetryFlushPayload.model_validate(
        {
            "client_id": "client_123",
            "batch": [
                {
                    "timestamp": "2026-01-01T00:00:00",
                    "request_id": "req_123",
                    "model": "custom-model",
                    "provider": "custom-provider",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "metadata": {"method": "responses.create"},
                }
            ],
        }
    )
    request = SimpleNamespace(app=app)

    result = await telemetry_flush(payload, request)

    assert result["client_id"] == "client_123"
    assert result["accepted"] == 1
    assert result["processed"] == 1
    assert result["records"][0]["request_id"] == "req_123"


@pytest.mark.anyio
async def test_custom_pricing_route_registers_authenticated_override():
    payload = CustomPricingPayload.model_validate(
        {
            "model": "custom-model",
            "provider": "custom-provider",
            "input_cost_per_1m_tokens": 1.25,
            "output_cost_per_1m_tokens": 2.50,
            "currency": "USD",
        }
    )
    request = SimpleNamespace(app=app)
    context = AuthContext(
        user_id=app.state.api_key_service.record.user_id,
        api_key_id=app.state.api_key_service.record.id,
    )

    result = await custom_pricing(payload, request, context)

    assert result["model"] == "custom-model"
    assert result["provider"] == "custom-provider"
    assert result["pricing"]["input_cost_per_1m_tokens"] == 1.25
    assert app.state.pricing_manager.calls[0]["model"] == "custom-model"


def test_sdk_destination_routes_are_registered():
    paths = {route.path for route in app.routes}

    assert AUTH_VERIFY_PATH in paths
    assert TELEMETRY_FLUSH_PATH in paths
    assert CUSTOM_PRICING_PATH in paths
