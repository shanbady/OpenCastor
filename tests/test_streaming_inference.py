"""Tests for castor.inference.streaming — continuous vision inference loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from castor.inference.streaming import (
    StreamingInferenceLoop,
    StreamingStats,
    _DEFAULT_FPS,
    _DEFAULT_MIN_CONFIDENCE,
    _MAX_FPS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_loop(
    confidence: float = 0.9,
    cmd: str = "move_forward",
    fps: float = 10.0,
    min_confidence: float = 0.75,
    dry_run: bool = False,
) -> StreamingInferenceLoop:
    frame = b"fake-frame-data"
    get_frame = AsyncMock(return_value=frame)
    think = AsyncMock(return_value={"confidence": confidence, "cmd": cmd})
    execute = AsyncMock()
    return StreamingInferenceLoop(
        get_frame_fn=get_frame,
        think_fn=think,
        execute_fn=execute,
        fps=fps,
        min_confidence=min_confidence,
        dry_run=dry_run,
    )


# ── Constructor ───────────────────────────────────────────────────────────────


def test_default_fps():
    loop = make_loop(fps=_DEFAULT_FPS)
    assert loop.fps == _DEFAULT_FPS


def test_fps_hard_cap():
    loop = make_loop(fps=999.0)
    assert loop.fps == _MAX_FPS


def test_default_min_confidence():
    loop = make_loop(min_confidence=_DEFAULT_MIN_CONFIDENCE)
    assert loop.min_confidence == _DEFAULT_MIN_CONFIDENCE


def test_interval_calculation():
    loop = make_loop(fps=2.0)
    assert loop.interval == pytest.approx(0.5, rel=0.01)


def test_not_running_initially():
    loop = make_loop()
    assert not loop.is_running


# ── from_config ───────────────────────────────────────────────────────────────


def test_from_config_reads_fps():
    config = {"agent": {"streaming": {"fps": 5, "min_confidence": 0.9}}}
    loop = StreamingInferenceLoop.from_config(
        config, AsyncMock(), AsyncMock(), AsyncMock()
    )
    assert loop.fps == 5.0
    assert loop.min_confidence == 0.9


def test_from_config_uses_defaults_when_missing():
    loop = StreamingInferenceLoop.from_config({}, AsyncMock(), AsyncMock(), AsyncMock())
    assert loop.fps == _DEFAULT_FPS
    assert loop.min_confidence == _DEFAULT_MIN_CONFIDENCE


def test_from_config_dry_run():
    config = {"agent": {"streaming": {"dry_run": True}}}
    loop = StreamingInferenceLoop.from_config(
        config, AsyncMock(), AsyncMock(), AsyncMock()
    )
    assert loop.dry_run is True


# ── Start / stop ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_sets_running():
    loop = make_loop()
    await loop.start()
    assert loop.is_running
    await loop.stop()


@pytest.mark.asyncio
async def test_stop_clears_running():
    loop = make_loop()
    await loop.start()
    await loop.stop()
    assert not loop.is_running


@pytest.mark.asyncio
async def test_double_start_is_safe():
    loop = make_loop()
    await loop.start()
    await loop.start()  # second call should be no-op
    assert loop.is_running
    await loop.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    loop = make_loop()
    await loop.stop()  # should not raise


# ── Confidence gate ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_passes_high_confidence():
    loop = make_loop(confidence=0.95, min_confidence=0.8)
    await loop._tick(None)
    loop._execute.assert_awaited_once()
    assert loop.stats.frames_gated_pass == 1
    assert loop.stats.actions_executed == 1


@pytest.mark.asyncio
async def test_tick_blocks_low_confidence():
    loop = make_loop(confidence=0.5, min_confidence=0.8)
    await loop._tick(None)
    loop._execute.assert_not_awaited()
    assert loop.stats.frames_gated_block == 1
    assert loop.stats.actions_executed == 0


@pytest.mark.asyncio
async def test_tick_exact_threshold_passes():
    """Confidence exactly at threshold should pass."""
    loop = make_loop(confidence=0.75, min_confidence=0.75)
    await loop._tick(None)
    assert loop.stats.frames_gated_pass == 1


# ── Dry run ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_does_not_execute():
    loop = make_loop(confidence=0.99, min_confidence=0.5, dry_run=True)
    await loop._tick(None)
    loop._execute.assert_not_awaited()
    assert loop.stats.actions_executed == 0
    assert loop.stats.frames_gated_pass == 1


# ── Stats ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_increment_on_tick():
    loop = make_loop(confidence=0.9, min_confidence=0.5)
    await loop._tick(None)
    await loop._tick(None)
    assert loop.stats.frames_captured == 2
    assert loop.stats.actions_executed == 2


def test_stats_summary_string():
    s = StreamingStats(frames_captured=10, actions_executed=5)
    summary = s.summary()
    assert "frames=10" in summary
    assert "actions=5" in summary


@pytest.mark.asyncio
async def test_stats_reset_on_restart():
    loop = make_loop()
    await loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    old_frames = loop.stats.frames_captured
    await loop.start()
    assert loop.stats.frames_captured == 0  # reset on restart
    await loop.stop()


# ── Error resilience ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_survives_execute_error():
    """Execute errors propagate from _tick; the _run loop catches them and increments errors."""
    frame = b"frame"
    get_frame = AsyncMock(return_value=frame)
    think = AsyncMock(return_value={"confidence": 0.99, "cmd": "move"})
    execute = AsyncMock(side_effect=Exception("motor fault"))
    loop = StreamingInferenceLoop(get_frame, think, execute, fps=10, min_confidence=0.5)

    # _tick raises — _run wraps this in try/except and increments errors
    # Verify that by running the full loop briefly and checking stats.errors
    await loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()
    assert loop.stats.errors > 0  # at least one error was caught
    assert loop.stats.frames_captured > 0  # frames were still captured
