"""
GPIO Direct Control Driver (Raspberry Pi).

Drives digital output pins for simple control: relays, LEDs, signal lines.
Supports RPi.GPIO (legacy) and gpiod (libgpiod v2).

RCAN config:
  drivers:
  - id: gpio_ctrl
    protocol: gpio
    active_high: true
    pins:
      forward: 17
      backward: 27
      left: 22
      right: 23
      stop: 24

Install: pip install RPi.GPIO   (or gpiod for newer kernels)
"""

import logging
import time

from castor.drivers.base import DriverBase

logger = logging.getLogger("OpenCastor.GPIO")

try:
    import RPi.GPIO as _GPIO

    HAS_RPIGPIO = True
except ImportError:
    HAS_RPIGPIO = False

try:
    import gpiod

    HAS_GPIOD = True
except ImportError:
    HAS_GPIOD = False


class GPIODriver(DriverBase):
    """Digital GPIO output driver for Raspberry Pi.

    Maps directional actions (forward/backward/left/right/stop) to GPIO pins.
    Falls back to mock logging when no GPIO library is available.
    """

    def __init__(self, config: dict):
        self.config = config
        self.pins: dict = {str(k): int(v) for k, v in config.get("pins", {}).items()}
        self.active_high: bool = config.get("active_high", True)
        self._mode = "mock"
        self._active_pins: set = set()
        self._gpiod_lines: dict = {}
        self._gpiod_chip = None

        if HAS_RPIGPIO:
            try:
                _GPIO.setmode(_GPIO.BCM)
                _GPIO.setwarnings(False)
                for pin in self.pins.values():
                    _GPIO.setup(pin, _GPIO.OUT, initial=_GPIO.LOW)
                self._mode = "hardware"
                logger.info("GPIO driver ready (RPi.GPIO BCM) pins=%s", self.pins)
            except Exception as exc:
                logger.warning("RPi.GPIO init failed: %s — mock mode", exc)
        elif HAS_GPIOD:
            try:
                self._gpiod_chip = gpiod.Chip("gpiochip0")
                for name, pin in self.pins.items():
                    line = self._gpiod_chip.get_line(pin)
                    line.request(
                        consumer="opencastor",
                        type=gpiod.LINE_REQ_DIR_OUT,
                        default_vals=[0],
                    )
                    self._gpiod_lines[name] = line
                self._mode = "hardware"
                logger.info("GPIO driver ready (gpiod) pins=%s", self.pins)
            except Exception as exc:
                logger.warning("gpiod init failed: %s — mock mode", exc)
        else:
            logger.info("GPIO driver: no GPIO library — mock mode")

    # ── Internal helpers ──────────────────────────────────────────────

    def _set_pin(self, name: str, high: bool):
        if name not in self.pins:
            return
        pin = self.pins[name]
        level = high if self.active_high else not high
        if self._mode != "hardware":
            logger.debug("MOCK GPIO %s(pin=%d) → %s", name, pin, "HIGH" if level else "LOW")
            return
        if HAS_RPIGPIO:
            _GPIO.output(pin, _GPIO.HIGH if level else _GPIO.LOW)
        elif name in self._gpiod_lines:
            self._gpiod_lines[name].set_value(1 if level else 0)

    def _clear_all(self):
        for name in self.pins:
            self._set_pin(name, False)
        self._active_pins.clear()

    # ── DriverBase interface ──────────────────────────────────────────

    def move(self, action: dict):
        linear = action.get("linear", 0.0)
        angular = action.get("angular", 0.0)
        self._clear_all()
        if linear > 0.1:
            self._set_pin("forward", True)
            self._active_pins.add("forward")
        elif linear < -0.1:
            self._set_pin("backward", True)
            self._active_pins.add("backward")
        if angular > 0.1:
            self._set_pin("right", True)
            self._active_pins.add("right")
        elif angular < -0.1:
            self._set_pin("left", True)
            self._active_pins.add("left")

    def stop(self):
        self._clear_all()
        # Pulse the stop pin for 50 ms if present
        if "stop" in self.pins:
            self._set_pin("stop", True)
            time.sleep(0.05)
            self._set_pin("stop", False)

    def close(self):
        self._clear_all()
        if self._mode == "hardware":
            if HAS_RPIGPIO:
                try:
                    _GPIO.cleanup()
                except Exception:
                    pass
            elif HAS_GPIOD and self._gpiod_chip:
                try:
                    self._gpiod_chip.close()
                except Exception:
                    pass

    def health_check(self) -> dict:
        return {
            "ok": True,
            "mode": self._mode,
            "pins": self.pins,
            "active_pins": list(self._active_pins),
            "error": None,
        }
