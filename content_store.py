"""In-memory content store for Provenance Guard.

Tracks submitted content and its classification + status, so the appeal
workflow can look up a submission by content_id, verify creator_id
linkage, and move status to under_review. Placeholder until the
SQLite-backed Content/Decision records described in planning.md's Data
Model are implemented (Milestone 1: Setup).
"""
from datetime import datetime, timezone

_content = {}


def save_content(content_id: str, creator_id: str, content_text: str, attr_result: str,
                 score: float, label: str, content_type: str = "text", details: dict = None) -> dict:
    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "content_text": content_text,
        "content_type": content_type,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "status": "classified",
        "attr_result": attr_result,
        "score": score,
        "label": label,
        # Extra decision detail (ensemble votes/agreement, verified badge, ...)
        # kept alongside the record so the analytics view can aggregate without
        # re-parsing the audit log.
        "details": details or {},
    }
    _content[content_id] = record
    return record


def get_all_content() -> list:
    return list(_content.values())


def get_content(content_id: str) -> dict:
    return _content.get(content_id)


def update_status(content_id: str, status: str) -> dict:
    record = _content.get(content_id)
    if record is not None:
        record["status"] = status
    return record
