"""Unit tests for castor.confidence_gate."""

from __future__ import annotations

from castor.confidence_gate import ConfidenceGate, ConfidenceGateEnforcer, GateOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def enforcer(*gates: ConfidenceGate) -> ConfidenceGateEnforcer:
    return ConfidenceGateEnforcer(list(gates))


# ---------------------------------------------------------------------------
# Basic pass / block
# ---------------------------------------------------------------------------


def test_passes_when_above_threshold():
    e = enforcer(ConfidenceGate(scope="control", min_confidence=0.8, on_fail="block"))
    assert e.evaluate("control", 0.9) == GateOutcome.PASS


def test_blocks_when_below_threshold():
    e = enforcer(ConfidenceGate(scope="control", min_confidence=0.8, on_fail="block"))
    assert e.evaluate("control", 0.7) == GateOutcome.BLOCK


def test_escalates_when_below_threshold_and_on_fail_escalate():
    e = enforcer(ConfidenceGate(scope="nav", min_confidence=0.8, on_fail="escalate"))
    assert e.evaluate("nav", 0.7) == GateOutcome.ESCALATE


def test_bypass_when_on_fail_allow():
    e = enforcer(ConfidenceGate(scope="nav", min_confidence=0.8, on_fail="allow"))
    assert e.evaluate("nav", 0.3) == GateOutcome.BYPASS


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_confidence_exactly_at_threshold_passes():
    e = enforcer(ConfidenceGate(scope="nav", min_confidence=0.8, on_fail="block"))
    # confidence == threshold: should pass (>= check)
    assert e.evaluate("nav", 0.8) == GateOutcome.PASS


def test_confidence_zero_blocks():
    e = enforcer(ConfidenceGate(scope="nav", min_confidence=0.5, on_fail="block"))
    assert e.evaluate("nav", 0.0) == GateOutcome.BLOCK


def test_confidence_one_passes():
    e = enforcer(ConfidenceGate(scope="nav", min_confidence=0.99, on_fail="block"))
    assert e.evaluate("nav", 1.0) == GateOutcome.PASS


def test_none_confidence_triggers_gate():
    e = enforcer(ConfidenceGate(scope="nav", min_confidence=0.5, on_fail="block"))
    assert e.evaluate("nav", None) == GateOutcome.BLOCK


def test_no_gate_for_scope_passes():
    e = enforcer(ConfidenceGate(scope="control", min_confidence=0.8, on_fail="block"))
    assert e.evaluate("unknown_scope", 0.1) == GateOutcome.PASS


# ---------------------------------------------------------------------------
# dry_run / no gate always passes (no gate = PASS regardless)
# The enforcer itself has no dry_run; callers skip evaluation.
# We test the "no gate = PASS" invariant as the dry_run equivalent.
# ---------------------------------------------------------------------------


def test_empty_enforcer_always_passes():
    e = enforcer()
    assert e.evaluate("any", 0.0) == GateOutcome.PASS
    assert e.evaluate("any", None) == GateOutcome.PASS


# ---------------------------------------------------------------------------
# Per-scope default gates (RCAN spec §16.2)
# ---------------------------------------------------------------------------
from castor.confidence_gate import ConfidenceGateManager  # noqa: E402


def test_control_scope_blocks_low_confidence():
    """CONTROL scope default (min=0.75) must block confidence=0.5."""
    ConfidenceGateManager.reset_default()
    outcome = ConfidenceGateManager.check("control", 0.5)
    assert outcome == GateOutcome.BLOCK


def test_status_scope_always_passes():
    """STATUS scope default (min=0.0) must pass even confidence=0.0."""
    ConfidenceGateManager.reset_default()
    outcome = ConfidenceGateManager.check("status", 0.0)
    assert outcome == GateOutcome.PASS


def test_none_confidence_control_blocked():
    """None confidence for CONTROL scope → blocked (fail-safe)."""
    ConfidenceGateManager.reset_default()
    outcome = ConfidenceGateManager.check("control", None)
    assert outcome == GateOutcome.BLOCK


def test_none_confidence_status_passes():
    """None confidence for STATUS scope → passes (reads are safe)."""
    ConfidenceGateManager.reset_default()
    outcome = ConfidenceGateManager.check("status", None)
    assert outcome == GateOutcome.PASS
