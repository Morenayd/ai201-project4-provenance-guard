"""Analytics for Provenance Guard's dashboard.

Aggregates the in-memory stores into the numbers the dashboard renders. Four
metric groups:

1. Detection patterns - how submissions distribute across the five
   attribution bands, and across the two content modalities.
2. Appeal rate - what fraction of submissions were appealed. A creator-
   friendly system should watch this: a rising appeal rate is a signal the
   detector may be over-flagging.
3. Signal agreement rate (the chosen "additional metric") - the mean ensemble
   vote agreement across submissions. It measures how often the signals
   actually corroborated each other rather than a blended number papering
   over a split decision; low agreement means the verdicts rest on shaky,
   internally-contested evidence.
4. Confidence summary - mean score and the verified-human footprint.
"""
from appeals import get_appeals
from content_store import get_all_content
from creator_store import get_verified_creators

ATTR_ORDER = [
    "highly_likely_human",
    "likely_human",
    "uncertain",
    "likely_ai",
    "highly_likely_ai",
]


def compute_analytics() -> dict:
    content = get_all_content()
    appeals = get_appeals()
    total = len(content)

    attr_counts = {name: 0 for name in ATTR_ORDER}
    type_counts = {}
    agreements = []
    scores = []
    verified_submissions = 0

    for record in content:
        attr_counts[record["attr_result"]] = attr_counts.get(record["attr_result"], 0) + 1
        ctype = record.get("content_type", "text")
        type_counts[ctype] = type_counts.get(ctype, 0) + 1
        scores.append(record.get("score", 0.0))
        details = record.get("details", {})
        vote = details.get("vote", {})
        if "agreement" in vote:
            agreements.append(vote["agreement"])
        if details.get("verified_human"):
            verified_submissions += 1

    appealed_content_ids = {a["content_id"] for a in appeals}
    appeal_rate = round(len(appealed_content_ids) / total, 4) if total else 0.0
    mean_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    mean_agreement = round(sum(agreements) / len(agreements), 4) if agreements else 0.0
    verified_share = round(verified_submissions / total, 4) if total else 0.0

    return {
        "total_submissions": total,
        "detection_patterns": {
            "by_attribution": attr_counts,
            "by_content_type": type_counts,
        },
        "appeals": {
            "total_submissions": total,
            "appealed": len(appealed_content_ids),
            "appeal_rate": appeal_rate,
        },
        "signal_agreement": {
            "mean_agreement": mean_agreement,
            "samples": len(agreements),
        },
        "confidence": {
            "mean_score": mean_score,
        },
        "verified_creators": {
            "total_verified": len(get_verified_creators()),
            "verified_submissions": verified_submissions,
            "verified_submission_share": verified_share,
        },
    }
