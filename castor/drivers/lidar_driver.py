"""
2D LIDAR driver for OpenCastor — RPLidar A1/A2/C1/S2.

Env:
  LIDAR_PORT     — serial port (default /dev/ttyUSB0)
  LIDAR_BAUD     — baud rate (default 115200)
  LIDAR_TIMEOUT  — read timeout seconds (default 3)
  LIDAR_HISTORY_DB — SQLite path for scan history
                     (default ~/.castor/lidar_history.db; set to "none" to disable)

REST API:
  GET /api/lidar/scan      — {scan: [{angle_deg, distance_mm, quality}], latency_ms, mode}
  GET /api/lidar/obstacles — {min_distance_mm, nearest_angle_deg, sectors: {front,left,right,rear}}

Install: pip install rplidar-roboticia
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OpenCastor.Lidar")

try:
    from rplidar import RPLidar as _RPLidar

    HAS_RPLIDAR = True
except ImportError:
    HAS_RPLIDAR = False

# ── Singleton ─────────────────────────────────────────────────────────────────

_singleton: Optional[LidarDriver] = None
_singleton_lock = threading.Lock()

# ── Sector definitions ────────────────────────────────────────────────────────
# Each sector: (name, min_angle_deg, max_angle_deg) — all values 0–360.
# Front covers ±45° around 0°/360° (wraps around), others are contiguous ranges.
_SECTORS = {
    "front": (315.0, 45.0),  # wraps around 0°
    "right": (45.0, 135.0),
    "rear": (135.0, 225.0),
    "left": (225.0, 315.0),
}

# ── History constants ──────────────────────────────────────────────────────────
_HISTORY_PRUNE_INTERVAL = 1000  # prune every N inserts
_HISTORY_DEFAULT_WINDOW_S = 86400.0  # 24 hours

_HISTORY_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL NOT NULL,
    min_distance_mm REAL,
    front_mm        REAL,
    left_mm         REAL,
    right_mm        REAL,
    rear_mm         REAL,
    point_count     INTEGER
);
"""
_HISTORY_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS scans_ts ON scans (ts);"


def _angle_in_sector(angle: float, lo: float, hi: float) -> bool:
    """Return True if *angle* falls within [lo, hi], handling 360° wrap."""
    if lo <= hi:
        return lo <= angle <= hi
    # Wrapping sector (e.g. front: 315–360 ∪ 0–45)
    return angle >= lo or angle <= hi


