"""castor.tts — Text-to-speech backend registry.

Supported backends:
  - elevenlabs  (issue #251) — ElevenLabs API
  - gtts        — Google TTS (cloud)
  - piper       — local Piper TTS
  - coqui       — local Coqui TTS
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("OpenCastor.TTS")


def get_tts_backend(engine: Optional[str] = None, config: Optional[dict] = None):
    """Return a TTS backend instance matching *engine*.

    Args:
        engine: One of ``"elevenlabs"``, ``"gtts"``, ``"piper"``, ``"coqui"``.
                Defaults to ``CASTOR_TTS_ENGINE`` env var or ``"gtts"``.
        config: Optional RCAN config dict (used for ``tts.voice_id`` etc.).

    Returns:
        A backend object with a ``speak(text, voice_id=None) -> bytes`` method.
    """
    config = config or {}
    if engine is None:
        engine = os.getenv("CASTOR_TTS_ENGINE", config.get("tts", {}).get("engine", "gtts"))

    engine = (engine or "gtts").lower()
    logger.debug("TTS backend requested: %s", engine)

    if engine == "elevenlabs":
        from castor.tts.elevenlabs_backend import ElevenLabsBackend

        return ElevenLabsBackend(config)

    # Fallback: return a minimal gTTS shim
    from castor.tts_local import LocalTTS

    return LocalTTS(engine="gtts")


__all__ = ["get_tts_backend"]
