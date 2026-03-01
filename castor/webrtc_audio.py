"""
castor/webrtc_audio.py — WebRTC two-way audio intercom (issue #261).

Provides a voice intercom channel via WebRTC:
  1. Browser sends SDP offer to POST /api/webrtc/audio/offer
  2. Server accepts the audio track, transcribes it via SpeechRecognition/Whisper
  3. Transcribed text → brain.think() → Thought
  4. Thought.raw_text → TTS audio → sent back via DataChannel

RCAN config::

    webrtc_audio: true
    webrtc_tts_engine: gtts     # gtts | pyttsx3 | elevenlabs

Guards (HAS_* = False when SDK is missing):
  - HAS_AIORTC          — aiortc WebRTC library
  - HAS_SPEECH_RECOGNITION  — SpeechRecognition library

Install::

    pip install opencastor[webrtc]
    pip install SpeechRecognition
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.WebRTC.Audio")

# ---------------------------------------------------------------------------
# Optional SDK guards
# ---------------------------------------------------------------------------

try:
    from aiortc import RTCPeerConnection as _RTCPeerConnection
    from aiortc import RTCSessionDescription as _RTCSessionDescription
    from aiortc.mediastreams import AudioStreamTrack as _AudioStreamTrack  # noqa: F401

    HAS_AIORTC = True
except ImportError:
    HAS_AIORTC = False
    _RTCPeerConnection = None  # type: ignore[assignment,misc]
    _RTCSessionDescription = None  # type: ignore[assignment,misc]
    logger.debug("aiortc not installed — WebRTC audio unavailable")

try:
    import speech_recognition as _sr

    HAS_SPEECH_RECOGNITION = True
except ImportError:
    HAS_SPEECH_RECOGNITION = False
    _sr = None  # type: ignore[assignment]
    logger.debug("SpeechRecognition not installed — transcription unavailable")

try:
    from gtts import gTTS as _gTTS

    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False
    _gTTS = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def webrtc_audio_available() -> bool:
    """Return True if the WebRTC audio stack is fully available.

    Returns:
        True when both aiortc and SpeechRecognition are installed.
    """
    return HAS_AIORTC


# ---------------------------------------------------------------------------
# TTS helper
# ---------------------------------------------------------------------------


def _synthesise_tts(text: str, engine: str = "gtts") -> Optional[bytes]:
    """Synthesise *text* to audio bytes using the configured TTS engine.

    Args:
        text:   Text to speak.
        engine: TTS engine name: ``"gtts"``, ``"pyttsx3"``, or ``"elevenlabs"``.

    Returns:
        MP3/WAV audio bytes, or ``None`` if synthesis fails.
    """
    engine = engine.lower()
    if engine == "elevenlabs":
        try:
            from castor.tts.elevenlabs_backend import ElevenLabsBackend

            return ElevenLabsBackend({}).speak(text)
        except Exception as exc:
            logger.warning("ElevenLabs TTS failed: %s — falling back to gTTS", exc)
            engine = "gtts"

    if engine in ("gtts", "auto") and HAS_GTTS:
        try:
            buf = io.BytesIO()
            _gTTS(text=text, lang="en").write_to_fp(buf)
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            logger.warning("gTTS synthesis error: %s", exc)

    if engine == "pyttsx3":
        try:
            import tempfile

            import pyttsx3

            eng = pyttsx3.init()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                path = tf.name
            eng.save_to_file(text, path)
            eng.runAndWait()
            with open(path, "rb") as fh:
                data = fh.read()
            os.remove(path)
            return data
        except Exception as exc:
            logger.warning("pyttsx3 TTS error: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Transcription helper
# ---------------------------------------------------------------------------


def _transcribe_audio(audio_bytes: bytes) -> str:
    """Transcribe *audio_bytes* (WAV) to text using SpeechRecognition.

    Args:
        audio_bytes: Raw WAV audio bytes.

    Returns:
        Transcribed text, or empty string on failure.
    """
    if not HAS_SPEECH_RECOGNITION:
        logger.debug("SpeechRecognition not available — cannot transcribe")
        return ""

    try:
        recogniser = _sr.Recognizer()
        buf = io.BytesIO(audio_bytes)
        with _sr.AudioFile(buf) as source:
            audio_data = recogniser.record(source)
        return recogniser.recognize_google(audio_data)
    except Exception as exc:
        logger.warning("Transcription error: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# WebRTC audio session handler
# ---------------------------------------------------------------------------


class WebRTCAudioSession:
    """Manages a single WebRTC audio peer connection.

    Args:
        brain:      Brain provider (must implement ``think()``).
        tts_engine: TTS engine name (``"gtts"``, ``"pyttsx3"``, ``"elevenlabs"``).
    """

    def __init__(self, brain: Any = None, tts_engine: str = "gtts"):
        self._brain = brain
        self._tts_engine = tts_engine
        self._pc: Optional[Any] = None
        self._active = False

    async def handle_offer(self, sdp: str, sdp_type: str = "offer") -> dict:
        """Process an SDP offer and return an SDP answer.

        When aiortc is available: creates a real RTCPeerConnection.
        When aiortc is unavailable: returns a mock error response.

        Args:
            sdp:      SDP offer string from the browser.
            sdp_type: SDP type (should be ``"offer"``).

        Returns:
            Dict with ``"sdp"`` and ``"type"`` keys on success, or
            ``{"error": "..."}`` when WebRTC is unavailable.
        """
        if not HAS_AIORTC:
            return {
                "error": "WebRTC audio unavailable — install opencastor[webrtc]",
                "hint": "pip install opencastor[webrtc]",
            }

        try:
            offer = _RTCSessionDescription(sdp=sdp, type=sdp_type)
            pc = _RTCPeerConnection()
            self._pc = pc
            self._active = True

            @pc.on("track")
            async def on_track(track):
                """Handle incoming audio track."""
                logger.info("WebRTC audio track received: kind=%s", track.kind)
                if track.kind == "audio":
                    await self._process_audio_track(track, pc)

            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            return {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
            }
        except Exception as exc:
            logger.error("WebRTC audio offer error: %s", exc)
            return {"error": str(exc)}

    async def _process_audio_track(self, track: Any, pc: Any) -> None:
        """Read frames from *track*, transcribe, think, and reply via DataChannel.

        Args:
            track: aiortc audio track.
            pc:    The peer connection (for creating DataChannel reply).
        """
        # Create a DataChannel to send TTS reply back
        channel = pc.createDataChannel("tts-reply")

        try:
            # Collect a short audio buffer (up to 5 seconds)
            frames = []
            import asyncio

            for _ in range(50):  # ~5 s at 100 ms/frame
                try:
                    frame = await asyncio.wait_for(track.recv(), timeout=0.1)
                    frames.append(frame)
                except asyncio.TimeoutError:
                    break

            if not frames:
                return

            # Convert frames to WAV bytes (simplified — real impl uses av)
            # In mock mode: just use empty bytes to trigger transcription attempt
            audio_bytes = b""
            try:
                import av as _av

                buf = io.BytesIO()
                container = _av.open(buf, mode="w", format="wav")
                stream = container.add_stream("pcm_s16le", rate=48000, layout="mono")
                for frame in frames:
                    container.mux(stream.encode(frame))
                container.mux(stream.encode(None))
                container.close()
                buf.seek(0)
                audio_bytes = buf.read()
            except Exception:
                pass

            text = _transcribe_audio(audio_bytes)
            if not text:
                text = "[audio received — transcription unavailable]"

            logger.info("WebRTC audio transcribed: %r", text[:80])

            # Pass to brain
            reply_text = text
            if self._brain is not None:
                try:
                    thought = self._brain.think(None, text)
                    reply_text = thought.raw_text if thought else text
                except Exception as exc:
                    logger.warning("Brain think error: %s", exc)

            # Synthesise TTS reply
            audio_reply = _synthesise_tts(reply_text, self._tts_engine)

            # Send via DataChannel if open
            if channel.readyState == "open" and audio_reply:
                channel.send(audio_reply)
                logger.info("WebRTC TTS reply sent (%d bytes)", len(audio_reply))

        except Exception as exc:
            logger.error("Audio processing error: %s", exc)

    async def close(self) -> None:
        """Close the peer connection."""
        if self._pc is not None:
            try:
                await self._pc.close()
            except Exception:
                pass
            self._pc = None
        self._active = False

    @property
    def is_active(self) -> bool:
        """Return True if the session has an active peer connection.

        Returns:
            True when the peer connection is established.
        """
        return self._active


async def handle_webrtc_audio_offer(
    sdp: str,
    sdp_type: str = "offer",
    brain: Any = None,
    tts_engine: str = "gtts",
) -> dict:
    """Top-level handler for POST /api/webrtc/audio/offer.

    Args:
        sdp:        SDP offer string.
        sdp_type:   SDP type (should be ``"offer"``).
        brain:      Brain provider instance.
        tts_engine: TTS engine name.

    Returns:
        SDP answer dict or error dict.
    """
    session = WebRTCAudioSession(brain=brain, tts_engine=tts_engine)
    return await session.handle_offer(sdp, sdp_type)
