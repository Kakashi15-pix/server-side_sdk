"""
Pricing configuration and management for LLM providers.
Implements signal-plus-pull model with primary upstream sync and local fallback.
"""
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable
from pathlib import Path
import requests
import logging

from pricing.extractors import get_extractor, CostBreakdown
from pricing.aggregator import RequestDetails, RequestDetailsBuffer, get_request_buffer

logger = logging.getLogger(__name__)

# Upstream pricing source
LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
PRICING_SYNC_INTERVAL_HOURS = 336  # 14 days
PRICING_CACHE_PATH = Path(__file__).parent / "pricing_cache.json"
PRICING_SYNC_STATE_PATH = Path(__file__).parent / "pricing_sync.json"


class PricingManager:
    """Manages pricing data with upstream sync and local fallback."""

    def __init__(self):
        self.pricing_data: Dict[str, Dict[str, Any]] = {}
        self.custom_pricing_data: Dict[str, Dict[str, Any]] = {}
        self.sync_state = self._load_sync_state()
        self._load_pricing()
        
        # Initial cold start sync if no cache exists
        if not self.pricing_data:
            logger.info("No local pricing cache found. Forcing initial sync from LiteLLM.")
            self.sync_from_upstream()

    def _load_sync_state(self) -> Dict[str, Any]:
       
        if PRICING_SYNC_STATE_PATH.exists():
            try:
                with open(PRICING_SYNC_STATE_PATH) as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Failed to load sync state: {e}")
        return {"last_sync": None, "last_hash": None, "sync_failures": 0}

    def _save_sync_state(self) -> None:
        """Save sync state tracking."""
        try:
            with open(PRICING_SYNC_STATE_PATH, "w") as f:
                json.dump(self.sync_state, f)
        except Exception as e:
            logger.warning(f"Failed to save sync state: {e}")

    def _should_sync(self) -> bool:
        """Check if sync should occur based on interval."""
        last_sync = self.sync_state.get("last_sync")
        if not last_sync:
            return True
        
        last_sync_dt = datetime.fromisoformat(last_sync)
        return datetime.utcnow() - last_sync_dt > timedelta(hours=PRICING_SYNC_INTERVAL_HOURS)

    def _get_hash(self, data: Dict[str, Any]) -> str:
        """Compute hash of pricing data for change detection."""
        json_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def sync_from_upstream(self) -> bool:
        """
        Sync pricing from LiteLLM upstream.
        Returns True if successful, False otherwise (fallback to local).
        """
        if not self._should_sync():
            logger.debug("Pricing sync interval not reached")
            return True

        try:
            logger.debug(f"Syncing pricing from {LITELLM_PRICING_URL}")
            response = requests.get(LITELLM_PRICING_URL, timeout=10)
            response.raise_for_status()
            
            upstream_data = response.json()
            current_hash = self._get_hash(upstream_data)
            
            # Only update if content changed
            if current_hash != self.sync_state.get("last_hash"):
                self.pricing_data = upstream_data
                self.sync_state.update({
                    "last_sync": datetime.utcnow().isoformat(),
                    "last_hash": current_hash,
                    "sync_failures": 0
                })
                self._save_sync_state()
                logger.info("Pricing data synced successfully")
                return True
            else:
                logger.debug("Pricing data unchanged from upstream")
                self.sync_state["last_sync"] = datetime.utcnow().isoformat()
                self._save_sync_state()
                return True
                
        except Exception as e:
            logger.warning(f"Failed to sync pricing: {e}")
            self.sync_state["sync_failures"] = self.sync_state.get("sync_failures", 0) + 1
            self._save_sync_state()
            return False

    def _load_pricing(self) -> None:
        """Load pricing from cache or bundled file."""
        # Try cache first
        if PRICING_CACHE_PATH.exists():
            try:
                with open(PRICING_CACHE_PATH) as f:
                    self.pricing_data = json.load(f)
                logger.debug("Loaded pricing from cache")
                return
            except Exception as e:
                logger.debug(f"Failed to load pricing cache: {e}")

        logger.warning("No pricing data available in cache")
        self.pricing_data = {}

    def _save_cache(self) -> None:
        """Save pricing data to cache."""
        try:
            with open(PRICING_CACHE_PATH, "w") as f:
                json.dump(self.pricing_data, f)
        except Exception as e:
            logger.warning(f"Failed to save pricing cache: {e}")

    def get_pricing(self, model: str, provider: str = None) -> Optional[Dict[str, Any]]:
        """
        Get pricing for a model.
        
        Args:
            model: Model identifier (e.g., 'claude-3-opus-20240229')
            provider: Optional provider name (inferred from model if not provided)
        
        Returns:
            Pricing dict with input_cost_per_1m_tokens, output_cost_per_1m_tokens, etc.
            None if model not found.
        """
        if self.custom_pricing_data:
            if model in self.custom_pricing_data:
                return self.custom_pricing_data[model]

            if provider:
                provider_model = f"{provider}/{model}"
                if provider_model in self.custom_pricing_data:
                    return self.custom_pricing_data[provider_model]

        if not self.pricing_data:
            return None

        # Try exact match first
        if model in self.pricing_data:
            return self.pricing_data[model]

        # If provider given, try provider-prefixed lookup
        if provider:
            provider_model = f"{provider}/{model}"
            if provider_model in self.pricing_data:
                return self.pricing_data[provider_model]

        logger.warning(f"Pricing not found for model: {model}")
        return None

    def set_custom_pricing(
        self,
        *,
        model: str,
        provider: Optional[str] = None,
        input_cost_per_1m_tokens: float,
        output_cost_per_1m_tokens: float,
        cache_creation_cost_per_1m_tokens: Optional[float] = None,
        cache_read_cost_per_1m_tokens: Optional[float] = None,
        source: Optional[str] = None,
        currency: str = "USD",
    ) -> Dict[str, Dict[str, Any]]:
        """
        Register a client-specific pricing override.

        This is the custom-pricing path for onboarding flows where the client
        provides their own rates (for example, from historical invoices).
        The override is checked before falling back to LiteLLM pricing.
        """
        pricing_entry: Dict[str, Any] = {
            "input_cost_per_1m_tokens": input_cost_per_1m_tokens,
            "output_cost_per_1m_tokens": output_cost_per_1m_tokens,
            "currency": currency,
        }

        if cache_creation_cost_per_1m_tokens is not None:
            pricing_entry["cache_creation_cost_per_1m_tokens"] = cache_creation_cost_per_1m_tokens
        if cache_read_cost_per_1m_tokens is not None:
            pricing_entry["cache_read_cost_per_1m_tokens"] = cache_read_cost_per_1m_tokens
        if source is not None:
            pricing_entry["source"] = source

        self.custom_pricing_data[model] = pricing_entry
        if provider:
            self.custom_pricing_data[f"{provider}/{model}"] = pricing_entry

        return {
            "model": model,
            "provider": provider,
            "pricing": pricing_entry,
        }

    def update_from_response(self, data: Dict[str, Any]) -> None:
        """Update pricing cache from upstream data."""
        if data:
            self.pricing_data = data
            self._save_cache()


