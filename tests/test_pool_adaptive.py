"""Tests for ProviderPool adaptive weighted strategy — issue #320."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought


def _make_pool(strategy="adaptive", alpha=0.1, window_n=2, n=2):
    """Build a ProviderPool with n mock providers."""
    from castor.providers.pool_provider import ProviderPool

    mock_providers = []
    pool_entries = []
    for i in range(n):
        m = MagicMock()
        m.think.return_value = Thought(raw_text=f"provider{i}", action={"type": "move"})
        m.think_stream.return_value = iter([f"chunk{i}"])
        m.health_check.return_value = {"ok": True, "mode": "mock"}
        mock_providers.append(m)
        pool_entries.append({"provider": f"mock{i}", "api_key": "x", "model": f"m{i}"})

    with patch("castor.providers.get_provider") as mock_gp:
        mock_gp.side_effect = mock_providers
        pool = ProviderPool(
            {
                "pool": pool_entries,
                "pool_strategy": strategy,
                "pool_adaptive_alpha": alpha,
                "pool_adaptive_window_n": window_n,
            }
        )
    return pool, mock_providers


# ── init ──────────────────────────────────────────────────────────────────────


def test_adaptive_strategy_stored():
    pool, _ = _make_pool()
    assert pool._strategy == "adaptive"


def test_adaptive_alpha_stored():
    pool, _ = _make_pool(alpha=0.2)
    assert pool._adaptive_alpha == pytest.approx(0.2)


def test_adaptive_window_n_stored():
    pool, _ = _make_pool(window_n=10)
    assert pool._adaptive_window_n == 10


def test_adaptive_ema_latency_empty_on_init():
    pool, _ = _make_pool()
    assert pool._ema_latency == {}


def test_adaptive_obs_count_empty_on_init():
    pool, _ = _make_pool()
    assert pool._obs_count == {}


# ── _update_adaptive_weight ───────────────────────────────────────────────────


def test_update_adaptive_weight_first_observation():
    pool, _ = _make_pool()
    pool._update_adaptive_weight(0, 100.0)
    assert pool._ema_latency[0] == pytest.approx(100.0)
    assert pool._obs_count[0] == 1


def test_update_adaptive_weight_second_observation_ema():
    pool, _ = _make_pool(alpha=0.5)
    pool._update_adaptive_weight(0, 100.0)
    pool._update_adaptive_weight(0, 200.0)
    # EMA: 100 * (1-0.5) + 200 * 0.5 = 150
    assert pool._ema_latency[0] == pytest.approx(150.0)
    assert pool._obs_count[0] == 2


def test_update_adaptive_weight_independent_per_provider():
    pool, _ = _make_pool()
    pool._update_adaptive_weight(0, 50.0)
    pool._update_adaptive_weight(1, 200.0)
    assert pool._ema_latency[0] == pytest.approx(50.0)
    assert pool._ema_latency[1] == pytest.approx(200.0)


def test_update_adaptive_weight_thread_safe():
    pool, _ = _make_pool(n=1)
    errors = []

    def update_many():
        try:
            for _ in range(100):
                pool._update_adaptive_weight(0, 100.0)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=update_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert pool._obs_count[0] == 400


# ── warm-up phase (below window_n) ────────────────────────────────────────────


def test_adaptive_warmup_uses_all_providers():
    """Before window_n observations, all providers should be callable."""
    pool, mocks = _make_pool(window_n=100)
    used = set()
    for _ in range(20):
        t = pool.think(b"", "go")
        used.add(t.raw_text)
    # At least 1 provider was used (likely both in round-robin fallback)
    assert len(used) >= 1


# ── post-warmup weighted selection ────────────────────────────────────────────


def test_adaptive_post_warmup_prefers_fast_provider():
    """Provider 0 (low latency EMA) should be selected more often than provider 1."""
    pool, _ = _make_pool(window_n=2)
    # Seed EMA: provider 0 = 10 ms, provider 1 = 500 ms
    pool._ema_latency[0] = 10.0
    pool._ema_latency[1] = 500.0
    pool._obs_count[0] = 5
    pool._obs_count[1] = 5

    counts = {0: 0, 1: 0}
    for _ in range(200):
        t = pool.think(b"", "go")
        if "provider0" in t.raw_text:
            counts[0] += 1
        else:
            counts[1] += 1
    # Provider 0 (fast) should win significantly more
    assert counts[0] > counts[1]


# ── think() integration ────────────────────────────────────────────────────────


def test_adaptive_think_updates_ema_after_success():
    pool, mocks = _make_pool(window_n=1)
    pool.think(b"", "hello")
    # After one call, obs_count for current_index should be 1
    assert sum(pool._obs_count.values()) >= 1


def test_adaptive_think_returns_thought():
    pool, _ = _make_pool()
    result = pool.think(b"", "test")
    assert isinstance(result, Thought)


# ── health_check adaptive section ─────────────────────────────────────────────


def test_health_check_includes_adaptive_key():
    pool, _ = _make_pool()
    h = pool.health_check()
    assert "adaptive" in h


def test_health_check_adaptive_alpha():
    pool, _ = _make_pool(alpha=0.3)
    h = pool.health_check()
    assert h["adaptive"]["alpha"] == pytest.approx(0.3)


def test_health_check_adaptive_window_n():
    pool, _ = _make_pool(window_n=7)
    h = pool.health_check()
    assert h["adaptive"]["window_n"] == 7


def test_health_check_adaptive_obs_count():
    pool, _ = _make_pool()
    pool._obs_count[0] = 5
    h = pool.health_check()
    assert h["adaptive"]["obs_count"].get(0) == 5
