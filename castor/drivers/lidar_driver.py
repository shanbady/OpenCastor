"""
2D LIDAR driver for OpenCastor — RPLidar A1/A2/C1/S2.

Env:
  LIDAR_PORT     — serial port (default /dev/ttyUSB0)
  LIDAR_BAUD     — baud rate (default 115200)
  LIDAR_TIMEOUT  — read timeout seconds (default 3)

REST API:
  GET /api/lidar/scan      — {scan: [{angle_deg, distance_mm, quality}], latency_ms, mode}
  GET /api/lidar/obstacles — {min_distance_mm, nearest_angle_deg, sectors: {front,left,right,rear}}

Install: pip install rplidar-roboticia
"""

import logging
import math
import os
import threading
from typing import Optional

logger = logging.getLogger("OpenCastor.Lidar")

try:
    from rplidar import RPLidar as _RPLidar

    HAS_RPLIDAR = True
except ImportError:
    HAS_RPLIDAR = False

# ── Singleton ─────────────────────────────────────────────────────────────────

_singleton: Optional["LidarDriver"] = None
_singleton_lock = threading.Lock()

# ── Sector definitions ────────────────────────────────────────────────────────
# Each sector: (name, min_angle_deg, max_angle_deg) — all values 0–360.
# Front covers ±45° around 0°/360° (wraps around), others are contiguous ranges.
_SECTORS = {
    "front": (315.0, 45.0),    # wraps around 0°
    "right": (45.0, 135.0),
    "rear": (135.0, 225.0),
    "left": (225.0, 315.0),
}


def _angle_in_sector(angle: float, lo: float, hi: float) -> bool:
    """Return True if *angle* falls within [lo, hi], handling 360° wrap."""
    if lo <= hi:
        return lo <= angle <= hi
    # Wrapping sector (e.g. front: 315–360 ∪ 0–45)
    return angle >= lo or angle <= hi


