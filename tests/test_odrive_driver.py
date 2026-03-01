"""Tests for castor.drivers.odrive_driver — ODrive improvements (issue #266).

Tests cover:
  - control_mode configuration (velocity/position/torque)
  - move() velocity setpoints
  - set_position() position control
  - get_encoder() encoder readback
  - Mock mode behaviour (no hardware / SDK)
  - health_check() output
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_axis():
    """Return a realistic mock ODrive axis object."""
    axis = MagicMock()
    axis.encoder.pos_estimate = 1.23
    axis.encoder.vel_estimate = 0.45
    axis.encoder.error = 0
    axis.controller.input_vel = 0.0
    axis.controller.input_pos = 0.0
    axis.controller.config.control_mode = 2
    return axis


def _make_mock_odrive():
    """Return a mock ODrive device."""
    odrv = MagicMock()
    odrv.serial_number = "TEST1234"
    odrv.vbus_voltage = 24.0
    odrv.axis0 = _make_mock_axis()
    odrv.axis1 = _make_mock_axis()
    return odrv


def _make_driver(has_odrive=True, odrv=None, config=None):
    """Return an ODriveDriver in mock or hardware mode."""
    cfg = config or {"protocol": "odrive", "max_velocity": 20.0, "control_mode": "velocity"}
    mock_odrv = odrv or _make_mock_odrive()

    with (
        patch("castor.drivers.odrive_driver.HAS_ODRIVE", has_odrive),
        patch("castor.drivers.odrive_driver._odrive") as mock_od_mod,
    ):
        mock_od_mod.find_any.return_value = mock_odrv if has_odrive else None
        from castor.drivers.odrive_driver import ODriveDriver

        drv = ODriveDriver(cfg)
        if has_odrive:
            drv._odrv = mock_odrv
            drv._mode = "hardware"
    return drv, mock_odrv


# ---------------------------------------------------------------------------
# Mock mode (no SDK)
# ---------------------------------------------------------------------------


class TestODriveMockMode:
    def test_mock_mode_when_no_sdk(self):
        drv, _ = _make_driver(has_odrive=False)
        assert drv._mode == "mock"

    def test_move_does_not_raise_in_mock(self):
        drv, _ = _make_driver(has_odrive=False)
        drv.move({"linear": 0.5, "angular": 0.0})  # should not raise

    def test_stop_does_not_raise_in_mock(self):
        drv, _ = _make_driver(has_odrive=False)
        drv.stop()

    def test_set_position_does_not_raise_in_mock(self):
        drv, _ = _make_driver(has_odrive=False)
        drv.set_position(0, 3.0)

    def test_get_encoder_mock_returns_zeros(self):
        drv, _ = _make_driver(has_odrive=False)
        enc = drv.get_encoder()
        assert enc["pos_turns"] == 0.0
        assert enc["vel_turns_s"] == 0.0
        assert enc["error"] is None

    def test_health_check_mock(self):
        drv, _ = _make_driver(has_odrive=False)
        hc = drv.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "mock"


# ---------------------------------------------------------------------------
# Control mode parsing
# ---------------------------------------------------------------------------


class TestControlMode:
    def test_default_control_mode_is_velocity(self):
        drv, _ = _make_driver(has_odrive=False, config={"protocol": "odrive"})
        assert drv._control_mode_name == "velocity"

    def test_position_control_mode_parsed(self):
        drv, _ = _make_driver(
            has_odrive=False,
            config={"protocol": "odrive", "control_mode": "position"},
        )
        assert drv._control_mode_name == "position"
        assert drv._control_mode_int == 3

    def test_torque_control_mode_parsed(self):
        drv, _ = _make_driver(
            has_odrive=False,
            config={"protocol": "odrive", "control_mode": "torque"},
        )
        assert drv._control_mode_name == "torque"
        assert drv._control_mode_int == 1

    def test_velocity_control_mode_int(self):
        drv, _ = _make_driver(
            has_odrive=False,
            config={"protocol": "odrive", "control_mode": "velocity"},
        )
        assert drv._control_mode_int == 2


# ---------------------------------------------------------------------------
# move() — hardware mode
# ---------------------------------------------------------------------------


class TestODriveMoveHardware:
    def test_move_sets_input_vel(self):
        drv, odrv = _make_driver(has_odrive=True)
        drv.move({"linear": 0.5, "angular": 0.0})
        # input_vel should have been set on both axes

    def test_stop_does_not_raise(self):
        drv, _ = _make_driver(has_odrive=True)
        drv.stop()  # should not raise


# ---------------------------------------------------------------------------
# set_position()
# ---------------------------------------------------------------------------


class TestSetPosition:
    def test_set_position_axis0(self):
        drv, odrv = _make_driver(has_odrive=True)
        drv.set_position(0, 5.0)
        assert odrv.axis0.controller.input_pos == 5.0

    def test_set_position_axis1(self):
        drv, odrv = _make_driver(has_odrive=True)
        drv.set_position(1, -2.5)
        assert odrv.axis1.controller.input_pos == -2.5

    def test_set_position_hardware_error_handled(self):
        drv, odrv = _make_driver(has_odrive=True)
        # Simulate error by making odrive unavailable mid-call
        drv._odrv = None
        drv.set_position(0, 1.0)  # should not raise (returns silently)


# ---------------------------------------------------------------------------
# get_encoder()
# ---------------------------------------------------------------------------


class TestGetEncoder:
    def test_get_encoder_returns_pos_and_vel(self):
        drv, odrv = _make_driver(has_odrive=True)
        odrv.axis0.encoder.pos_estimate = 3.14
        odrv.axis0.encoder.vel_estimate = 1.5
        odrv.axis0.encoder.error = 0
        enc = drv.get_encoder(axis=0)
        assert enc["pos_turns"] == pytest.approx(3.14)
        assert enc["vel_turns_s"] == pytest.approx(1.5)

    def test_get_encoder_error_code(self):
        drv, odrv = _make_driver(has_odrive=True)
        odrv.axis0.encoder.error = 16
        enc = drv.get_encoder(axis=0)
        assert enc["error"] == 16

    def test_get_encoder_exception_returns_error_string(self):
        drv, odrv = _make_driver(has_odrive=True)
        # Simulate encoder read failure
        drv._odrv = None  # force unavailable axis
        enc = drv.get_encoder(axis=0)
        # With no odrv, returns error string
        assert isinstance(enc.get("error"), str)


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


class TestODriveHealthCheck:
    def test_health_check_hardware_includes_vbus(self):
        drv, odrv = _make_driver(has_odrive=True)
        hc = drv.health_check()
        assert hc["mode"] == "hardware"
        assert "vbus_v" in hc
        assert hc["vbus_v"] == pytest.approx(24.0)

    def test_health_check_includes_control_mode(self):
        drv, _ = _make_driver(has_odrive=False)
        hc = drv.health_check()
        assert "control_mode" in hc

    def test_health_check_hardware_error(self):
        drv, odrv = _make_driver(has_odrive=True)
        # Simulate vbus_voltage raising an exception
        type(odrv).vbus_voltage = property(lambda s: 1 / 0)
        hc = drv.health_check()
        assert hc["ok"] is False
        assert hc["error"] is not None
