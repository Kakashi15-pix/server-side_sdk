from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field

from auth import AuthContext, verify_api_key
from manager import RequestDetails, get_backend_pricing_orchestrator, get_pricing_manager
from routes import AUTH_VERIFY_PATH, CUSTOM_PRICING_PATH, TELEMETRY_FLUSH_PATH

app = FastAPI(title="Server-side SDK API")


class TelemetryRequest(BaseModel):
    timestamp: datetime
    request_id: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TelemetryFlushPayload(BaseModel):
    client_id: str
    batch: List[TelemetryRequest] = Field(default_factory=list)


class CustomPricingPayload(BaseModel):
    model: str
    provider: str
    input_cost_per_1m_tokens: float
    output_cost_per_1m_tokens: float
    cache_creation_cost_per_1m_tokens: Optional[float] = None
    cache_read_cost_per_1m_tokens: Optional[float] = None
    source: Optional[str] = None
    currency: str = "USD"


def _get_orchestrator(request: Request):
    return getattr(request.app.state, "pricing_orchestrator", None) or get_backend_pricing_orchestrator()


def _get_pricing_manager(request: Request):
    return getattr(request.app.state, "pricing_manager", None) or get_pricing_manager()


@app.get(AUTH_VERIFY_PATH)
async def auth_verify(context: AuthContext = Depends(verify_api_key)) -> dict:
    """Lightweight verification endpoint used by the SDK client.

    Returns the minimal identity object the client expects: `user_id` and
    `api_key_id`. The endpoint intentionally exposes no sensitive details.
    """
    return {"user_id": str(context.user_id), "api_key_id": str(context.api_key_id)}


@app.post(TELEMETRY_FLUSH_PATH)
async def telemetry_flush(payload: TelemetryFlushPayload, request: Request) -> dict:
    """Receive SDK request-detail batches and compute backend cost records."""

    batch = [
        RequestDetails(
            timestamp=item.timestamp,
            request_id=item.request_id,
            model=item.model,
            provider=item.provider,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            cache_read_tokens=item.cache_read_tokens,
            cache_creation_tokens=item.cache_creation_tokens,
            stop_reason=item.stop_reason,
            metadata=item.metadata,
        )
        for item in payload.batch
    ]

    processed = _get_orchestrator(request).process_batch(batch)
    return {
        "client_id": payload.client_id,
        "accepted": len(batch),
        "processed": len(processed),
        "records": processed,
    }


@app.post(CUSTOM_PRICING_PATH)
async def custom_pricing(
    payload: CustomPricingPayload,
    request: Request,
    context: AuthContext = Depends(verify_api_key),
) -> dict:
    """Register account-scoped custom pricing supplied by an authenticated SDK client."""

    result = _get_pricing_manager(request).set_custom_pricing(
        model=payload.model,
        provider=payload.provider,
        input_cost_per_1m_tokens=payload.input_cost_per_1m_tokens,
        output_cost_per_1m_tokens=payload.output_cost_per_1m_tokens,
        cache_creation_cost_per_1m_tokens=payload.cache_creation_cost_per_1m_tokens,
        cache_read_cost_per_1m_tokens=payload.cache_read_cost_per_1m_tokens,
        source=payload.source,
        currency=payload.currency,
    )

    return {
        "user_id": str(context.user_id),
        "api_key_id": str(context.api_key_id),
        **result,
    }