class BackendPricingOrchestrator:
    """
    Server-side orchestrator for interceptor -> aggregator -> extractor flow.

    Interceptor buffers raw response payloads. On flush, this orchestrator:
    1. extracts usage/model via provider extractor,
    2. resolves pricing,
    3. computes cost,
    4. optionally persists processed records via callback.
    """

    def __init__(
        self,
        pricing_manager: Optional[PricingManager] = None,
        on_persist: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ):
        self.pricing_manager = pricing_manager or get_pricing_manager()
        self.on_persist = on_persist

    def register_buffer(self, buffer: Optional[RequestDetailsBuffer] = None) -> RequestDetailsBuffer:
        """Attach this orchestrator to a request buffer flush callback."""
        request_buffer = buffer or get_request_buffer()
        request_buffer.set_on_flush(self.process_batch)
        return request_buffer

    def process_batch(self, batch: List[RequestDetails]) -> List[Dict[str, Any]]:
        """Process a flushed request batch and optionally persist it."""
        processed: List[Dict[str, Any]] = []

        for request in batch:
            record = self.process_request(request)
            if record:
                processed.append(record)

        if self.on_persist and processed:
            self.on_persist(processed)

        return processed

    def process_request(self, request: RequestDetails) -> Optional[Dict[str, Any]]:
        """Process one request details record into a costed backend record."""
        provider = (request.provider or "").lower()
        model = request.model
        stop_reason = request.stop_reason
        usage = {
            "input_tokens": request.input_tokens,
            "output_tokens": request.output_tokens,
            "cache_creation_tokens": request.cache_creation_tokens,
            "cache_read_tokens": request.cache_read_tokens,
        }

        metadata = request.metadata or {}
        raw_response = metadata.get("raw_response") if isinstance(metadata, dict) else None

        extractor = get_extractor(provider)
        if extractor and isinstance(raw_response, dict):
            extracted_usage = extractor.extract_usage(raw_response)
            if extracted_usage:
                usage = extracted_usage

            extracted_model = extractor.extract_model(raw_response)
            if extracted_model:
                model = extracted_model

            if hasattr(extractor, "extract_stop_reason"):
                extracted_stop_reason = extractor.extract_stop_reason(raw_response)
                if extracted_stop_reason:
                    stop_reason = extracted_stop_reason

        pricing = self.pricing_manager.get_pricing(model, provider=provider) or {}
        breakdown = CostBreakdown(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            model=model,
            provider=provider,
            stop_reason=stop_reason,
            raw_usage=usage,
        )

        # Compute costs from pricing and usage (backend-only operation)
        if pricing:
            breakdown = self._compute_cost(usage, pricing, model, provider, stop_reason)

        return {
            "request_id": request.request_id,
            "timestamp": request.timestamp.isoformat(),
            "provider": provider,
            "model": model,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": breakdown.input_tokens,
                "output_tokens": breakdown.output_tokens,
                "cache_creation_tokens": breakdown.cache_creation_tokens,
                "cache_read_tokens": breakdown.cache_read_tokens,
            },
            "cost": {
                "input_cost": breakdown.input_cost,
                "output_cost": breakdown.output_cost,
                "cache_creation_cost": breakdown.cache_creation_cost,
                "cache_read_cost": breakdown.cache_read_cost,
                "total_cost": breakdown.total_cost,
            },
            "metadata": metadata,
        }

    def _compute_cost(
        self,
        usage: Dict[str, int],
        pricing: Dict[str, float],
        model: str,
        provider: str,
        stop_reason: Optional[str],
    ) -> CostBreakdown:
        """Compute cost from usage and pricing data (backend-only)."""
        breakdown = CostBreakdown(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            model=model,
            provider=provider,
            stop_reason=stop_reason,
            raw_usage=usage,
        )

        # Get pricing rates
        input_rate = pricing.get("input_cost_per_1m_tokens", 0)
        output_rate = pricing.get("output_cost_per_1m_tokens", 0)

        # Provider-specific cache cost handling
        if provider == "anthropic":
            cache_creation_rate = pricing.get(
                "cache_creation_cost_per_1m_tokens",
                input_rate * 1.25,  # Default: 25% premium
            )
            cache_read_rate = pricing.get(
                "cache_read_cost_per_1m_tokens",
                input_rate * 0.1,  # Default: 10% of input
            )
        else:
            cache_creation_rate = 0.0
            cache_read_rate = pricing.get("cache_read_cost_per_1m_tokens", input_rate * 0.1)

        # Calculate costs (divide by 1M tokens)
        breakdown.input_cost = (breakdown.input_tokens * input_rate) / 1_000_000
        breakdown.output_cost = (breakdown.output_tokens * output_rate) / 1_000_000
        breakdown.cache_creation_cost = (
            breakdown.cache_creation_tokens * cache_creation_rate
        ) / 1_000_000
        breakdown.cache_read_cost = (
            breakdown.cache_read_tokens * cache_read_rate
        ) / 1_000_000

        breakdown.total_cost = (
            breakdown.input_cost
            + breakdown.output_cost
            + breakdown.cache_creation_cost
            + breakdown.cache_read_cost
        )

        return breakdown


# Global pricing manager instance
_pricing_manager = None
_backend_orchestrator = None


def get_pricing_manager() -> PricingManager:
    """Get or create global pricing manager."""
    global _pricing_manager
    if _pricing_manager is None:
        _pricing_manager = PricingManager()
    return _pricing_manager


def get_backend_pricing_orchestrator(
    on_persist: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
) -> BackendPricingOrchestrator:
    """Get or create global backend pricing orchestrator."""
    global _backend_orchestrator
    if _backend_orchestrator is None:
        _backend_orchestrator = BackendPricingOrchestrator(on_persist=on_persist)
    elif on_persist is not None:
        _backend_orchestrator.on_persist = on_persist
    return _backend_orchestrator
