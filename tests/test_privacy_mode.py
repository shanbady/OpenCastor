"""Tests for castor.privacy_mode."""

import os
from unittest.mock import patch

import pytest

import castor.privacy_mode as pm_mod
from castor.privacy_mode import PrivacyModeManager, get_privacy_mode


@pytest.fixture(autouse=True)
def reset_singleton():
    pm_mod._singleton = None
    yield
    pm_mod._singleton = None


class TestPrivacyModeManagerInit:
    def test_default_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CASTOR_PRIVACY_MODE", None)
            mgr = PrivacyModeManager()
        assert mgr.enabled is False

    def test_env_enables(self):
        with patch.dict(os.environ, {"CASTOR_PRIVACY_MODE": "1"}):
            mgr = PrivacyModeManager()
        assert mgr.enabled is True

    def test_env_true_string(self):
        with patch.dict(os.environ, {"CASTOR_PRIVACY_MODE": "true"}):
            mgr = PrivacyModeManager()
        assert mgr.enabled is True


class TestPrivacyModeManagerEnable:
    def test_enable(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.enabled is True

    def test_disable(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        mgr.disable()
        assert mgr.enabled is False

    def test_enable_clears_violations(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        mgr.check_provider("openai")  # creates a violation
        mgr.enable()  # re-enable should clear
        assert mgr.status()["violation_count"] == 0


class TestPrivacyModeManagerChecks:
    def test_check_provider_allowed_when_disabled(self):
        mgr = PrivacyModeManager()
        assert mgr.check_provider("openai") is True

    def test_check_provider_local_allowed(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.check_provider("ollama") is True
        assert mgr.check_provider("mlx") is True
        assert mgr.check_provider("onnx") is True

    def test_check_provider_cloud_blocked(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.check_provider("openai") is False
        assert mgr.check_provider("google") is False
        assert mgr.check_provider("anthropic") is False
        assert mgr.check_provider("groq") is False

    def test_check_webhook_blocked(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.check_webhook("https://hooks.slack.com/...") is False

    def test_check_webhook_allowed_when_disabled(self):
        mgr = PrivacyModeManager()
        assert mgr.check_webhook("https://example.com") is True

    def test_check_tts_engine_local_allowed(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.check_tts_engine("piper") is True
        assert mgr.check_tts_engine("espeak") is True

    def test_check_tts_engine_cloud_blocked(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.check_tts_engine("gtts") is False

    def test_check_camera_upload_blocked(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        assert mgr.check_camera_upload("s3://my-bucket") is False

    def test_violations_accumulate(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        mgr.check_provider("openai")
        mgr.check_provider("google")
        mgr.check_webhook("https://x.com")
        s = mgr.status()
        assert s["violation_count"] == 3
        assert len(s["recent_violations"]) == 3


class TestPrivacyModeManagerStatus:
    def test_status_structure(self):
        mgr = PrivacyModeManager()
        s = mgr.status()
        assert "enabled" in s
        assert "local_providers" in s
        assert "local_tts_engines" in s
        assert "violation_count" in s
        assert "recent_violations" in s

    def test_recent_violations_capped_at_10(self):
        mgr = PrivacyModeManager()
        mgr.enable()
        for _ in range(15):
            mgr.check_provider("openai")
        s = mgr.status()
        assert len(s["recent_violations"]) == 10


class TestGetPrivacyModeSingleton:
    def test_returns_singleton(self):
        a = get_privacy_mode()
        b = get_privacy_mode()
        assert a is b
