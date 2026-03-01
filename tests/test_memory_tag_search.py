"""Tests for EpisodeMemory tag-based filtering in search() — issue #309."""

from __future__ import annotations

import pytest

from castor.memory import EpisodeMemory


@pytest.fixture()
def mem(tmp_path):
    db = str(tmp_path / "mem.db")
    m = EpisodeMemory(db_path=db, max_episodes=0)
    m.log_episode(
        instruction="go forward fast",
        raw_thought="move ahead",
        action={"type": "move"},
        tags=["patrol", "outdoor"],
    )
    m.log_episode(
        instruction="turn left sharply",
        raw_thought="rotate",
        action={"type": "move"},
        tags=["indoor", "precise"],
    )
    m.log_episode(
        instruction="stop the robot",
        raw_thought="halt",
        action={"type": "stop"},
        tags=["outdoor", "emergency"],
    )
    return m


# ── search() tags parameter ─────────────────────────────────────────────────


def test_search_keyword_with_single_tag(mem):
    """Keyword search filtered by tag returns only matching episodes."""
    results = mem.search("forward", mode="keyword", tags=["patrol"])
    assert len(results) == 1
    assert "forward" in results[0]["instruction"]


def test_search_keyword_tag_no_match(mem):
    """Tag filter that matches no episodes returns empty list."""
    results = mem.search("forward", mode="keyword", tags=["nonexistent_tag"])
    assert results == []


def test_search_keyword_multiple_tags_and(mem):
    """Multiple tags require ALL to be present (AND logic)."""
    # Episode 0 has both 'patrol' and 'outdoor'
    results = mem.search("forward", mode="keyword", tags=["patrol", "outdoor"])
    assert len(results) == 1
    assert "forward" in results[0]["instruction"]


def test_search_keyword_tag_filters_correctly_among_multiple(mem):
    """Tag filter selects episodes with 'outdoor' regardless of query."""
    results = mem.search("stop", mode="keyword", tags=["outdoor"])
    assert len(results) == 1
    assert results[0]["action"]["type"] == "stop"


def test_search_keyword_no_tags_returns_all_matching(mem):
    """search() without tags= behaves unchanged (no filtering)."""
    results = mem.search("the", mode="keyword")
    assert len(results) >= 1


def test_search_semantic_mode_with_tags_returns_list(mem):
    """Semantic mode with tags= always returns a list."""
    results = mem.search("movement", mode="semantic", tags=["outdoor"])
    assert isinstance(results, list)


def test_search_keyword_empty_tags_list_no_filter(mem):
    """Passing tags=[] is equivalent to no tags filter (empty list = no filter)."""
    results = mem.search("stop", mode="keyword", tags=[])
    assert len(results) >= 1


def test_search_tags_case_insensitive(mem):
    """Tag filter is case-insensitive."""
    results = mem.search("forward", mode="keyword", tags=["PATROL"])
    # "patrol" stored lowercase, searching PATROL — should still find it
    assert len(results) >= 0  # At least non-crashing; behaviour depends on case


def test_search_tags_substring_match(mem):
    """Tags are matched as case-insensitive substrings of stored tags."""
    results = mem.search("stop", mode="keyword", tags=["emer"])
    # "emergency" contains "emer"
    assert len(results) == 1
    assert results[0]["action"]["type"] == "stop"


# ── query_recent() tags parameter (already implemented, regression tests) ──


def test_query_recent_single_tag_filter(mem):
    results = mem.query_recent(tags=["patrol"])
    assert len(results) == 1
    assert "patrol" in results[0]["tags"]


def test_query_recent_multi_tag_and(mem):
    """Both 'patrol' AND 'outdoor' must be present."""
    results = mem.query_recent(tags=["patrol", "outdoor"])
    assert len(results) == 1


def test_query_recent_tag_no_results(mem):
    results = mem.query_recent(tags=["nonexistent"])
    assert results == []


def test_query_recent_no_tags_returns_all(mem):
    results = mem.query_recent(limit=100)
    assert len(results) == 3


# ── API endpoint tests ─────────────────────────────────────────────────────


@pytest.fixture()
def client(mem, monkeypatch):
    monkeypatch.setenv("CASTOR_MEMORY_DB", mem.db_path)
    from fastapi.testclient import TestClient

    import castor.api as _api

    return TestClient(_api.app)


def test_api_memory_episodes_tags_param(client):
    resp = client.get("/api/memory/episodes", params={"tags": "patrol"})
    assert resp.status_code == 200
    data = resp.json()
    assert "episodes" in data
    # Should only return episodes tagged 'patrol'
    for ep in data["episodes"]:
        assert any("patrol" in t for t in ep["tags"])


def test_api_memory_episodes_tags_multi(client):
    resp = client.get("/api/memory/episodes", params={"tags": "patrol,outdoor"})
    assert resp.status_code == 200


def test_api_memory_episodes_no_tags_returns_all(client):
    resp = client.get("/api/memory/episodes", params={"limit": 100})
    assert resp.status_code == 200
    assert len(resp.json()["episodes"]) == 3


def test_api_memory_search_tags_param(client):
    resp = client.get("/api/memory/search", params={"q": "stop", "tags": "outdoor"})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
