"""Tests for ProviderPool burst detection — issue #331."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought


def _make_pool(burst_ms=1000, cooldown_s=5.0, n=2, strategy="round_robin"):
    from castor.providers.pool_provider import ProviderPool

    mocks = []
    pool_entries = []
    for i in range(n):
        m = MagicMock()
        m.think.return_value = Thought(raw_text=f"p{i}", action={"type": "stop"})
        m.health_check.return_value = {"ok": True, "mode": "mock"}
        mocks.append(m)
        pool_entries.append({"provider": f"mock{i}", "api_key": "x", "model": f"m{i}"})

    with patch("castor.providers.get_provider") as gp:
        gp.side_effect = mocks
        pool = ProviderPool(
            {
                "pool": pool_entries,
                "pool_strategy": strategy,
                "pool_burst_latency_ms": burst_ms,
                "pool_burst_cooldown_s": cooldown_s,
            }
        )
    return pool, mocks


# ── config ────────────────────────────────────────────────────────────────────


def test_burst_latency_ms_stored():
    pool, _ = _make_pool(burst_ms=2000)
    assert pool._burst_latency_ms == pytest.approx(2000)


def test_burst_cooldown_s_stored():
    pool, _ = _make_pool(cooldown_s=15)
    assert pool._burst_cooldown_s == pytest.approx(15)


def test_burst_demoted_until_empty_on_init():
    pool, _ = _make_pool()
    assert pool._burst_demoted_until == {}


# ── _burst_check ──────────────────────────────────────────────────────────────


def test_burst_check_demotes_on_spike():
    pool, _ = _make_pool(burst_ms=500, cooldown_s=30)
    pool._burst_check(0, 600.0)  # 600ms > 500ms threshold
    assert 0 in pool._burst_demoted_until
    assert pool._burst_demoted_until[0] > time.time()


def test_burst_check_no_demotion_below_threshold():
    pool, _ = _make_pool(burst_ms=1000, cooldown_s=30)
    pool._burst_check(0, 400.0)  # below threshold
    assert 0 not in pool._burst_demoted_until


def test_burst_check_disabled_when_threshold_zero():
    pool, _ = _make_pool(burst_ms=0)
    pool._burst_check(0, 99999.0)  # very high latency, but disabled
    assert pool._burst_demoted_until == {}


def test_burst_check_sets_correct_expiry():
    pool, _ = _make_pool(burst_ms=100, cooldown_s=60)
    before = time.time()
    pool._burst_check(0, 200.0)
    assert pool._burst_demoted_until[0] >= before + 60


# ── _is_burst_demoted ─────────────────────────────────────────────────────────


def test_is_burst_demoted_true_after_spike():
    pool, _ = _make_pool(burst_ms=100, cooldown_s=60)
    pool._burst_check(0, 200.0)
    assert pool._is_burst_demoted(0) is True


def test_is_burst_demoted_false_initially():
    pool, _ = _make_pool()
    assert pool._is_burst_demoted(0) is False


def test_is_burst_demoted_false_after_expiry():
    pool, _ = _make_pool(burst_ms=100, cooldown_s=0.01)
    pool._burst_check(0, 200.0)
    time.sleep(0.05)  # wait for cooldown
    assert pool._is_burst_demoted(0) is False


def test_is_burst_demoted_false_when_disabled():
    pool, _ = _make_pool(burst_ms=0)
    pool._burst_demoted_until[0] = time.time() + 9999  # manually set
    assert pool._is_burst_demoted(0) is False


# ── _get_healthy_indices ──────────────────────────────────────────────────────


def test_healthy_indices_excludes_burst_demoted():
    pool, _ = _make_pool(n=2, burst_ms=100, cooldown_s=60)
    pool._burst_demoted_until[0] = time.time() + 60
    healthy = pool._get_healthy_indices()
    assert 0 not in healthy
    assert 1 in healthy


def test_healthy_indices_falls_back_when_all_demoted():
    pool, _ = _make_pool(n=2, burst_ms=100, cooldown_s=60)
    pool._burst_demoted_until[0] = time.time() + 60
    pool._burst_demoted_until[1] = time.time() + 60
    healthy = pool._get_healthy_indices()
    # Falls back to all providers when all demoted
    assert 0 in healthy
    assert 1 in healthy


# ── health_check ─────────────────────────────────────────────────────────────


def test_health_check_includes_burst_detection():
    pool, _ = _make_pool(burst_ms=500)
    h = pool.health_check()
    assert "burst_detection" in h


def test_health_check_burst_threshold():
    pool, _ = _make_pool(burst_ms=750)
    h = pool.health_check()
    assert h["burst_detection"]["threshold_ms"] == pytest.approx(750)


def test_health_check_burst_demoted_count():
    pool, _ = _make_pool(n=2, burst_ms=100, cooldown_s=60)
    pool._burst_demoted_until[0] = time.time() + 60
    h = pool.health_check()
    assert h["burst_detection"]["demoted_count"] == 1


def test_health_check_burst_absent_when_disabled():
    pool, _ = _make_pool(burst_ms=0)
    h = pool.health_check()
    assert "burst_detection" not in h
