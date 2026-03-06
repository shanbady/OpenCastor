"""
Prometheus metrics exporter for OpenCastor.

Exposes robot telemetry, safety events, provider latency, and action
metrics in Prometheus text format at /metrics.

Config (robot.rcan.yaml):
    telemetry:
      prometheus:
        enabled: true
        port: 9090          # separate scrape port (optional)
        path: /metrics      # on main gateway port if port not set

Metrics exported:
    opencastor_action_total{action_type, approved}         counter
    opencastor_action_duration_ms{action_type}             histogram (buckets)
    opencastor_safety_blocks_total{action_type, reason}    counter
    opencastor_provider_latency_ms{provider, model}        histogram
    opencastor_confidence_gate_value{action_type}          gauge (last value)
    opencastor_sensor_distance_mm                          gauge
    opencastor_battery_percent                             gauge
    opencastor_uptime_seconds                              gauge
    opencastor_commitment_records_total                    counter
    opencastor_failover_total{from_provider, to_provider}  counter
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class PrometheusRegistry:
    """
    Lightweight Prometheus text-format registry.

    Zero external dependencies — generates /metrics output directly.
    For production use, swap this with the official prometheus_client library.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Counters: {metric_name: {label_tuple: float}}
        self._counters: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        # Gauges: {metric_name: {label_tuple: float}}
        self._gauges: dict[str, dict[tuple, float]] = defaultdict(lambda: defaultdict(float))
        # Histograms: {metric_name: {label_tuple: [sum, count, {bucket: count}]}}
        self._histograms: dict[str, dict[tuple, list]] = defaultdict(dict)
        self._help: dict[str, str] = {}
        self._type: dict[str, str] = {}

        # Pre-register known metrics
        self._register_metrics()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _register_metrics(self) -> None:
        metrics = [
            ("opencastor_action_total", "counter", "Total robot actions by type and approval status"),
            ("opencastor_safety_blocks_total", "counter", "Safety-blocked actions by type and reason"),
            ("opencastor_provider_latency_ms_sum", "gauge", "Provider inference latency sum (ms)"),
            ("opencastor_provider_latency_ms_count", "gauge", "Provider inference call count"),
            ("opencastor_action_duration_ms_sum", "gauge", "Action execution duration sum (ms)"),
            ("opencastor_action_duration_ms_count", "gauge", "Action execution duration count"),
            ("opencastor_confidence_gate_value", "gauge", "Last confidence gate value per action type"),
            ("opencastor_sensor_distance_mm", "gauge", "Current sensor distance reading (mm)"),
            ("opencastor_battery_percent", "gauge", "Battery charge percentage"),
            ("opencastor_uptime_seconds", "gauge", "Robot uptime in seconds"),
            ("opencastor_commitment_records_total", "counter", "Total CommitmentRecords sealed"),
            ("opencastor_failover_total", "counter", "Provider failover events"),
            ("opencastor_hitl_gate_pending", "gauge", "HiTL gate pending approvals"),
        ]
        for name, metric_type, help_text in metrics:
            self._help[name] = help_text
            self._type[name] = metric_type

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def inc_counter(self, name: str, labels: dict | None = None, value: float = 1.0) -> None:
        with self._lock:
            key = tuple(sorted((labels or {}).items()))
            self._counters[name][key] += value

    def set_gauge(self, name: str, value: float, labels: dict | None = None) -> None:
        with self._lock:
            key = tuple(sorted((labels or {}).items()))
            self._gauges[name][key] = value

    def observe_histogram(self, name: str, value: float, labels: dict | None = None) -> None:
        """Record a histogram observation (simplified: stores sum + count)."""
        with self._lock:
            key = tuple(sorted((labels or {}).items()))
            sum_key = f"{name}_sum"
            count_key = f"{name}_count"
            self._gauges[sum_key][key] = self._gauges[sum_key].get(key, 0.0) + value
            self._gauges[count_key][key] = self._gauges[count_key].get(key, 0.0) + 1

    # ------------------------------------------------------------------
    # Domain-specific helpers
    # ------------------------------------------------------------------

    def record_action(self, action_type: str, approved: bool, duration_ms: float) -> None:
        self.inc_counter("opencastor_action_total", {
            "action_type": action_type, "approved": str(approved).lower()
        })
        self.observe_histogram("opencastor_action_duration_ms", duration_ms, {
            "action_type": action_type
        })

    def record_safety_block(self, action_type: str, reason: str) -> None:
        short_reason = reason[:40].replace("\n", " ") if reason else "unknown"
        self.inc_counter("opencastor_safety_blocks_total", {
            "action_type": action_type, "reason": short_reason
        })

    def record_provider_latency(self, provider: str, latency_ms: float, model: str = "") -> None:
        self.observe_histogram("opencastor_provider_latency_ms", latency_ms, {
            "provider": provider, "model": model
        })

    def record_confidence(self, action_type: str, confidence: float) -> None:
        self.set_gauge("opencastor_confidence_gate_value", confidence, {
            "action_type": action_type
        })

    def record_commitment(self) -> None:
        self.inc_counter("opencastor_commitment_records_total")

    def record_failover(self, from_provider: str, to_provider: str) -> None:
        self.inc_counter("opencastor_failover_total", {
            "from_provider": from_provider, "to_provider": to_provider
        })

    def update_sensor(self, distance_mm: float | None = None, battery_pct: float | None = None) -> None:
        if distance_mm is not None:
            self.set_gauge("opencastor_sensor_distance_mm", distance_mm)
        if battery_pct is not None:
            self.set_gauge("opencastor_battery_percent", battery_pct)

    def update_uptime(self, boot_time: float) -> None:
        self.set_gauge("opencastor_uptime_seconds", time.time() - boot_time)

    # ------------------------------------------------------------------
    # Text format rendering
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            # Counters
            for name, label_map in self._counters.items():
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                    lines.append(f"# TYPE {name} counter")
                for labels_tuple, value in label_map.items():
                    label_str = _format_labels(labels_tuple)
                    lines.append(f"{name}{label_str} {value}")

            # Gauges
            for name, label_map in self._gauges.items():
                if name in self._help:
                    lines.append(f"# HELP {name} {self._help[name]}")
                    lines.append(f"# TYPE {name} gauge")
                for labels_tuple, value in label_map.items():
                    label_str = _format_labels(labels_tuple)
                    lines.append(f"{name}{label_str} {value}")

        return "\n".join(lines) + "\n"


def _format_labels(labels_tuple: tuple) -> str:
    if not labels_tuple:
        return ""
    parts = [f'{k}="{v}"' for k, v in labels_tuple]
    return "{" + ",".join(parts) + "}"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: PrometheusRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> PrometheusRegistry:
    """Return (or create) the module-level PrometheusRegistry singleton."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = PrometheusRegistry()
    return _registry
