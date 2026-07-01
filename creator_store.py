"""In-memory store of verified-human creators for Provenance Guard.

Holds, per creator_id, the provenance certificate they earned (see
certificates.py) and the verification evidence. Lets the submission path stamp
a "Verified Human Creator" badge onto content from a verified creator without
re-running verification. Placeholder until a SQLite-backed creator/credential
table lands (planning.md's Data Model, Milestone 1).
"""
from datetime import datetime, timezone

_creators = {}


def save_verified_creator(creator_id: str, certificate: dict, evidence: dict) -> dict:
    record = {
        "creator_id": creator_id,
        "certificate": certificate,
        "evidence": evidence,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _creators[creator_id] = record
    return record


def get_verified_creator(creator_id: str) -> dict:
    return _creators.get(creator_id)


def is_verified(creator_id: str) -> bool:
    return creator_id in _creators


def get_certificate(creator_id: str) -> dict:
    record = _creators.get(creator_id)
    return record["certificate"] if record else None


def get_verified_creators() -> list:
    return list(_creators.values())
