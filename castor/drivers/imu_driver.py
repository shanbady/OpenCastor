"""
IMU driver for OpenCastor.

Supports MPU6050, BNO055, ICM-42688-P via I2C (smbus2).

Env:
  IMU_I2C_BUS      — I2C bus number (default 1)
  IMU_I2C_ADDRESS  — hex address (default 0x68 for MPU6050, 0x28 for BNO055)
  IMU_MODEL        — "mpu6050" | "bno055" | "icm42688" | "auto" (default auto)

REST API:
  GET /api/imu/latest      — {accel_g, gyro_dps, mag_uT, temp_c, mode}
  GET /api/imu/calibrate   — trigger BNO055 calibration (others: no-op)

Install: pip install smbus2
"""

import logging
import math
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("OpenCastor.IMU")

try:
    import smbus2

    HAS_SMBUS2 = True
except ImportError:
    HAS_SMBUS2 = False

# ── Register maps ─────────────────────────────────────────────────────────────

# MPU6050 / ICM-42688-P shared registers
_MPU_ADDR_PWR_MGMT_1 = 0x6B
_MPU_ADDR_ACCEL_XOUT_H = 0x3B
_MPU_ADDR_TEMP_OUT_H = 0x41
_MPU_ADDR_GYRO_XOUT_H = 0x43
_MPU_ADDR_WHO_AM_I = 0x75

# MPU6050 WHO_AM_I value
_MPU6050_WHO_AM_I = 0x68

# ICM-42688-P WHO_AM_I value
_ICM42688_WHO_AM_I = 0x47

# ICM-42688-P specific registers
_ICM_ADDR_PWR_MGMT0 = 0x4E
_ICM_ADDR_ACCEL_DATA_X1 = 0x1F
_ICM_ADDR_GYRO_DATA_X1 = 0x25
_ICM_ADDR_TEMP_DATA1 = 0x1D

# BNO055 registers
_BNO_ADDR_CHIP_ID = 0x00
_BNO_CHIP_ID = 0xA0
_BNO_ADDR_OPR_MODE = 0x3D
_BNO_ADDR_CALIB_STAT = 0x35
_BNO_ADDR_ACC_DATA_X_LSB = 0x28
_BNO_ADDR_GYR_DATA_X_LSB = 0x14
_BNO_ADDR_MAG_DATA_X_LSB = 0x0E
_BNO_ADDR_TEMP = 0x34
_BNO_OPR_MODE_NDOF = 0x0C  # 9-DOF fusion mode

# Scale factors
_MPU6050_ACCEL_SCALE = 16384.0  # LSB/g  (±2g default)
_MPU6050_GYRO_SCALE = 131.0  # LSB/dps (±250dps default)
_ICM42688_ACCEL_SCALE = 2048.0  # LSB/g  (±16g default FS_SEL=0 → use 16g range)
_ICM42688_GYRO_SCALE = 16.4  # LSB/dps (±2000dps default)
_BNO055_ACCEL_SCALE = 100.0  # LSB/m·s⁻² → convert to g by dividing by 9.80665*100
_BNO055_GYRO_SCALE = 16.0  # LSB/dps

# Singleton
_singleton: Optional["IMUDriver"] = None
_singleton_lock = threading.Lock()


def _s16(high: int, low: int) -> int:
    """Combine two bytes into a signed 16-bit integer."""
    val = (high << 8) | low
    return val if val < 32768 else val - 65536


