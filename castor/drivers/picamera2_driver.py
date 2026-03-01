"""
castor/drivers/picamera2_driver.py — Raspberry Pi Camera Module 3 backend (issue #254).

Implements CameraManager backend using picamera2 (libcamera-based driver).
Supports hardware and mock modes via HAS_PICAMERA2 guard.

RCAN config::

    cameras:
    - id: front
      type: picamera2
      index: 0
      resolution: [1920, 1080]   # optional, default 1920x1080
      hdr: false                  # optional HDR mode
      autofocus: true             # optional continuous autofocus

Install::

    pip install opencastor[rpi]
    # or: pip install picamera2>=0.3

Auto-detect: checks ``/proc/device-tree/model`` for "Raspberry Pi" and
whether ``picamera2`` is importable.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("OpenCastor.Camera.Picamera2")

# ---------------------------------------------------------------------------
# Optional SDK guard
# ---------------------------------------------------------------------------

try:
    from picamera2 import Picamera2 as _Picamera2
    from picamera2.controls import Controls as _Controls  # noqa: F401

    HAS_PICAMERA2 = True
except ImportError:
    HAS_PICAMERA2 = False
    _Picamera2 = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Platform detection helpers
# ---------------------------------------------------------------------------


def _is_raspberry_pi() -> bool:
    """Return True if running on a Raspberry Pi board."""
    model_path = "/proc/device-tree/model"
    if os.path.exists(model_path):
        try:
            with open(model_path, errors="replace") as fh:
                return "Raspberry Pi" in fh.read()
        except OSError:
            pass
    return False


def _auto_detect() -> bool:
    """Return True if picamera2 can be used on this system."""
    return _is_raspberry_pi() and HAS_PICAMERA2


# ---------------------------------------------------------------------------
# Mock camera (used when picamera2 is not available)
# ---------------------------------------------------------------------------

# Minimal 1×1 white JPEG used as placeholder capture.
_MOCK_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n"
    b"\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d"
    b"\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1b\xff\xc0\x00"
    b"\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01"
    b"\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
    b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00"
    b"\xf5\x00\xff\xd9"
)


class Picamera2Driver:
    """Raspberry Pi Camera Module 3 driver using picamera2.

    Falls back to a mock implementation when ``picamera2`` is not installed
    or when running outside a Raspberry Pi.

    Args:
        config: RCAN camera config dict with keys:
                ``index`` (int, default 0),
                ``resolution`` ([w, h], default [1920, 1080]),
                ``hdr`` (bool, default False),
                ``autofocus`` (bool, default True).
    """

    def __init__(self, config: dict):
        self.config = config
        self._index: int = int(config.get("index", 0))
        resolution = config.get("resolution", [1920, 1080])
        self._width: int = int(resolution[0])
        self._height: int = int(resolution[1])
        self._hdr: bool = bool(config.get("hdr", False))
        self._autofocus: bool = bool(config.get("autofocus", True))
        self._cam: Optional[Any] = None
        self._mode = "mock"
        self._model = "mock"

        if HAS_PICAMERA2:
            try:
                self._cam = _Picamera2(self._index)
                cam_config = self._cam.create_still_configuration(
                    main={"size": (self._width, self._height)}
                )
                self._cam.configure(cam_config)
                self._cam.start()
                self._mode = "hardware"
                info = self._cam.camera_properties
                self._model = info.get("Model", "unknown")
                logger.info(
                    "picamera2 initialised: index=%d model=%s resolution=%dx%d",
                    self._index,
                    self._model,
                    self._width,
                    self._height,
                )
                if self._autofocus:
                    try:
                        self._cam.set_controls({"AfMode": 2, "AfTrigger": 0})
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("picamera2 init error: %s — mock mode", exc)
                self._cam = None
        else:
            logger.info("picamera2 not available — mock mode")

    # ── Public interface ─────────────────────────────────────────────────────

    def capture(self) -> bytes:
        """Capture a JPEG image from the camera.

        Returns:
            JPEG image as bytes.  Returns a placeholder image in mock mode.
        """
        if self._cam is not None:
            try:
                arr = self._cam.capture_array()
                # Convert numpy array to JPEG bytes
                try:
                    import cv2  # noqa: F401

                    ret, buf = cv2.imencode(".jpg", arr)
                    if ret:
                        return buf.tobytes()
                except ImportError:
                    pass
                try:
                    from PIL import Image

                    img = Image.fromarray(arr)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG")
                    buf.seek(0)
                    return buf.read()
                except ImportError:
                    pass
                # Last resort: return raw bytes
                return arr.tobytes()
            except Exception as exc:
                logger.error("capture error: %s — returning mock", exc)

        logger.debug("Mock capture returning placeholder JPEG")
        return _MOCK_JPEG

    def trigger_autofocus(self) -> bool:
        """Trigger a one-shot autofocus cycle.

        Returns:
            True if autofocus was triggered successfully, False otherwise.
        """
        if self._cam is not None:
            try:
                self._cam.set_controls({"AfMode": 1, "AfTrigger": 0})
                return True
            except Exception as exc:
                logger.warning("autofocus trigger error: %s", exc)
                return False
        return False  # mock mode

    def set_hdr(self, enabled: bool) -> None:
        """Toggle HDR mode.

        Args:
            enabled: True to enable HDR, False to disable.
        """
        self._hdr = enabled
        if self._cam is not None:
            try:
                self._cam.set_controls({"HdrMode": 1 if enabled else 0})
            except Exception as exc:
                logger.warning("HDR set error: %s", exc)

    def close(self) -> None:
        """Release the camera resource."""
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            except Exception:
                pass
            self._cam = None

    def health_check(self) -> dict:
        """Return camera status dict.

        Returns:
            Dict with ``ok``, ``mode`` (``"hardware"`` | ``"mock"``),
            ``model``, and ``resolution``.
        """
        return {
            "ok": True,
            "mode": self._mode,
            "model": self._model,
            "resolution": f"{self._width}x{self._height}",
            "hdr": self._hdr,
            "autofocus": self._autofocus,
            "error": None,
        }
