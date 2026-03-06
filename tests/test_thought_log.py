"""Unit tests for castor.thought_log.ThoughtLog."""

from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from typing import Any
from unittest.mock import MagicMock

import pytest

from castor.thought_log import ThoughtLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_thought(
    thought_id: str = "t-001",
    confidence: float = 0.9,
    action: dict | None = None,
    raw_text: str | None = "reasoning text",
    provider: str = "openai",
    model: str = "gpt-4",
) -> Any:
    t = MagicMock()
    t.id = thought_id
    t.confidence = confidence
    t.action = action or {"type": "move", "params": {}}
    t.raw_text = raw_text
    t.provider = provider
    t.model = model
    t.model_version = None
    t.layer = "fast"
    t.escalated = False
    return t


# ---------------------------------------------------------------------------
# record() stores entry
# ---------------------------------------------------------------------------


def test_record_stores_entry():
    log = ThoughtLog()
    t = make_thought("t-001", confidence=0.85)
    log.record(t)

    entry = log.get("t-001", include_reasoning=True)
    assert entry is not None
    assert entry["id"] == "t-001"
    assert entry["confidence"] == pytest.approx(0.85)
    assert "timestamp_ms" in entry
    assert entry["action"] is not None


def test_record_stores_cmd_as_action():
    log = ThoughtLog()
    action = {"type": "grip", "params": {"force": 10}}
    t = make_thought("t-002", action=action)
    log.record(t)

    entry = log.get("t-002", include_reasoning=True)
    assert entry["action"]["type"] == "grip"


# ---------------------------------------------------------------------------
# get() retrieves correct entry
# ---------------------------------------------------------------------------


def test_get_returns_none_for_missing():
    log = ThoughtLog()
    assert log.get("no-such-id") is None


def test_get_returns_correct_entry_among_many():
    log = ThoughtLog()
    for i in range(5):
        log.record(make_thought(f"t-{i:03d}", confidence=i * 0.1))

    entry = log.get("t-003")
    assert entry is not None
    assert entry["id"] == "t-003"
    assert entry["confidence"] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# max_memory cap — deque evicts oldest when full
# ---------------------------------------------------------------------------


def test_max_memory_evicts_oldest():
    log = ThoughtLog(max_memory=3)
    for i in range(5):
        log.record(make_thought(f"t-{i:03d}"))

    # Oldest (t-000, t-001) should be evicted
    assert log.get("t-000") is None
    assert log.get("t-001") is None
    # Most recent three should be present
    assert log.get("t-002") is not None
    assert log.get("t-003") is not None
    assert log.get("t-004") is not None


def test_deque_length_capped():
    log = ThoughtLog(max_memory=5)
    for i in range(10):
        log.record(make_thought(f"t-{i:03d}"))
    assert len(log._store) == 5


# ---------------------------------------------------------------------------
# JSONL persistence
# ---------------------------------------------------------------------------


def test_jsonl_writes_entries():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name

    try:
        log = ThoughtLog(storage_path=path)
        log.record(make_thought("t-persist"))

        with open(path) as f:
            lines = f.readlines()

        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["id"] == "t-persist"
    finally:
        os.unlink(path)


def test_jsonl_multiple_entries():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name

    try:
        log = ThoughtLog(storage_path=path)
        for i in range(3):
            log.record(make_thought(f"t-{i}"))

        with open(path) as f:
            lines = f.readlines()

        assert len(lines) == 3
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# include_reasoning=False omits reasoning field
# ---------------------------------------------------------------------------


def test_include_reasoning_false_omits_field():
    log = ThoughtLog()
    t = make_thought("t-reason", raw_text="detailed reasoning here")
    log.record(t)

    entry = log.get("t-reason", include_reasoning=False)
    assert entry is not None
    assert "reasoning" not in entry


def test_include_reasoning_true_includes_field():
    log = ThoughtLog()
    t = make_thought("t-reason2", raw_text="detailed reasoning here")
    log.record(t)

    entry = log.get("t-reason2", include_reasoning=True)
    assert entry is not None
    assert "reasoning" in entry
    assert entry["reasoning"] == "detailed reasoning here"


# ---------------------------------------------------------------------------
# list_recent
# ---------------------------------------------------------------------------


def test_list_recent_returns_newest_last():
    log = ThoughtLog()
    for i in range(10):
        log.record(make_thought(f"t-{i:03d}"))

    recent = log.list_recent(limit=3)
    assert len(recent) == 3
    assert recent[-1]["id"] == "t-009"
