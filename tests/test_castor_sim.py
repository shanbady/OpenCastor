"""Tests for castor.sim — Gazebo/Webots launch wrapper (issue #265)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from castor.sim import (
    _build_sim_args,
    _wait_for_gateway,
    find_simulator,
    launch_simulator,
    list_available_simulators,
)

# ---------------------------------------------------------------------------
# find_simulator
# ---------------------------------------------------------------------------


class TestFindSimulator:
    def test_gazebo_found_when_in_path(self):
        with patch("shutil.which", return_value="/usr/bin/gz"):
            result = find_simulator("gazebo")
        assert result == "/usr/bin/gz"

    def test_webots_found_when_in_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/webots"):
            result = find_simulator("webots")
        assert result == "/usr/local/bin/webots"

    def test_returns_none_when_not_in_path(self):
        with patch("shutil.which", return_value=None):
            result = find_simulator("gazebo")
        assert result is None

    def test_tries_multiple_gazebo_binaries(self):
        calls = []

        def fake_which(name):
            calls.append(name)
            return None

        with patch("shutil.which", side_effect=fake_which):
            find_simulator("gazebo")
        assert len(calls) > 1  # tried gz, gazebo, gzserver

    def test_tries_multiple_webots_binaries(self):
        calls = []

        def fake_which(name):
            calls.append(name)
            return None

        with patch("shutil.which", side_effect=fake_which):
            find_simulator("webots")
        assert len(calls) >= 1


# ---------------------------------------------------------------------------
# list_available_simulators
# ---------------------------------------------------------------------------


class TestListAvailableSimulators:
    def test_returns_empty_when_none_in_path(self):
        with patch("shutil.which", return_value=None):
            result = list_available_simulators()
        assert result == []

    def test_returns_gazebo_when_gz_in_path(self):
        def fake_which(name):
            return "/usr/bin/gz" if name == "gz" else None

        with patch("shutil.which", side_effect=fake_which):
            result = list_available_simulators()
        assert "gazebo" in result

    def test_returns_both_when_both_available(self):
        with patch("shutil.which", return_value="/usr/bin/sim"):
            result = list_available_simulators()
        assert "gazebo" in result
        assert "webots" in result


# ---------------------------------------------------------------------------
# _build_sim_args
# ---------------------------------------------------------------------------


class TestBuildSimArgs:
    def test_gazebo_headless_includes_flag(self):
        args = _build_sim_args("/usr/bin/gz", "gazebo", "robot.rcan.yaml", headless=True)
        assert "--headless" in args

    def test_gazebo_not_headless_no_flag(self):
        args = _build_sim_args("/usr/bin/gz", "gazebo", "robot.rcan.yaml", headless=False)
        assert "--headless" not in args

    def test_webots_headless_includes_flags(self):
        args = _build_sim_args("/usr/bin/webots", "webots", "robot.rcan.yaml", headless=True)
        assert "--headless" in args

    def test_binary_is_first_element(self):
        args = _build_sim_args("/usr/bin/gz", "gazebo", "cfg.yaml")
        assert args[0] == "/usr/bin/gz"

    def test_extra_args_appended(self):
        args = _build_sim_args("/usr/bin/gz", "gazebo", "cfg.yaml", extra_args=["my_world.sdf"])
        assert "my_world.sdf" in args


# ---------------------------------------------------------------------------
# _wait_for_gateway
# ---------------------------------------------------------------------------


class TestWaitForGateway:
    def test_returns_true_on_first_success(self):
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _wait_for_gateway("http://localhost:8000/health", timeout=2.0)
        assert result is True

    def test_returns_false_on_timeout(self):
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            result = _wait_for_gateway("http://localhost:9999/health", timeout=0.1, interval=0.05)
        assert result is False


# ---------------------------------------------------------------------------
# launch_simulator
# ---------------------------------------------------------------------------


class TestLaunchSimulator:
    def test_raises_when_binary_not_found(self, tmp_path):
        cfg = tmp_path / "robot.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="not found in PATH"),
        ):
            launch_simulator("gazebo", str(cfg), start_gateway=False)

    def test_returns_zero_on_success(self, tmp_path):
        cfg = tmp_path / "robot.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0

        with (
            patch("shutil.which", return_value="/usr/bin/gz"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            rc = launch_simulator("gazebo", str(cfg), start_gateway=False)
        assert rc == 0

    def test_non_zero_exit_code_propagated(self, tmp_path):
        cfg = tmp_path / "robot.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")

        mock_proc = MagicMock()
        mock_proc.wait.return_value = 1
        mock_proc.returncode = 1

        with (
            patch("shutil.which", return_value="/usr/bin/gz"),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            rc = launch_simulator("gazebo", str(cfg), start_gateway=False)
        assert rc == 1

    def test_sets_castor_gateway_url_env(self, tmp_path):
        cfg = tmp_path / "robot.rcan.yaml"
        cfg.write_text("rcan_version: '1.1.0'\n")

        captured_env = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured_env.update(env or {})
            proc = MagicMock()
            proc.wait.return_value = 0
            proc.returncode = 0
            return proc

        with (
            patch("shutil.which", return_value="/usr/bin/gz"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            launch_simulator("gazebo", str(cfg), start_gateway=False)

        assert "CASTOR_GATEWAY_URL" in captured_env
