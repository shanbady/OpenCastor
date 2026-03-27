"""OpenCastor: The Universal Runtime for Embodied AI."""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("opencastor")
except Exception:
    __version__ = "2026.3.20.4"  # fallback


def initialize_safety(safety_layer, config: dict):
    """Initialize and wire the full Protocol 66 safety stack.

    Creates a :class:`~castor.safety.monitor.SensorMonitor` from the
    ``config["monitor"]`` section and connects it to *safety_layer* via
    :func:`~castor.safety.monitor.wire_safety_layer`.  Call this once during
    runtime startup, then call ``monitor.start()`` to begin polling.

    Args:
        safety_layer: A :class:`~castor.fs.safety.SafetyLayer` (or
                      ``CastorFS.safety``) instance.
        config:       Runtime config dict (uses ``config["monitor"]`` sub-key).

    Returns:
        The started :class:`~castor.safety.monitor.SensorMonitor` instance.
    """
    from castor.safety.monitor import MonitorThresholds, SensorMonitor, wire_safety_layer

    monitor_cfg = config.get("monitor", {})
    thresholds_cfg = monitor_cfg.get("thresholds", {})
    thresholds = MonitorThresholds(**thresholds_cfg) if thresholds_cfg else None
    monitor = SensorMonitor(
        thresholds=thresholds,
        interval=float(monitor_cfg.get("interval", 5.0)),
        consecutive_critical=int(monitor_cfg.get("consecutive_critical", 3)),
    )
    wire_safety_layer(monitor, safety_layer)
    return monitor


def install_hint(extra: str) -> str:
    """Return the install command for an optional extra, aware of uv environments.

    When the current Python environment is managed by **uv**, the standard
    ``pip install`` command won't persist across ``uv run`` invocations
    (``uv sync`` removes packages not in the lockfile).  This helper detects
    that case and returns ``uv add`` instead, which adds the extra to the
    project lockfile so it survives future syncs.
    """
    import os
    import sys

    cfg_path = os.path.join(sys.prefix, "pyvenv.cfg")
    try:
        with open(cfg_path) as fh:
            for line in fh:
                if line.startswith("uv"):
                    return f"uv add opencastor[{extra}]"
    except OSError:
        pass
    return f"pip install opencastor[{extra}]"


__all__ = ["__version__", "initialize_safety", "install_hint"]
