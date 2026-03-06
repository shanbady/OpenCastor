"""Tests for castor.providers.failover — ProviderFailoverChain."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.providers.failover import (
    ProviderFailoverChain,
    FallbackSpec,
    FailoverResult,
)


def make_provider(response: str = "ok", raises: Exception | None = None):
    """Create a mock provider with a think() method."""
    mock = MagicMock()
    if raises:
        mock.think = MagicMock(side_effect=raises)
    else:
        mock.think = MagicMock(return_value=response)
    return mock


def make_async_provider(response: str = "ok", raises: Exception | None = None):
    """Create a mock async provider."""
    mock = MagicMock()
    if raises:
        async def _think(*a, **kw):
            raise raises
        mock.think = _think
    else:
        async def _think(*a, **kw):
            return response
        mock.think = _think
    return mock


# ---------------------------------------------------------------------------
# Basic pass-through
# ---------------------------------------------------------------------------


def test_primary_succeeds_no_fallback():
    primary = make_provider("primary response")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("ollama", "qwen2.5:7b"),
    )
    result = asyncio.run(chain.think("test instruction"))
    assert result.thought == "primary response"
    assert result.provider_used == "ollama"
    assert result.fallback_used is False
    assert result.attempts == 1


def test_primary_succeeds_fallback_not_called():
    primary = make_provider("primary")
    fallback = make_provider("fallback")
    spec = FallbackSpec(provider="anthropic", model="claude-haiku-3-5")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("ollama", "qwen"),
        fallbacks=[(spec, fallback)],
    )
    result = asyncio.run(chain.think("test"))
    assert result.thought == "primary"
    fallback.think.assert_not_called()


# ---------------------------------------------------------------------------
# Fallover triggering
# ---------------------------------------------------------------------------


def test_fallback_on_connection_error():
    primary = make_provider(raises=ConnectionError("refused"))
    fallback = make_provider("fallback response")
    spec = FallbackSpec(provider="anthropic", model="haiku")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("ollama", "qwen"),
        fallbacks=[(spec, fallback)],
        fallback_on=["connection_error"],
    )
    result = asyncio.run(chain.think("test"))
    assert result.thought == "fallback response"
    assert result.fallback_used is True
    assert result.provider_used == "anthropic"
    assert result.attempts == 2


def test_fallback_on_timeout():
    primary = make_provider(raises=TimeoutError("timed out"))
    fallback = make_provider("backup")
    spec = FallbackSpec(provider="anthropic", model="haiku")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("hf", "qwen"),
        fallbacks=[(spec, fallback)],
        fallback_on=["timeout"],
    )
    result = asyncio.run(chain.think("test"))
    assert result.thought == "backup"
    assert result.fallback_used is True


def test_fallback_on_rate_limit_by_message():
    primary = make_provider(raises=Exception("Rate limit exceeded: 429"))
    fallback = make_provider("rate-limit-fallback")
    spec = FallbackSpec(provider="ollama", model="local")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("anthropic", "claude"),
        fallbacks=[(spec, fallback)],
        fallback_on=["rate_limit"],
    )
    result = asyncio.run(chain.think("test"))
    assert result.thought == "rate-limit-fallback"


def test_non_fallback_error_propagates():
    """Errors not in fallback_on should propagate immediately."""
    primary = make_provider(raises=ValueError("bad input"))
    fallback = make_provider("shouldnt reach")
    spec = FallbackSpec(provider="anthropic", model="haiku")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("ollama", "qwen"),
        fallbacks=[(spec, fallback)],
        fallback_on=["timeout"],  # ValueError not in list
    )
    with pytest.raises(ValueError):
        asyncio.run(chain.think("test"))
    fallback.think.assert_not_called()


def test_all_providers_fail():
    primary = make_provider(raises=ConnectionError("primary down"))
    fallback1 = make_provider(raises=ConnectionError("fb1 down"))
    fallback2 = make_provider(raises=ConnectionError("fb2 down"))
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("hf", "qwen"),
        fallbacks=[
            (FallbackSpec(provider="ollama", model="q"), fallback1),
            (FallbackSpec(provider="anthropic", model="h"), fallback2),
        ],
        fallback_on=["connection_error"],
    )
    with pytest.raises(ConnectionError):
        asyncio.run(chain.think("test"))
    assert chain.last_provider_used == "hf"  # never updated past primary


# ---------------------------------------------------------------------------
# Chain state
# ---------------------------------------------------------------------------


def test_last_provider_updated_on_success():
    primary = make_provider(raises=ConnectionError("down"))
    fallback = make_provider("ok")
    spec = FallbackSpec(provider="ollama", model="local")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("hf", "qwen"),
        fallbacks=[(spec, fallback)],
        fallback_on=["connection_error"],
    )
    asyncio.run(chain.think("test"))
    assert chain.last_provider_used == "ollama"
    assert chain.last_model_used == "local"


def test_stats_tracked():
    primary = make_provider("ok")
    chain = ProviderFailoverChain(
        primary=primary,
        primary_spec=("ollama", "qwen"),
    )
    asyncio.run(chain.think("a"))
    asyncio.run(chain.think("b"))
    assert chain.stats["ollama"] == 2


# ---------------------------------------------------------------------------
# FailoverResult
# ---------------------------------------------------------------------------


def test_failover_result_no_fallback():
    primary = make_provider("response")
    chain = ProviderFailoverChain(primary=primary, primary_spec=("ollama", "qwen"))
    result = asyncio.run(chain.think("test"))
    assert isinstance(result, FailoverResult)
    assert result.latency_ms >= 0
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# FallbackSpec
# ---------------------------------------------------------------------------


def test_fallback_spec_defaults():
    spec = FallbackSpec(provider="ollama", model="qwen")
    assert spec.timeout_ms == 5000
    assert spec.label == ""
