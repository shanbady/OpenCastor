"""
OpenCastor Confidence Gate — F2.

Protocol-level check that blocks or escalates commands falling below
configured confidence thresholds per scope.

Config example (RCAN YAML):
    agent:
      confidence_gates:
        - scope: control
          min_confidence: 0.6
          on_fail: escalate
        - scope: nav
          min_confidence: 0.5
          on_fail: block
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional


class GateOutcome(Enum):
    PASS = "pass"
    ESCALATE = "escalate"
    BLOCK = "block"
    BYPASS = "bypass"  # on_fail: allow — command proceeds, flagged in audit


@dataclass
class ConfidenceGate:
    scope: str
    min_confidence: float
    on_fail: Literal["escalate", "block", "allow"] = "escalate"


class ConfidenceGateEnforcer:
    """Evaluates confidence gates for given scopes."""

    def __init__(self, gates: list[ConfidenceGate]):
        self._gates: dict[str, ConfidenceGate] = {g.scope: g for g in gates}

    def evaluate(self, scope: str, confidence: Optional[float]) -> GateOutcome:
        """Return the gate outcome for *scope* at *confidence*.

        Args:
            scope:      The gate scope name (e.g. ``"control"``).
            confidence: The confidence value from the Thought, or None.
                        None is treated as 0.0 for threshold comparison:
                        - CONTROL (min=0.75): None → 0.0 < 0.75 → BLOCK (fail-safe)
                        - STATUS  (min=0.0):  None → 0.0 < 0.0 → PASS  (reads are safe)

        Returns:
            :class:`GateOutcome`.PASS if no gate is configured or threshold met.
            Appropriate failure outcome if the gate triggers.
        """
        gate = self._gates.get(scope)
        if gate is None:
            return GateOutcome.PASS
        # Treat None as 0.0: CONTROL (0.75 min) blocks; STATUS (0.0 min) passes.
        effective = confidence if confidence is not None else 0.0
        if effective < gate.min_confidence:
            if gate.on_fail == "escalate":
                return GateOutcome.ESCALATE
            elif gate.on_fail == "block":
                return GateOutcome.BLOCK
            else:  # allow
                return GateOutcome.BYPASS
        return GateOutcome.PASS


# ---------------------------------------------------------------------------
# Per-scope defaults (RCAN spec §16.2)
# ---------------------------------------------------------------------------
#: Default confidence gates indexed by canonical scope name (lowercase).
#: CONTROL is strictest — motor commands have real-world consequences.
_DEFAULT_GATES: dict[str, ConfidenceGate] = {
    "control": ConfidenceGate(scope="control", min_confidence=0.75, on_fail="block"),
    "config": ConfidenceGate(scope="config", min_confidence=0.65, on_fail="block"),
    "training": ConfidenceGate(scope="training", min_confidence=0.60, on_fail="block"),
    "status": ConfidenceGate(scope="status", min_confidence=0.0, on_fail="block"),
}


class ConfidenceGateManager:
    """Singleton-style manager that holds the active per-scope gate set.

    Usage::

        outcome = ConfidenceGateManager.default().check("control", 0.5)
        if outcome == GateOutcome.BLOCK:
            ...
    """

    _default: ConfidenceGateEnforcer | None = None

    @classmethod
    def default(cls) -> ConfidenceGateEnforcer:
        """Return (or create) the default enforcer with RCAN §16.2 thresholds."""
        if cls._default is None:
            cls._default = ConfidenceGateEnforcer(list(_DEFAULT_GATES.values()))
        return cls._default

    @classmethod
    def reset_default(cls) -> None:
        """Reset the cached default enforcer (useful in tests)."""
        cls._default = None

    @classmethod
    def check(cls, scope: str, confidence: Optional[float]) -> GateOutcome:
        """Convenience: evaluate *scope* / *confidence* against the default gates."""
        return cls.default().evaluate(scope, confidence)
