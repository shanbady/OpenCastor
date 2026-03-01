"""Tests for castor.webrtc_audio — WebRTC two-way audio (issue #261)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(has_aiortc=False, brain=None, tts_engine="gtts"):
    with patch("castor.webrtc_audio.HAS_AIORTC", has_aiortc):
        from castor.webrtc_audio import WebRTCAudioSession

        return WebRTCAudioSession(brain=brain, tts_engine=tts_engine)


# ---------------------------------------------------------------------------
# webrtc_audio_available()
# ---------------------------------------------------------------------------


class TestWebRTCAudioAvailable:
    def test_false_without_aiortc(self):
        with patch("castor.webrtc_audio.HAS_AIORTC", False):
            from castor.webrtc_audio import webrtc_audio_available

            assert webrtc_audio_available() is False

    def test_true_with_aiortc(self):
        with patch("castor.webrtc_audio.HAS_AIORTC", True):
            from castor.webrtc_audio import webrtc_audio_available

            assert webrtc_audio_available() is True


# ---------------------------------------------------------------------------
# WebRTCAudioSession.handle_offer — no aiortc
# ---------------------------------------------------------------------------


class TestHandleOfferNoAiortc:
    def test_returns_error_without_aiortc(self):
        session = _make_session(has_aiortc=False)
        result = asyncio.run(session.handle_offer("sdp-data", "offer"))
        assert "error" in result

    def test_error_includes_hint(self):
        session = _make_session(has_aiortc=False)
        result = asyncio.run(session.handle_offer("sdp-data", "offer"))
        assert "hint" in result

    def test_is_not_active_without_aiortc(self):
        session = _make_session(has_aiortc=False)
        assert session.is_active is False


# ---------------------------------------------------------------------------
# WebRTCAudioSession.handle_offer — with mock aiortc
# ---------------------------------------------------------------------------


class TestHandleOfferWithMockAiortc:
    def _mock_pc(self):
        pc = MagicMock()
        pc.localDescription = MagicMock(sdp="answer-sdp", type="answer")
        pc.setRemoteDescription = AsyncMock()
        pc.createAnswer = AsyncMock(return_value=MagicMock())
        pc.setLocalDescription = AsyncMock()
        pc.on = MagicMock(return_value=lambda f: f)  # decorator passthrough
        return pc

    def test_returns_sdp_answer(self):
        pc = self._mock_pc()
        mock_sdp_desc = MagicMock()

        with (
            patch("castor.webrtc_audio.HAS_AIORTC", True),
            patch("castor.webrtc_audio._RTCPeerConnection", return_value=pc),
            patch("castor.webrtc_audio._RTCSessionDescription", return_value=mock_sdp_desc),
        ):
            from castor.webrtc_audio import WebRTCAudioSession

            session = WebRTCAudioSession()
            result = asyncio.run(session.handle_offer("offer-sdp", "offer"))
        assert result.get("sdp") == "answer-sdp"
        assert result.get("type") == "answer"

    def test_sets_active_flag(self):
        pc = self._mock_pc()
        mock_sdp_desc = MagicMock()

        with (
            patch("castor.webrtc_audio.HAS_AIORTC", True),
            patch("castor.webrtc_audio._RTCPeerConnection", return_value=pc),
            patch("castor.webrtc_audio._RTCSessionDescription", return_value=mock_sdp_desc),
        ):
            from castor.webrtc_audio import WebRTCAudioSession

            session = WebRTCAudioSession()
            asyncio.run(session.handle_offer("sdp", "offer"))
            assert session.is_active is True


# ---------------------------------------------------------------------------
# WebRTCAudioSession.close
# ---------------------------------------------------------------------------


class TestSessionClose:
    def test_close_sets_inactive(self):
        session = _make_session(has_aiortc=False)
        asyncio.run(session.close())
        assert session.is_active is False

    def test_close_with_mock_pc(self):
        session = _make_session(has_aiortc=False)
        mock_pc = MagicMock()
        mock_pc.close = AsyncMock()
        session._pc = mock_pc
        session._active = True
        asyncio.run(session.close())
        assert session._pc is None
        assert session.is_active is False


# ---------------------------------------------------------------------------
# _synthesise_tts
# ---------------------------------------------------------------------------


class TestSynthesiseTTS:
    def test_gtts_returns_bytes(self):
        mock_gtts = MagicMock()
        mock_gtts.write_to_fp.side_effect = lambda f: f.write(b"MP3")
        with (
            patch("castor.webrtc_audio.HAS_GTTS", True),
            patch("castor.webrtc_audio._gTTS", return_value=mock_gtts),
        ):
            from castor.webrtc_audio import _synthesise_tts

            result = _synthesise_tts("hello", "gtts")
        assert isinstance(result, bytes)

    def test_unknown_engine_returns_none_when_no_gtts(self):
        with (
            patch("castor.webrtc_audio.HAS_GTTS", False),
        ):
            from castor.webrtc_audio import _synthesise_tts

            result = _synthesise_tts("hello", "unknown-engine")
        assert result is None


# ---------------------------------------------------------------------------
# _transcribe_audio
# ---------------------------------------------------------------------------


class TestTranscribeAudio:
    def test_returns_empty_without_speech_recognition(self):
        with patch("castor.webrtc_audio.HAS_SPEECH_RECOGNITION", False):
            from castor.webrtc_audio import _transcribe_audio

            result = _transcribe_audio(b"audio-bytes")
        assert result == ""

    def test_returns_text_when_recognised(self):
        mock_recogniser = MagicMock()
        mock_recogniser.recognize_google.return_value = "hello robot"
        mock_audio = MagicMock()
        mock_recogniser.record.return_value = mock_audio
        mock_audiofile = MagicMock()
        mock_audiofile.__enter__ = lambda s: s
        mock_audiofile.__exit__ = MagicMock(return_value=False)

        with (
            patch("castor.webrtc_audio.HAS_SPEECH_RECOGNITION", True),
            patch("castor.webrtc_audio._sr") as mock_sr,
        ):
            mock_sr.Recognizer.return_value = mock_recogniser
            mock_sr.AudioFile.return_value = mock_audiofile
            from castor.webrtc_audio import _transcribe_audio

            result = _transcribe_audio(b"wav-audio")
        assert result == "hello robot"

    def test_returns_empty_on_recognition_error(self):
        mock_recogniser = MagicMock()
        mock_recogniser.recognize_google.side_effect = Exception("no match")
        mock_recogniser.record.return_value = MagicMock()
        mock_audiofile = MagicMock()
        mock_audiofile.__enter__ = lambda s: s
        mock_audiofile.__exit__ = MagicMock(return_value=False)
        with (
            patch("castor.webrtc_audio.HAS_SPEECH_RECOGNITION", True),
            patch("castor.webrtc_audio._sr") as mock_sr,
        ):
            mock_sr.Recognizer.return_value = mock_recogniser
            mock_sr.AudioFile.return_value = mock_audiofile
            from castor.webrtc_audio import _transcribe_audio

            result = _transcribe_audio(b"bad-audio")
        assert result == ""


# ---------------------------------------------------------------------------
# handle_webrtc_audio_offer (module-level function)
# ---------------------------------------------------------------------------


class TestHandleWebRTCAudioOffer:
    def test_returns_error_without_aiortc(self):
        with patch("castor.webrtc_audio.HAS_AIORTC", False):
            from castor.webrtc_audio import handle_webrtc_audio_offer

            result = asyncio.run(handle_webrtc_audio_offer("sdp-data"))
        assert "error" in result
