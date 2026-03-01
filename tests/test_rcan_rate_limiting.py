"""Tests for castor.rate_limiting — RCAN-driven rate limiting (issue #258)."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from castor.rate_limiting import (
    EndpointLimit,
    RateLimitConfig,
    RateLimiter,
    get_limiter,
    init_limiter,
)

# ---------------------------------------------------------------------------
# RateLimitConfig.from_rcan
# ---------------------------------------------------------------------------


class TestRateLimitConfigFromRcan:
    def test_parses_single_rule(self):
        cfg = {
            "rate_limits": [
                {"endpoint": "/api/command", "per_ip": 5, "per_user": 10, "window_s": 30}
            ]
        }
        rl = RateLimitConfig.from_rcan(cfg)
        assert len(rl.limits) == 1
        assert rl.limits[0].endpoint == "/api/command"
        assert rl.limits[0].per_ip == 5
        assert rl.limits[0].per_user == 10
        assert rl.limits[0].window_s == 30.0

    def test_parses_multiple_rules(self):
        cfg = {
            "rate_limits": [
                {"endpoint": "/api/command", "per_ip": 10},
                {"endpoint": "/api/webhook", "per_ip": 5},
            ]
        }
        rl = RateLimitConfig.from_rcan(cfg)
        assert len(rl.limits) == 2

    def test_empty_config(self):
        rl = RateLimitConfig.from_rcan({})
        assert rl.limits == []

    def test_missing_endpoint_skipped(self):
        cfg = {"rate_limits": [{"per_ip": 5}]}
        rl = RateLimitConfig.from_rcan(cfg)
        assert rl.limits == []

    def test_default_values(self):
        cfg = {"rate_limits": [{"endpoint": "/api/command"}]}
        rl = RateLimitConfig.from_rcan(cfg)
        lim = rl.limits[0]
        assert lim.per_ip == 10
        assert lim.per_user == 20
        assert lim.window_s == 60.0

    def test_non_dict_entry_skipped(self):
        cfg = {"rate_limits": ["bad-entry", {"endpoint": "/api/command", "per_ip": 1}]}
        rl = RateLimitConfig.from_rcan(cfg)
        assert len(rl.limits) == 1

    def test_get_limit_returns_matching_rule(self):
        cfg = {"rate_limits": [{"endpoint": "/api/command", "per_ip": 7}]}
        rl = RateLimitConfig.from_rcan(cfg)
        lim = rl.get_limit("/api/command")
        assert lim is not None
        assert lim.per_ip == 7

    def test_get_limit_returns_none_for_unknown(self):
        rl = RateLimitConfig.from_rcan({})
        assert rl.get_limit("/api/unknown") is None

    def test_to_dict(self):
        cfg = {
            "rate_limits": [
                {"endpoint": "/api/command", "per_ip": 3, "per_user": 6, "window_s": 10}
            ]
        }
        rl = RateLimitConfig.from_rcan(cfg)
        d = rl.to_dict()
        assert d[0]["endpoint"] == "/api/command"
        assert d[0]["per_ip"] == 3


# ---------------------------------------------------------------------------
# RateLimiter.check — IP limits
# ---------------------------------------------------------------------------


class TestRateLimiterIP:
    def _make_limiter(self, per_ip=3, window_s=60.0):
        cfg = RateLimitConfig(
            limits=[EndpointLimit(endpoint="/api/command", per_ip=per_ip, window_s=window_s)]
        )
        return RateLimiter(cfg)

    def test_allows_up_to_limit(self):
        lim = self._make_limiter(per_ip=3)
        for _ in range(3):
            lim.check("/api/command", ip="1.2.3.4")  # should not raise

    def test_raises_429_on_excess(self):
        lim = self._make_limiter(per_ip=3)
        for _ in range(3):
            lim.check("/api/command", ip="1.2.3.4")
        with pytest.raises(HTTPException) as exc_info:
            lim.check("/api/command", ip="1.2.3.4")
        assert exc_info.value.status_code == 429

    def test_429_includes_retry_after(self):
        lim = self._make_limiter(per_ip=1)
        lim.check("/api/command", ip="1.2.3.4")
        with pytest.raises(HTTPException) as exc_info:
            lim.check("/api/command", ip="1.2.3.4")
        assert "Retry-After" in exc_info.value.headers

    def test_different_ips_independent(self):
        lim = self._make_limiter(per_ip=1)
        lim.check("/api/command", ip="1.1.1.1")
        lim.check("/api/command", ip="2.2.2.2")  # different IP — should pass

    def test_no_limit_for_unconfigured_endpoint(self):
        lim = self._make_limiter()
        for _ in range(100):
            lim.check("/api/other", ip="1.2.3.4")  # no rule — always passes

    def test_reset_clears_history(self):
        lim = self._make_limiter(per_ip=1)
        lim.check("/api/command", ip="1.2.3.4")
        lim.reset()
        lim.check("/api/command", ip="1.2.3.4")  # should pass after reset


# ---------------------------------------------------------------------------
# RateLimiter.check — user limits
# ---------------------------------------------------------------------------


class TestRateLimiterUser:
    def _make_limiter(self, per_user=2):
        cfg = RateLimitConfig(
            limits=[
                EndpointLimit(endpoint="/api/command", per_ip=0, per_user=per_user, window_s=60.0)
            ]
        )
        return RateLimiter(cfg)

    def test_allows_up_to_user_limit(self):
        lim = self._make_limiter(per_user=2)
        lim.check("/api/command", user="alice")
        lim.check("/api/command", user="alice")

    def test_raises_429_on_user_excess(self):
        lim = self._make_limiter(per_user=1)
        lim.check("/api/command", user="bob")
        with pytest.raises(HTTPException) as exc_info:
            lim.check("/api/command", user="bob")
        assert exc_info.value.status_code == 429

    def test_different_users_independent(self):
        lim = self._make_limiter(per_user=1)
        lim.check("/api/command", user="alice")
        lim.check("/api/command", user="bob")  # different user


# ---------------------------------------------------------------------------
# Sliding window expiry
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    def test_requests_expire_after_window(self):
        cfg = RateLimitConfig(
            limits=[EndpointLimit(endpoint="/api/command", per_ip=1, window_s=0.05)]
        )
        lim = RateLimiter(cfg)
        lim.check("/api/command", ip="1.2.3.4")
        time.sleep(0.1)  # wait for window to expire
        lim.check("/api/command", ip="1.2.3.4")  # should pass again


# ---------------------------------------------------------------------------
# init_limiter / get_limiter
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def test_init_limiter_creates_singleton(self):
        cfg = {"rate_limits": [{"endpoint": "/api/command", "per_ip": 5}]}
        lim = init_limiter(cfg)
        assert lim is get_limiter()

    def test_init_limiter_returns_rate_limiter(self):
        lim = init_limiter({})
        assert isinstance(lim, RateLimiter)


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_update_config_replaces_rules(self):
        cfg1 = RateLimitConfig(limits=[EndpointLimit(endpoint="/api/command", per_ip=1)])
        lim = RateLimiter(cfg1)
        lim.check("/api/command", ip="x")
        with pytest.raises(HTTPException):
            lim.check("/api/command", ip="x")

        cfg2 = RateLimitConfig(limits=[EndpointLimit(endpoint="/api/command", per_ip=100)])
        lim.update_config(cfg2)
        # After update, limit is 100 — should pass easily
        for _ in range(5):
            lim.check("/api/command", ip="x")
