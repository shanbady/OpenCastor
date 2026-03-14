"""
HLaboratories ACB v2.0 — Actuator Control Board driver.

Supports:
  - USB-C serial control (pyserial)
  - CAN Bus control (python-can, see transport= config key)
  - Mock fallback when neither is installed

ACB v2.0 hardware:
  - STM32G474RET6 controller
  - 3-phase BLDC motor with hall-effect encoder
  - 12V–30V, up to 40A (XT60 power)
  - USB-C for firmware updates + control
  - CAN Bus @ 1Mbit/s (2-pin JST GH)

RCAN config (USB-C)::

    drivers:
      - id: left_hip
        protocol: acb
        port: /dev/ttyACM0    # or 'auto' for USB VID/PID detection
        pole_pairs: 7
        control_mode: velocity  # velocity | position | torque | voltage
        max_velocity: 50.0      # rad/s
        pid:
          vel_p: 0.25
          vel_i: 1.0
          vel_d: 0.0
          pos_p: 20.0
          pos_i: 1.0
          pos_d: 0.0
          curr_p: 0.5
          curr_i: 0.1
          curr_d: 0.001

RCAN config (CAN Bus)::

    drivers:
      - id: right_knee
        protocol: acb
        transport: can
        can_interface: socketcan
        can_channel: can0
        can_node_id: 1
        pole_pairs: 7
        control_mode: position

Install::

    pip install opencastor[hlabs]   # adds python-can
    # pyserial is already a core dependency

References:
  - ACB docs: https://docs.hlaboratories.com/motors/ACB%20v2.0
  - Firmware: https://github.com/h-laboratories/acb-v2.0

Note: The serial/CAN protocol below is a reasonable default for STM32 devices.
      The actual ACB v2.0 firmware protocol should be validated against the
      firmware source before production use.  Users may subclass AcbDriver and
      override ``_send_usb`` / ``_read_encoder_can`` to match a specific firmware.

USB serial protocol (newline-delimited JSON)::

    # Commands sent to device:
    # {"cmd": "set_velocity", "value": 1.5}
    # {"cmd": "set_position", "value": 3.14}
    # {"cmd": "set_torque",   "value": 0.5}
    # {"cmd": "get_encoder"}
    # {"cmd": "get_version"}
    # {"cmd": "set_pole_pairs", "value": 7}
    # {"cmd": "calibrate_encoder"}
    # {"cmd": "set_pid", "key": "vel_p", "value": 0.25}
    #
    # Response:
    # {"pos_rad": 0.0, "vel_rad_s": 0.0, "current_a": 0.0, "error_flags": 0}
    # {"version": "1.0.0"}
    # {"zero_electrical_angle": 0.0, "ok": true}

CAN protocol::

    # Node ID = config['can_node_id']
    # Frame ID = (node_id << 5) | cmd_id   (11-bit standard CAN)
    # CMD_ID_SET_VELOCITY = 0x01, data = float32 LE
    # CMD_ID_SET_POSITION = 0x02, data = float32 LE
    # CMD_ID_SET_TORQUE   = 0x05, data = float32 LE
    # CMD_ID_GET_ENCODER  = 0x03, data = empty
    # RESP_ENCODER        = 0x04, data = 3x float32 LE (pos_rad, vel_rad_s, current_a)
    # CMD_ID_CALIBRATE    = 0x10, data = JSON bytes (up to 8 bytes)
    # CMD_ID_SET_PID      = 0x12, data = float32 LE
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.AcbDriver")

# ---------------------------------------------------------------------------
# Optional SDK guards
# ---------------------------------------------------------------------------

try:
    import serial as _serial

    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False
    _serial = None  # type: ignore[assignment]

try:
    from castor.drivers.can_transport import CanTransport

    HAS_CAN_TRANSPORT = True
except ImportError:
    HAS_CAN_TRANSPORT = False
    CanTransport = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# CAN command IDs
# ---------------------------------------------------------------------------
_CMD_SET_VELOCITY = 0x01
_CMD_SET_POSITION = 0x02
_CMD_GET_ENCODER = 0x03
_RESP_ENCODER = 0x04
_CMD_SET_TORQUE = 0x05
_CMD_CALIBRATE = 0x10
_CMD_SET_POLE_PAIRS = 0x11
_CMD_SET_PID = 0x12


# ---------------------------------------------------------------------------
# CalibrationResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class CalibrationResult:
    """Result of an ACB motor calibration sequence."""

    success: bool
    zero_electrical_angle: float
    pole_pairs: int
    pid_applied: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage."""
        return {
            "success": self.success,
            "zero_electrical_angle": self.zero_electrical_angle,
            "pole_pairs": self.pole_pairs,
            "pid_applied": self.pid_applied,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# AcbDriver
# ---------------------------------------------------------------------------


class AcbDriver(DriverBase):
    """HLaboratories ACB v2.0 actuator driver.

    Supports USB-C serial (JSON protocol) and CAN Bus transport.
    Degrades gracefully to mock mode when hardware is unavailable.

    Args:
        config: RCAN driver config dict.  Relevant keys:
                ``protocol`` (``"acb"``),
                ``transport`` (``"usb"`` | ``"can"``; default ``"usb"``),
                ``port`` (USB serial port path or ``"auto"``),
                ``pole_pairs`` (int; default 7),
                ``control_mode`` (``"velocity"`` | ``"position"`` | ``"torque"`` | ``"voltage"``),
                ``max_velocity`` (float rad/s; default 50.0),
                ``can_interface``, ``can_channel``, ``can_node_id`` (CAN only),
                ``pid`` (dict with vel_p/vel_i/vel_d/pos_p/pos_i/pos_d/curr_p/curr_i/curr_d),
                ``telemetry_hz`` (int; default 50),
                ``calibrate_on_boot`` (bool; default ``False``).
    """

    def __init__(self, config: dict):
        self.config = config
        self._transport_type: str = config.get("transport", "usb").lower()
        self._port: Optional[str] = config.get("port", "/dev/ttyACM0")
        self._pole_pairs: int = int(config.get("pole_pairs", 7))
        self._control_mode: str = config.get("control_mode", "velocity").lower()
        self._max_velocity: float = float(config.get("max_velocity", 50.0))
        self._pid: dict = dict(config.get("pid") or {})
        self._driver_id: str = config.get("id", "acb")
        self._telemetry_hz: int = int(config.get("telemetry_hz", 50))
        self._can_node_id: int = int(config.get("can_node_id", 1))

        # Internal state
        self._mode = "mock"
        self._serial_conn = None
        self._can: Optional[CanTransport] = None
        self._serial_lock = threading.Lock()
        self._telemetry: dict = {
            "pos_rad": 0.0,
            "vel_rad_s": 0.0,
            "current_a": 0.0,
            "voltage_v": 0.0,
            "error_flags": 0,
            "is_calibrated": False,
            "control_mode": self._control_mode,
            "ts": time.time(),
        }
        self._telemetry_lock = threading.RLock()
        self._telemetry_thread: Optional[threading.Thread] = None
        self._running = False

        # Auto-detect port when requested
        if self._transport_type == "usb" and str(self._port or "").lower() == "auto":
            self._port = self._auto_detect_port()

        env_mock = os.getenv("ACB_MOCK", "").strip().lower() in ("1", "true")
        force_mock = bool(config.get("mock", False)) or env_mock

        if not force_mock:
            if self._transport_type == "can":
                self._init_can(config)
            else:
                self._init_usb()

        if self._mode == "mock":
            logger.info(
                "ACB driver running in mock mode (id=%s transport=%s)",
                self._driver_id,
                self._transport_type,
            )

        # Optional boot calibration
        if config.get("calibrate_on_boot", False) and self._mode == "hardware":
            logger.info("ACB calibrate_on_boot=true — running calibration for %s", self._driver_id)
            self.calibrate()

        self._start_telemetry_loop()

    # ── Init helpers ──────────────────────────────────────────────────────────

    def _auto_detect_port(self) -> Optional[str]:
        """Auto-detect ACB USB serial port.

        Returns:
            First detected port string, or ``None`` if none found.
        """
        try:
            from castor.hardware_detect import detect_acb_usb

            ports = detect_acb_usb()
            if ports:
                logger.info("ACB auto-detected on %s", ports[0])
                return ports[0]
        except Exception as exc:
            logger.debug("ACB auto-detect error: %s", exc)
        logger.warning("ACB port=auto but no device found — running in mock mode")
        return None

    def _init_usb(self) -> None:
        """Attempt USB-C serial connection."""
        if not HAS_PYSERIAL:
            logger.warning("pyserial not installed — ACB USB mock mode")
            return
        if not self._port:
            logger.warning("ACB: no port configured — mock mode")
            return
        try:
            self._serial_conn = _serial.Serial(self._port, baudrate=115200, timeout=0.1)
            self._mode = "hardware"
            logger.info("ACB connected via USB on %s", self._port)
        except Exception as exc:
            logger.warning("ACB USB init failed on %s: %s — mock mode", self._port, exc)

    def _init_can(self, config: dict) -> None:
        """Attempt CAN bus connection."""
        if not HAS_CAN_TRANSPORT:
            logger.warning("python-can not installed — ACB CAN mock mode")
            return
        try:
            self._can = CanTransport(
                interface=config.get("can_interface", "socketcan"),
                channel=config.get("can_channel", "can0"),
                bitrate=int(config.get("can_bitrate", 1_000_000)),
            )
            if self._can.connected:
                self._mode = "hardware"
                logger.info(
                    "ACB connected via CAN node_id=%d on %s/%s",
                    self._can_node_id,
                    config.get("can_interface", "socketcan"),
                    config.get("can_channel", "can0"),
                )
            else:
                logger.warning(
                    "ACB CAN bus failed to open (%s/%s) — mock mode",
                    config.get("can_interface", "socketcan"),
                    config.get("can_channel", "can0"),
                )
        except Exception as exc:
            logger.warning("ACB CAN init failed: %s — mock mode", exc)

    # ── USB serial helpers ────────────────────────────────────────────────────

    def _send_usb(self, cmd: dict) -> Optional[dict]:
        """Send a newline-delimited JSON command; return parsed response or ``None``."""
        if not self._serial_conn:
            return None
        payload = (json.dumps(cmd) + "\n").encode()
        try:
            with self._serial_lock:
                self._serial_conn.write(payload)
                raw = self._serial_conn.readline()
            if raw:
                return json.loads(raw.decode().strip())
        except Exception as exc:
            logger.warning("ACB USB send error: %s", exc)
        return None

    # ── CAN helpers ───────────────────────────────────────────────────────────

    def _send_can_f32(self, cmd_id: int, value: float) -> None:
        """Pack a single float32 LE into a CAN frame and send it."""
        if not self._can:
            return
        data = struct.pack("<f", float(value))
        self._can.send(self._can_node_id, cmd_id, data)

    def _read_encoder_can(self) -> dict:
        """Request encoder state via CAN and return the parsed dict."""
        if not self._can:
            return {"pos_rad": 0.0, "vel_rad_s": 0.0, "current_a": 0.0, "error_flags": 0}
        try:
            self._can.send(self._can_node_id, _CMD_GET_ENCODER, b"")
            resp = self._can.recv(timeout=0.05)
            if resp:
                _, cmd_id, data = resp
                if cmd_id == _RESP_ENCODER and len(data) >= 12:
                    pos, vel, curr = struct.unpack("<fff", data[:12])
                    return {
                        "pos_rad": float(pos),
                        "vel_rad_s": float(vel),
                        "current_a": float(curr),
                        "error_flags": 0,
                    }
        except Exception as exc:
            logger.debug("ACB CAN encoder read error: %s", exc)
        return {"pos_rad": 0.0, "vel_rad_s": 0.0, "current_a": 0.0, "error_flags": 0}

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def _start_telemetry_loop(self) -> None:
        """Spawn the background encoder polling thread."""
        if self._telemetry_hz <= 0:
            return
        self._running = True
        self._telemetry_thread = threading.Thread(
            target=self._telemetry_loop,
            daemon=True,
            name=f"acb-telemetry-{self._driver_id}",
        )
        self._telemetry_thread.start()

    def _telemetry_loop(self) -> None:
        """Background loop: polls ``get_encoder()`` at ``_telemetry_hz`` Hz."""
        interval = 1.0 / max(1, self._telemetry_hz)
        while self._running:
            try:
                enc = self.get_encoder()
                with self._telemetry_lock:
                    self._telemetry.update(
                        {
                            "pos_rad": enc.get("pos_rad", 0.0),
                            "vel_rad_s": enc.get("vel_rad_s", 0.0),
                            "current_a": enc.get("current_a", 0.0),
                            "error_flags": enc.get("error_flags", 0),
                            "control_mode": self._control_mode,
                            "ts": time.time(),
                        }
                    )
            except Exception as exc:
                logger.debug("ACB telemetry loop error: %s", exc)
            time.sleep(interval)

    def get_telemetry(self) -> dict:
        """Return latest encoder telemetry snapshot (thread-safe).

        Returns:
            Dict with ``pos_rad``, ``vel_rad_s``, ``current_a``, ``voltage_v``,
            ``error_flags``, ``is_calibrated``, ``control_mode``, ``ts``.
        """
        with self._telemetry_lock:
            return dict(self._telemetry)

    # ── DriverBase interface ──────────────────────────────────────────────────

    def _move(self, linear: float = 0.0, angular: float = 0.0) -> None:
        """Map linear speed to velocity setpoint.

        For a single-axis ACB joint, ``linear`` scales to the velocity setpoint;
        ``angular`` is ignored (single-axis device, not a differential drive).

        Args:
            linear:  Forward/backward speed in [-1.0, 1.0].  Scaled by ``max_velocity``.
            angular: Not used for single-axis ACB joints.
        """
        vel = linear * self._max_velocity

        if self._mode == "mock":
            logger.debug("MOCK ACB move: linear=%.3f vel=%.3f rad/s", linear, vel)
            return

        self.set_velocity(vel)

    def set_velocity(self, rad_s: float) -> None:
        """Send a velocity setpoint.

        Args:
            rad_s: Target velocity in rad/s.
        """
        if self._mode == "mock":
            logger.debug("MOCK ACB set_velocity: %.3f rad/s", rad_s)
            return

        if self._transport_type == "can" and self._can:
            try:
                self._send_can_f32(_CMD_SET_VELOCITY, rad_s)
            except Exception as exc:
                logger.error("ACB CAN set_velocity error: %s", exc)
        elif self._serial_conn:
            self._send_usb({"cmd": "set_velocity", "value": rad_s})

    def set_position(self, pos_rad: float) -> None:
        """Send a position setpoint.

        Requires ``control_mode: position`` in the RCAN config.

        Args:
            pos_rad: Target position in radians.
        """
        if self._mode == "mock":
            logger.debug("MOCK ACB set_position: %.4f rad", pos_rad)
            return

        if self._transport_type == "can" and self._can:
            try:
                self._send_can_f32(_CMD_SET_POSITION, pos_rad)
            except Exception as exc:
                logger.error("ACB CAN set_position error: %s", exc)
        elif self._serial_conn:
            self._send_usb({"cmd": "set_position", "value": pos_rad})

    def set_torque(self, nm: float) -> None:
        """Send a torque setpoint.

        Requires ``control_mode: torque`` in the RCAN config.

        Args:
            nm: Target torque in Nm.
        """
        if self._mode == "mock":
            logger.debug("MOCK ACB set_torque: %.4f Nm", nm)
            return

        if self._transport_type == "can" and self._can:
            try:
                self._send_can_f32(_CMD_SET_TORQUE, nm)
            except Exception as exc:
                logger.error("ACB CAN set_torque error: %s", exc)
        elif self._serial_conn:
            self._send_usb({"cmd": "set_torque", "value": nm})

    def get_encoder(self) -> dict:
        """Read current encoder state.

        Returns:
            Dict with ``pos_rad`` (float), ``vel_rad_s`` (float),
            ``current_a`` (float), ``error_flags`` (int).
        """
        if self._mode == "mock":
            return {"pos_rad": 0.0, "vel_rad_s": 0.0, "current_a": 0.0, "error_flags": 0}

        if self._transport_type == "can" and self._can:
            return self._read_encoder_can()

        if self._serial_conn:
            resp = self._send_usb({"cmd": "get_encoder"})
            if resp:
                return {
                    "pos_rad": float(resp.get("pos_rad", 0.0)),
                    "vel_rad_s": float(resp.get("vel_rad_s", 0.0)),
                    "current_a": float(resp.get("current_a", 0.0)),
                    "error_flags": int(resp.get("error_flags", 0)),
                }

        return {"pos_rad": 0.0, "vel_rad_s": 0.0, "current_a": 0.0, "error_flags": 0}

    def calibrate(self) -> CalibrationResult:
        """Run motor calibration sequence.

        Steps:
          1. Send ``pole_pairs`` to device.
          2. Trigger encoder calibration; wait for response (up to 10 s).
          3. Read back zero electrical angle.
          4. Push all PID values from config.
          5. Run brief velocity ramp test (0 → 10 rad/s → 0) to verify.
          6. Cache result to ``~/.opencastor/calibration/{driver_id}.json``.

        Returns:
            :class:`CalibrationResult` describing the outcome.
        """
        pid = dict(self._pid) if self._pid else {}

        if self._mode == "mock":
            result = CalibrationResult(
                success=True,
                zero_electrical_angle=0.0,
                pole_pairs=self._pole_pairs,
                pid_applied=pid,
                error=None,
            )
            with self._telemetry_lock:
                self._telemetry["is_calibrated"] = True
            self._cache_calibration(result)
            logger.debug("MOCK ACB calibrate: success (id=%s)", self._driver_id)
            return result

        try:
            # 1. Set pole pairs
            self._send_calibration_cmd({"cmd": "set_pole_pairs", "value": self._pole_pairs})

            # 2. Trigger encoder calibration with extended timeout
            resp = self._send_calibration_cmd({"cmd": "calibrate_encoder"}, timeout_s=10.0)
            if resp is None:
                return CalibrationResult(
                    success=False,
                    zero_electrical_angle=0.0,
                    pole_pairs=self._pole_pairs,
                    pid_applied=pid,
                    error="No response from ACB — transport error or timeout",
                )
            zero_angle = float(resp.get("zero_electrical_angle", 0.0))

            # 3. Push PID values
            for key, val in pid.items():
                self._send_calibration_cmd({"cmd": "set_pid", "key": key, "value": float(val)})

            # 4. Brief velocity ramp test
            self.set_velocity(10.0)
            time.sleep(0.5)
            self.set_velocity(0.0)
            time.sleep(0.2)

            result = CalibrationResult(
                success=True,
                zero_electrical_angle=zero_angle,
                pole_pairs=self._pole_pairs,
                pid_applied=pid,
                error=None,
            )
            with self._telemetry_lock:
                self._telemetry["is_calibrated"] = True
        except Exception as exc:
            logger.error("ACB calibration failed (id=%s): %s", self._driver_id, exc)
            result = CalibrationResult(
                success=False,
                zero_electrical_angle=0.0,
                pole_pairs=self._pole_pairs,
                pid_applied=pid,
                error=str(exc),
            )

        self._cache_calibration(result)
        return result

    def _send_calibration_cmd(self, cmd: dict, timeout_s: float = 2.0) -> Optional[dict]:
        """Send a calibration command over the active transport.

        Args:
            cmd:       JSON command dict.
            timeout_s: Serial read timeout override (USB only).

        Returns:
            Parsed response dict, or ``None``.
        """
        if self._transport_type == "usb" and self._serial_conn:
            if timeout_s != 2.0:
                raw = b""
                try:
                    with self._serial_lock:
                        old_timeout = self._serial_conn.timeout
                        self._serial_conn.timeout = timeout_s
                        try:
                            payload = (json.dumps(cmd) + "\n").encode()
                            self._serial_conn.write(payload)
                            raw = self._serial_conn.readline()
                        finally:
                            self._serial_conn.timeout = old_timeout
                    if raw:
                        return json.loads(raw.decode().strip())
                except Exception as exc:
                    logger.warning("ACB calibration cmd error: %s", exc)
                return None
            return self._send_usb(cmd)

        # CAN: pack cmd into up to 8 bytes and send on CALIBRATE command ID
        if self._transport_type == "can" and self._can:
            data = json.dumps(cmd).encode()[:8]
            self._can.send(self._can_node_id, _CMD_CALIBRATE, data)
        return None

    def _cache_calibration(self, result: CalibrationResult) -> None:
        """Write calibration result to ``~/.opencastor/calibration/{driver_id}.json``."""
        try:
            cache_dir = pathlib.Path.home() / ".opencastor" / "calibration"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{self._driver_id}.json").write_text(
                json.dumps(result.to_dict(), indent=2)
            )
        except Exception as exc:
            logger.debug("ACB calibration cache write error: %s", exc)

    def stop(self) -> None:
        """Zero velocity (emergency stop)."""
        if self._mode == "mock":
            logger.debug("MOCK ACB stop (id=%s)", self._driver_id)
            return
        self.set_velocity(0.0)

    def close(self) -> None:
        """Stop the motor, terminate telemetry thread, and release resources."""
        self._running = False
        if hasattr(self, "_telemetry_thread") and self._telemetry_thread is not None:
            self._telemetry_thread.join(timeout=2.0)
        self.stop()
        if self._serial_conn:
            try:
                self._serial_conn.close()
            except Exception:
                pass
            self._serial_conn = None
        if self._can:
            try:
                self._can.close()
            except Exception:
                pass
            self._can = None

    def health_check(self) -> dict:
        """Return driver health status.

        Returns:
            Dict with ``ok``, ``mode``, ``transport``, ``control_mode``,
            ``pole_pairs``, optional ``port``/``can_node_id``/``firmware_version``,
            and ``error``.
        """
        base: dict = {
            "driver_id": self._driver_id,
            "transport": self._transport_type,
            "control_mode": self._control_mode,
            "pole_pairs": self._pole_pairs,
        }
        if self._mode == "mock":
            return {"ok": True, "mode": "mock", "error": None, **base}

        if self._transport_type == "usb" and self._serial_conn:
            try:
                resp = self._send_usb({"cmd": "get_version"})
                fw = resp.get("version", "unknown") if resp else "unknown"
                return {
                    "ok": self._serial_conn.is_open,
                    "mode": "hardware",
                    "port": self._port,
                    "firmware_version": fw,
                    "error": None,
                    **base,
                }
            except Exception as exc:
                return {"ok": False, "mode": "hardware", "error": str(exc), **base}

        if self._transport_type == "can" and self._can:
            return {
                "ok": True,
                "mode": "hardware",
                "can_node_id": self._can_node_id,
                "error": None,
                **base,
            }

        return {"ok": False, "mode": "mock", "error": "no device connected", **base}
