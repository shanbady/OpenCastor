"""R2RAM Consent Manager — local enforcement of robot-to-robot authorization.

Loads consent records from Firestore and enforces scope-based access control
before any cross-owner command reaches the local castor gateway.

Ownership tiers (RCAN spec §11 R2RAM):
    same-owner   — identical RRN owner prefix → implicit consent, any scope
    trusted-peer — mutually listed, explicit scopes, time-bounded
    unknown      — blocked by default; only DISCOVER allowed
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

# Scope hierarchy (additive): control implies chat implies status implies discover
SCOPE_HIERARCHY: dict[str, int] = {
    "discover": 0,
    "status": 1,
    "chat": 2,
    "control": 3,
    "safety": 99,  # safety is special — always allowed for ESTOP, never for RESUME
    "transparency": 0,
}

# ESTOP is always honored from any robot with a valid RURI (R2RAM §7)
ESTOP_EXCEPTION_SCOPES = {"safety"}


class ConsentManager:
    """Enforces R2RAM authorization rules for incoming remote commands."""

    def __init__(self, robot_rrn: str, owner: str, db: Any = None) -> None:
        """
        Args:
            robot_rrn: This robot's RRN (e.g. "RRN-000000000001")
            owner:     RRN owner prefix (e.g. "rrn://craigm26")
            db:        Firestore client (optional — for online consent lookup)
        """
        self.robot_rrn = robot_rrn
        self.owner = owner
        self._db = db
        self._cache: dict[str, dict[str, Any]] = {}  # peer_owner → consent record

    # ------------------------------------------------------------------
    # Core authorization
    # ------------------------------------------------------------------

    def is_authorized(
        self,
        requester_owner: str,
        requested_scope: str,
        instruction: str = "",
        is_estop: bool = False,
    ) -> tuple[bool, str]:
        """Check whether a requester is authorized for the given scope.

        Returns:
            (authorized: bool, reason: str)
        """
        # Safety exception: ESTOP from any identified source is always honored
        if is_estop and requested_scope == "safety":
            if requester_owner:
                log.info("ESTOP from %s honored (R2RAM safety exception)", requester_owner)
                return True, "estop_exception"
            else:
                log.warning("ESTOP from anonymous source blocked")
                return False, "anonymous_estop_blocked"

        # RESUME requires authorization (prevents cycling attacks)
        if requested_scope == "safety" and "resume" in instruction.lower():
            if not self._is_same_owner(requester_owner):
                authorized, reason = self._check_consent(requester_owner, "control")
                if not authorized:
                    log.warning("RESUME from %s blocked — control scope required", requester_owner)
                    return False, "resume_requires_control_scope"
            return True, "resume_authorized"

        # Same-owner: implicit consent for all scopes
        if self._is_same_owner(requester_owner):
            log.debug("Same-owner request from %s authorized", requester_owner)
            return True, "same_owner"

        # Cross-owner: check consent record
        return self._check_consent(requester_owner, requested_scope)

    def _is_same_owner(self, requester_owner: str) -> bool:
        """Compare owner prefixes, normalizing for formatting differences."""

        def _normalize(o: str) -> str:
            return o.rstrip("/").lower().replace("rrn://", "")

        return _normalize(requester_owner) == _normalize(self.owner)

    def _check_consent(self, requester_owner: str, requested_scope: str) -> tuple[bool, str]:
        """Check Firestore consent records for a cross-owner peer."""
        record = self._get_consent_record(requester_owner)
        if not record:
            log.info("No consent record for %s — blocked", requester_owner)
            return False, "no_consent_record"

        # Check expiry
        expires_at = record.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at)
                if exp < datetime.now(timezone.utc):
                    log.info("Consent for %s expired at %s", requester_owner, expires_at)
                    return False, "consent_expired"
            except ValueError:
                pass

        # Check status
        if record.get("status") != "approved":
            return False, f"consent_status_{record.get('status', 'unknown')}"

        # Check scope
        granted_scopes: list[str] = record.get("granted_scopes", [])
        req_level = SCOPE_HIERARCHY.get(requested_scope, 99)

        for granted in granted_scopes:
            granted_level = SCOPE_HIERARCHY.get(granted, -1)
            if granted_level >= req_level:
                log.debug(
                    "Scope %s authorized for %s via granted scope %s",
                    requested_scope,
                    requester_owner,
                    granted,
                )
                return True, f"granted_via_{granted}"

        log.info(
            "Scope %s not in granted scopes %s for %s",
            requested_scope,
            granted_scopes,
            requester_owner,
        )
        return False, "scope_not_granted"

    # ------------------------------------------------------------------
    # Consent record lookup
    # ------------------------------------------------------------------

    def _get_consent_record(self, peer_owner: str) -> dict[str, Any] | None:
        """Load consent record from Firestore (with in-process cache)."""
        if peer_owner in self._cache:
            return self._cache[peer_owner]

        if not self._db:
            return None

        try:
            # Normalize owner to use as doc ID
            doc_id = peer_owner.replace("rrn://", "").replace("/", "_")
            ref = (
                self._db.collection("robots")
                .document(self.robot_rrn)
                .collection("consent_peers")
                .document(doc_id)
            )
            doc = ref.get()
            if doc.exists:
                record = doc.to_dict()
                self._cache[peer_owner] = record
                return record
        except Exception as e:
            log.warning("Consent record lookup failed: %s", e)

        return None

    def grant_consent(
        self,
        peer_owner: str,
        peer_rrn: str,
        peer_ruri: str,
        granted_scopes: list[str],
        duration_hours: int = 24,
        consent_id: str | None = None,
        direction: str = "inbound",
    ) -> str:
        """Write a consent grant to Firestore and local cache.

        Returns the consent_id.
        """
        if not consent_id:
            consent_id = str(uuid.uuid4())

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=duration_hours)).isoformat()

        record = {
            "peer_rrn": peer_rrn,
            "peer_owner": peer_owner,
            "peer_ruri": peer_ruri,
            "granted_scopes": granted_scopes,
            "established_at": now.isoformat(),
            "expires_at": expires_at,
            "consent_id": consent_id,
            "direction": direction,
            "status": "approved",
        }

        # Cache locally
        self._cache[peer_owner] = record

        # Persist to Firestore
        if self._db:
            try:
                doc_id = peer_owner.replace("rrn://", "").replace("/", "_")
                ref = (
                    self._db.collection("robots")
                    .document(self.robot_rrn)
                    .collection("consent_peers")
                    .document(doc_id)
                )
                ref.set(record, merge=True)
                log.info(
                    "Consent granted to %s, scopes=%s, expires=%s",
                    peer_owner,
                    granted_scopes,
                    expires_at,
                )
            except Exception as e:
                log.error("Failed to persist consent grant: %s", e)

        return consent_id

    def revoke_consent(self, peer_owner: str) -> None:
        """Revoke consent for a peer owner."""
        self._cache.pop(peer_owner, None)

        if self._db:
            try:
                doc_id = peer_owner.replace("rrn://", "").replace("/", "_")
                ref = (
                    self._db.collection("robots")
                    .document(self.robot_rrn)
                    .collection("consent_peers")
                    .document(doc_id)
                )
                ref.update(
                    {
                        "status": "revoked",
                        "revoked_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                log.info("Consent revoked for %s", peer_owner)
            except Exception as e:
                log.error("Failed to persist consent revocation: %s", e)

    def invalidate_cache(self) -> None:
        """Clear the in-process consent cache (forces re-read from Firestore)."""
        self._cache.clear()
