"""In-memory appeal store for Provenance Guard.

Backs the review queue described in planning.md's Appeals Workflow:
content_id, original classification, reasoning, and status, in one place
a human reviewer could page through. Placeholder until the SQLite-backed
Appeal record described in planning.md's Data Model is implemented
(Milestone 1: Setup).
"""
import uuid
from datetime import datetime, timezone

_appeals = []


def save_appeal(content_id: str, creator_id: str, reasoning: str) -> dict:
    appeal = {
        "appeal_id": str(uuid.uuid4()),
        "content_id": content_id,
        "creator_id": creator_id,
        "reasoning": reasoning,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "status": "under_review",
    }
    _appeals.append(appeal)
    return appeal


def get_appeals() -> list:
    return list(_appeals)
