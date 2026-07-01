"""Audit log for Provenance Guard.

Keeps an in-memory list (for an eventual /log endpoint, per planning.md
Milestone 4) and appends each event as a JSON line to audit.log so the
trail survives process restarts. Placeholder until the SQLite-backed
audit log described in planning.md's Data Model is implemented
(Milestone 1: Setup).
"""
import json
import logging
import uuid
from datetime import datetime, timezone

_events = []

_file_logger = logging.getLogger("provenance_guard.audit")
_file_logger.setLevel(logging.INFO)
_file_logger.propagate = False
if not _file_logger.handlers:
    _handler = logging.FileHandler("audit.log")
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _file_logger.addHandler(_handler)


def log_event(content_id: str, event_type: str, payload: dict) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "content_id": content_id,
        "event_type": event_type,
        "payload": payload,
    }
    _events.append(event)
    _file_logger.info(json.dumps(event))
    return event


def get_events() -> list:
    return list(_events)


def get_log(limit: int = 50) -> list:
    """Return the most recent audit entries, newest first."""
    return list(reversed(_events[-limit:]))
