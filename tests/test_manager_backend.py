"""Tests for backend pricing orchestration."""

from datetime import datetime

from manager import BackendPricingOrchestrator, RequestDetails


class _PricingManager:
    def get_pricing(self, model, provider=None):
        return {
            "input_cost_per_1m_tokens": 3.0,
            "output_cost_per_1m_tokens": 15.0,
            "cache_creation_cost_per_1m_tokens": 3.75,
            "cache_read_cost_per_1m_tokens": 0.3,
        }


class TestBackendPricingOrchestrator:
    """Test backend request processing orchestration."""

    def test_process_request_with_raw_response_extraction(self):
        orchestrator = BackendPricingOrchestrator(pricing_manager=_PricingManager())

        request = RequestDetails(
            timestamp=datetime.utcnow(),
            request_id="req_001",
            model="unknown",
            provider="anthropic",
            input_tokens=0,
            output_tokens=0,
            metadata={
                "raw_response": {
                    "model": "claude-3-haiku-20240307",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                    "stop_reason": "end_turn",
                }
            },
        )

        record = orchestrator.process_request(request)

        assert record is not None
        assert record["request_id"] == "req_001"
        assert record["provider"] == "anthropic"
        assert record["model"] == "claude-3-haiku-20240307"
        assert record["usage"]["input_tokens"] == 100
        assert record["usage"]["output_tokens"] == 50
        assert record["cost"]["total_cost"] > 0

    def test_process_request_without_raw_response_uses_fallback_usage(self):
        orchestrator = BackendPricingOrchestrator(pricing_manager=_PricingManager())

        request = RequestDetails(
            timestamp=datetime.utcnow(),
            request_id="req_002",
            model="claude-3-haiku-20240307",
            provider="anthropic",
            input_tokens=200,
            output_tokens=80,
            metadata={},
        )

        record = orchestrator.process_request(request)

        assert record is not None
        assert record["usage"]["input_tokens"] == 200
        assert record["usage"]["output_tokens"] == 80
        assert record["cost"]["total_cost"] > 0

    def test_process_batch_with_persist_callback(self):
        persisted = []

        def on_persist(records):
            persisted.extend(records)

        orchestrator = BackendPricingOrchestrator(
            pricing_manager=_PricingManager(),
            on_persist=on_persist,
        )

        requests = [
            RequestDetails(
                timestamp=datetime.utcnow(),
                request_id="req_003",
                model="claude-3-haiku-20240307",
                provider="anthropic",
                input_tokens=10,
                output_tokens=5,
                metadata={},
            ),
            RequestDetails(
                timestamp=datetime.utcnow(),
                request_id="req_004",
                model="gpt-3.5-turbo",
                provider="openai",
                input_tokens=20,
                output_tokens=8,
                metadata={},
            ),
        ]

        processed = orchestrator.process_batch(requests)

        assert len(processed) == 2
        assert len(persisted) == 2
        assert {r["request_id"] for r in processed} == {"req_003", "req_004"}
