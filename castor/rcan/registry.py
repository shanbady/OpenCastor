"""
RCAN §21 Robot Registry Framework (RRF) protocol stubs.

Implements REGISTRY_REGISTER and REGISTRY_RESOLVE message types for
registering robots with the RRF and resolving Robot Registration Numbers (RRNs)
to Robot URIs (RURIs) and associated metadata.

RRN Format (Structured URI)
---------------------------
The canonical RRN format is a structured URI with 2–4 path segments::

    rrn://[org]/[category]/[model]/[id]     # full structured (recommended)
    rrn://[org]/[category]/[id]             # category + id (model omitted)
    rrn://[org]/[id]                        # legacy flat format (category=robot assumed)

Valid categories: ``robot``, ``component``, ``sensor``, ``assembly``

Examples::

    rrn://opencastor.com/robot/v2/unit-001
    rrn://opencastor.com/component/hailo8/module-42
    rrn://luxonis.com/sensor/oak-d/cam-007
    rrn://opencastor.com/assembly/perception-stack/asm-003
    rrn://example.org/robots/rover-1        # legacy 3-segment; still valid

Spec: https://rcan.dev/spec/section-21/
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from castor.rcan.message import MessageType

# ---------------------------------------------------------------------------
# RRN category taxonomy
# ---------------------------------------------------------------------------


class RRNCategory(str, Enum):
    """Entity type categories encoded in a structured RRN path.

    Attributes:
        ROBOT:      A fully assembled, manufactured robot unit.  First-class
                    registry citizen — has a reachable RURI and runs a castor
                    gateway.
        COMPONENT:  An individual hardware piece (e.g. Hailo-8 NPU, motor
                    controller).  May be registered for inventory/tracking even
                    if it has no independent network identity.
        SENSOR:     A passive sensing device (camera, LiDAR, IMU, etc.).
                    Registered for telemetry correlation and fleet management.
        ASSEMBLY:   A modular subsystem (e.g. perception stack = OAK-D +
                    Hailo-8 + mount).  Useful for fleet operations where
                    assemblies are swapped between robot bodies.
    """

    ROBOT = "robot"
    COMPONENT = "component"
    SENSOR = "sensor"
    ASSEMBLY = "assembly"


# ---------------------------------------------------------------------------
# RRN helpers
# ---------------------------------------------------------------------------

_RRN_SCHEME = "rrn://"
_VALID_CATEGORIES = {c.value for c in RRNCategory}
# Allow alphanumerics, hyphens, dots, underscores in each segment
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate_rrn(rrn: str) -> None:
    """Validate a Robot Registration Number (RRN) URI format.

    Accepts both the legacy flat format and the new structured format::

        rrn://[org]/[id]                             # legacy — 2 path segments
        rrn://[org]/[category]/[id]                  # 3 segments
        rrn://[org]/[category]/[model]/[id]          # 4 segments (recommended)

    The ``[category]`` segment, when present, must be one of:
    ``robot``, ``component``, ``sensor``, ``assembly``.

    Args:
        rrn: The RRN string to validate.

    Raises:
        ValueError: If the RRN does not conform to the expected format.
    """
    if not isinstance(rrn, str):
        raise ValueError(f"RRN must be a string, got {type(rrn).__name__}: {rrn!r}")
    if not rrn:
        raise ValueError("RRN must not be empty")
    if not rrn.startswith(_RRN_SCHEME):
        raise ValueError(
            f"RRN must start with {_RRN_SCHEME!r} (Robot Registration Number URI scheme), "
            f"got: {rrn!r}"
        )
    rest = rrn[len(_RRN_SCHEME) :]
    parts = rest.split("/")

    if len(parts) < 2:
        raise ValueError(f"RRN must have at least 2 path segments (org + id), got: {rrn!r}")
    if len(parts) > 4:
        raise ValueError(
            f"RRN must have at most 4 path segments (org/category/model/id), got: {rrn!r}"
        )

    for i, segment in enumerate(parts):
        if not segment:
            raise ValueError(f"RRN segment {i} is empty in: {rrn!r}")
        if not _SEGMENT_RE.match(segment):
            raise ValueError(f"RRN segment {i} contains invalid characters: {segment!r} in {rrn!r}")

    # Validate category if present (segment index 1 in 3- or 4-part RRNs)
    if len(parts) >= 3:
        category = parts[1]
        if category not in _VALID_CATEGORIES:
            raise ValueError(
                f"RRN category {category!r} is not valid. "
                f"Expected one of: {sorted(_VALID_CATEGORIES)}. "
                f"RRN: {rrn!r}"
            )


def _parse_rrn(rrn: str) -> dict[str, Optional[str]]:
    """Parse a structured RRN into its components.

    Returns a dict with keys ``org``, ``category``, ``model``, ``id``.
    Fields not present in the RRN are returned as ``None``.
    Legacy 2-segment RRNs return ``category=None``.

    Args:
        rrn: A validated RRN string.

    Returns:
        Dict with keys: ``org``, ``category``, ``model``, ``id``.

    Examples::

        _parse_rrn("rrn://opencastor.com/robot/v2/unit-001")
        # {"org": "opencastor.com", "category": "robot",
        #  "model": "v2", "id": "unit-001"}

        _parse_rrn("rrn://opencastor.com/component/hailo8/module-42")
        # {"org": "opencastor.com", "category": "component",
        #  "model": "hailo8", "id": "module-42"}

        _parse_rrn("rrn://example.org/rover-1")  # legacy
        # {"org": "example.org", "category": None, "model": None, "id": "rover-1"}
    """
    _validate_rrn(rrn)
    parts = rrn[len(_RRN_SCHEME) :].split("/")
    return {
        "org": parts[0],
        "category": parts[1] if len(parts) >= 3 else None,
        "model": parts[2] if len(parts) == 4 else None,
        "id": parts[-1],
    }


# ---------------------------------------------------------------------------
# Wire message dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RegistryMessage:
    """Payload for REGISTRY_REGISTER (§21.2).

    Sent by a robot or component to register itself with the RRF.

    RRN format — use the structured URI form for new registrations::

        rrn://[org]/[category]/[model]/[id]

        rrn://opencastor.com/robot/v2/unit-001
        rrn://opencastor.com/component/hailo8/module-42
        rrn://luxonis.com/sensor/oak-d/cam-007

    Attributes:
        msg_id:     Unique message identifier (UUID).
        rrn:        Robot Registration Number — structured URI identifying
                    this entity in the global registry.
        ruri:       Robot URI — the reachable RCAN endpoint (e.g.
                    ``rcan://192.168.1.10:8000/rover-1``).
        public_key: PEM-encoded public key for ownership proof.
        timestamp:  Unix timestamp of the registration request.
        metadata:   Optional structured metadata.  Recognised keys:

                    - ``model`` (str): hardware model name / product line
                    - ``serial`` (str): manufacturer serial number
                    - ``manufacturer`` (str): organisation name
                    - ``category`` (str): explicit category override
                      (``robot`` | ``component`` | ``sensor`` | ``assembly``)
                    - ``components`` (list[str]): child RRNs for assemblies
                    - ``parent_rrn`` (str): parent RRN for components
                    - ``firmware`` (str): firmware / software version string
    """

    msg_id: str
    rrn: str
    ruri: str
    public_key: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_rrn(self.rrn)

    @property
    def category(self) -> Optional[RRNCategory]:
        """Return the :class:`RRNCategory` parsed from the RRN, or ``None`` for legacy RRNs."""
        parsed = _parse_rrn(self.rrn)
        cat = parsed.get("category")
        if cat is None:
            return None
        try:
            return RRNCategory(cat)
        except ValueError:
            return None

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_REGISTER type."""
        payload: dict[str, Any] = {
            "rrn": self.rrn,
            "ruri": self.ruri,
            "public_key": self.public_key,
            "timestamp": self.timestamp,
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return {
            "type": MessageType.REGISTRY_REGISTER,
            "msg_id": self.msg_id,
            "payload": payload,
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryMessage:
        """Parse a REGISTRY_REGISTER message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If any required field is missing or RRN is malformed.
        """
        payload = data.get("payload", data)
        required = ("rrn", "ruri", "public_key")
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing required field: '{key}'")
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        return cls(
            msg_id=msg_id,
            rrn=payload["rrn"],
            ruri=payload["ruri"],
            public_key=payload["public_key"],
            timestamp=payload.get("timestamp", time.time()),
            metadata=payload.get("metadata", {}),
        )


@dataclass
class RegistryResolveRequest:
    """Payload for REGISTRY_RESOLVE request (§21.3).

    Sent to the RRF to look up an RRN and retrieve RURI + metadata.

    Attributes:
        rrn:    Robot Registration Number to resolve.
        msg_id: Unique message identifier (UUID).
    """

    rrn: str
    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        _validate_rrn(self.rrn)

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_RESOLVE type."""
        return {
            "type": MessageType.REGISTRY_RESOLVE,
            "msg_id": self.msg_id,
            "payload": {"rrn": self.rrn},
        }


@dataclass
class RegistryResolveResponse:
    """Payload for REGISTRY_RESOLVE response (§21.3).

    Returned by the RRF with resolved RURI and verification status.

    Attributes:
        rrn:      The Robot Registration Number that was resolved.
        ruri:     Resolved Robot URI (reachable endpoint).
        verified: Whether the robot's identity has been cryptographically verified.
        tier:     Service tier (e.g. ``'free'``, ``'pro'``, ``'enterprise'``).
    """

    rrn: str
    ruri: str
    verified: bool
    tier: str

    def to_message(self) -> dict[str, Any]:
        """Serialize to a response dict using REGISTRY_RESOLVE_RESULT type (§21.5)."""
        return {
            "type": MessageType.REGISTRY_RESOLVE_RESULT,
            "payload": {
                "rrn": self.rrn,
                "ruri": self.ruri,
                "verified": self.verified,
                "tier": self.tier,
            },
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryResolveResponse:
        """Parse a REGISTRY_RESOLVE response message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If any required field is missing.
        """
        payload = data.get("payload", data)
        required = ("rrn", "ruri", "verified", "tier")
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing required field: '{key}'")
        return cls(
            rrn=payload["rrn"],
            ruri=payload["ruri"],
            verified=payload["verified"],
            tier=payload["tier"],
        )


@dataclass
class RegistryRegisterResult:
    """Result payload for REGISTRY_REGISTER (§21.4 — REGISTRY_REGISTER_RESULT).

    Sent by the RRF to the registering entity after processing a
    ``REGISTRY_REGISTER`` request.

    Attributes:
        msg_id:  Unique message identifier (UUID).
        status:  ``"success"`` or ``"failure"``.
        rrn:     Assigned or confirmed Robot Registration Number (present on success).
        error:   Human-readable error description (present on failure).
    """

    msg_id: str
    status: str  # "success" | "failure"
    rrn: Optional[str] = None
    error: Optional[str] = None

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_REGISTER_RESULT type."""
        payload: dict[str, Any] = {"status": self.status}
        if self.rrn is not None:
            payload["rrn"] = self.rrn
        if self.error is not None:
            payload["error"] = self.error
        return {
            "type": MessageType.REGISTRY_REGISTER_RESULT,
            "msg_id": self.msg_id,
            "payload": payload,
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryRegisterResult:
        """Parse a REGISTRY_REGISTER_RESULT message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If required fields are missing or inconsistent with status:
                        ``status`` is always required; ``rrn`` is required on success;
                        ``error`` is required on failure.
        """
        payload = data.get("payload", data)
        if "status" not in payload:
            raise ValueError("Missing required field: 'status'")
        status = payload["status"]
        if status == "success" and "rrn" not in payload:
            raise ValueError("RegistryRegisterResult: 'rrn' is required when status='success'")
        if status == "failure" and "error" not in payload:
            raise ValueError("RegistryRegisterResult: 'error' is required when status='failure'")
        rrn = payload.get("rrn")
        if rrn is not None:
            _validate_rrn(rrn)
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        return cls(
            msg_id=msg_id,
            status=status,
            rrn=rrn,
            error=payload.get("error"),
        )


@dataclass
class RegistryResolveResult:
    """Result payload for REGISTRY_RESOLVE (§21.5 — REGISTRY_RESOLVE_RESULT).

    Sent by the RRF in response to a ``REGISTRY_RESOLVE`` request.

    Attributes:
        msg_id:   Unique message identifier (UUID).
        status:   ``"found"``, ``"not_found"``, or ``"auth_failure"``.
        rrn:      The Robot Registration Number that was queried.
        ruri:     Resolved RURI (present when status is ``"found"``).
        error:    Human-readable error description (present on failure).
        verified: Whether the robot's identity is cryptographically verified.
        tier:     Service tier of the registered entity.
    """

    msg_id: str
    status: str  # "found" | "not_found" | "auth_failure"
    rrn: str
    ruri: Optional[str] = None
    error: Optional[str] = None
    verified: bool = False
    tier: str = "free"

    def to_message(self) -> dict[str, Any]:
        """Serialize to RCAN message format using REGISTRY_RESOLVE_RESULT type."""
        payload: dict[str, Any] = {
            "status": self.status,
            "rrn": self.rrn,
            "verified": self.verified,
            "tier": self.tier,
        }
        if self.ruri is not None:
            payload["ruri"] = self.ruri
        if self.error is not None:
            payload["error"] = self.error
        return {
            "type": MessageType.REGISTRY_RESOLVE_RESULT,
            "msg_id": self.msg_id,
            "payload": payload,
        }

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> RegistryResolveResult:
        """Parse a REGISTRY_RESOLVE_RESULT message dict.

        Args:
            data: Raw message dict (as returned by ``to_message()``).

        Raises:
            ValueError: If required fields are missing or inconsistent with status:
                        ``status`` and ``rrn`` are always required; ``ruri`` is required
                        when status is ``"found"``; ``rrn`` is validated as a legal RRN.
        """
        payload = data.get("payload", data)
        for key in ("status", "rrn"):
            if key not in payload:
                raise ValueError(f"Missing required field: '{key}'")
        status = payload["status"]
        rrn = payload["rrn"]
        _validate_rrn(rrn)
        if status == "found" and "ruri" not in payload:
            raise ValueError("RegistryResolveResult: 'ruri' is required when status='found'")
        msg_id = data.get("msg_id") or str(uuid.uuid4())
        return cls(
            msg_id=msg_id,
            status=status,
            rrn=rrn,
            ruri=payload.get("ruri"),
            error=payload.get("error"),
            verified=payload.get("verified", False),
            tier=payload.get("tier", "free"),
        )
