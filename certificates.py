"""Provenance certificates: the "verified human" credential.

A creator can earn a tamper-evident credential attesting that a human
completed a verification step (see service.verify_creator). The credential is
a small JSON payload signed with HMAC-SHA256 over a server-side secret, so
anyone holding it can be checked (`verify_certificate`) but nobody can forge
one or alter a field without invalidating the signature.

This is deliberately self-contained and stateless per credential: the
signature covers the payload, so a certificate can be handed to a client and
presented back later without the server having to trust the client's copy.
The set of who is currently verified is tracked separately in
creator_store.py; this module only mints and checks the credential itself.

The signing secret comes from PROVENANCE_SECRET (falls back to a clearly
labelled development secret so the prototype runs out of the box - a real
deployment MUST set it, since the dev secret is public in this file).
"""
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

_DEV_SECRET = "dev-only-insecure-provenance-secret-change-me"

# Bumped if the payload shape ever changes, so old certificates can be
# recognised as a different version rather than silently mis-verified.
CERT_VERSION = 1


def _secret() -> bytes:
    return os.environ.get("PROVENANCE_SECRET", _DEV_SECRET).encode("utf-8")


def _canonical(payload: dict) -> bytes:
    """Deterministic byte encoding of the signed fields (sorted keys)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(payload: dict) -> str:
    return hmac.new(_secret(), _canonical(payload), hashlib.sha256).hexdigest()


def issue_certificate(creator_id: str, method: str = "writing_sample_attestation") -> dict:
    """Mint a signed 'verified human' credential for a creator.

    `method` records how the human check was satisfied so the credential
    carries its own provenance. Returns a dict with the signed payload plus
    the detached `signature`.
    """
    payload = {
        "version": CERT_VERSION,
        "cert_id": str(uuid.uuid4()),
        "creator_id": creator_id,
        "credential": "verified_human",
        "method": method,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    return {**payload, "signature": _sign(payload)}


def verify_certificate(certificate: dict) -> bool:
    """Return True iff the certificate's signature matches its payload."""
    if not isinstance(certificate, dict) or "signature" not in certificate:
        return False
    payload = {k: v for k, v in certificate.items() if k != "signature"}
    expected = _sign(payload)
    # Constant-time compare to avoid leaking the signature via timing.
    return hmac.compare_digest(expected, str(certificate["signature"]))


def badge_for(certificate: dict) -> dict:
    """Render the display badge shown on a verified creator's content.

    Kept next to issuance so the badge text and the credential stay in sync.
    Returns None-safe display fields the UI/API can render directly.
    """
    if not certificate or not verify_certificate(certificate):
        return {
            "verified": False,
            "label": "Unverified creator",
            "icon": "",
            "detail": "This creator has not completed human verification.",
        }
    return {
        "verified": True,
        "label": "Verified Human Creator",
        "icon": "✓",  # check mark
        "detail": (
            "The creator completed human verification on "
            f"{certificate.get('issued_at', 'an earlier date')} "
            f"(credential {certificate.get('cert_id', '?')})."
        ),
    }
