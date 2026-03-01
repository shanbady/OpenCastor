"""
castor/voice.py — Shared audio transcription module.

Provides a tiered transcription pipeline:
    1. OpenAI Whisper API (if OPENAI_API_KEY set)
    2. Local openai-whisper package (if installed)
    3. whisper.cpp CLI binary (if WHISPER_CPP_BIN is set or whisper-cpp is on PATH)
    4. Google SpeechRecognition (always available as fallback)
    5. Returns None if all engines fail or none are available

Usage::

    from castor.voice import transcribe_bytes

    with open("audio.ogg", "rb") as f:
        text = transcribe_bytes(f.read(), hint_format="ogg")
    # → "turn left and go forward"

The preferred engine can be forced via the ``engine`` parameter or the
``CASTOR_VOICE_ENGINE`` environment variable ("whisper_api", "whisper_local",
"whisper_cpp", "google", "auto").
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.Voice")

# ---------------------------------------------------------------------------
# Engine availability probes (lazy — checked once per process)
# ---------------------------------------------------------------------------

_HAS_OPENAI: Optional[bool] = None
_HAS_WHISPER_LOCAL: Optional[bool] = None
_HAS_WHISPER_CPP: Optional[bool] = None
_HAS_SPEECH_RECOGNITION: Optional[bool] = None
_HAS_WAKE_WORD: Optional[bool] = None


def _probe_openai() -> bool:
    global _HAS_OPENAI
    if _HAS_OPENAI is None:
        try:
            import openai  # noqa: F401

            _HAS_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
        except ImportError:
            _HAS_OPENAI = False
    return _HAS_OPENAI


def _probe_whisper_local() -> bool:
    global _HAS_WHISPER_LOCAL
    if _HAS_WHISPER_LOCAL is None:
        try:
            import whisper  # noqa: F401

            _HAS_WHISPER_LOCAL = True
        except ImportError:
            _HAS_WHISPER_LOCAL = False
    return _HAS_WHISPER_LOCAL


def _probe_whisper_cpp() -> bool:
    """Check whether the whisper.cpp CLI binary is available."""
    global _HAS_WHISPER_CPP
    if _HAS_WHISPER_CPP is None:
        bin_path = os.getenv("WHISPER_CPP_BIN", "whisper-cpp")
        if bin_path == "mock":
            _HAS_WHISPER_CPP = True
            return _HAS_WHISPER_CPP
        # shutil.which covers both absolute paths and PATH lookup
        if shutil.which(bin_path) is not None:
            _HAS_WHISPER_CPP = True
        else:
            # Fallback: try --version to confirm binary runs
            try:
                subprocess.run(
                    [bin_path, "--version"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                _HAS_WHISPER_CPP = True
            except (FileNotFoundError, OSError):
                _HAS_WHISPER_CPP = False
        logger.debug("whisper.cpp probe: available=%s (bin=%s)", _HAS_WHISPER_CPP, bin_path)
    return _HAS_WHISPER_CPP


def _probe_speech_recognition() -> bool:
    global _HAS_SPEECH_RECOGNITION
    if _HAS_SPEECH_RECOGNITION is None:
        try:
            import speech_recognition  # noqa: F401

            _HAS_SPEECH_RECOGNITION = True
        except ImportError:
            _HAS_SPEECH_RECOGNITION = False
    return _HAS_SPEECH_RECOGNITION


def _probe_wake_word() -> bool:
    """Check whether a wake-word backend is available.

    Detection order:
        1. ``WAKE_WORD_BIN=mock`` → always available (for testing).
        2. ``openwakeword`` package importable → available.
        3. ``pvporcupine`` package importable → available.
        4. Otherwise → not available.

    Caches the result in the module-level ``_HAS_WAKE_WORD`` flag.
    """
    global _HAS_WAKE_WORD
    if _HAS_WAKE_WORD is None:
        if os.getenv("WAKE_WORD_BIN") == "mock":
            _HAS_WAKE_WORD = True
            return _HAS_WAKE_WORD
        try:
            import openwakeword  # noqa: F401

            _HAS_WAKE_WORD = True
            return _HAS_WAKE_WORD
        except ImportError:
            pass
        try:
            import pvporcupine  # noqa: F401

            _HAS_WAKE_WORD = True
            return _HAS_WAKE_WORD
        except ImportError:
            pass
        _HAS_WAKE_WORD = False
    return _HAS_WAKE_WORD


# ---------------------------------------------------------------------------
# Individual engine implementations
# ---------------------------------------------------------------------------


def _transcribe_whisper_api(audio_bytes: bytes, hint_format: str = "ogg") -> Optional[str]:
    """Transcribe via OpenAI Whisper API."""
    try:
        from openai import OpenAI

        client = OpenAI()
        ext = hint_format.lstrip(".")
        # Whisper API accepts: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "rb") as audio_file:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
            text = result.text.strip()
            logger.debug("Whisper API transcription: %d chars", len(text))
            return text or None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Whisper API transcription failed: %s", exc)
        return None


def _transcribe_whisper_local(audio_bytes: bytes, hint_format: str = "ogg") -> Optional[str]:
    """Transcribe using local openai-whisper package."""
    try:
        import whisper

        ext = hint_format.lstrip(".")
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            model = whisper.load_model("base")
            result = model.transcribe(tmp_path)
            text = result.get("text", "").strip()
            logger.debug("Local Whisper transcription: %d chars", len(text))
            return text or None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("Local Whisper transcription failed: %s", exc)
        return None


def _transcribe_whisper_cpp(audio_bytes: bytes) -> Optional[str]:
    """Transcribe via the whisper.cpp CLI binary.

    Writes audio to a temporary WAV file, invokes the binary with
    ``--output-txt``, reads the resulting ``<tmp>.txt`` file, then
    cleans up both temp files.

    Environment variables:
        WHISPER_CPP_BIN   — path to the whisper.cpp binary (default: "whisper-cpp").
                            Set to "mock" to return a fixed string without running anything.
        WHISPER_CPP_MODEL — optional model file path, passed as ``--model <path>``.
    """
    bin_path = os.getenv("WHISPER_CPP_BIN", "whisper-cpp")
    model_path = os.getenv("WHISPER_CPP_MODEL", "")

    # Mock mode — useful for testing without an actual binary installed
    if bin_path == "mock":
        logger.debug("whisper.cpp: mock mode — returning fixed transcription")
        return "mock transcription"

    tmp_wav_path: Optional[str] = None
    txt_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_wav_path = tmp.name

        txt_path = tmp_wav_path + ".txt"

        cmd = [bin_path]
        if model_path:
            cmd += ["--model", model_path]
        cmd += ["--output-txt", tmp_wav_path]

        logger.debug("whisper.cpp: running %s", cmd)
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "whisper.cpp exited with code %d: %s",
                result.returncode,
                result.stderr.decode(errors="replace").strip(),
            )
            return None

        with open(txt_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()

        logger.debug("whisper.cpp transcription: %d chars", len(text))
        return text or None

    except FileNotFoundError:
        logger.warning("whisper.cpp binary not found: %s — skipping engine", bin_path)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("whisper.cpp timed out after 60s")
        return None
    except Exception as exc:
        logger.warning("whisper.cpp transcription failed: %s", exc)
        return None
    finally:
        for path in (tmp_wav_path, txt_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _transcribe_google_sr(audio_bytes: bytes, hint_format: str = "ogg") -> Optional[str]:
    """Transcribe via Google SpeechRecognition (free, no API key required)."""
    try:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        ext = hint_format.lstrip(".").lower()

        # speech_recognition works best with WAV; convert OGG/OGG-Opus/MP3 if possible
        audio_data = audio_bytes
        if ext in ("ogg", "oga", "mp3", "m4a", "aac", "webm", "opus"):
            audio_data = _convert_to_wav(audio_bytes, ext)
            if audio_data is None:
                logger.debug("Audio format conversion failed; trying raw bytes with Google SR")
                audio_data = audio_bytes

        audio_file = io.BytesIO(audio_data)
        with sr.AudioFile(audio_file) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio).strip()
        logger.debug("Google SR transcription: %d chars", len(text))
        return text or None
    except Exception as exc:
        logger.warning("Google SR transcription failed: %s", exc)
        return None


def _convert_to_wav(audio_bytes: bytes, src_format: str) -> Optional[bytes]:
    """Convert audio to WAV using pydub (optional dependency)."""
    try:
        from pydub import AudioSegment  # noqa

        seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format=src_format)
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        logger.debug("pydub conversion failed (%s): %s", src_format, exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_VALID_ENGINES = ("auto", "whisper_api", "whisper_local", "whisper_cpp", "google", "wake_word")

# Heuristic confidence scores per engine
_ENGINE_CONFIDENCE: dict[str, float] = {
    "whisper_cpp": 0.85,
    "whisper_local": 0.90,
    "openai": 0.95,
    "whisper_api": 0.95,
    "google": 0.80,
    "mock": 0.50,
}


def transcribe_bytes(
    audio_bytes: bytes,
    hint_format: str = "ogg",
    engine: str = "auto",
    language: str = "en",
) -> Optional[dict]:
    """Transcribe audio bytes to text.

    Args:
        audio_bytes: Raw audio file bytes (any common format).
        hint_format: File extension hint for the audio format, e.g. "ogg", "mp3", "wav".
        engine: Transcription engine override. One of "auto", "whisper_api",
                "whisper_local", "whisper_cpp", "google". Defaults to the
                ``CASTOR_VOICE_ENGINE`` env var, then "auto" (tries in order:
                whisper_api → whisper_local → whisper_cpp → google).
        language: Language code hint (currently used by Google SR; Whisper auto-detects).

    Returns:
        Dict with keys ``text`` (str), ``confidence`` (float 0-1), and ``engine`` (str),
        or None if transcription failed / no audio provided.
    """
    if not audio_bytes:
        return None

    # Resolve engine preference
    resolved_engine = engine
    if resolved_engine == "auto":
        resolved_engine = os.getenv("CASTOR_VOICE_ENGINE", "auto")

    t0 = time.time()
    text: Optional[str] = None
    actual_engine = resolved_engine

    if resolved_engine == "whisper_api":
        text = _transcribe_whisper_api(audio_bytes, hint_format)
    elif resolved_engine == "whisper_local":
        text = _transcribe_whisper_local(audio_bytes, hint_format)
    elif resolved_engine == "whisper_cpp":
        text = _transcribe_whisper_cpp(audio_bytes)
    elif resolved_engine == "google":
        text = _transcribe_google_sr(audio_bytes, hint_format)
    else:
        # auto: try engines in priority order, track which one succeeded
        if _probe_openai():
            logger.debug("voice: trying Whisper API")
            text = _transcribe_whisper_api(audio_bytes, hint_format)
            if text is not None:
                actual_engine = "whisper_api"
        if text is None and _probe_whisper_local():
            logger.debug("voice: trying local Whisper")
            text = _transcribe_whisper_local(audio_bytes, hint_format)
            if text is not None:
                actual_engine = "whisper_local"
        if text is None and _probe_whisper_cpp():
            logger.debug("voice: trying whisper.cpp")
            text = _transcribe_whisper_cpp(audio_bytes)
            if text is not None:
                actual_engine = "whisper_cpp"
        if text is None and _probe_speech_recognition():
            logger.debug("voice: trying Google SR")
            text = _transcribe_google_sr(audio_bytes, hint_format)
            if text is not None:
                actual_engine = "google"

    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if text:
        logger.info(
            "Transcribed %d audio bytes → %d chars (engine=%s, %.0fms)",
            len(audio_bytes),
            len(text),
            actual_engine,
            elapsed_ms,
        )
    else:
        logger.warning(
            "Transcription returned empty result (engine=%s, %.0fms, format=%s)",
            resolved_engine,
            elapsed_ms,
            hint_format,
        )
        return None

    confidence = _ENGINE_CONFIDENCE.get(actual_engine, _ENGINE_CONFIDENCE["mock"])
    return {"text": text, "confidence": confidence, "engine": actual_engine}


def available_engines() -> list[str]:
    """Return list of transcription engines available in this environment."""
    engines = []
    if _probe_openai():
        engines.append("whisper_api")
    if _probe_whisper_local():
        engines.append("whisper_local")
    if _probe_whisper_cpp():
        engines.append("whisper_cpp")
    if _probe_speech_recognition():
        engines.append("google")
    return engines


# ---------------------------------------------------------------------------
# Wake-word audio streaming
# ---------------------------------------------------------------------------

# Environment variable defaults for audio streaming
_WAKE_WORD_SAMPLE_RATE_DEFAULT: int = 16000
_WAKE_WORD_CHUNK_SIZE_DEFAULT: int = 512


class MockAudioStream:
    """Minimal mock audio stream that produces zero-padded int16 chunks.

    Used in mock mode or when neither pyaudio nor sounddevice is available,
    so tests and CI environments don't require real audio hardware.
    """

    def read(self, chunk_size: int) -> bytes:
        """Return ``chunk_size * 2`` zero bytes (int16 = 2 bytes per sample)."""
        return bytes(chunk_size * 2)

    def stop_stream(self) -> None:
        """No-op — compatible with pyaudio stream interface."""

    def close(self) -> None:
        """No-op — compatible with pyaudio stream interface."""


@contextlib.contextmanager
def stream_audio_source(
    device: Optional[int] = None,
    sample_rate: int = 16000,
    chunk_size: int = 512,
):
    """Context manager yielding an audio stream object.

    Tries pyaudio first, then sounddevice as fallback.  In mock mode
    (``WAKE_WORD_BIN=mock``) or when neither library is available, yields a
    :class:`MockAudioStream` that produces zero-padded int16 chunks.

    Args:
        device:      Input device index, or ``None`` for the system default.
        sample_rate: Sample rate in Hz (default 16 000).
        chunk_size:  Audio chunk size in samples (default 512).

    Yields:
        An object with a ``read(chunk_size) -> bytes`` interface.
    """
    if os.getenv("WAKE_WORD_BIN") == "mock":
        logger.debug("stream_audio_source: mock mode — yielding MockAudioStream")
        yield MockAudioStream()
        return

    # Try pyaudio first
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        open_kwargs: dict = dict(
            rate=sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=chunk_size,
        )
        if device is not None:
            open_kwargs["input_device_index"] = device
        stream = pa.open(**open_kwargs)
        logger.debug(
            "stream_audio_source: opened pyaudio stream (device=%s, rate=%d)", device, sample_rate
        )
        try:
            yield stream
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
        return
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("stream_audio_source: pyaudio failed: %s — trying sounddevice", exc)

    # Try sounddevice as fallback
    try:
        import sounddevice as sd

        logger.debug(
            "stream_audio_source: opened sounddevice stream (device=%s, rate=%d)",
            device,
            sample_rate,
        )

        class _SoundDeviceStream:
            """Thin wrapper to expose sounddevice RawInputStream as read() interface."""

            def __init__(self, inner) -> None:
                self._inner = inner

            def read(self, n: int) -> bytes:
                data, _ = self._inner.read(n)
                return bytes(data)

            def stop_stream(self) -> None:
                self._inner.stop()

            def close(self) -> None:
                self._inner.close()

        sd_kwargs: dict = dict(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=chunk_size,
        )
        if device is not None:
            sd_kwargs["device"] = device
        inner_stream = sd.RawInputStream(**sd_kwargs)
        inner_stream.start()
        wrapped = _SoundDeviceStream(inner_stream)
        try:
            yield wrapped
        finally:
            wrapped.stop_stream()
            wrapped.close()
        return
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("stream_audio_source: sounddevice failed: %s — falling back to mock", exc)

    # Neither library available — fall back to mock
    logger.warning(
        "stream_audio_source: no audio library available "
        "(install pyaudio or sounddevice). Using MockAudioStream."
    )
    yield MockAudioStream()


def get_audio_config() -> dict:
    """Return the active audio capture configuration as a dictionary.

    Reads environment variables ``WAKE_WORD_AUDIO_DEVICE``,
    ``WAKE_WORD_SAMPLE_RATE``, and ``WAKE_WORD_CHUNK_SIZE``.  The ``library``
    key reflects which audio backend would be used (``"pyaudio"``,
    ``"sounddevice"``, or ``"mock"``).

    Returns:
        Dict with keys: ``device`` (int or None), ``sample_rate`` (int),
        ``chunk_size`` (int), ``library`` (str).
    """
    # Resolve device
    device_env = os.getenv("WAKE_WORD_AUDIO_DEVICE", "")
    device: Optional[int] = None
    if device_env and device_env.lower() not in ("", "default", "none"):
        try:
            device = int(device_env)
        except ValueError:
            device = None

    # Resolve sample_rate
    try:
        sample_rate = int(os.getenv("WAKE_WORD_SAMPLE_RATE", str(_WAKE_WORD_SAMPLE_RATE_DEFAULT)))
    except ValueError:
        sample_rate = _WAKE_WORD_SAMPLE_RATE_DEFAULT

    # Resolve chunk_size
    try:
        chunk_size = int(os.getenv("WAKE_WORD_CHUNK_SIZE", str(_WAKE_WORD_CHUNK_SIZE_DEFAULT)))
    except ValueError:
        chunk_size = _WAKE_WORD_CHUNK_SIZE_DEFAULT

    # Detect which library is available
    if os.getenv("WAKE_WORD_BIN") == "mock":
        library = "mock"
    else:
        library = "mock"
        try:
            import pyaudio  # noqa: F401

            library = "pyaudio"
        except ImportError:
            try:
                import sounddevice  # noqa: F401

                library = "sounddevice"
            except ImportError:
                pass

    return {
        "device": device,
        "sample_rate": sample_rate,
        "chunk_size": chunk_size,
        "library": library,
    }


# ---------------------------------------------------------------------------
# Wake-word detection
# ---------------------------------------------------------------------------

# Environment variable defaults
_WAKE_WORD_SENSITIVITY_DEFAULT: float = 0.5
_WAKE_WORD_MODEL_DEFAULT: str = "hey_jarvis"


class WakeWordDetector:
    """Lightweight wrapper around wake-word detection backends.

    Supports ``openwakeword``, ``pvporcupine``, and a mock mode
    (``WAKE_WORD_BIN=mock``) for testing without hardware or libraries.

    Usage::

        detector = WakeWordDetector(sensitivity=0.6, model="hey_jarvis")
        detector.start(callback=lambda text: print("Wake word triggered:", text))
        # … do other work …
        detector.stop()

    Environment variables:
        WAKE_WORD_BIN          — set to ``"mock"`` to enable mock mode.
        WAKE_WORD_SENSITIVITY  — float 0–1 (default 0.5).
        WAKE_WORD_MODEL        — model name or path (default ``"hey_jarvis"``).
        WAKE_WORD_AUDIO_DEVICE — input device index (int or "default"; default: system default).
        WAKE_WORD_SAMPLE_RATE  — sample rate in Hz (default 16000).
        WAKE_WORD_CHUNK_SIZE   — audio chunk size in samples (default 512).
    """

    def __init__(self, sensitivity: float = 0.5, model: str = "hey_jarvis") -> None:
        self._sensitivity = sensitivity
        self._model = model
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._callback = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self, callback=None) -> None:  # type: ignore[override]
        """Start background microphone listener.

        Reads ``WAKE_WORD_AUDIO_DEVICE``, ``WAKE_WORD_SAMPLE_RATE``, and
        ``WAKE_WORD_CHUNK_SIZE`` from the environment to configure audio capture.
        When a real backend (openwakeword or pvporcupine) is available, delegates
        audio capture to :func:`stream_audio_source` via ``_start_with_audio_stream()``.

        Args:
            callback: Called with a single string argument when the wake word
                      is detected.  If ``None``, triggers are silently discarded.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.debug("WakeWordDetector: already running — ignoring start()")
            return

        self._stop_event.clear()
        self._running = True
        self._callback = callback

        if os.getenv("WAKE_WORD_BIN") == "mock":
            self._thread = threading.Thread(
                target=self._mock_loop,
                args=(callback,),
                daemon=True,
            )
            self._thread.start()
            logger.debug("WakeWordDetector: mock mode started")
            return

        if _probe_wake_word():
            # Real backend dispatch — openwakeword takes priority
            try:
                import openwakeword  # noqa: F401

                self._thread = threading.Thread(
                    target=self._start_with_audio_stream,
                    args=(callback, "openwakeword"),
                    daemon=True,
                )
                self._thread.start()
                logger.info(
                    "WakeWordDetector: openwakeword backend started (model=%s, sensitivity=%.2f)",
                    self._model,
                    self._sensitivity,
                )
                return
            except ImportError:
                pass

            try:
                import pvporcupine  # noqa: F401

                self._thread = threading.Thread(
                    target=self._start_with_audio_stream,
                    args=(callback, "pvporcupine"),
                    daemon=True,
                )
                self._thread.start()
                logger.info(
                    "WakeWordDetector: pvporcupine backend started (model=%s, sensitivity=%.2f)",
                    self._model,
                    self._sensitivity,
                )
                return
            except ImportError:
                pass

        self._running = False
        logger.warning(
            "WakeWordDetector: no wake-word library available "
            "(install openwakeword or pvporcupine, or set WAKE_WORD_BIN=mock). "
            "Listener not started."
        )

    def stop(self) -> None:
        """Stop the background listener thread."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        logger.debug("WakeWordDetector: stopped")

    @property
    def running(self) -> bool:
        """True if the background listener thread is active."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal loops
    # ------------------------------------------------------------------

    def _start_with_audio_stream(self, callback, backend: str) -> None:
        """Background thread that reads from :func:`stream_audio_source` and feeds chunks to the engine.

        Args:
            callback: Callable to invoke when the wake word is detected.
            backend:  ``"openwakeword"`` or ``"pvporcupine"``.
        """
        # Resolve audio parameters from environment
        audio_cfg = get_audio_config()
        device = audio_cfg["device"]
        sample_rate = audio_cfg["sample_rate"]
        chunk_size = audio_cfg["chunk_size"]

        try:
            if backend == "openwakeword":
                self._openwakeword_stream_loop(callback, device, sample_rate, chunk_size)
            elif backend == "pvporcupine":
                self._pvporcupine_stream_loop(callback, device, sample_rate, chunk_size)
        except Exception as exc:
            logger.error("WakeWordDetector: %s stream loop failed: %s", backend, exc)
        finally:
            self._running = False

    def _openwakeword_stream_loop(
        self,
        callback,
        device: Optional[int],
        sample_rate: int,
        chunk_size: int,
    ) -> None:
        """openwakeword inference loop driven by :func:`stream_audio_source`."""
        try:
            import numpy as np
            import openwakeword

            oww_model = openwakeword.Model(
                wakeword_models=[self._model],
                inference_framework="tflite",
            )
        except Exception as exc:
            logger.error("WakeWordDetector: failed to load openwakeword model: %s", exc)
            return

        with stream_audio_source(
            device=device, sample_rate=sample_rate, chunk_size=chunk_size
        ) as stream:
            while not self._stop_event.is_set():
                try:
                    raw = stream.read(chunk_size)
                except Exception as exc:
                    logger.warning("WakeWordDetector: audio read error: %s", exc)
                    break
                pcm = np.frombuffer(raw, dtype=np.int16)
                prediction = oww_model.predict(pcm)
                for ww, score in prediction.items():
                    if score >= self._sensitivity:
                        logger.info(
                            "WakeWordDetector: wake word '%s' detected (score=%.3f)",
                            ww,
                            score,
                        )
                        if callback is not None:
                            try:
                                callback(ww)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("WakeWordDetector: callback raised: %s", exc)

    def _pvporcupine_stream_loop(
        self,
        callback,
        device: Optional[int],
        sample_rate: int,
        chunk_size: int,
    ) -> None:
        """pvporcupine inference loop driven by :func:`stream_audio_source`."""
        try:
            import pvporcupine

            access_key = os.getenv("PORCUPINE_ACCESS_KEY", "")
            porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=[self._model] if self._model else ["porcupine"],
                sensitivities=[self._sensitivity],
            )
        except Exception as exc:
            logger.error("WakeWordDetector: failed to create pvporcupine engine: %s", exc)
            return

        try:
            frame_length = porcupine.frame_length
            with stream_audio_source(
                device=device, sample_rate=porcupine.sample_rate, chunk_size=frame_length
            ) as stream:
                while not self._stop_event.is_set():
                    try:
                        raw = stream.read(frame_length)
                    except Exception as exc:
                        logger.warning("WakeWordDetector: audio read error: %s", exc)
                        break
                    import struct

                    pcm = struct.unpack_from("h" * frame_length, raw)
                    result = porcupine.process(pcm)
                    if result >= 0:
                        logger.info(
                            "WakeWordDetector: pvporcupine keyword index %d detected", result
                        )
                        if callback is not None:
                            try:
                                callback(self._model)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("WakeWordDetector: callback raised: %s", exc)
        finally:
            porcupine.delete()

    def _mock_loop(self, callback) -> None:  # type: ignore[override]
        """Mock loop: fires callback once every 60 s until stopped."""
        while not self._stop_event.is_set():
            # Wait up to 60 s, checking stop every second
            for _ in range(60):
                if self._stop_event.is_set():
                    return
                time.sleep(1)
            if not self._stop_event.is_set():
                logger.debug("WakeWordDetector: mock wake word triggered")
                if callback is not None:
                    try:
                        callback("mock wake word")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("WakeWordDetector: callback raised: %s", exc)


def get_wake_word_detector(
    sensitivity: Optional[float] = None,
    model: Optional[str] = None,
) -> WakeWordDetector:
    """Factory that returns a configured :class:`WakeWordDetector`.

    Reads ``WAKE_WORD_SENSITIVITY`` and ``WAKE_WORD_MODEL`` env vars when the
    corresponding arguments are not explicitly provided.

    Args:
        sensitivity: Detection threshold 0–1.  Defaults to the
                     ``WAKE_WORD_SENSITIVITY`` env var or ``0.5``.
        model:       Wake-word model name or path.  Defaults to the
                     ``WAKE_WORD_MODEL`` env var or ``"hey_jarvis"``.

    Returns:
        A :class:`WakeWordDetector` instance (not yet started).
    """
    if sensitivity is None:
        try:
            sensitivity = float(
                os.getenv("WAKE_WORD_SENSITIVITY", str(_WAKE_WORD_SENSITIVITY_DEFAULT))
            )
        except ValueError:
            sensitivity = _WAKE_WORD_SENSITIVITY_DEFAULT

    if model is None:
        model = os.getenv("WAKE_WORD_MODEL", _WAKE_WORD_MODEL_DEFAULT)

    return WakeWordDetector(sensitivity=sensitivity, model=model)
