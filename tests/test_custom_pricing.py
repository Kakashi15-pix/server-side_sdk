"""Unit tests for custom client pricing overrides."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from manager import PricingManager


@pytest.fixture
def pricing_manager(monkeypatch):
    def _load_pricing(self):
        self.pricing_data = {
            "gpt-3.5-turbo": {
                "input_cost_per_1m_tokens": 0.50,
                "output_cost_per_1m_tokens": 1.50,
                "cache_read_cost_per_1m_tokens": 0.25,
            }
        }

    monkeypatch.setattr(PricingManager, "_load_pricing", _load_pricing)
    monkeypatch.setattr(PricingManager, "sync_from_upstream", lambda self: True)
    return PricingManager()


def test_get_pricing_uses_litellm_when_no_custom_override(pricing_manager):
    pricing = pricing_manager.get_pricing("gpt-3.5-turbo", provider="openai")

    assert pricing is not None
    assert pricing["input_cost_per_1m_tokens"] == 0.50
    assert pricing["output_cost_per_1m_tokens"] == 1.50


def test_set_custom_pricing_overrides_provider_model(pricing_manager):
    pricing_manager.set_custom_pricing(
        model="gpt-3.5-turbo",
        provider="openai",
        input_cost_per_1m_tokens=1.25,
        output_cost_per_1m_tokens=2.50,
        cache_read_cost_per_1m_tokens=0.10,
        source="client_invoice",
    )

    pricing = pricing_manager.get_pricing("gpt-3.5-turbo", provider="openai")

    assert pricing is not None
    assert pricing["input_cost_per_1m_tokens"] == 1.25
    assert pricing["output_cost_per_1m_tokens"] == 2.50
    assert pricing["cache_read_cost_per_1m_tokens"] == 0.10
    assert pricing["source"] == "client_invoice"


def test_set_custom_pricing_keeps_fallback_pricing_for_other_models(pricing_manager):
    pricing_manager.set_custom_pricing(
        model="claude-3-haiku-20240307",
        provider="anthropic",
        input_cost_per_1m_tokens=3.0,
        output_cost_per_1m_tokens=15.0,
    )

    fallback = pricing_manager.get_pricing("gpt-3.5-turbo", provider="openai")
    override = pricing_manager.get_pricing("claude-3-haiku-20240307", provider="anthropic")

    assert fallback is not None
    assert fallback["input_cost_per_1m_tokens"] == 0.50
    assert override is not None
    assert override["input_cost_per_1m_tokens"] == 3.0
