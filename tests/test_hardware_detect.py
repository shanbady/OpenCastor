"""Unit tests for castor.hardware_detect."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# scan_i2c
# ---------------------------------------------------------------------------


def test_scan_i2c_returns_empty_on_non_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from castor.hardware_detect import scan_i2c

    result = scan_i2c()
    assert result == []


def test_scan_i2c_parses_output(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fake_output = (
        "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
        "00:          -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "40: 40 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
        "70: -- -- -- -- -- -- -- --\n"
    )
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_output

    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1"]),
        patch("subprocess.run", return_value=mock_result),
    ):
        from importlib import reload
        import castor.hardware_detect as hd

        devices = hd.scan_i2c()

    assert any(d["address"] == "0x40" for d in devices)


def test_scan_i2c_handles_file_not_found(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["i2c-1"]),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        from castor.hardware_detect import scan_i2c

        result = scan_i2c()
    assert result == []


# ---------------------------------------------------------------------------
# scan_usb_serial
# ---------------------------------------------------------------------------


def test_scan_usb_serial_returns_ports(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with (
        patch("os.path.isdir", return_value=True),
        patch("os.listdir", return_value=["ttyUSB0", "ttyACM0", "tty0"]),
    ):
        from castor.hardware_detect import scan_usb_serial

        ports = scan_usb_serial()

    assert "/dev/ttyUSB0" in ports
    assert "/dev/ttyACM0" in ports
    # tty0 should not be included
    assert "/dev/tty0" not in ports


def test_scan_usb_serial_empty_on_non_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from castor.hardware_detect import scan_usb_serial

    assert scan_usb_serial() == []


# ---------------------------------------------------------------------------
# detect_hardware
# ---------------------------------------------------------------------------


def test_detect_hardware_returns_expected_keys():
    with (
        patch("castor.hardware_detect.scan_i2c", return_value=[]),
        patch("castor.hardware_detect.scan_usb_serial", return_value=[]),
        patch("castor.hardware_detect.scan_usb_descriptors", return_value=[]),
        patch("castor.hardware_detect.scan_cameras", return_value=[]),
        patch("castor.hardware_detect._detect_platform", return_value="generic"),
    ):
        from castor.hardware_detect import detect_hardware

        hw = detect_hardware()

    for key in ("i2c_devices", "usb_serial", "cameras", "platform"):
        assert key in hw


# ---------------------------------------------------------------------------
# suggest_preset
# ---------------------------------------------------------------------------


def test_suggest_preset_generic_when_nothing():
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "generic",
    }
    preset, confidence, reason = suggest_preset(hw)
    assert isinstance(preset, str)
    assert len(preset) > 0


def test_suggest_preset_rpi_rc_car_with_pca9685():
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [{"bus": 1, "address": "0x40"}],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "rpi",
    }
    preset, confidence, reason = suggest_preset(hw)
    assert "rpi_rc_car" in preset


def test_suggest_preset_lego_via_usb_descriptor():
    from castor.hardware_detect import suggest_preset

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": ["bus 001 device 002: id 0694:0005 lego group"],
        "cameras": [],
        "platform": "generic",
    }
    preset, _conf, _reason = suggest_preset(hw)
    assert "lego" in preset


# ---------------------------------------------------------------------------
# _detect_platform
# ---------------------------------------------------------------------------


def test_detect_platform_returns_string():
    from castor.hardware_detect import _detect_platform

    result = _detect_platform()
    assert result in ("rpi", "jetson", "generic")


def test_detect_platform_rpi(tmp_path):
    model_file = tmp_path / "model"
    model_file.write_text("Raspberry Pi 4 Model B")
    with patch("builtins.open", lambda path, *a, **kw: open(model_file)):
        from castor.hardware_detect import _detect_platform

        assert _detect_platform() == "rpi"


# ---------------------------------------------------------------------------
# print_scan_results
# ---------------------------------------------------------------------------


def test_print_scan_results_empty(capsys):
    from castor.hardware_detect import print_scan_results

    hw = {
        "i2c_devices": [],
        "usb_serial": [],
        "usb_descriptors": [],
        "cameras": [],
        "platform": "generic",
    }
    print_scan_results(hw)
    out = capsys.readouterr().out
    assert "Hardware Scan Results" in out


def test_print_scan_results_full(capsys):
    from castor.hardware_detect import print_scan_results

    hw = {
        "i2c_devices": [{"bus": 1, "address": "0x40"}],
        "usb_serial": ["/dev/ttyUSB0"],
        "usb_descriptors": [],
        "cameras": [{"type": "usb", "device": "/dev/video0", "accessible": True}],
        "platform": "rpi",
    }
    print_scan_results(hw)
    out = capsys.readouterr().out
    assert "Hardware Scan Results" in out
    assert "0x40" in out
    assert "ttyUSB0" in out
