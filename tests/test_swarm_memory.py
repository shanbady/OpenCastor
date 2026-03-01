"""Tests for castor.swarm_memory — ALMA swarm collective memory (issue #260)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from castor.swarm_memory import (
    SwarmMemorySync,
    _http_get,
    _http_post,
    _node_base_url,
    load_swarm_nodes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_swarm_yaml(tmp_path, nodes=None):
    """Write a minimal swarm.yaml and return its path."""
    if nodes is None:
        nodes = [
            {"name": "bot-a", "ip": "10.0.0.1", "port": 8000, "token": "tok-a"},
            {"name": "bot-b", "ip": "10.0.0.2", "port": 8001, "token": "tok-b"},
        ]
    import yaml

    content = {"nodes": nodes}
    p = tmp_path / "swarm.yaml"
    p.write_text(yaml.dump(content))
    return str(p)


def _make_memory(episodes=None):
    """Return a mock EpisodeMemory."""
    mem = MagicMock()
    mem.query_recent.return_value = episodes or [
        {
            "id": "ep-1",
            "instruction": "move forward",
            "raw_thought": "ok",
            "action_json": '{"type":"move"}',
            "latency_ms": 200.0,
            "image_hash": None,
            "outcome": "ok",
            "source": "api",
        }
    ]
    return mem


# ---------------------------------------------------------------------------
# load_swarm_nodes
# ---------------------------------------------------------------------------


class TestLoadSwarmNodes:
    def test_loads_nodes_from_yaml(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path)
        nodes = load_swarm_nodes(yaml_path)
        assert len(nodes) == 2
        assert nodes[0]["name"] == "bot-a"

    def test_returns_empty_when_file_missing(self, tmp_path):
        nodes = load_swarm_nodes(str(tmp_path / "nonexistent.yaml"))
        assert nodes == []

    def test_returns_empty_when_yaml_unavailable(self, tmp_path):
        p = tmp_path / "swarm.yaml"
        p.write_text("nodes:\n  - name: x\n")
        with patch.dict("sys.modules", {"yaml": None}):
            # even if yaml unavailable, at worst returns []
            nodes = load_swarm_nodes(str(p))
            assert isinstance(nodes, list)


# ---------------------------------------------------------------------------
# _node_base_url
# ---------------------------------------------------------------------------


class TestNodeBaseUrl:
    def test_uses_ip_when_present(self):
        node = {"ip": "192.168.1.5", "port": 8000}
        assert _node_base_url(node) == "http://192.168.1.5:8000"

    def test_falls_back_to_host(self):
        node = {"host": "bot.local", "port": 9000}
        assert _node_base_url(node) == "http://bot.local:9000"

    def test_default_port(self):
        node = {"ip": "10.0.0.1"}
        assert _node_base_url(node) == "http://10.0.0.1:8000"


# ---------------------------------------------------------------------------
# _http_post / _http_get (mocked urllib)
# ---------------------------------------------------------------------------


class TestHttpHelpers:
    def test_http_post_success(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"received": 5}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _http_post("http://test/api", {"data": 1})
        assert result["received"] == 5

    def test_http_post_error_returns_error_dict(self):
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            result = _http_post("http://bad/api", {})
        assert "error" in result

    def test_http_get_success(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"episodes": []}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _http_get("http://test/api/episodes")
        assert "episodes" in result

    def test_http_get_error_returns_error_dict(self):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = _http_get("http://bad/episodes")
        assert "error" in result


# ---------------------------------------------------------------------------
# SwarmMemorySync.push
# ---------------------------------------------------------------------------


class TestSwarmMemorySyncPush:
    def test_push_calls_each_node(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path)
        mem = _make_memory()
        sync = SwarmMemorySync(mem, swarm_yaml_path=yaml_path)

        responses = [b'{"received": 1}', b'{"received": 1}']
        call_count = 0

        def fake_urlopen(req, timeout=None):
            nonlocal call_count
            resp = MagicMock()
            resp.read.return_value = responses[call_count % len(responses)]
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            call_count += 1
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            results = sync.push(last_n=10)

        assert len(results) == 2
        assert call_count == 2

    def test_push_returns_error_on_failure(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path, nodes=[{"name": "bad", "ip": "1.1.1.1"}])
        mem = _make_memory()
        sync = SwarmMemorySync(mem, swarm_yaml_path=yaml_path)

        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            results = sync.push()

        assert "bad" in results
        assert results["bad"]["ok"] is False
        assert results["bad"]["error"] is not None


# ---------------------------------------------------------------------------
# SwarmMemorySync.receive
# ---------------------------------------------------------------------------


class TestSwarmMemorySyncReceive:
    def test_receive_stores_new_episodes(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path)
        mem = MagicMock()
        mem.query_recent.return_value = []
        sync = SwarmMemorySync(mem, swarm_yaml_path=yaml_path)

        episodes = [
            {
                "id": "ep-remote-1",
                "instruction": "stop",
                "raw_thought": "",
                "action_json": "{}",
                "latency_ms": 0.0,
                "outcome": "ok",
                "source": "api",
            }
        ]
        result = sync.receive(episodes)
        assert result["received"] == 1
        assert result["skipped"] == 0

    def test_receive_deduplicates(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path)
        mem = MagicMock()
        sync = SwarmMemorySync(mem, swarm_yaml_path=yaml_path)

        ep = {"id": "dup-id", "instruction": "x", "action_json": "{}", "outcome": "ok"}
        sync.receive([ep])
        result = sync.receive([ep])  # second receive — same ID
        assert result["skipped"] == 1
        assert result["received"] == 0


# ---------------------------------------------------------------------------
# SwarmMemorySync.fetch_from_node
# ---------------------------------------------------------------------------


class TestSwarmMemorySyncFetch:
    def test_fetch_returns_episodes(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path)
        mem = MagicMock()
        sync = SwarmMemorySync(mem, swarm_yaml_path=yaml_path)

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"episodes": [{"id": "r1"}]}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            eps = sync.fetch_from_node("bot-a")
        assert len(eps) == 1

    def test_fetch_returns_empty_for_unknown_node(self, tmp_path):
        yaml_path = _make_swarm_yaml(tmp_path)
        mem = MagicMock()
        sync = SwarmMemorySync(mem, swarm_yaml_path=yaml_path)
        eps = sync.fetch_from_node("nonexistent-node")
        assert eps == []