def _resolve_history_db_path() -> Optional[str]:
    """Resolve the history DB path from env or default.

    Returns None when logging is disabled ("none").
    """
    raw = os.getenv("LIDAR_HISTORY_DB", "").strip()
    if raw == "":
        # Use default path
        raw = os.path.join(os.path.expanduser("~"), ".castor", "lidar_history.db")
    if raw.lower() == "none":
        return None
    return raw


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
        config: Optional[Dict[str, Any]] = None,
    ):
        cfg = config or {}
        self._port: str = port or cfg.get("port") or os.getenv("LIDAR_PORT", "/dev/ttyUSB0")
        self._baud: int = int(baud or cfg.get("baud") or os.getenv("LIDAR_BAUD", "115200"))
        self._timeout: float = float(
            timeout or cfg.get("timeout") or os.getenv("LIDAR_TIMEOUT", "3")
        )
        self._mode = "mock"
        self._lidar: Optional[object] = None  # _RPLidar instance
        self._lock = threading.Lock()
        self._scan_count: int = 0
        self._last_scan: list = []

        # ── History DB ────────────────────────────────────────────────────────
        self._history_db_path: Optional[str] = _resolve_history_db_path()
        self._history_con: Optional[Any] = None  # sqlite3.Connection
        self._history_insert_count: int = 0

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
            points.append(
                {
                    "angle_deg": round(angle, 1),
                    "distance_mm": round(dist, 1),
                    "quality": 15,
                }
            )
        return points

    # ── History DB helpers ────────────────────────────────────────────────────

    def _ensure_history_db(self) -> bool:
        """Open and initialise the history SQLite DB if not already done.

        Returns True when the connection is ready, False on any error.
        """
        if self._history_con is not None:
            return True
        if self._history_db_path is None:
            return False
        try:
            db_dir = os.path.dirname(self._history_db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            con = sqlite3.connect(self._history_db_path, check_same_thread=False)
            con.execute(_HISTORY_CREATE_TABLE)
            con.execute(_HISTORY_CREATE_INDEX)
            con.commit()
            self._history_con = con
            logger.debug("LidarDriver: history DB opened at %s", self._history_db_path)
            return True
        except Exception as exc:
            logger.warning("LidarDriver: could not open history DB: %s", exc)
            return False

    def _log_scan(self, obstacles: dict, point_count: int) -> None:
        """Append one scan summary row to the history DB.

        Silently swallows all exceptions so that a DB failure never
        propagates into ``scan()``.
        """
        if self._history_db_path is None:
            return
        try:
            if not self._ensure_history_db():
                return
            ts = time.time()
            sectors = obstacles.get("sectors", {})
            self._history_con.execute(  # type: ignore[union-attr]
                "INSERT INTO scans "
                "(ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    obstacles.get("min_distance_mm"),
                    sectors.get("front"),
                    sectors.get("left"),
                    sectors.get("right"),
                    sectors.get("rear"),
                    point_count,
                ),
            )
            self._history_con.commit()  # type: ignore[union-attr]
            self._history_insert_count += 1

            # Auto-prune every _HISTORY_PRUNE_INTERVAL inserts
            if self._history_insert_count % _HISTORY_PRUNE_INTERVAL == 0:
                cutoff = ts - _HISTORY_DEFAULT_WINDOW_S
                self._history_con.execute(  # type: ignore[union-attr]
                    "DELETE FROM scans WHERE ts < ?", (cutoff,)
                )
                self._history_con.commit()  # type: ignore[union-attr]
                logger.debug(
                    "LidarDriver: pruned history rows older than %.0f s",
                    _HISTORY_DEFAULT_WINDOW_S,
                )
        except Exception as exc:
            logger.warning("LidarDriver: history log error: %s", exc)

    def get_scan_history(self, window_s: float = 60.0, limit: int = 500) -> List[Dict[str, Any]]:
        """Return recent scan summaries from the history DB.

        Args:
            window_s: Time window in seconds to look back (default 60 s).
            limit:    Maximum number of rows to return (default 500).

        Returns:
            List of dicts with keys
            ``{ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count}``,
            ordered newest-first. Returns an empty list when history is disabled or on error.
        """
        if self._history_db_path is None:
            return []
        try:
            if not self._ensure_history_db():
                return []
            cutoff = time.time() - window_s
            cur = self._history_con.execute(  # type: ignore[union-attr]
                "SELECT ts, min_distance_mm, front_mm, left_mm, right_mm, rear_mm, point_count "
                "FROM scans WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (cutoff, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "ts": row[0],
                    "min_distance_mm": row[1],
                    "front_mm": row[2],
                    "left_mm": row[3],
                    "right_mm": row[4],
                    "rear_mm": row[5],
                    "point_count": row[6],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning("LidarDriver: get_scan_history error: %s", exc)
            return []

    # ── Core scan ─────────────────────────────────────────────────────────────

    def scan(self) -> list:
        """Perform one full rotation scan.

        Returns a list of dicts: {angle_deg, distance_mm, quality}.
        Caches the last scan so obstacles() can use it without re-scanning.
        After completing the scan, logs a summary row to the history DB.
        """
        if self._mode != "hardware" or self._lidar is None:
            result = self._mock_scan()
            self._last_scan = result
            obs = self.obstacles()
            self._log_scan(obs, len(result))
            return result

        with self._lock:
            try:
                points = []
                # iter_scans() yields complete 360° sweeps; we take the first one
                for _scan_no, scan_data in enumerate(self._lidar.iter_scans()):
                    for quality, angle, distance in scan_data:
                        if distance > 0:
                            points.append(
                                {
                                    "angle_deg": round(float(angle), 1),
                                    "distance_mm": round(float(distance), 1),
                                    "quality": int(quality),
                                }
                            )
                    break  # one sweep is enough
                self._scan_count += 1
                self._last_scan = points
                obs = self.obstacles()
                self._log_scan(obs, len(points))
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

    def close(self) -> None:
        """Stop the motor, disconnect, and close the history DB."""
        self.stop()
        if self._history_con is not None:
            try:
                self._history_con.close()
            except Exception:
                pass
            self._history_con = None
        logger.info("LidarDriver: closed (port=%s)", self._port)

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
