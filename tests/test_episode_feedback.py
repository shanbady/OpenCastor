"""Tests for episode feedback — 👍/👎 rating system (issue #262).

Tests cover:
  - EpisodeMemory.rate_episode()
  - EpisodeMemory.flag_episode()
  - EpisodeMemory.query_flagged()
  - DB migration (reward_score + flagged columns)
"""

from __future__ import annotations

import pytest

from castor.memory import EpisodeMemory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem(tmp_path):
    """Create a fresh in-memory EpisodeMemory for each test."""
    db = str(tmp_path / "test_feedback.db")
    return EpisodeMemory(db_path=db)


def _add_episode(mem: EpisodeMemory, instruction: str = "test") -> str:
    """Add a test episode and return its ID."""
    return mem.log_episode(
        instruction=instruction,
        raw_thought="ok",
        action={"type": "move"},
        latency_ms=100.0,
        outcome="ok",
    )


# ---------------------------------------------------------------------------
# rate_episode()
# ---------------------------------------------------------------------------


class TestRateEpisode:
    def test_rate_positive(self, mem):
        ep_id = _add_episode(mem, "forward")
        result = mem.rate_episode(ep_id, +1.0)
        assert result is True

    def test_rate_negative(self, mem):
        ep_id = _add_episode(mem, "backward")
        result = mem.rate_episode(ep_id, -1.0)
        assert result is True

    def test_rate_persists_score(self, mem):
        ep_id = _add_episode(mem, "turn")
        mem.rate_episode(ep_id, +1.0)
        ep = mem.get_episode(ep_id)
        assert ep is not None
        assert float(ep.get("reward_score", 0)) == pytest.approx(1.0)

    def test_rate_negative_persists(self, mem):
        ep_id = _add_episode(mem, "spin")
        mem.rate_episode(ep_id, -1.0)
        ep = mem.get_episode(ep_id)
        assert float(ep.get("reward_score", 0)) == pytest.approx(-1.0)

    def test_rate_unknown_id_returns_false(self, mem):
        result = mem.rate_episode("nonexistent-id", +1.0)
        assert result is False

    def test_rate_overwrites_previous(self, mem):
        ep_id = _add_episode(mem, "stop")
        mem.rate_episode(ep_id, +1.0)
        mem.rate_episode(ep_id, -1.0)
        ep = mem.get_episode(ep_id)
        assert float(ep.get("reward_score", 0)) == pytest.approx(-1.0)

    def test_rate_zero(self, mem):
        ep_id = _add_episode(mem, "wait")
        result = mem.rate_episode(ep_id, 0.0)
        assert result is True


# ---------------------------------------------------------------------------
# flag_episode()
# ---------------------------------------------------------------------------


class TestFlagEpisode:
    def test_flag_returns_true(self, mem):
        ep_id = _add_episode(mem, "dangerous")
        result = mem.flag_episode(ep_id)
        assert result is True

    def test_flag_persists(self, mem):
        ep_id = _add_episode(mem, "bad action")
        mem.flag_episode(ep_id)
        ep = mem.get_episode(ep_id)
        assert int(ep.get("flagged", 0)) == 1

    def test_unflagged_episode_has_zero_flag(self, mem):
        ep_id = _add_episode(mem, "good action")
        ep = mem.get_episode(ep_id)
        assert int(ep.get("flagged", 0)) == 0

    def test_flag_unknown_id_returns_false(self, mem):
        result = mem.flag_episode("no-such-id")
        assert result is False

    def test_flag_and_rate_together(self, mem):
        ep_id = _add_episode(mem, "error")
        mem.rate_episode(ep_id, -1.0)
        mem.flag_episode(ep_id)
        ep = mem.get_episode(ep_id)
        assert float(ep.get("reward_score", 0)) == pytest.approx(-1.0)
        assert int(ep.get("flagged", 0)) == 1


# ---------------------------------------------------------------------------
# query_flagged()
# ---------------------------------------------------------------------------


class TestQueryFlagged:
    def test_returns_only_flagged(self, mem):
        good_id = _add_episode(mem, "good")
        bad_id = _add_episode(mem, "bad")
        mem.flag_episode(bad_id)
        flagged = mem.query_flagged()
        ids = [ep["id"] for ep in flagged]
        assert bad_id in ids
        assert good_id not in ids

    def test_returns_empty_when_none_flagged(self, mem):
        _add_episode(mem)
        assert mem.query_flagged() == []

    def test_limit_respected(self, mem):
        for i in range(5):
            ep_id = _add_episode(mem, f"ep-{i}")
            mem.flag_episode(ep_id)
        flagged = mem.query_flagged(limit=3)
        assert len(flagged) == 3

    def test_flagged_episodes_ordered_newest_first(self, mem):
        ids = []
        for i in range(3):
            ep_id = _add_episode(mem, f"step-{i}")
            mem.flag_episode(ep_id)
            ids.append(ep_id)
        flagged = mem.query_flagged()
        # Newest first: last added should be first
        assert flagged[0]["id"] == ids[-1]


# ---------------------------------------------------------------------------
# DB schema migration
# ---------------------------------------------------------------------------


class TestFeedbackSchema:
    def test_reward_score_column_exists(self, mem):
        ep_id = _add_episode(mem)
        ep = mem.get_episode(ep_id)
        assert "reward_score" in ep

    def test_flagged_column_exists(self, mem):
        ep_id = _add_episode(mem)
        ep = mem.get_episode(ep_id)
        assert "flagged" in ep

    def test_default_reward_score_is_zero(self, mem):
        ep_id = _add_episode(mem)
        ep = mem.get_episode(ep_id)
        assert float(ep.get("reward_score", -99)) == pytest.approx(0.0)

    def test_default_flagged_is_zero(self, mem):
        ep_id = _add_episode(mem)
        ep = mem.get_episode(ep_id)
        assert int(ep.get("flagged", -1)) == 0
