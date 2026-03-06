"""Tests for castor.export — export_bundle() and _sanitize_config() (issue #485)."""

import json
import os
import zipfile

import pytest
import yaml

from castor.export import export_bundle, _sanitize_config


# ── Fixtures ──────────────────────────────────────────────────────────────────


MINIMAL_CONFIG = {
    "rcan_version": "1.2",
    "metadata": {"robot_name": "testbot", "model": "arm-v2"},
    "agent": {"provider": "openai", "model": "gpt-4o", "api_key": "sk-secret"},
    "channels": [
        {"type": "discord", "token": "discord-token-123", "guild_id": "abc"},
    ],
    "drivers": [{"protocol": "serial"}],
}


def _write_config(tmp_path, config: dict = None) -> str:
    cfg_path = tmp_path / "robot.rcan.yaml"
    cfg_path.write_text(yaml.dump(config or MINIMAL_CONFIG))
    return str(cfg_path)


# ── export_bundle() — zip format ──────────────────────────────────────────────


def test_export_bundle_produces_zip(tmp_path):
    """export_bundle() creates a .zip file in tmp_path."""
    cfg = _write_config(tmp_path)
    output = str(tmp_path / "bundle.zip")
    result = export_bundle(config_path=cfg, output_path=output, fmt="zip")

    assert result == output
    assert os.path.exists(output)
    assert zipfile.is_zipfile(output)


def test_export_bundle_zip_contains_expected_files(tmp_path):
    """The zip contains metadata.json and config.rcan.yaml."""
    cfg = _write_config(tmp_path)
    output = str(tmp_path / "bundle.zip")
    export_bundle(config_path=cfg, output_path=output, fmt="zip")

    with zipfile.ZipFile(output) as zf:
        names = zf.namelist()
    assert "metadata.json" in names
    assert "config.rcan.yaml" in names


def test_export_bundle_json_format(tmp_path):
    """fmt='json' produces a .json file with metadata and config keys."""
    cfg = _write_config(tmp_path)
    output = str(tmp_path / "bundle.json")
    result = export_bundle(config_path=cfg, output_path=output, fmt="json")

    assert os.path.exists(result)
    with open(result) as f:
        data = json.load(f)
    assert "metadata" in data
    assert "config" in data
    assert data["metadata"]["robot_name"] == "testbot"


def test_export_bundle_missing_config_raises(tmp_path):
    """export_bundle raises FileNotFoundError for a missing config."""
    with pytest.raises((FileNotFoundError, OSError)):
        export_bundle(config_path=str(tmp_path / "nonexistent.yaml"), output_path=str(tmp_path / "out.zip"))


# ── _sanitize_config() ────────────────────────────────────────────────────────


def test_sanitize_config_strips_api_key():
    """api_key in agent block is redacted."""
    cfg = {"agent": {"api_key": "sk-super-secret", "model": "gpt-4o"}}
    sanitized = _sanitize_config(cfg)
    assert sanitized["agent"]["api_key"] == "<REDACTED>"
    assert sanitized["agent"]["model"] == "gpt-4o"


def test_sanitize_config_strips_channel_tokens():
    """token/secret/key fields in channels are redacted."""
    cfg = {
        "channels": [
            {"type": "discord", "token": "tok-abc", "webhook_secret": "s3cr3t"},
            {"type": "slack", "api_key": "xoxb-123"},
        ]
    }
    sanitized = _sanitize_config(cfg)
    for ch in sanitized["channels"]:
        for key, val in ch.items():
            if any(s in key.lower() for s in ("token", "secret", "key")):
                assert val == "<REDACTED>", f"Expected redaction for {key}"


def test_sanitize_config_does_not_mutate_original():
    """_sanitize_config returns a deep copy; original is unchanged."""
    cfg = {"agent": {"api_key": "real-key"}}
    _ = _sanitize_config(cfg)
    assert cfg["agent"]["api_key"] == "real-key"


def test_sanitize_config_preserves_non_secret_fields():
    """Non-secret fields survive sanitization unchanged."""
    cfg = {
        "rcan_version": "1.2",
        "metadata": {"robot_name": "r2d2"},
        "agent": {"provider": "openai", "model": "gpt-4o", "api_key": "hidden"},
    }
    sanitized = _sanitize_config(cfg)
    assert sanitized["rcan_version"] == "1.2"
    assert sanitized["metadata"]["robot_name"] == "r2d2"
    assert sanitized["agent"]["provider"] == "openai"
    assert sanitized["agent"]["model"] == "gpt-4o"
