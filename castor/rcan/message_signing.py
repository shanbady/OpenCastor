"""
RCAN message signing integration for OpenCastor (issue #441).

Wires rcan-py's Ed25519 signing module into the OpenCastor action pipeline.
Signs outbound RCAN messages when a signing key is configured.

Config (robot.rcan.yaml):
    agent:
      signing:
        enabled: true
        key_path: ~/.opencastor/signing_key.pem   # auto-generated if missing
        key_id: ""                                 # optional; derived from pub key if empty

Environment:
    OPENCASTOR_SIGNING_KEY_PATH — override key path
    OPENCASTOR_SIGNING_ENABLED  — "true" / "false" to override config

Usage:
    from castor.rcan.message_signing import get_signer, sign_action_payload

    signer = get_signer(config)
    if signer:
        signed = signer.sign_action(action_dict)
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MessageSigner:
    """
    Signs outbound RCAN action payloads with an Ed25519 key.

    Thread-safe singleton pattern; one instance per process.
    """

    def __init__(self, key_path: Path, key_id: str = "") -> None:
        self._key_path = key_path
        self._key_pair: Any = None
        self._key_id = key_id
        self._lock = threading.Lock()
        self._available = False
        self._load_or_generate()

    def _load_or_generate(self) -> None:
        """Load existing key or generate a new Ed25519 key pair."""
        try:
            from rcan.signing import KeyPair

            if self._key_path.exists():
                self._key_pair = KeyPair.load(str(self._key_path))
                logger.info("RCAN signing key loaded: %s", self._key_path)
            else:
                self._key_path.parent.mkdir(parents=True, exist_ok=True)
                self._key_pair = KeyPair.generate()
                self._key_pair.save(str(self._key_path))
                logger.info("RCAN signing key generated: %s", self._key_path)

            if not self._key_id:
                # Derive key_id from first 8 hex chars of SHA-256 of public PEM
                import hashlib

                pub_bytes = self._key_pair.public_pem.encode()
                self._key_id = hashlib.sha256(pub_bytes).hexdigest()[:8]

            self._available = True
        except ImportError:
            logger.debug("rcan[crypto] not installed — message signing disabled")
        except Exception as exc:
            logger.warning("RCAN message signing setup failed (non-fatal): %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_key_pem(self) -> str:
        """Return the public key in PEM format for sharing."""
        if self._key_pair and hasattr(self._key_pair, "public_pem"):
            return self._key_pair.public_pem
        return ""

    def sign_message(self, message: dict) -> dict:
        """
        Add an Ed25519 signature to a RCAN message dict.

        Returns the message dict with a 'signature' block added.
        Does not modify the input; returns a new dict.
        """
        if not self._available or self._key_pair is None:
            return message

        try:
            import json

            msg_copy = dict(message)
            # Sign the canonical message bytes (sorted JSON, no 'signature' field)
            payload = {k: v for k, v in msg_copy.items() if k != "signature"}
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

            with self._lock:
                sig_hex = self._key_pair.sign(payload_bytes).hex()

            msg_copy["signature"] = {
                "alg": "Ed25519",
                "kid": self._key_id,
                "sig": sig_hex,
            }
            return msg_copy
        except Exception as exc:
            logger.debug("RCAN message signing failed (non-fatal): %s", exc)
            return message

    def sign_action(self, action: dict) -> dict:
        """
        Inject a signature block into an action dict for the commitment chain.

        The action dict is signed as-is (type, params, confidence, etc.)
        Returns a new dict with 'rcan_sig' key added.
        """
        if not self._available or self._key_pair is None:
            return action

        try:
            import json

            payload_bytes = json.dumps(action, sort_keys=True, separators=(",", ":")).encode()
            with self._lock:
                sig_hex = self._key_pair.sign(payload_bytes).hex()

            signed = dict(action)
            signed["rcan_sig"] = {
                "alg": "Ed25519",
                "kid": self._key_id,
                "sig": sig_hex,
            }
            return signed
        except Exception as exc:
            logger.debug("RCAN action signing failed (non-fatal): %s", exc)
            return action

    def verify_action(self, action: dict) -> bool:
        """Verify a signed action dict. Returns True if valid or unsigned."""
        sig_block = action.get("rcan_sig")
        if not sig_block:
            return True  # unsigned is not invalid by default

        if not self._available or self._key_pair is None:
            return True  # can't verify without key — assume ok

        try:
            import json

            payload = {k: v for k, v in action.items() if k != "rcan_sig"}
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            sig_bytes = bytes.fromhex(sig_block["sig"])

            with self._lock:
                return self._key_pair.verify(payload_bytes, sig_bytes)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_signer: MessageSigner | None = None
_signer_lock = threading.Lock()


def get_signer(config: dict | None = None) -> MessageSigner | None:
    """
    Return the module-level MessageSigner singleton.

    Instantiated on first call using config. Returns None if signing
    is disabled or rcan[crypto] is not installed.
    """
    global _signer

    with _signer_lock:
        if _signer is not None:
            return _signer if _signer.available else None

        cfg = config or {}
        agent_cfg = cfg.get("agent", {})
        signing_cfg = agent_cfg.get("signing", {})

        # Check enabled flag
        env_enabled = os.environ.get("OPENCASTOR_SIGNING_ENABLED", "")
        if env_enabled.lower() == "false":
            return None
        if env_enabled.lower() != "true" and not signing_cfg.get("enabled", False):
            return None

        # Key path
        env_key_path = os.environ.get("OPENCASTOR_SIGNING_KEY_PATH", "")
        key_path_str = (
            env_key_path
            or signing_cfg.get("key_path", "")
            or str(Path.home() / ".opencastor" / "signing_key.pem")
        )
        key_path = Path(key_path_str).expanduser()
        key_id = signing_cfg.get("key_id", "")

        signer = MessageSigner(key_path=key_path, key_id=key_id)
        if signer.available:
            _signer = signer
            return _signer
        return None


def sign_action_payload(action: dict, config: dict | None = None) -> dict:
    """
    Convenience wrapper: sign an action dict if signing is configured.

    Returns the original dict unchanged if signing is disabled.
    """
    signer = get_signer(config)
    if signer is None:
        return action
    return signer.sign_action(action)
