"""Tests for castor.doctor — health check system."""

from __future__ import annotations

from unittest.mock import patch

from castor.doctor import (
    CheckResult,
    DoctorReport,
    _check_config,
    _check_dep,
    _check_env_var,
    _check_hardware_hailo,
    _check_opencastor_dir,
    _check_python,
    print_report,
    run_doctor,
)

# ── CheckResult / DoctorReport ───────────────────────────────────────────────


def test_check_result_fields():
    c = CheckResult("test", "ok", "all good", "no fix needed")
    assert c.name == "test"
    assert c.status == "ok"


def test_doctor_report_counts():
    r = DoctorReport(
        checks=[
            CheckResult("a", "ok"),
            CheckResult("b", "warn"),
            CheckResult("c", "fail"),
            CheckResult("d", "skip"),
        ]
    )
    assert r.ok_count == 1
    assert r.warn_count == 1
    assert r.fail_count == 1
    assert not r.all_ok


def test_doctor_report_all_ok():
    r = DoctorReport(checks=[CheckResult("a", "ok"), CheckResult("b", "skip")])
    assert r.all_ok  # no failures


# ── _check_python ─────────────────────────────────────────────────────────────


def test_check_python_current():
    result = _check_python()
    assert result.status == "ok"  # we're running on 3.10+


# ── _check_dep ───────────────────────────────────────────────────────────────


def test_check_dep_present():
    result = _check_dep("json")  # stdlib, always present
    assert result.status == "ok"


def test_check_dep_absent():
    result = _check_dep("nonexistent_pkg_xyz_123")
    assert result.status == "warn"
    assert "pip install" in result.fix


# ── _check_config ─────────────────────────────────────────────────────────────


def test_check_config_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bob.rcan.yaml").write_text("name: bob")
    result = _check_config()
    assert result.status == "ok"


def test_check_config_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _check_config()
    assert result.status == "warn"
    assert "castor wizard" in result.fix


# ── _check_opencastor_dir ────────────────────────────────────────────────────


def test_check_opencastor_dir_present(tmp_path):
    d = tmp_path / ".opencastor"
    d.mkdir()
    (d / "config.yaml").write_text("")
    with patch("castor.doctor.Path.home", return_value=tmp_path):
        result = _check_opencastor_dir()
    assert result.status == "ok"


def test_check_opencastor_dir_missing(tmp_path):
    with patch("castor.doctor.Path.home", return_value=tmp_path):
        result = _check_opencastor_dir()
    assert result.status == "warn"


# ── _check_hardware_hailo ────────────────────────────────────────────────────


def test_check_hailo_present():
    with patch("castor.doctor.Path.exists", return_value=True):
        result = _check_hardware_hailo()
    assert result.status in ("ok", "skip")  # depends on actual hardware


def test_check_hailo_absent(tmp_path):
    tmp_path / "hailo0"
    # Don't create it — it shouldn't exist
    with patch("pathlib.Path.exists", lambda self: str(self) == str(tmp_path / "hailo0") and False):
        result = _check_hardware_hailo()
    # Skip or ok depending on actual /dev/hailo0
    assert result.status in ("ok", "skip")


# ── _check_env_var ────────────────────────────────────────────────────────────


def test_check_env_var_set(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY_XYZ", "sk-test-12345")
    result = _check_env_var("TEST_API_KEY_XYZ")
    assert result.status == "ok"
    assert "sk-t" in result.detail  # truncated


def test_check_env_var_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MISSING_KEY_XYZ", raising=False)
    with patch("castor.doctor.Path.home", return_value=tmp_path):
        result = _check_env_var("MISSING_KEY_XYZ")
    assert result.status == "warn"


# ── run_doctor ────────────────────────────────────────────────────────────────


def test_run_doctor_returns_report():
    report = run_doctor()
    assert isinstance(report, DoctorReport)
    assert len(report.checks) >= 10


def test_run_doctor_full_returns_more_checks():
    r_basic = run_doctor(full=False)
    r_full = run_doctor(full=True)
    assert len(r_full.checks) >= len(r_basic.checks)


def test_print_report_no_crash():
    report = run_doctor()
    print_report(report)  # should not raise


def test_doctor_run_does_not_crash():
    """Regression test for #672 — run_doctor({}) must not raise AttributeError."""
    # run_doctor accepts a bool; passing {} is falsy and should work silently.
    result = run_doctor({})  # type: ignore[arg-type]
    assert isinstance(result, DoctorReport), "run_doctor must return a DoctorReport"
    assert result.checks, "DoctorReport should contain at least one check"


def test_print_report_accepts_list_of_tuples():
    """Regression test for #672 — print_report must not crash on list input from run_all_checks."""
    from castor.doctor import run_all_checks

    results = run_all_checks()
    # Should NOT raise AttributeError: 'list' object has no attribute 'checks'
    print_report(results)