class LidarDriver:
    """2D LIDAR driver for RPLidar A1/A2/C1/S2 series.

    Performs a single full rotation scan on demand. Falls back to a mock
    sine-wave wall pattern when the rplidar library is unavailable or the
    device cannot be opened.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: Optional[int] = None,
        timeout: Optional[float] = None,
    ):
        self._port: str = port or os.getenv("LIDAR_PORT", "/dev/ttyUSB0")
        self._baud: int = int(baud or os.getenv("LIDAR_BAUD", "115200"))
        self._timeout: float = float(timeout or os.getenv("LIDAR_TIMEOUT", "3"))
        self._mode = "mock"
        self._lidar: Optional[object] = None  # _RPLidar instance
        self._lock = threading.Lock()
        self._scan_count: int = 0
        self._last_scan: list = []

        if not HAS_RPLIDAR:
            logger.info(
                "LidarDriver: rplidar not installed — mock mode "
                "(install: pip install rplidar-roboticia)"
            )
            return

        try:
            self._lidar = _RPLidar(self._port, baudrate=self._baud, timeout=self._timeout)
            info = self._lidar.get_info()
            logger.info(
                "RPLidar connected on %s: model=%s firmware=%s hardware=%s",
                self._port,
                info.get("model", "?"),
                info.get("firmware", "?"),
                info.get("hardware", "?"),
            )
            self._mode = "hardware"
        except Exception as exc:
            logger.warning("LidarDriver: could not open %s: %s — mock mode", self._port, exc)
            self._lidar = None

    # ── Mock data generation ──────────────────────────────────────────────────

    def _mock_scan(self) -> list:
        """Generate a fake 360-point scan: sine-wave wall + obstacle at 90°."""
        points = []
        for i in range(360):
            angle = float(i)
            # Base wall 2000 mm away with gentle undulation
            dist = 2000.0 + math.sin(math.radians(angle * 2)) * 150.0
            # Simulated obstacle at ~90° (right side), range 400 mm, ±15° wide
            if 75 <= angle <= 105:
                obstacle_dist = 400.0 + math.sin(math.radians((angle - 90) * 12)) * 30.0
                dist = min(dist, obstacle_dist)
            points.append({
                "angle_deg": round(angle, 1),
                "distance_mm": round(dist, 1),
                "quality": 15,
            })
        return points

    # ── Core scan ─────────────────────────────────────────────────────────────

    def scan(self) -> list:
        """Perform one full rotation scan.

        Returns a list of dicts: {angle_deg, distance_mm, quality}.
        Caches the last scan so obstacles() can use it without re-scanning.
        """
        if self._mode != "hardware" or self._lidar is None:
            result = self._mock_scan()
            self._last_scan = result
            return result

        with self._lock:
            try:
                points = []
                # iter_scans() yields complete 360° sweeps; we take the first one
                for _scan_no, scan_data in enumerate(self._lidar.iter_scans()):
                    for quality, angle, distance in scan_data:
                        if distance > 0:
                            points.append({
                                "angle_deg": round(float(angle), 1),
                                "distance_mm": round(float(distance), 1),
                                "quality": int(quality),
                            })
                    break  # one sweep is enough
                self._scan_count += 1
                self._last_scan = points
                return points
            except Exception as exc:
                logger.error("LidarDriver scan error: %s", exc)
                return self._last_scan  # return stale data rather than empty

    # ── Obstacle analysis ─────────────────────────────────────────────────────

    def obstacles(self) -> dict:
        """Analyse the most recent scan and return per-sector minimum distances.

        Returns:
            {
              min_distance_mm: float,
              nearest_angle_deg: float,
              sectors: {front, right, rear, left}  — min mm per sector
            }

        If no scan has been taken yet, triggers one automatically.
        """
        data = self._last_scan if self._last_scan else self.scan()

        if not data:
            empty = {s: None for s in _SECTORS}
            return {"min_distance_mm": None, "nearest_angle_deg": None, "sectors": empty}

        sector_min: dict = {name: float("inf") for name in _SECTORS}
        global_min = float("inf")
        global_angle = 0.0

        for point in data:
            angle = point["angle_deg"]
            dist = point["distance_mm"]
            if dist <= 0:
                continue
            if dist < global_min:
                global_min = dist
                global_angle = angle
            for name, (lo, hi) in _SECTORS.items():
                if _angle_in_sector(angle, lo, hi):
                    if dist < sector_min[name]:
                        sector_min[name] = dist

        # Replace inf with None for clean JSON
        sector_result = {
            k: (round(v, 1) if v != float("inf") else None) for k, v in sector_min.items()
        }

        return {
            "min_distance_mm": round(global_min, 1) if global_min != float("inf") else None,
            "nearest_angle_deg": round(global_angle, 1),
            "sectors": sector_result,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Connect to the LIDAR device (no-op in mock mode)."""
        if self._mode != "hardware" or self._lidar is None:
            logger.debug("LidarDriver.start(): mock mode — skipping")
            return
        with self._lock:
            try:
                self._lidar.start_motor()
                logger.info("LidarDriver: motor started on %s", self._port)
            except Exception as exc:
                logger.warning("LidarDriver.start() failed: %s", exc)

    def stop(self):
        """Stop the motor and disconnect from the device."""
        if self._lidar is None:
            return
        with self._lock:
            try:
                self._lidar.stop()
                self._lidar.stop_motor()
                self._lidar.disconnect()
                logger.info("LidarDriver: disconnected from %s", self._port)
            except Exception as exc:
                logger.warning("LidarDriver.stop() error: %s", exc)

    def health_check(self) -> dict:
        """Return driver health information."""
        return {
            "ok": True,
            "mode": self._mode,
            "port": self._port,
            "baud": self._baud,
            "scan_count": self._scan_count,
            "error": None,
        }


# ── Singleton factory ─────────────────────────────────────────────────────────


def get_lidar(
    port: Optional[str] = None,
    baud: Optional[int] = None,
    timeout: Optional[float] = None,
) -> LidarDriver:
    """Return the process-wide LidarDriver singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = LidarDriver(port=port, baud=baud, timeout=timeout)
    return _singleton
