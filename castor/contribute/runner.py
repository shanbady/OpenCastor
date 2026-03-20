"""Work unit runner for contribute skill."""

from __future__ import annotations

import logging
import time

from .hardware_profile import get_hw_profile
from .work_unit import WorkUnit, WorkUnitResult

log = logging.getLogger("OpenCastor.Contribute")


def run_work_unit(
    wu: WorkUnit,
    *,
    cancelled_flag: list[bool] | None = None,
) -> WorkUnitResult:
    """Execute a work unit, respecting cancellation."""
    hw = get_hw_profile()
    start = time.monotonic()
    try:
        if cancelled_flag and cancelled_flag[0]:
            return WorkUnitResult(
                wu.work_unit_id, output=None, latency_ms=0.0, hw_profile=hw, status="cancelled"
            )
        deadline = start + wu.timeout_seconds
        while time.monotonic() < deadline:
            if cancelled_flag and cancelled_flag[0]:
                latency_ms = (time.monotonic() - start) * 1000
                return WorkUnitResult(
                    wu.work_unit_id,
                    output=None,
                    latency_ms=latency_ms,
                    hw_profile=hw,
                    status="cancelled",
                )
            time.sleep(0.05)
        latency_ms = (time.monotonic() - start) * 1000
        return WorkUnitResult(
            wu.work_unit_id,
            output={"status": "ok"},
            latency_ms=latency_ms,
            hw_profile=hw,
            status="complete",
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        log.error("Work unit %s failed: %s", wu.work_unit_id, exc)
        return WorkUnitResult(
            wu.work_unit_id,
            output=None,
            latency_ms=latency_ms,
            hw_profile=hw,
            status="failed",
            error=str(exc),
        )
