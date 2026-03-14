"""SO-ARM101 vision utilities — camera ROI capture for grasp targeting.

Provides a lightweight, gracefully-degrading helper to grab a single frame
from a V4L2 camera and return region-of-interest (ROI) metadata.  All errors
are caught and return ``None`` so callers can treat camera availability as
purely optional.
"""

from __future__ import annotations

from typing import Optional


def get_camera_frame_roi(camera_device: str = "/dev/video0") -> Optional[dict]:
    """Capture a single frame and return ROI metadata for grasp targeting.

    Opens the V4L2 device at *camera_device*, reads one frame, releases the
    capture, and returns a dict of basic ROI metadata.  If the camera is
    unavailable or cv2 is not installed the function returns ``None`` without
    raising an exception — camera presence is always optional for the arm CLI.

    Args:
        camera_device: V4L2 device path (default ``"/dev/video0"``).

    Returns:
        Dict with keys ``width``, ``height``, ``center_x``, ``center_y``,
        ``timestamp_ms``, and ``device``; or ``None`` if unavailable.
    """
    try:
        import cv2  # type: ignore[import]

        cap = cv2.VideoCapture(camera_device)
        if not cap.isOpened():
            return None
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        h, w = frame.shape[:2]
        return {
            "width": w,
            "height": h,
            "center_x": w // 2,
            "center_y": h // 2,
            "timestamp_ms": int(__import__("time").time() * 1000),
            "device": camera_device,
        }
    except Exception:
        return None
