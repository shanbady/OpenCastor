"""SO-ARM101 hardware module for OpenCastor.

Runtime arm commands must go through :func:`write_arm_command` so that
every motor position/velocity write is enforced by the SafetyLayer.
"""

from __future__ import annotations

from castor.hardware.so_arm101.safety_bridge import write_arm_command

__all__ = ["write_arm_command"]
