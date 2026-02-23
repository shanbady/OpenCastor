"""
Stepper Motor Driver — NEMA 17/23 via DRV8825 / TMC2209 / A4988.

Controls two stepper motors via GPIO STEP/DIR/EN signals for differential drive.
Each motor runs in a background thread for non-blocking stepping.

RCAN config:
  drivers:
  - id: steppers
    protocol: stepper
    steps_per_rev: 200
    microstep: 16
    max_rpm: 60
    left_motor:
      step_pin: 18
      dir_pin: 23
      en_pin: 24
    right_motor:
      step_pin: 19
      dir_pin: 25
      en_pin: 12

Install: pip install RPi.GPIO
"""

import logging
import threading
import time

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.Stepper")

try:
    import RPi.GPIO as _GPIO

    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False


class _StepperMotor:
    """Single stepper motor: STEP / DIR / optional EN GPIO control."""

    def __init__(
        self,
        step_pin: int,
        dir_pin: int,
        en_pin: int | None,
        steps_per_rev: int,
        microstep: int,
        hardware: bool,
    ):
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.en_pin = en_pin
        self._total_steps = steps_per_rev * microstep
        self._hardware = hardware
        self._running = False
        self._thread: threading.Thread | None = None

        if hardware and HAS_GPIO:
            try:
                _GPIO.setup(step_pin, _GPIO.OUT)
                _GPIO.setup(dir_pin, _GPIO.OUT)
                if en_pin is not None:
                    _GPIO.setup(en_pin, _GPIO.OUT)
                    _GPIO.output(en_pin, _GPIO.LOW)  # active-low enable
            except Exception as exc:
                logger.warning("Stepper pin setup failed: %s — forcing mock", exc)
                self._hardware = False

    def enable(self, on: bool):
        if self._hardware and HAS_GPIO and self.en_pin is not None:
            _GPIO.output(self.en_pin, _GPIO.LOW if on else _GPIO.HIGH)

    def step_continuous(self, rpm: float):
        """Start stepping at *rpm* (negative = reverse). Returns immediately."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.3)
        if rpm == 0.0:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, args=(rpm,), daemon=True)
        self._thread.start()

    def _loop(self, rpm: float):
        direction = rpm > 0
        steps_per_sec = abs(rpm) / 60.0 * self._total_steps
        delay = max(0.0001, 1.0 / (2.0 * steps_per_sec))
        if self._hardware and HAS_GPIO:
            _GPIO.output(self.dir_pin, _GPIO.HIGH if direction else _GPIO.LOW)
        while self._running:
            if self._hardware and HAS_GPIO:
                _GPIO.output(self.step_pin, _GPIO.HIGH)
                time.sleep(delay)
                _GPIO.output(self.step_pin, _GPIO.LOW)
                time.sleep(delay)
            else:
                time.sleep(delay * 2)

    def halt(self):
        self._running = False


class StepperDriver(DriverBase):
    """Two-motor stepper driver for differential-drive robots."""

    def __init__(self, config: dict):
        self.config = config
        self._max_rpm: float = float(config.get("max_rpm", 60))
        steps_per_rev = int(config.get("steps_per_rev", 200))
        microstep = int(config.get("microstep", 16))
        self._mode = "mock"

        if HAS_GPIO:
            try:
                _GPIO.setmode(_GPIO.BCM)
                _GPIO.setwarnings(False)
                self._mode = "hardware"
            except Exception as exc:
                logger.warning("GPIO init failed: %s — mock mode", exc)

        hardware = self._mode == "hardware"

        def _make_motor(key: str, defaults: dict) -> _StepperMotor:
            cfg = config.get(key, {})
            return _StepperMotor(
                step_pin=int(cfg.get("step_pin", defaults["step"])),
                dir_pin=int(cfg.get("dir_pin", defaults["dir"])),
                en_pin=int(cfg["en_pin"]) if "en_pin" in cfg else None,
                steps_per_rev=steps_per_rev,
                microstep=microstep,
                hardware=hardware,
            )

        self._left = _make_motor("left_motor", {"step": 18, "dir": 23})
        self._right = _make_motor("right_motor", {"step": 19, "dir": 25})
        logger.info("StepperDriver ready, mode=%s, max_rpm=%.0f", self._mode, self._max_rpm)

    def move(self, action: dict):
        linear = float(action.get("linear", 0.0))
        angular = float(action.get("angular", 0.0))
        l_rpm = max(-self._max_rpm, min(self._max_rpm, (linear - angular) * self._max_rpm))
        r_rpm = max(-self._max_rpm, min(self._max_rpm, (linear + angular) * self._max_rpm))
        if self._mode == "hardware":
            self._left.step_continuous(l_rpm)
            self._right.step_continuous(r_rpm)
        else:
            logger.debug("MOCK stepper move: left_rpm=%.1f right_rpm=%.1f", l_rpm, r_rpm)

    def stop(self):
        self._left.halt()
        self._right.halt()

    def close(self):
        self.stop()
        if HAS_GPIO and self._mode == "hardware":
            try:
                _GPIO.cleanup()
            except Exception:
                pass

    def health_check(self) -> dict:
        return {
            "ok": True,
            "mode": self._mode,
            "max_rpm": self._max_rpm,
            "error": None,
        }
