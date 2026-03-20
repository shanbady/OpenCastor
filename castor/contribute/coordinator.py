"""Coordinator abstractions for contribute skill."""

from __future__ import annotations

import abc
import logging
import random
import time

from .work_unit import WorkUnit, WorkUnitResult

log = logging.getLogger("OpenCastor.Contribute")


class Coordinator(abc.ABC):
    @abc.abstractmethod
    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None: ...

    @abc.abstractmethod
    def submit_result(self, result: WorkUnitResult) -> bool: ...


class BOINCCoordinator(Coordinator):
    def __init__(self, url: str, timeout: int = 10) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None:
        log.info("BOINC: requesting work unit from %s", self.url)
        return None  # TODO: implement BOINC XML-RPC

    def submit_result(self, result: WorkUnitResult) -> bool:
        return True


class SimulatedCoordinator(Coordinator):
    def fetch_work_unit(self, hw_profile: dict, projects: list[str]) -> WorkUnit | None:
        return WorkUnit(
            work_unit_id=f"sim-{int(time.time())}-{random.randint(1000, 9999)}",
            project=projects[0] if projects else "science",
            coordinator_url="simulated://localhost",
            model_format="numpy",
            input_data={"type": "synthetic"},
            timeout_seconds=2,
        )

    def submit_result(self, result: WorkUnitResult) -> bool:
        return True


def make_coordinator(coordinator_type: str, url: str) -> Coordinator:
    if coordinator_type == "simulated":
        return SimulatedCoordinator()
    return BOINCCoordinator(url)
