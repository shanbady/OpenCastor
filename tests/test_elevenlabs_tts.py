"""Tests for castor.tts.elevenlabs_backend — ElevenLabs TTS (issue #251).

All tests run without a real ElevenLabs account:
  - SDK and API-key paths are patched.
  - gTTS fallback is patched to avoid network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(has_sdk=True, api_key="test-key", config=None):
    """Return an ElevenLabsBackend with mocked SDK."""
    config = config or {}
    mock_client = MagicMock()
    # list_voices
    mock_voice = MagicMock()
    mock_voice.voice_id = "abc123"
    mock_voice.name = "TestVoice"
    mock_voice.labels = {"gender": "female"}
    mock_voices_resp = MagicMock()
    mock_voices_resp.voices = [mock_voice]
    mock_client.voices.get_all.return_value = mock_voices_resp
    # text_to_speech
    mock_client.text_to_speech.convert.return_value = b"MP3_AUDIO"

    with (
        patch("castor.tts.elevenlabs_backend.HAS_ELEVENLABS", has_sdk),
        patch("castor.tts.elevenlabs_backend._ElevenLabsClient", return_value=mock_client),
        patch.dict("os.environ", {"ELEVENLABS_API_KEY": api_key} if api_key else {}, clear=False),
    ):
        from castor.tts.elevenlabs_backend import ElevenLabsBackend

        backend = ElevenLabsBackend(config)
        backend._client = mock_client if has_sdk and api_key else None
        backend._mode = "hardware" if has_sdk and api_key else "mock"
        return backend, mock_client


# ---------------------------------------------------------------------------
# __init__ / mode detection
# ---------------------------------------------------------------------------


class TestElevenLabsInit:
    def test_hardware_mode_with_sdk_and_key(self):
        backend, _ = _make_backend(has_sdk=True, api_key="sk-abc")
        assert backend._mode == "hardware"

    def test_mock_mode_without_sdk(self):
        backend, _ = _make_backend(has_sdk=False, api_key="sk-abc")
        assert backend._mode == "mock"

    def test_mock_mode_without_key(self):
        backend, _ = _make_backend(has_sdk=True, api_key="")
        assert backend._mode == "mock"

    def test_default_voice_id(self):
        backend, _ = _make_backend()
        assert backend._voice_id == "21m00Tcm4TlvDq8ikWAM"

    def test_custom_voice_id_from_config(self):
        cfg = {"tts": {"voice_id": "custom-voice-id"}}
        backend, _ = _make_backend(config=cfg)
        assert backend._voice_id == "custom-voice-id"

    def test_custom_model_id_from_config(self):
        cfg = {"tts": {"model_id": "eleven_multilingual_v2"}}
        backend, _ = _make_backend(config=cfg)
        assert backend._model_id == "eleven_multilingual_v2"

    def test_api_key_from_config(self):
        cfg = {"tts": {"api_key": "cfg-key"}}
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("ELEVENLABS_API_KEY", None)
            from castor.tts.elevenlabs_backend import ElevenLabsBackend

            with (
                patch("castor.tts.elevenlabs_backend.HAS_ELEVENLABS", True),
                patch("castor.tts.elevenlabs_backend._ElevenLabsClient") as MockCls,
            ):
                MockCls.return_value = MagicMock()
                b = ElevenLabsBackend(cfg)
                assert b._api_key == "cfg-key"


# ---------------------------------------------------------------------------
# speak()
# ---------------------------------------------------------------------------


class TestElevenLabsSpeak:
    def test_speak_returns_bytes(self):
        backend, client = _make_backend()
        client.text_to_speech.convert.return_value = b"AUDIO"
        result = backend.speak("hello robot")
        assert isinstance(result, bytes)
        assert result == b"AUDIO"

    def test_speak_with_custom_voice_id(self):
        backend, client = _make_backend()
        client.text_to_speech.convert.return_value = b"AUDIO2"
        result = backend.speak("hi", voice_id="custom-vid")
        assert result == b"AUDIO2"
        call_kwargs = client.text_to_speech.convert.call_args
        assert call_kwargs.kwargs.get("voice_id") == "custom-vid" or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] == "custom-vid"
        )

    def test_speak_generator_chunks(self):
        backend, client = _make_backend()
        client.text_to_speech.convert.return_value = iter([b"chunk1", b"chunk2"])
        result = backend.speak("text")
        assert result == b"chunk1chunk2"

    def test_speak_falls_back_to_gtts_on_error(self):
        backend, client = _make_backend()
        client.text_to_speech.convert.side_effect = RuntimeError("API error")
        mock_gtts = MagicMock()
        mock_gtts.write_to_fp.side_effect = lambda f: f.write(b"GTTS_AUDIO")
        with (
            patch("castor.tts.elevenlabs_backend.HAS_GTTS", True),
            patch("castor.tts.elevenlabs_backend._gTTS", return_value=mock_gtts),
        ):
            result = backend.speak("hello")
        assert isinstance(result, bytes)

    def test_speak_mock_mode_uses_gtts(self):
        backend, _ = _make_backend(has_sdk=False, api_key="")
        mock_gtts = MagicMock()
        mock_gtts.write_to_fp.side_effect = lambda f: f.write(b"GTTS_MOCK")
        with (
            patch("castor.tts.elevenlabs_backend.HAS_GTTS", True),
            patch("castor.tts.elevenlabs_backend._gTTS", return_value=mock_gtts),
        ):
            result = backend.speak("hello mock")
        assert isinstance(result, bytes)

    def test_speak_no_sdk_no_gtts_raises(self):
        backend, _ = _make_backend(has_sdk=False, api_key="")
        with (
            patch("castor.tts.elevenlabs_backend.HAS_GTTS", False),
            pytest.raises(RuntimeError, match="Neither ElevenLabs nor gTTS"),
        ):
            backend.speak("fail")


# ---------------------------------------------------------------------------
# list_voices()
# ---------------------------------------------------------------------------


class TestElevenLabsVoices:
    def test_list_voices_hardware_mode(self):
        backend, client = _make_backend()
        voices = backend.list_voices()
        assert isinstance(voices, list)
        assert len(voices) >= 1
        assert "voice_id" in voices[0]
        assert "name" in voices[0]

    def test_list_voices_mock_mode_returns_catalogue(self):
        backend, _ = _make_backend(has_sdk=False, api_key="")
        voices = backend.list_voices()
        assert isinstance(voices, list)
        assert len(voices) == 10  # _MOCK_VOICES has 10 entries

    def test_list_voices_api_error_returns_mock(self):
        backend, client = _make_backend()
        client.voices.get_all.side_effect = RuntimeError("API error")
        voices = backend.list_voices()
        assert len(voices) == 10  # falls back to _MOCK_VOICES


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


class TestElevenLabsHealthCheck:
    def test_health_check_hardware(self):
        backend, _ = _make_backend()
        hc = backend.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "hardware"
        assert "voice_id" in hc
        assert hc["error"] is None

    def test_health_check_mock(self):
        backend, _ = _make_backend(has_sdk=False, api_key="")
        hc = backend.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "mock"


# ---------------------------------------------------------------------------
# castor.tts factory
# ---------------------------------------------------------------------------


class TestTTSFactory:
    def test_get_tts_backend_elevenlabs(self):
        with (
            patch("castor.tts.elevenlabs_backend.HAS_ELEVENLABS", True),
            patch("castor.tts.elevenlabs_backend._ElevenLabsClient") as MockCls,
            patch.dict("os.environ", {"ELEVENLABS_API_KEY": "k"}, clear=False),
        ):
            MockCls.return_value = MagicMock()
            from castor.tts import get_tts_backend
            from castor.tts.elevenlabs_backend import ElevenLabsBackend

            backend = get_tts_backend("elevenlabs", {})
            assert isinstance(backend, ElevenLabsBackend)
