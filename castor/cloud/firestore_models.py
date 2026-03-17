"""Firestore document models for the OpenCastor remote fleet management schema.

Firestore layout:
    /robots/{rrn}/                      — robot identity + live state
    /robots/{rrn}/commands/{cmd_id}     — remote command queue
    /robots/{rrn}/telemetry/{ts}        — telemetry history (optional, capped)
    /robots/{rrn}/consent_requests/{id} — R2RAM incoming consent requests
    /robots/{rrn}/consent_peers/{owner} — granted outbound consent records
    /owners/{uid}/robots                — index: Firebase UID → RRN list
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Robot document  /robots/{rrn}
# ---------------------------------------------------------------------------


@dataclass
class RobotStatus:
    online: bool
    last_seen: str = field(default_factory=_utcnow)
    error: str | None = None


@dataclass
class RobotDoc:
    """Top-level robot document in Firestore."""

    rrn: str
    name: str
    owner: str  # RRN owner prefix, e.g. "rrn://craigm26"
    firebase_uid: str  # Firebase Auth UID of the owner
    ruri: str  # RCAN RURI
    capabilities: list[str]  # ["chat", "nav", "control", "vision"]
    version: str  # OpenCastor version
    bridge_version: str
    registered_at: str = field(default_factory=_utcnow)
    status: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Command document  /robots/{rrn}/commands/{cmd_id}
# ---------------------------------------------------------------------------


class CommandStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass
class CommandDoc:
    """Remote command queued for a robot."""

    instruction: str
    scope: str  # discover | status | chat | control | safety
    issued_by_uid: str  # Firebase Auth UID
    issued_by_owner: str  # RRN owner prefix of issuer
    issued_at: str = field(default_factory=_utcnow)
    message_type: str = "command"  # "command" | "consent_request" | "estop"
    status: str = CommandStatus.PENDING
    # R2RAM
    granted_scopes: list[str] = field(default_factory=list)
    consent_id: str | None = None
    # Result (filled by bridge after execution)
    result: dict[str, Any] | None = None
    error: str | None = None
    ack_at: str | None = None
    completed_at: str | None = None
    # Optional: robot-to-robot source
    source_rrn: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ---------------------------------------------------------------------------
# Consent request  /robots/{rrn}/consent_requests/{id}
# ---------------------------------------------------------------------------


class ConsentStatus:
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class ConsentRequestDoc:
    """Incoming R2RAM consent request from another robot/owner."""

    from_rrn: str
    from_owner: str
    from_ruri: str
    requested_scopes: list[str]
    reason: str
    duration_hours: int
    status: str = ConsentStatus.PENDING
    created_at: str = field(default_factory=_utcnow)
    resolved_at: str | None = None
    resolved_by_uid: str | None = None
    granted_scopes: list[str] = field(default_factory=list)
    consent_id: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ---------------------------------------------------------------------------
# Consent peer record  /robots/{rrn}/consent_peers/{peer_owner}
# ---------------------------------------------------------------------------


@dataclass
class ConsentPeerDoc:
    """Established consent relationship with another owner's robots."""

    peer_rrn: str
    peer_owner: str
    peer_ruri: str
    granted_scopes: list[str]
    established_at: str = field(default_factory=_utcnow)
    expires_at: str | None = None
    consent_id: str | None = None
    direction: str = "inbound"  # "inbound" | "outbound" | "mutual"
    status: str = ConsentStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}
