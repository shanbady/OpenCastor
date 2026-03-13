"""
RCAN §19 Behavior/Skill Invocation Protocol.

Implements INVOKE and INVOKE_RESULT message types for triggering
named behaviors/skills on a robot runtime.

Spec: https://rcan.dev/spec/section-19/
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from castor.rcan.message import MessageType

logger = logging.getLogger("OpenCastor.RCAN.Invoke")


@dataclass
class InvokeRequest:
    """INVOKE message payload (§19.2)."""

    skill: str  # Skill/behavior name (e.g. "nav.go_to", "arm.pick")
    params: dict[str, Any] = field(default_factory=dict)
    invoke_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout_ms: Optional[int] = None  # None = no explicit timeout
    reply_to: Optional[str] = None  # RURI to send INVOKE_RESULT to

    def to_message(self, source_ruri: str, target_ruri: str) -> dict[str, Any]:
        """Serialize to RCAN message format.

        Uses ``msg_id`` as the wire-format correlation field per §19.3 of the
        RCAN specification.  The Python attribute is kept as ``invoke_id`` for
        backward-compatibility with existing call-sites.
        """
        payload: dict[str, Any] = {
            "skill": self.skill,
            "params": self.params,
            "reply_to": self.reply_to,
        }
        if self.timeout_ms is not None:
            payload["timeout_ms"] = self.timeout_ms
        return {
            "type": MessageType.INVOKE,
            "source_ruri": source_ruri,
            "target_ruri": target_ruri,
            "msg_id": self.invoke_id,  # §19.3 — wire field is msg_id
            "payload": payload,
            "timestamp": time.time(),
        }


@dataclass
class InvokeResult:
    """INVOKE_RESULT message payload (§19.3)."""

    invoke_id: str
    status: str  # "success" | "failure" | "timeout" | "not_found"
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: Optional[float] = None

    def to_message(self, source_ruri: str, target_ruri: str) -> dict[str, Any]:
        """Serialize to RCAN message format.

        Uses ``reply_to`` as the wire-format correlation field per §19.3 of the
        RCAN specification, echoing the ``msg_id`` from the originating INVOKE.
        """
        return {
            "type": MessageType.INVOKE_RESULT,
            "source_ruri": source_ruri,
            "target_ruri": target_ruri,
            "reply_to": self.invoke_id,  # §19.3 — correlates to INVOKE msg_id
            "payload": {
                "status": self.status,
                "result": self.result,
                "error": self.error,
                "duration_ms": self.duration_ms,
            },
            "timestamp": time.time(),
        }


class SkillRegistry:
    """Registry of callable skills/behaviors (§19.2).

    Skills are registered by name and invoked via INVOKE messages.
    Each skill is a callable that accepts params dict and returns result dict.

    Example::

        registry = SkillRegistry()

        @registry.register("nav.go_to")
        def go_to(params):
            x, y = params["x"], params["y"]
            # ... navigation logic ...
            return {"reached": True, "final_pos": [x, y]}
    """

    def __init__(self) -> None:
        self._skills: dict[str, Callable] = {}

    def register(self, name: str) -> Callable:
        """Decorator to register a skill by name."""

        def decorator(fn: Callable) -> Callable:
            self._skills[name] = fn
            logger.debug("Registered skill: %s", name)
            return fn

        return decorator

    def register_fn(self, name: str, fn: Callable) -> None:
        """Register a skill function directly."""
        self._skills[name] = fn
        logger.debug("Registered skill: %s", name)

    def has(self, name: str) -> bool:
        """Check if a skill is registered."""
        return name in self._skills

    def list_skills(self) -> list[str]:
        """Return list of registered skill names."""
        return list(self._skills.keys())

    def invoke(self, request: InvokeRequest) -> InvokeResult:
        """Invoke a skill by name with params. Returns InvokeResult.

        If ``request.timeout_ms`` is set, the skill is executed in a thread and
        cancelled (best-effort) once the deadline elapses, returning a
        ``"timeout"`` result instead of blocking indefinitely.

        Args:
            request: InvokeRequest with skill name, params, and optional timeout.

        Returns:
            InvokeResult with status and result or error.
        """
        start = time.monotonic()

        if not self.has(request.skill):
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="not_found",
                error=f"Skill '{request.skill}' not registered. Available: {self.list_skills()}",
            )

        timeout_s = request.timeout_ms / 1000.0 if request.timeout_ms is not None else None

        # Run the skill in a thread so we can enforce timeout_ms without blocking.
        # shutdown(wait=False) abandons the thread on timeout rather than waiting for it
        # to finish (Python threads cannot be forcibly killed, but we stop blocking on them).
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._skills[request.skill], request.params)
        try:
            raw = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False)
            logger.warning("Skill '%s' timed out after %d ms", request.skill, request.timeout_ms)
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="timeout",
                error=f"Skill '{request.skill}' timed out after {request.timeout_ms} ms",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except TimeoutError:
            executor.shutdown(wait=False)
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="timeout",
                error="Skill execution timed out",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            executor.shutdown(wait=False)
            logger.exception("Skill '%s' raised exception", request.skill)
            return InvokeResult(
                invoke_id=request.invoke_id,
                status="failure",
                error=str(exc),
                duration_ms=(time.monotonic() - start) * 1000,
            )
        executor.shutdown(wait=False)

        duration_ms = (time.monotonic() - start) * 1000
        return InvokeResult(
            invoke_id=request.invoke_id,
            status="success",
            result=raw if isinstance(raw, dict) else {"value": raw},
            duration_ms=duration_ms,
        )
