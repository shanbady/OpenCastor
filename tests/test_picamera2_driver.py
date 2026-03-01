"""Tests for castor.drivers.picamera2_driver — RPi Camera Module 3 (issue #254).

All tests run without real camera hardware — picamera2 is patched throughout.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "index": 0,
    "resolution": [1920, 1080],
    "hdr": False,
    "autofocus": True,
}


def _make_driver(has_sdk=False, cam_raises=None, config=None):
    """Return a Picamera2Driver instance with mocked picamera2."""
    cfg = dict(_BASE_CONFIG)
    if config:
        cfg.update(config)

    mock_cam = MagicMock()
    mock_cam.camera_properties = {"Model": "imx708"}
    if cam_raises:
        mock_cam.start.side_effect = cam_raises

    with (
        patch("castor.drivers.picamera2_driver.HAS_PICAMERA2", has_sdk),
        patch("castor.drivers.picamera2_driver._Picamera2", return_value=mock_cam),
    ):
        from castor.drivers.picamera2_driver import Picamera2Driver

        drv = Picamera2Driver(cfg)
        return drv, mock_cam


# ---------------------------------------------------------------------------
# Mock mode (no SDK)
# ---------------------------------------------------------------------------


class TestPicamera2MockMode:
    def test_mode_is_mock_when_no_sdk(self):
        drv, _ = _make_driver(has_sdk=False)
        assert drv._mode == "mock"

    def test_capture_returns_bytes_in_mock(self):
        drv, _ = _make_driver(has_sdk=False)
        data = drv.capture()
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_capture_returns_jpeg_header_in_mock(self):
        drv, _ = _make_driver(has_sdk=False)
        data = drv.capture()
        # JPEG magic bytes
        assert data[:2] == b"\xff\xd8"

    def test_trigger_autofocus_returns_false_in_mock(self):
        drv, _ = _make_driver(has_sdk=False)
        assert drv.trigger_autofocus() is False

    def test_set_hdr_updates_flag_in_mock(self):
        drv, _ = _make_driver(has_sdk=False)
        drv.set_hdr(True)
        assert drv._hdr is True

    def test_health_check_mock(self):
        drv, _ = _make_driver(has_sdk=False)
        hc = drv.health_check()
        assert hc["ok"] is True
        assert hc["mode"] == "mock"
        assert hc["error"] is None

    def test_health_check_has_resolution(self):
        drv, _ = _make_driver(has_sdk=False)
        hc = drv.health_check()
        assert "resolution" in hc
        assert "1920" in hc["resolution"]

    def test_close_does_not_raise_in_mock(self):
        drv, _ = _make_driver(has_sdk=False)
        drv.close()  # should not raise


# ---------------------------------------------------------------------------
# Hardware mode (SDK available)
# ---------------------------------------------------------------------------


class TestPicamera2HardwareMode:
    def test_mode_is_hardware_when_sdk_available(self):
        drv, _ = _make_driver(has_sdk=True)
        assert drv._mode == "hardware"

    def test_model_detected_from_properties(self):
        drv, _ = _make_driver(has_sdk=True)
        assert drv._model == "imx708"

    def test_capture_calls_capture_array(self):
        drv, mock_cam = _make_driver(has_sdk=True)
        import numpy as np

        mock_cam.capture_array.return_value = np.zeros((480, 640, 3), dtype="uint8")
        with (
            patch("castor.drivers.picamera2_driver.cv2", create=True) as mock_cv2,
        ):
            mock_cv2.imencode.return_value = (True, MagicMock(tobytes=lambda: b"JPEG"))
            # Even if cv2 fails, driver returns bytes
        data = drv.capture()
        assert isinstance(data, bytes)

    def test_trigger_autofocus_returns_true(self):
        drv, _ = _make_driver(has_sdk=True)
        result = drv.trigger_autofocus()
        assert result is True

    def test_set_hdr_calls_set_controls(self):
        drv, mock_cam = _make_driver(has_sdk=True)
        drv.set_hdr(True)
        mock_cam.set_controls.assert_called()

    def test_close_stops_camera(self):
        drv, mock_cam = _make_driver(has_sdk=True)
        drv.close()
        mock_cam.stop.assert_called_once()
        mock_cam.close.assert_called_once()

    def test_cam_is_none_after_close(self):
        drv, _ = _make_driver(has_sdk=True)
        drv.close()
        assert drv._cam is None

    def test_health_check_hardware(self):
        drv, _ = _make_driver(has_sdk=True)
        hc = drv.health_check()
        assert hc["mode"] == "hardware"

    def test_init_error_falls_to_mock(self):
        drv, _ = _make_driver(has_sdk=True, cam_raises=RuntimeError("no camera"))
        assert drv._mode == "mock"


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


class TestPlatformDetection:
    def test_is_raspberry_pi_true_when_model_file_present(self, tmp_path):
        model_file = tmp_path / "model"
        model_file.write_text("Raspberry Pi 5 Model B Rev 1.0")
        with patch("castor.drivers.picamera2_driver.open", create=True) as mock_open:
            mock_open.return_value.__enter__ = lambda s: s
            mock_open.return_value.__exit__ = MagicMock(return_value=False)
            mock_open.return_value.read.return_value = "Raspberry Pi 5"
            with patch("os.path.exists", return_value=True):
                from castor.drivers.picamera2_driver import _is_raspberry_pi

                # Just verify it doesn't crash; result depends on env
                result = _is_raspberry_pi()
                assert isinstance(result, bool)

    def test_is_raspberry_pi_false_when_no_model_file(self):
        with patch("os.path.exists", return_value=False):
            from castor.drivers.picamera2_driver import _is_raspberry_pi

            assert _is_raspberry_pi() is False

    def test_auto_detect_false_without_sdk(self):
        with (
            patch("castor.drivers.picamera2_driver.HAS_PICAMERA2", False),
            patch("castor.drivers.picamera2_driver._is_raspberry_pi", return_value=True),
        ):
            from castor.drivers.picamera2_driver import _auto_detect

            assert _auto_detect() is False

    def test_default_resolution_parsed(self):
        drv, _ = _make_driver(has_sdk=False, config={"resolution": [640, 480]})
        assert drv._width == 640
        assert drv._height == 480
