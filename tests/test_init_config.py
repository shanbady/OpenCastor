"""Tests for castor.init_config — non-interactive config scaffolding."""

import os
import tempfile

import pytest

from castor.init_config import generate_config, write_config


def test_generate_config_has_required_fields():
    config = generate_config(robot_name="test-robot")
    assert "rcan_version" in config
    assert "test-robot" in config
    assert "agent:" in config
    assert "rcan_protocol:" in config


def test_generate_config_is_valid_yaml():
    import yaml

    config = generate_config(robot_name="test-robot")
    parsed = yaml.safe_load(config)
    assert isinstance(parsed, dict)
    assert parsed["metadata"]["robot_name"] == "test-robot"


def test_write_config_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        output = os.path.join(tmpdir, "robot.rcan.yaml")
        result = write_config(output, robot_name="test-bot")
        assert os.path.exists(result)
        with open(result) as f:
            content = f.read()
        assert "test-bot" in content


def test_write_config_no_overwrite_raises():
    with tempfile.TemporaryDirectory() as tmpdir:
        output = os.path.join(tmpdir, "robot.rcan.yaml")
        write_config(output, robot_name="first")
        with pytest.raises(FileExistsError):
            write_config(output, robot_name="second", overwrite=False)


def test_write_config_overwrite_succeeds():
    with tempfile.TemporaryDirectory() as tmpdir:
        output = os.path.join(tmpdir, "robot.rcan.yaml")
        write_config(output, robot_name="first")
        write_config(output, robot_name="second", overwrite=True)
        import yaml

        with open(output) as f:
            parsed = yaml.safe_load(f)
        assert parsed["metadata"]["robot_name"] == "second"


def test_write_config_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        output = os.path.join(tmpdir, "nested", "dir", "robot.rcan.yaml")
        result = write_config(output, robot_name="test")
        assert os.path.exists(result)
