"""
castor/tts/elevenlabs_backend.py — ElevenLabs TTS backend (issue #251).

Implements the Speaker interface: ``speak(text, voice_id=None) -> bytes``.

Credentials (priority order):
  1. ``ELEVENLABS_API_KEY`` environment variable
  2. ``.env`` file (loaded by castor.auth)
  3. ``tts.api_key`` in RCAN config

RCAN config::

    tts:
      engine: elevenlabs
      voice_id: "21m00Tcm4TlvDq8ikWAM"   # Rachel (default)
      model_id: "eleven_monolingual_v1"

Install::

    pip install opencastor[elevenlabs]
    # or: pip install elevenlabs>=1.0

Fallback: gTTS cloud synthesis when API key is missing or unavailable.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.TTS.ElevenLabs")

# ---------------------------------------------------------------------------
# Optional SDK guard
# ---------------------------------------------------------------------------

try:
    from elevenlabs import ElevenLabs as _ElevenLabsClient
    from elevenlabs import Voice as _Voice  # noqa: F401

    HAS_ELEVENLABS = True
except ImportError:
    HAS_ELEVENLABS = False
    _ElevenLabsClient = None  # type: ignore[assignment,misc]

try:
    from gtts import gTTS as _gTTS

    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False
    _gTTS = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Default voice catalogue (returned when no API key is configured)
# ---------------------------------------------------------------------------

_MOCK_VOICES: list[dict] = [
    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "gender": "female"},
    {"voice_id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi", "gender": "female"},
    {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella", "gender": "female"},
    {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "gender": "male"},
    {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli", "gender": "female"},
    {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "gender": "male"},
    {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold", "gender": "male"},
    {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam", "gender": "male"},
    {"voice_id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam", "gender": "male"},
    {"voice_id": "g5CIjZEefAph4nQFvHAz", "name": "Ethan", "gender": "male"},
]


class ElevenLabsBackend:
    """ElevenLabs TTS backend with gTTS fallback.

    Args:
        config: RCAN config dict.  Reads ``tts.voice_id`` and ``tts.model_id``.
    """

    def __init__(self, config: dict):
        self.config = config
        tts_cfg = config.get("tts", {}) if isinstance(config, dict) else {}
        self._voice_id: str = tts_cfg.get("voice_id", "21m00Tcm4TlvDq8ikWAM")
        self._model_id: str = tts_cfg.get("model_id", "eleven_monolingual_v1")

        # Resolve API key
        self._api_key: Optional[str] = (
            os.getenv("ELEVENLABS_API_KEY") or tts_cfg.get("api_key") or config.get("api_key")
        )

        self._client = None
        self._mode = "mock"

        if HAS_ELEVENLABS and self._api_key:
            try:
                self._client = _ElevenLabsClient(api_key=self._api_key)
                self._mode = "hardware"
                logger.info("ElevenLabs TTS client initialised (voice=%s)", self._voice_id)
            except Exception as exc:
                logger.warning("ElevenLabs init error: %s — using fallback", exc)
        elif not HAS_ELEVENLABS:
            logger.info("elevenlabs SDK not installed — using gTTS fallback")
        else:
            logger.info("ELEVENLABS_API_KEY not set — using gTTS fallback")

    # ── Public interface ─────────────────────────────────────────────────────

    def speak(self, text: str, voice_id: Optional[str] = None) -> bytes:
        """Synthesise *text* and return raw MP3 audio bytes.

        Args:
            text:     Text to synthesise.
            voice_id: ElevenLabs voice ID.  Falls back to ``tts.voice_id`` from config.

        Returns:
            MP3 audio bytes.

        Raises:
            RuntimeError: If both ElevenLabs and gTTS are unavailable.
        """
        vid = voice_id or self._voice_id

        if self._client is not None:
            try:
                audio = self._client.text_to_speech.convert(
                    voice_id=vid,
                    text=text,
                    model_id=self._model_id,
                )
                # SDK may return a generator of chunks
                if hasattr(audio, "__iter__") and not isinstance(audio, (bytes, bytearray)):
                    return b"".join(audio)
                return bytes(audio)  # type: ignore[arg-type]
            except Exception as exc:
                logger.error("ElevenLabs speak error: %s — falling back to gTTS", exc)

        return self._gtts_fallback(text)

    def _gtts_fallback(self, text: str) -> bytes:
        """Synthesise *text* using gTTS and return MP3 bytes."""
        if HAS_GTTS:
            buf = io.BytesIO()
            _gTTS(text=text, lang="en").write_to_fp(buf)
            buf.seek(0)
            return buf.read()
        raise RuntimeError("Neither ElevenLabs nor gTTS is available")

    def list_voices(self) -> list[dict]:
        """Return a list of available voices.

        Returns the live voice catalogue when a valid API key is configured;
        otherwise returns the built-in mock catalogue.

        Returns:
            List of dicts with ``voice_id``, ``name``, and ``gender`` keys.
        """
        if self._client is not None:
            try:
                resp = self._client.voices.get_all()
                voices_list = getattr(resp, "voices", None) or []
                return [
                    {
                        "voice_id": getattr(v, "voice_id", ""),
                        "name": getattr(v, "name", ""),
                        "gender": getattr(v, "labels", {}).get("gender", "unknown"),
                    }
                    for v in voices_list
                ]
            except Exception as exc:
                logger.warning("ElevenLabs list_voices error: %s — returning mock", exc)

        return list(_MOCK_VOICES)

    def health_check(self) -> dict:
        """Return backend status dict.

        Returns:
            Dict with ``ok``, ``mode`` (``"hardware"`` | ``"mock"``), and optional ``error``.
        """
        return {
            "ok": True,
            "mode": self._mode,
            "voice_id": self._voice_id,
            "has_sdk": HAS_ELEVENLABS,
            "has_key": bool(self._api_key),
            "error": None,
        }
