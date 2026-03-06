"""Unit tests for castor.telemetry (works without opentelemetry installed)."""
from __future__ import annotations


def test_has_otel_is_bool():
    from castor.telemetry import HAS_OTEL

    assert isinstance(HAS_OTEL, bool)


def test_private_has_otel_is_bool():
    import castor.telemetry as tel

    assert isinstance(tel._HAS_OTEL, bool)


def test_castor_telemetry_instantiates():
    from castor.telemetry import CastorTelemetry

    t = CastorTelemetry()
    assert t is not None


def test_init_otel_does_not_raise():
    from castor.telemetry import init_otel

    # Should not raise even if opentelemetry is not installed
    result = init_otel(service_name="test-service", exporter="console")
    assert isinstance(result, bool)


def test_trace_think_returns_context_manager():
    from castor.telemetry import trace_think

    span = trace_think(provider="test", model="fake", latency_ms=10.0, tokens=5)
    with span:
        pass  # must not raise


def test_trace_move_returns_context_manager():
    from castor.telemetry import trace_move

    span = trace_move(linear=0.5, angular=0.1, driver_mode="test")
    with span:
        pass  # must not raise


def test_get_telemetry_returns_singleton():
    from castor.telemetry import CastorTelemetry, get_telemetry

    t1 = get_telemetry()
    t2 = get_telemetry()
    assert isinstance(t1, CastorTelemetry)
    assert t1 is t2