class IMUDriver:
    """IMU driver supporting MPU6050, BNO055, and ICM-42688-P sensors.

    Auto-detects the connected sensor by probing known I2C addresses and
    reading WHO_AM_I / CHIP_ID registers. Falls back to a mock mode when
    smbus2 is not available or no sensor is found.
    """

    # Known probe targets: (address, model_hint)
    _PROBE_TARGETS = [
        (0x68, "mpu_or_icm"),  # MPU6050 or ICM-42688-P (AD0 low)
        (0x69, "mpu_or_icm"),  # MPU6050 or ICM-42688-P (AD0 high)
        (0x28, "bno055"),  # BNO055 (COM3 low)
        (0x29, "bno055"),  # BNO055 (COM3 high)
    ]

    def __init__(
        self,
        bus: int = 1,
        address: Optional[int] = None,
        model: str = "auto",
    ):
        self._bus_num = int(os.getenv("IMU_I2C_BUS", str(bus)))
        self._model = os.getenv("IMU_MODEL", model).lower()
        self._mode = "mock"
        self._bus: Optional[smbus2.SMBus] = None  # type: ignore[name-defined]
        self._address: int = 0x68
        self._detected_model: str = "none"
        self._lock = threading.Lock()
        self._orientation: dict = {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}
        self._last_orient_ts: float = 0.0

        # Resolve explicit address from env or constructor argument
        env_addr = os.getenv("IMU_I2C_ADDRESS", "")
        if env_addr:
            address = int(env_addr, 16)

        if not HAS_SMBUS2:
            logger.info(
                "IMU driver: smbus2 not installed — mock mode (install: pip install smbus2)"
            )
            self._detected_model = "mock"
            return

        try:
            self._bus = smbus2.SMBus(self._bus_num)
            if address is not None and self._model != "auto":
                # Explicit address + model: trust the caller
                self._address = address
                self._detected_model = self._model
                self._init_sensor()
            else:
                self._autodetect(address)
        except Exception as exc:
            logger.warning("IMU init failed: %s — mock mode", exc)
            self._detected_model = "mock"

    # ── Auto-detect ───────────────────────────────────────────────────────────

    def _autodetect(self, hint_address: Optional[int]):
        """Probe I2C bus to detect sensor model and address."""
        targets = [(hint_address, "auto")] if hint_address is not None else self._PROBE_TARGETS
        for addr, hint in targets:
            try:
                if hint in ("auto", "mpu_or_icm"):
                    who = self._bus.read_byte_data(addr, _MPU_ADDR_WHO_AM_I)
                    if who == _MPU6050_WHO_AM_I or who == addr:
                        self._address = addr
                        self._detected_model = "mpu6050"
                        self._init_sensor()
                        logger.info("IMU autodetected MPU6050 at 0x%02x", addr)
                        return
                    if who == _ICM42688_WHO_AM_I:
                        self._address = addr
                        self._detected_model = "icm42688"
                        self._init_sensor()
                        logger.info("IMU autodetected ICM-42688-P at 0x%02x", addr)
                        return
                if hint in ("auto", "bno055"):
                    chip = self._bus.read_byte_data(addr, _BNO_ADDR_CHIP_ID)
                    if chip == _BNO_CHIP_ID:
                        self._address = addr
                        self._detected_model = "bno055"
                        self._init_sensor()
                        logger.info("IMU autodetected BNO055 at 0x%02x", addr)
                        return
            except Exception:
                continue  # Address not responding — try next

        logger.warning("IMU: no sensor found on bus %d — mock mode", self._bus_num)
        self._detected_model = "mock"

    def _init_sensor(self):
        """Wake up / configure the detected sensor."""
        try:
            if self._detected_model == "mpu6050":
                # Clear sleep bit (bit 6 of PWR_MGMT_1)
                self._bus.write_byte_data(self._address, _MPU_ADDR_PWR_MGMT_1, 0x00)
                time.sleep(0.1)
                self._mode = "hardware"
                logger.info("MPU6050 initialized at 0x%02x on bus %d", self._address, self._bus_num)

            elif self._detected_model == "icm42688":
                # PWR_MGMT0: accel+gyro in low-noise mode (0x0F)
                self._bus.write_byte_data(self._address, _ICM_ADDR_PWR_MGMT0, 0x0F)
                time.sleep(0.05)
                self._mode = "hardware"
                logger.info(
                    "ICM-42688-P initialized at 0x%02x on bus %d", self._address, self._bus_num
                )

            elif self._detected_model == "bno055":
                time.sleep(0.65)  # BNO055 boot time
                # Set NDOF fusion mode
                self._bus.write_byte_data(self._address, _BNO_ADDR_OPR_MODE, _BNO_OPR_MODE_NDOF)
                time.sleep(0.02)
                self._mode = "hardware"
                logger.info("BNO055 initialized at 0x%02x on bus %d", self._address, self._bus_num)

        except Exception as exc:
            logger.warning("IMU sensor init failed: %s — mock mode", exc)
            self._detected_model = "mock"

    # ── Low-level reads ───────────────────────────────────────────────────────

    def _read_mpu6050(self) -> dict:
        """Read raw data from MPU6050."""
        raw = self._bus.read_i2c_block_data(self._address, _MPU_ADDR_ACCEL_XOUT_H, 14)
        ax = _s16(raw[0], raw[1]) / _MPU6050_ACCEL_SCALE
        ay = _s16(raw[2], raw[3]) / _MPU6050_ACCEL_SCALE
        az = _s16(raw[4], raw[5]) / _MPU6050_ACCEL_SCALE
        temp_c = _s16(raw[6], raw[7]) / 340.0 + 36.53
        gx = _s16(raw[8], raw[9]) / _MPU6050_GYRO_SCALE
        gy = _s16(raw[10], raw[11]) / _MPU6050_GYRO_SCALE
        gz = _s16(raw[12], raw[13]) / _MPU6050_GYRO_SCALE
        return {
            "accel_g": {"x": round(ax, 4), "y": round(ay, 4), "z": round(az, 4)},
            "gyro_dps": {"x": round(gx, 4), "y": round(gy, 4), "z": round(gz, 4)},
            "mag_uT": None,
            "temp_c": round(temp_c, 2),
            "mode": "hardware",
            "model": "mpu6050",
        }

    def _read_icm42688(self) -> dict:
        """Read raw data from ICM-42688-P.

        Register layout is similar to MPU6050 but starting at different base
        addresses and using different scale factors.
        """
        # Temperature (2 bytes)
        temp_raw = self._bus.read_i2c_block_data(self._address, _ICM_ADDR_TEMP_DATA1, 2)
        temp_c = _s16(temp_raw[0], temp_raw[1]) / 132.48 + 25.0

        # Accel (6 bytes: X_H, X_L, Y_H, Y_L, Z_H, Z_L)
        acc_raw = self._bus.read_i2c_block_data(self._address, _ICM_ADDR_ACCEL_DATA_X1, 6)
        ax = _s16(acc_raw[0], acc_raw[1]) / _ICM42688_ACCEL_SCALE
        ay = _s16(acc_raw[2], acc_raw[3]) / _ICM42688_ACCEL_SCALE
        az = _s16(acc_raw[4], acc_raw[5]) / _ICM42688_ACCEL_SCALE

        # Gyro (6 bytes)
        gyr_raw = self._bus.read_i2c_block_data(self._address, _ICM_ADDR_GYRO_DATA_X1, 6)
        gx = _s16(gyr_raw[0], gyr_raw[1]) / _ICM42688_GYRO_SCALE
        gy = _s16(gyr_raw[2], gyr_raw[3]) / _ICM42688_GYRO_SCALE
        gz = _s16(gyr_raw[4], gyr_raw[5]) / _ICM42688_GYRO_SCALE

        return {
            "accel_g": {"x": round(ax, 4), "y": round(ay, 4), "z": round(az, 4)},
            "gyro_dps": {"x": round(gx, 4), "y": round(gy, 4), "z": round(gz, 4)},
            "mag_uT": None,
            "temp_c": round(temp_c, 2),
            "mode": "hardware",
            "model": "icm42688",
        }

    def _read_bno055(self) -> dict:
        """Read fused data from BNO055.

        BNO055 in NDOF mode outputs 16-bit little-endian values.
        """
        # Accelerometer (LSB first, 6 bytes)
        acc_raw = self._bus.read_i2c_block_data(self._address, _BNO_ADDR_ACC_DATA_X_LSB, 6)
        ax = _s16(acc_raw[1], acc_raw[0]) / (_BNO055_ACCEL_SCALE * 9.80665)
        ay = _s16(acc_raw[3], acc_raw[2]) / (_BNO055_ACCEL_SCALE * 9.80665)
        az = _s16(acc_raw[5], acc_raw[4]) / (_BNO055_ACCEL_SCALE * 9.80665)

        # Magnetometer (LSB first, 6 bytes)
        mag_raw = self._bus.read_i2c_block_data(self._address, _BNO_ADDR_MAG_DATA_X_LSB, 6)
        mx = _s16(mag_raw[1], mag_raw[0]) / 16.0  # 1/16 µT per LSB
        my = _s16(mag_raw[3], mag_raw[2]) / 16.0
        mz = _s16(mag_raw[5], mag_raw[4]) / 16.0

        # Gyroscope (LSB first, 6 bytes)
        gyr_raw = self._bus.read_i2c_block_data(self._address, _BNO_ADDR_GYR_DATA_X_LSB, 6)
        gx = _s16(gyr_raw[1], gyr_raw[0]) / _BNO055_GYRO_SCALE
        gy = _s16(gyr_raw[3], gyr_raw[2]) / _BNO055_GYRO_SCALE
        gz = _s16(gyr_raw[5], gyr_raw[4]) / _BNO055_GYRO_SCALE

        # Temperature (signed 8-bit, 1°C per LSB in NDOF mode)
        temp_c = self._bus.read_byte_data(self._address, _BNO_ADDR_TEMP)
        if temp_c > 127:
            temp_c -= 256

        return {
            "accel_g": {"x": round(ax, 4), "y": round(ay, 4), "z": round(az, 4)},
            "gyro_dps": {"x": round(gx, 4), "y": round(gy, 4), "z": round(gz, 4)},
            "mag_uT": {"x": round(mx, 2), "y": round(my, 2), "z": round(mz, 2)},
            "temp_c": float(temp_c),
            "mode": "hardware",
            "model": "bno055",
        }

    def _mock_read(self) -> dict:
        """Return static near-zero mock values with a tiny sinusoidal wobble."""
        t = time.monotonic()
        wobble = math.sin(t * 0.5) * 0.002
        return {
            "accel_g": {
                "x": round(wobble, 4),
                "y": round(wobble * 0.5, 4),
                "z": round(1.0 + wobble, 4),
            },
            "gyro_dps": {"x": round(wobble * 10, 4), "y": 0.0, "z": 0.0},
            "mag_uT": None,
            "temp_c": 25.0,
            "mode": "mock",
            "model": self._detected_model,
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def read(self) -> dict:
        """Read current IMU data.

        Returns a dict with keys: accel_g {x,y,z}, gyro_dps {x,y,z},
        mag_uT {x,y,z} or None, temp_c, mode, model.
        """
        if self._mode != "hardware" or self._bus is None:
            return self._mock_read()

        with self._lock:
            try:
                if self._detected_model == "mpu6050":
                    return self._read_mpu6050()
                if self._detected_model == "icm42688":
                    return self._read_icm42688()
                if self._detected_model == "bno055":
                    return self._read_bno055()
            except Exception as exc:
                logger.error("IMU read error: %s", exc)
                return {
                    "accel_g": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "gyro_dps": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "mag_uT": None,
                    "temp_c": 0.0,
                    "mode": "error",
                    "model": self._detected_model,
                    "error": str(exc),
                }
        return self._mock_read()

    def calibrate(self) -> dict:
        """Trigger calibration.

        BNO055: returns the current calibration status (sys/gyro/accel/mag 0–3).
        MPU6050 / ICM-42688-P: calibration not supported by hardware — returns a note.
        """
        if self._detected_model == "bno055" and self._mode == "hardware":
            with self._lock:
                try:
                    calib = self._bus.read_byte_data(self._address, _BNO_ADDR_CALIB_STAT)
                    sys_cal = (calib >> 6) & 0x03
                    gyro_cal = (calib >> 4) & 0x03
                    accel_cal = (calib >> 2) & 0x03
                    mag_cal = calib & 0x03
                    fully_calibrated = all(v == 3 for v in [sys_cal, gyro_cal, accel_cal, mag_cal])
                    return {
                        "ok": True,
                        "model": "bno055",
                        "sys": sys_cal,
                        "gyro": gyro_cal,
                        "accel": accel_cal,
                        "mag": mag_cal,
                        "fully_calibrated": fully_calibrated,
                    }
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}

        return {
            "ok": True,
            "model": self._detected_model,
            "note": "calibration not supported for this sensor",
        }

    def orientation(self) -> dict:
        """Return current orientation estimate with confidence.

        In mock mode returns zeros with confidence 0.5.

        In hardware mode integrates gyroscope readings over ``dt`` (seconds
        since the last call) to update ``_orientation`` via dead-reckoning.
        When a magnetometer is available (BNO055) a simple complementary
        filter blends the gyro integration with the mag-derived yaw, giving
        confidence 0.9.  Gyro-only gives confidence 0.7.

        Returns:
            {
                "yaw_deg":   float,
                "pitch_deg": float,
                "roll_deg":  float,
                "confidence": float,  # 0.5 mock | 0.7 gyro-only | 0.9 with mag
                "mode":      str,
            }
        """
        if self._mode != "hardware" or self._bus is None:
            return {
                "yaw_deg": 0.0,
                "pitch_deg": 0.0,
                "roll_deg": 0.0,
                "confidence": 0.5,
                "mode": "mock",
            }

        try:
            data = self.read()
            now = time.monotonic()
            dt = now - self._last_orient_ts if self._last_orient_ts > 0.0 else 0.0
            self._last_orient_ts = now

            gyro = data.get("gyro_dps", {})
            gx = float(gyro.get("x", 0.0))
            gy = float(gyro.get("y", 0.0))
            gz = float(gyro.get("z", 0.0))

            if dt > 0.0:
                self._orientation["roll_deg"] += gx * dt
                self._orientation["pitch_deg"] += gy * dt
                self._orientation["yaw_deg"] += gz * dt

            mag = data.get("mag_uT")
            if mag is not None:
                # Complementary filter: trust mag 10 % per step for yaw correction
                mx = float(mag.get("x", 0.0))
                my = float(mag.get("y", 0.0))
                if mx != 0.0 or my != 0.0:
                    mag_yaw = math.degrees(math.atan2(my, mx))
                    alpha = 0.10
                    self._orientation["yaw_deg"] = (1.0 - alpha) * self._orientation[
                        "yaw_deg"
                    ] + alpha * mag_yaw
                confidence = 0.9
            else:
                confidence = 0.7

            return {
                "yaw_deg": round(self._orientation["yaw_deg"], 4),
                "pitch_deg": round(self._orientation["pitch_deg"], 4),
                "roll_deg": round(self._orientation["roll_deg"], 4),
                "confidence": confidence,
                "mode": self._mode,
            }
        except Exception as exc:
            logger.warning("IMUDriver.orientation error: %s", exc)
            return {
                "yaw_deg": self._orientation.get("yaw_deg", 0.0),
                "pitch_deg": self._orientation.get("pitch_deg", 0.0),
                "roll_deg": self._orientation.get("roll_deg", 0.0),
                "confidence": 0.5,
                "mode": "error",
            }

    def reset_orientation(self) -> None:
        """Zero out the accumulated orientation estimate."""
        self._orientation = {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0}
        self._last_orient_ts = 0.0

    def health_check(self) -> dict:
        """Return driver health information."""
        return {
            "ok": self._mode in ("hardware", "mock"),
            "mode": self._mode,
            "model": self._detected_model,
            "address": hex(self._address) if self._mode == "hardware" else None,
            "bus": self._bus_num,
            "error": None,
        }

    def close(self):
        """Release the I2C bus handle."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                pass
            self._bus = None
        self._mode = "mock"


# ── Singleton factory ─────────────────────────────────────────────────────────


def get_imu(
    bus: Optional[int] = None,
    address: Optional[int] = None,
    model: Optional[str] = None,
) -> IMUDriver:
    """Return the process-wide IMUDriver singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _bus = bus if bus is not None else int(os.getenv("IMU_I2C_BUS", "1"))
            _model = model if model is not None else os.getenv("IMU_MODEL", "auto")
            env_addr = os.getenv("IMU_I2C_ADDRESS", "")
            _address = address
            if env_addr and _address is None:
                _address = int(env_addr, 16)
            _singleton = IMUDriver(bus=_bus, address=_address, model=_model)
    return _singleton
