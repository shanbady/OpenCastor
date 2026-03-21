"""Competition data models for OpenCastor (#735, #736).

Provides enums and dataclasses for sprint competitions, threshold races,
and leaderboard entries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class CompetitionFormat(str, Enum):
    SPRINT = "sprint"
    THRESHOLD_RACE = "threshold_race"
    BRACKET_SEASON = "bracket_season"


class CompetitionStatus(str, Enum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    LOCKED = "locked"
    COMPLETED = "completed"


def _parse_dt(val: object) -> datetime:
    """Parse a datetime value that may be a datetime or ISO string.

    Always returns a timezone-aware UTC datetime.
    """
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    dt = datetime.fromisoformat(str(val))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class SprintCompetition:
    id: str
    name: str
    format: CompetitionFormat
    hardware_tiers: list[str]
    starts_at: datetime
    ends_at: datetime
    prize_pool_credits: int
    status: CompetitionStatus
    created_at: datetime
    model_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "format": self.format.value,
            "hardware_tiers": self.hardware_tiers,
            "model_id": self.model_id,
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "prize_pool_credits": self.prize_pool_credits,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> SprintCompetition:
        return cls(
            id=data["id"],
            name=data["name"],
            format=CompetitionFormat(data["format"]),
            hardware_tiers=list(data.get("hardware_tiers", [])),
            model_id=data.get("model_id"),
            starts_at=_parse_dt(data["starts_at"]),
            ends_at=_parse_dt(data["ends_at"]),
            prize_pool_credits=int(data.get("prize_pool_credits", 0)),
            status=CompetitionStatus(data["status"]),
            created_at=_parse_dt(data["created_at"]),
        )


@dataclass
class SprintEntry:
    competition_id: str
    rrn: str
    best_score: float
    submitted_at: datetime
    rank: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "competition_id": self.competition_id,
            "rrn": self.rrn,
            "best_score": self.best_score,
            "submitted_at": self.submitted_at.isoformat(),
            "rank": self.rank,
        }

    @classmethod
    def from_dict(cls, data: dict, competition_id: str) -> SprintEntry:
        return cls(
            competition_id=competition_id,
            rrn=data["rrn"],
            best_score=float(data.get("best_score", 0.0)),
            submitted_at=_parse_dt(data["submitted_at"]),
            rank=data.get("rank"),
        )


# ---------------------------------------------------------------------------
# Threshold Race models (#736)
# ---------------------------------------------------------------------------


class RaceStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    EXPIRED = "expired"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass
class ThresholdRace:
    """A threshold race competition — ends the moment any robot verifiably hits the target score."""

    id: str
    name: str
    hardware_tier: str
    target_score: float
    scenario_pack_id: str
    prize_pool_credits: int
    soft_deadline: datetime
    status: RaceStatus = RaceStatus.OPEN
    model_id: Optional[str] = None
    winner_rrn: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "hardware_tier": self.hardware_tier,
            "model_id": self.model_id,
            "target_score": self.target_score,
            "scenario_pack_id": self.scenario_pack_id,
            "prize_pool_credits": self.prize_pool_credits,
            "soft_deadline": self.soft_deadline.isoformat(),
            "status": self.status.value,
            "winner_rrn": self.winner_rrn,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ThresholdRace:
        soft_deadline_raw = data.get("soft_deadline")
        if isinstance(soft_deadline_raw, datetime):
            soft_deadline = soft_deadline_raw
        elif soft_deadline_raw is not None:
            soft_deadline = _parse_dt(soft_deadline_raw)
        else:
            soft_deadline = datetime.max.replace(tzinfo=timezone.utc)

        return cls(
            id=data["id"],
            name=data["name"],
            hardware_tier=data["hardware_tier"],
            model_id=data.get("model_id"),
            target_score=float(data["target_score"]),
            scenario_pack_id=data.get("scenario_pack_id", "default"),
            prize_pool_credits=int(data.get("prize_pool_credits", 0)),
            soft_deadline=soft_deadline,
            status=RaceStatus(data.get("status", "open")),
            winner_rrn=data.get("winner_rrn"),
            created_at=int(data.get("created_at", time.time())),
        )


@dataclass
class ThresholdEntry:
    """A robot's best submission for a threshold race."""

    race_id: str
    rrn: str
    best_score: float
    submitted_at: int = field(default_factory=lambda: int(time.time()))
    verification_status: VerificationStatus = VerificationStatus.PENDING

    def to_dict(self) -> dict:
        return {
            "race_id": self.race_id,
            "rrn": self.rrn,
            "best_score": self.best_score,
            "submitted_at": self.submitted_at,
            "verification_status": self.verification_status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ThresholdEntry:
        return cls(
            race_id=data["race_id"],
            rrn=data["rrn"],
            best_score=float(data.get("best_score", 0.0)),
            submitted_at=int(data.get("submitted_at", time.time())),
            verification_status=VerificationStatus(data.get("verification_status", "pending")),
        )
