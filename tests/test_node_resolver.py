"""Tests for castor.rcan.node_resolver (RCAN §17)."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from castor.rcan.node_resolver import (
    NodeResolver,
    RCANResolverError,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def resolver(tmp_path, monkeypatch):
    """NodeResolver backed by a temporary SQLite DB."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return NodeResolver(root_url="https://rcan.example")


@pytest.fixture()
def sample_record():
    return {
        "manufacturer": "AcmeCorp",
        "model": "RoboX-3000",
        "attestation": "active",
    }


# ── Cache tests ───────────────────────────────────────────────────────────────


class TestCache:
    def test_cache_miss(self, resolver):
        assert resolver._cache_get("RRN-000012345678") is None

    def test_cache_set_and_fresh_hit(self, resolver, sample_record):
        rrn = "RRN-000012345678"
        resolver._cache_set(rrn, sample_record, "https://rcan.example", ttl=3600)
        result = resolver._cache_get(rrn)
        assert result is not None
        record, resolved_by, is_stale = result
        assert record == sample_record
        assert resolved_by == "https://rcan.example"
        assert is_stale is False

    def test_cache_stale(self, resolver, sample_record):
        rrn = "RRN-000099999999"
        # Write with a cached_at in the past (ttl=1 second, cached 10 seconds ago)
        db = resolver._get_db()
        db.execute(
            "INSERT OR REPLACE INTO resolve_cache "
            "(rrn, record_json, resolved_by, cached_at, ttl_seconds) VALUES (?,?,?,?,?)",
            (rrn, json.dumps(sample_record), "https://rcan.example", time.time() - 10, 1),
        )
        db.commit()
        result = resolver._cache_get(rrn)
        assert result is not None
        _, _, is_stale = result
        assert is_stale is True

    def test_cache_overwrite(self, resolver, sample_record):
        rrn = "RRN-000011111111"
        resolver._cache_set(rrn, sample_record, "https://node-a.example")
        updated = {**sample_record, "attestation": "suspended"}
        resolver._cache_set(rrn, updated, "https://node-b.example")
        result = resolver._cache_get(rrn)
        assert result is not None
        record, resolved_by, _ = result
        assert record["attestation"] == "suspended"
        assert resolved_by == "https://node-b.example"


# ── Resolve: cache hit ────────────────────────────────────────────────────────


class TestResolveCacheHit:
    def test_returns_from_fresh_cache(self, resolver, sample_record):
        rrn = "RRN-CA-12345678"
        resolver._cache_set(rrn, sample_record, "https://rcan.example")
        robot = resolver.resolve(rrn)
        assert robot.rrn == rrn
        assert robot.from_cache is True
        assert robot.stale is False
        assert robot.manufacturer == "AcmeCorp"

    def test_does_not_call_network_on_fresh_cache(self, resolver, sample_record):
        rrn = "RRN-CA-22222222"
        resolver._cache_set(rrn, sample_record, "https://rcan.example")
        with patch.object(resolver, "_fetch_json") as mock_fetch:
            robot = resolver.resolve(rrn)
            mock_fetch.assert_not_called()
        assert robot.from_cache is True


# ── Resolve: live fetch ───────────────────────────────────────────────────────


class TestResolveLiveFetch:
    def test_live_fetch_success(self, resolver, sample_record):
        rrn = "RRN-US-33333333"
        body = {"record": sample_record}
        headers = {
            "X-Resolved-By": "https://us.rcan.example",
            "Cache-Control": "max-age=7200",
        }
        with patch.object(resolver, "_fetch_json", return_value=(body, headers)):
            robot = resolver.resolve(rrn)
        assert robot.from_cache is False
        assert robot.stale is False
        assert robot.resolved_by == "https://us.rcan.example"
        assert robot.model == "RoboX-3000"

    def test_live_fetch_caches_result(self, resolver, sample_record):
        rrn = "RRN-US-44444444"
        body = {"record": sample_record}
        headers = {"X-Resolved-By": "https://us.rcan.example", "Cache-Control": "max-age=3600"}
        with patch.object(resolver, "_fetch_json", return_value=(body, headers)):
            resolver.resolve(rrn)
        cached = resolver._cache_get(rrn)
        assert cached is not None

    def test_live_fetch_fallback_resolved_by(self, resolver, sample_record):
        """When X-Resolved-By header absent, resolved_by defaults to root_url."""
        rrn = "RRN-US-55555555"
        body = {"record": sample_record}
        headers = {}
        with patch.object(resolver, "_fetch_json", return_value=(body, headers)):
            robot = resolver.resolve(rrn)
        assert robot.resolved_by == resolver.root_url

    def test_live_fetch_body_without_record_key(self, resolver, sample_record):
        """Body without 'record' wrapper is used directly."""
        rrn = "RRN-US-66666666"
        headers = {}
        with patch.object(resolver, "_fetch_json", return_value=(sample_record, headers)):
            robot = resolver.resolve(rrn)
        assert robot.manufacturer == "AcmeCorp"


# ── Resolve: stale fallback ───────────────────────────────────────────────────


class TestResolveStale:
    def _insert_stale(self, resolver, rrn, record):
        db = resolver._get_db()
        db.execute(
            "INSERT OR REPLACE INTO resolve_cache "
            "(rrn, record_json, resolved_by, cached_at, ttl_seconds) VALUES (?,?,?,?,?)",
            (rrn, json.dumps(record), "https://old.rcan.example", time.time() - 7200, 1),
        )
        db.commit()

    def test_stale_fallback_when_network_fails(self, resolver, sample_record):
        rrn = "RRN-DE-77777777"
        self._insert_stale(resolver, rrn, sample_record)
        with patch.object(resolver, "_fetch_json", side_effect=RCANResolverError("timeout")):
            robot = resolver.resolve(rrn)
        assert robot.from_cache is True
        assert robot.stale is True
        assert robot.rrn == rrn

    def test_raises_when_no_cache_and_network_fails(self, resolver):
        rrn = "RRN-DE-88888888"
        with patch.object(resolver, "_fetch_json", side_effect=RCANResolverError("timeout")):
            with pytest.raises(RCANResolverError, match="no cached record"):
                resolver.resolve(rrn)


# ── is_reachable ──────────────────────────────────────────────────────────────


class TestIsReachable:
    def test_reachable_returns_true_and_positive_latency(self, resolver):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, latency_ms = resolver.is_reachable(timeout=5)
        assert ok is True
        assert latency_ms >= 0

    def test_unreachable_returns_false(self, resolver):
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            ok, latency_ms = resolver.is_reachable(timeout=1)
        assert ok is False
        assert latency_ms >= 0


# ── close ─────────────────────────────────────────────────────────────────────


class TestClose:
    def test_close_idempotent(self, resolver, sample_record):
        # Force DB open
        resolver._cache_set("RRN-000011111111", sample_record, "https://x.example")
        resolver.close()
        resolver.close()  # second call must not raise
        assert resolver._db is None
