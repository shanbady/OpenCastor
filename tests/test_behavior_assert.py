"""Tests for BehaviorRunner assert step and _eval_condition — issue #373."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


def _make_runner():
    from castor.behaviors import BehaviorRunner

    runner = BehaviorRunner(driver=MagicMock(), brain=None, speaker=None)
    runner._running = True
    return runner


# ── Dispatch table ─────────────────────────────────────────────────────────────


def test_assert_registered():
    runner = _make_runner()
    assert "assert" in runner._step_handlers


# ── Missing condition key ──────────────────────────────────────────────────────


def test_assert_missing_condition_skips(caplog):
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_assert({"on_fail": "stop"})
    assert any("condition" in r.message.lower() for r in caplog.records)
    assert runner._running is True


# ── Passing conditions ─────────────────────────────────────────────────────────


def test_assert_passes_gt():
    runner = _make_runner()
    runner._step_assert({"condition": "5 > 3"})
    assert runner._running is True


def test_assert_passes_lt():
    runner = _make_runner()
    runner._step_assert({"condition": "1 < 2"})
    assert runner._running is True


def test_assert_passes_gte():
    runner = _make_runner()
    runner._step_assert({"condition": "3 >= 3"})
    assert runner._running is True


def test_assert_passes_lte():
    runner = _make_runner()
    runner._step_assert({"condition": "2 <= 2"})
    assert runner._running is True


def test_assert_passes_eq():
    runner = _make_runner()
    runner._step_assert({"condition": "4 == 4"})
    assert runner._running is True


def test_assert_passes_neq():
    runner = _make_runner()
    runner._step_assert({"condition": "4 != 5"})
    assert runner._running is True


# ── Failing conditions — on_fail: stop (default) ──────────────────────────────


def test_assert_fail_stops_runner():
    runner = _make_runner()
    runner._step_assert({"condition": "1 > 100"})
    assert runner._running is False


def test_assert_fail_default_on_fail_is_stop():
    runner = _make_runner()
    runner._step_assert({"condition": "0 > 1"})
    assert runner._running is False


# ── Failing conditions — on_fail: warn ────────────────────────────────────────


def test_assert_fail_warn_does_not_stop(caplog):
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_assert({"condition": "0 > 1", "on_fail": "warn"})
    assert runner._running is True
    assert any("failed" in r.message.lower() or "assert" in r.message.lower() for r in caplog.records)


# ── $var substitution ─────────────────────────────────────────────────────────


def test_assert_uses_var_substitution_pass():
    runner = _make_runner()
    runner._vars["battery"] = 80.0
    runner._step_assert({"condition": "$var.battery > 20"})
    assert runner._running is True


def test_assert_uses_var_substitution_fail():
    runner = _make_runner()
    runner._vars["battery"] = 5.0
    runner._step_assert({"condition": "$var.battery > 20"})
    assert runner._running is False


def test_assert_unset_var_causes_warn(caplog):
    runner = _make_runner()
    with caplog.at_level(logging.WARNING, logger="OpenCastor.Behaviors"):
        runner._step_assert({"condition": "$var.missing > 10", "on_fail": "warn"})
    # unset var → "None" → cannot evaluate as float → warning logged
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ── _eval_condition ────────────────────────────────────────────────────────────


def test_eval_condition_gt_true():
    runner = _make_runner()
    assert runner._eval_expr("10 > 5") is True


def test_eval_condition_gt_false():
    runner = _make_runner()
    assert runner._eval_expr("1 > 5") is False


def test_eval_condition_no_operator_raises():
    runner = _make_runner()
    with pytest.raises(ValueError):
        runner._eval_expr("no operator here")


def test_eval_condition_non_numeric_raises():
    runner = _make_runner()
    with pytest.raises(ValueError):
        runner._eval_expr("abc > 5")
