"""castor.skills.contribute — Idle compute donation skill."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("OpenCastor.Contribute")
_STATS_PATH = Path.home() / ".config" / "opencastor" / "contribute_stats.json"
_skill_instance: ContributeSkill | None = None


class ContributeSkill:
    def __init__(self) -> None:
        self._active = False
        self._cancel_flag: list[bool] = [False]
        self._thread: threading.Thread | None = None
        self._config: dict[str, Any] = {}
        self._stats = self._load_stats()

    def _load_stats(self) -> dict:
        try:
            if _STATS_PATH.exists():
                return json.loads(_STATS_PATH.read_text())
        except Exception:
            pass
        return {
            "work_units_total": 0,
            "contribute_minutes_today": 0,
            "contribute_minutes_lifetime": 0,
        }

    def _save_stats(self) -> None:
        try:
            _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STATS_PATH.write_text(json.dumps(self._stats))
        except Exception:
            pass

    def is_idle(self, last_active_ts: float, idle_after_minutes: int) -> bool:
        return (time.time() - last_active_ts) >= idle_after_minutes * 60

    def start(self, config: dict | None = None) -> None:
        if self._active:
            return
        self._config = config or {}
        self._cancel_flag[0] = False
        self._active = True
        self._thread = threading.Thread(target=self._contribute_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._cancel_flag[0] = True
        self._active = False
        if self._thread:
            self._thread.join(timeout=5)

    def status(self) -> dict:
        return {
            "enabled": self._config.get("enabled", False),
            "active": self._active,
            "project": (self._config.get("projects") or [None])[0],
            "work_units_total": self._stats.get("work_units_total", 0),
            "contribute_minutes_today": self._stats.get("contribute_minutes_today", 0),
            "contribute_minutes_lifetime": self._stats.get("contribute_minutes_lifetime", 0),
        }

    def _contribute_loop(self) -> None:
        from castor.contribute.coordinator import make_coordinator
        from castor.contribute.runner import run_work_unit

        projects = self._config.get("projects", ["science"])
        coordinator_type = self._config.get("coordinator", "simulated")
        coordinator_url = self._config.get("boinc_url", "")
        coordinator = make_coordinator(coordinator_type, coordinator_url)
        hw: dict = {}
        while self._active and not self._cancel_flag[0]:
            try:
                wu = coordinator.fetch_work_unit(hw, projects)
                if wu is None:
                    time.sleep(30)
                    continue
                start_t = time.time()
                result = run_work_unit(wu, cancelled_flag=self._cancel_flag)
                if result.status == "complete":
                    coordinator.submit_result(result)
                    elapsed_min = max(1, int((time.time() - start_t) / 60))
                    self._stats["work_units_total"] = self._stats.get("work_units_total", 0) + 1
                    self._stats["contribute_minutes_today"] = (
                        self._stats.get("contribute_minutes_today", 0) + elapsed_min
                    )
                    self._stats["contribute_minutes_lifetime"] = (
                        self._stats.get("contribute_minutes_lifetime", 0) + elapsed_min
                    )
                    self._save_stats()
            except Exception as exc:
                log.warning("Contribute loop error: %s", exc)
                time.sleep(10)


def get_contribute_status() -> dict:
    global _skill_instance
    if _skill_instance is None:
        _skill_instance = ContributeSkill()
    return _skill_instance.status()
