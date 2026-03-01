"""Tests for ProviderPool A/B test mode — issue #338."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from castor.providers.base import Thought


def _make_ab_pool(split=0.5, n=2):
    from castor.providers.pool_provider import ProviderPool

    mocks = []
    pool_entries = []
    for i in range(n):
        m = MagicMock()
        m.think.return_value = Thought(raw_text=f"p{i}", action={"type": "move"})
        m.health_check.return_value = {"ok": True, "mode": "mock"}
        mocks.append(m)
        pool_entries.append({"provider": f"mock{i}", "api_key": "x", "model": f"m{i}"})

    with patch("castor.providers.get_provider") as gp:
        gp.side_effect = mocks
        pool = ProviderPool(
            {
                "pool": pool_entries,
                "pool_strategy": "ab_test",
                "pool_ab_split": split,
            }
        )
    return pool, mocks


# ── config ────────────────────────────────────────────────────────────────────


def test_ab_strategy_stored():
    pool, _ = _make_ab_pool()
    assert pool._strategy == "ab_test"


def test_ab_split_stored():
    pool, _ = _make_ab_pool(split=0.7)
    assert pool._ab_split == pytest.approx(0.7)


def test_ab_stats_initialized():
    pool, _ = _make_ab_pool()
    assert 0 in pool._ab_stats
    assert 1 in pool._ab_stats


# ── _ab_group ─────────────────────────────────────────────────────────────────


def test_ab_group_returns_0_or_1():
    pool, _ = _make_ab_pool()
    for _ in range(50):
        g = pool._ab_group()
        assert g in (0, 1)


def test_ab_group_split_100_always_0():
    pool, _ = _make_ab_pool(split=1.0)
    for _ in range(20):
        assert pool._ab_group() == 0


def test_ab_group_split_0_always_1():
    pool, _ = _make_ab_pool(split=0.0)
    for _ in range(20):
        assert pool._ab_group() == 1


# ── _ab_provider_for_group ────────────────────────────────────────────────────


def test_ab_provider_for_group_0_returns_first():
    pool, mocks = _make_ab_pool()
    idx, p = pool._ab_provider_for_group(0)
    assert idx == 0
    assert p is pool._providers[0]


def test_ab_provider_for_group_1_returns_second():
    pool, mocks = _make_ab_pool()
    idx, p = pool._ab_provider_for_group(1)
    assert idx == 1
    assert p is pool._providers[1]


def test_ab_provider_single_provider_returns_0():
    pool, mocks = _make_ab_pool(n=1)
    idx, p = pool._ab_provider_for_group(1)
    assert idx == 0


# ── _ab_record ────────────────────────────────────────────────────────────────


def test_ab_record_success_increments_counter():
    pool, _ = _make_ab_pool()
    pool._ab_record(0, success=True)
    assert pool._ab_stats[0]["success"] == 1


def test_ab_record_fail_increments_counter():
    pool, _ = _make_ab_pool()
    pool._ab_record(1, success=False)
    assert pool._ab_stats[1]["fail"] == 1


def test_ab_record_multiple_calls_accumulate():
    pool, _ = _make_ab_pool()
    for _ in range(5):
        pool._ab_record(0, success=True)
    assert pool._ab_stats[0]["success"] == 5


# ── think() integration ────────────────────────────────────────────────────────


def test_ab_think_returns_thought():
    pool, _ = _make_ab_pool()
    result = pool.think(b"", "test")
    assert isinstance(result, Thought)


def test_ab_think_calls_one_of_two_providers():
    pool, mocks = _make_ab_pool(split=0.5)
    for _ in range(20):
        pool.think(b"", "go")
    total_calls = sum(m.think.call_count for m in mocks)
    assert total_calls == 20


def test_ab_think_records_outcomes():
    pool, _ = _make_ab_pool()
    for _ in range(10):
        pool.think(b"", "test")
    total_success = sum(pool._ab_stats[g]["success"] for g in (0, 1))
    assert total_success == 10


# ── health_check ─────────────────────────────────────────────────────────────


def test_health_check_includes_ab_test():
    pool, _ = _make_ab_pool()
    h = pool.health_check()
    assert "ab_test" in h


def test_health_check_ab_split():
    pool, _ = _make_ab_pool(split=0.3)
    h = pool.health_check()
    assert h["ab_test"]["split"] == pytest.approx(0.3)


def test_health_check_ab_groups():
    pool, _ = _make_ab_pool()
    h = pool.health_check()
    assert "groups" in h["ab_test"]
    assert 0 in h["ab_test"]["groups"]
    assert 1 in h["ab_test"]["groups"]


def test_health_check_ab_not_present_for_other_strategies():
    from castor.providers.pool_provider import ProviderPool

    m = MagicMock()
    m.think.return_value = Thought(raw_text="ok", action={})
    m.health_check.return_value = {"ok": True}
    with patch("castor.providers.get_provider", return_value=m):
        pool = ProviderPool({"pool": [{"provider": "mock"}], "pool_strategy": "round_robin"})
    assert "ab_test" not in pool.health_check()
