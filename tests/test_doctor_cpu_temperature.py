"""Tests for doctor.check_cpu_temperature() (Issue #424)."""

import sys
from unittest.mock import mock_open, patch, MagicMock

import pytest

from castor.doctor import check_cpu_temperature, run_all_checks


# ------------------------------------------------------------------
# Return shape
# ------------------------------------------------------------------


def test_check_cpu_temperature_returns_tuple():
    result = check_cpu_temperature()
    assert isinstance(result, tuple)
    assert len(result) == 3


def test_check_cpu_temperature_first_element_is_bool():
    ok, _, _ = check_cpu_temperature()
    assert isinstance(ok, bool)


def test_check_cpu_temperature_second_element_is_cpu_temperature():
    _, name, _ = check_cpu_temperature()
    assert name == "CPU temperature"


def test_check_cpu_temperature_third_element_is_string():
    _, _, detail = check_cpu_temperature()
    assert isinstance(detail, str)


def test_check_cpu_temperature_never_raises():
    try:
        check_cpu_temperature()
    except Exception as exc:
        pytest.fail(f"check_cpu_temperature raised: {exc}")


# ------------------------------------------------------------------
# Linux: thermal zone files
# ------------------------------------------------------------------


def test_check_cpu_temperature_ok_when_temp_below_threshold():
    """Temperature 50°C (50000 raw) should return ok=True."""
    with patch("sys.platform", "linux"):
        with patch("glob.glob", return_value=["/sys/class/thermal/thermal_zone0/temp"]):
            with patch("builtins.open", mock_open(read_data="50000\n")):
                ok, name, detail = check_cpu_temperature()
    assert ok is True
    assert "CPU temperature" in name
    assert "50.0" in detail


def test_check_cpu_temperature_fail_when_temp_at_threshold():
    """Temperature exactly 75°C should return ok=False."""
    with patch("sys.platform", "linux"):
        with patch("glob.glob", return_value=["/sys/class/thermal/thermal_zone0/temp"]):
            with patch("builtins.open", mock_open(read_data="75000\n")):
                ok, name, detail = check_cpu_temperature()
    assert ok is False
    assert "75" in detail


def test_check_cpu_temperature_fail_when_temp_above_threshold():
    """Temperature 90°C should return ok=False with descriptive message."""
    with patch("sys.platform", "linux"):
        with patch("glob.glob", return_value=["/sys/class/thermal/thermal_zone0/temp"]):
            with patch("builtins.open", mock_open(read_data="90000\n")):
                ok, name, detail = check_cpu_temperature()
    assert ok is False
    assert "high" in detail.lower() or "90" in detail


def test_check_cpu_temperature_takes_max_across_zones():
    """When multiple thermal zones exist, the max temp is used."""
    zones = [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/thermal/thermal_zone1/temp",
    ]
    zone_values = {"0": "45000\n", "1": "80000\n"}

    def fake_read(path):
        for k, v in zone_values.items():
            if k in path:
                return v
        return None

    with patch("sys.platform", "linux"):
        with patch("glob.glob", return_value=zones):
            with patch("castor.doctor._read_thermal_zone_file", side_effect=fake_read):
                ok, name, detail = check_cpu_temperature()

    # 80°C > 75°C threshold → should fail
    assert ok is False


def test_check_cpu_temperature_no_thermal_files_returns_skip():
    """When no thermal zone files exist, returns ok=True with skip message."""
    with patch("sys.platform", "linux"):
        with patch("glob.glob", return_value=[]):
            ok, name, detail = check_cpu_temperature()
    assert ok is True
    assert "No CPU temperature data available" in detail


def test_check_cpu_temperature_unreadable_zone_skipped():
    """If zone file raises OSError, it is skipped gracefully."""
    with patch("sys.platform", "linux"):
        with patch("glob.glob", return_value=["/sys/class/thermal/thermal_zone0/temp"]):
            with patch("builtins.open", side_effect=OSError("permission denied")):
                ok, name, detail = check_cpu_temperature()
    # No readable data → skip result
    assert ok is True


# ------------------------------------------------------------------
# No thermal data (unknown platform)
# ------------------------------------------------------------------


def test_check_cpu_temperature_unknown_platform_returns_skip():
    """On an unknown platform, returns the 'no data' skip result."""
    with patch("sys.platform", "win32"):
        ok, name, detail = check_cpu_temperature()
    assert ok is True
    assert "No CPU temperature data available" in detail


# ------------------------------------------------------------------
# integration with run_all_checks
# ------------------------------------------------------------------


def test_run_all_checks_includes_cpu_temperature():
    results = run_all_checks()
    names = [r[1] for r in results]
    assert "CPU temperature" in names


def test_run_all_checks_cpu_temperature_has_correct_shape():
    results = run_all_checks()
    cpu_result = next((r for r in results if r[1] == "CPU temperature"), None)
    assert cpu_result is not None
    ok, name, detail = cpu_result
    assert isinstance(ok, bool)
    assert isinstance(detail, str)
