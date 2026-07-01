"""Shared business logic for Provenance Guard.

This is the single pipeline both the Flask API (app.py) and the Gradio UI
(gradio_app.py) call, so the two interfaces can never drift apart. HTTP and
UI concerns stay in those files; everything about *what the system does*
lives here: run the signal ensemble, combine it, persist, audit, and handle
creator verification and appeals.

Functions raise the small typed errors below on bad input or upstream
failure; each interface maps them to its own status code / message.
"""
import uuid

from appeals import save_appeal
from audit_log import log_event
from certificates import badge_for, issue_certificate, verify_certificate
from content_store import get_content, save_content, update_status
from creator_store import get_certificate, is_verified, save_verified_creator
from scoring import combine_weighted, score_to_attribution, tally_votes
from signals import (
    llm_classification_score,
    metadata_provenance_score,
    stylometric_score,
    structural_repetition_score,
)

VALID_CONTENT_TYPES = ("text", "image_metadata")

# A verification writing sample needs enough text for the ensemble to judge
# it at all (short text is dampened toward neutral - see signals.py).
VERIFY_MIN_WORDS = 40

# The verification sample must NOT read as AI-generated. We reject only clear
# AI-leaning samples (score above the "uncertain" band); a human or genuinely
# uncertain reading is accepted, because clean human writing can legitimately
# land in "uncertain" and we don't want to gatekeep honest creators out.
VERIFY_MAX_SCORE = 0.55


class ServiceError(Exception):
    """Base class carrying an HTTP-ish status code for the API layer."""
    status_code = 400


class InvalidInput(ServiceError):
    status_code = 400


class SignalFailure(ServiceError):
    status_code = 502


class NotFound(ServiceError):
    status_code = 404


class Forbidden(ServiceError):
    status_code = 403


# --- Detection ensemble ------------------------------------------------------

def analyze(content_type: str = "text", text: str = None, metadata: dict = None) -> dict:
    """Run the signal ensemble for one submission and combine it.

    Does NOT persist anything - pure analysis, so callers can preview a result
    (e.g. during creator verification) without recording it. Returns a dict
    with every signal's full output, the per-signal scores, the weighted
    ensemble score, the attribution/label, and the transparency vote tally.
    """
    if content_type not in VALID_CONTENT_TYPES:
        raise InvalidInput(f"content_type must be one of {VALID_CONTENT_TYPES}")

    signals_used = {}
    signal_scores = {}

    if content_type == "text":
        if not isinstance(text, str) or not text.strip():
            raise InvalidInput("text is required and must be a non-empty string")
        # The LLM signal is the primary evidence for text; a failure here is
        # surfaced (not silently swallowed), matching the original 502 path.
        try:
            llm = llm_classification_score(text)
        except Exception as exc:  # noqa: BLE001 - upstream call, message forwarded
            raise SignalFailure(f"signal 1 (llm classification) failed: {exc}")
        signals_used["llm_classification"] = llm
        signal_scores["llm_classification"] = llm["score"]

        styl = stylometric_score(text)
        signals_used["stylometric"] = styl
        signal_scores["stylometric"] = styl["score"]

        struct = structural_repetition_score(text)
        signals_used["structural_repetition"] = struct
        signal_scores["structural_repetition"] = struct["score"]

    else:  # image_metadata
        if not isinstance(metadata, dict) or not metadata:
            raise InvalidInput("image_metadata submissions require a non-empty metadata object")
        meta = metadata_provenance_score(metadata)
        signals_used["metadata_provenance"] = meta
        signal_scores["metadata_provenance"] = meta["score"]

        # The caption, if any, is free text - fold the text signals in over it
        # so the two modalities share machinery. The LLM call is best-effort
        # here (metadata alone is already a meaningful verdict), so a Groq
        # outage degrades to metadata-only rather than failing the request.
        caption = str(metadata.get("caption", "")).strip()
        if caption:
            try:
                llm = llm_classification_score(caption)
                signals_used["llm_classification"] = llm
                signal_scores["llm_classification"] = llm["score"]
            except Exception as exc:  # noqa: BLE001
                signals_used["llm_classification"] = {"skipped": f"caption LLM check failed: {exc}"}
            styl = stylometric_score(caption)
            signals_used["stylometric"] = styl
            signal_scores["stylometric"] = styl["score"]

    score = combine_weighted(signal_scores)
    attr_result, label = score_to_attribution(score)
    vote = tally_votes(signal_scores)

    return {
        "content_type": content_type,
        "signals_used": signals_used,
        "signal_scores": signal_scores,
        "score": score,
        "attr_result": attr_result,
        "label": label,
        "vote": vote,
    }


def submit(creator_id: str, content_type: str = "text", text: str = None,
           metadata: dict = None) -> dict:
    """Analyze, persist, audit, and stamp the verified-human badge.

    Returns the client-facing result (content_id, attr_result, score, label,
    vote, and the creator's provenance badge).
    """
    if not isinstance(creator_id, str) or not creator_id.strip():
        raise InvalidInput("creator_id is required and must be a non-empty string")

    result = analyze(content_type=content_type, text=text, metadata=metadata)
    content_id = str(uuid.uuid4())

    # Stamp the provenance badge: verified-human creators carry it onto their
    # content so a reader sees the credential next to the detection verdict.
    certificate = get_certificate(creator_id) if is_verified(creator_id) else None
    badge = badge_for(certificate)

    stored_text = text if content_type == "text" else _metadata_summary(metadata)
    details = {
        "signal_scores": result["signal_scores"],
        "vote": result["vote"],
        "verified_human": badge["verified"],
    }
    save_content(content_id, creator_id, stored_text, result["attr_result"],
                 result["score"], result["label"],
                 content_type=content_type, details=details)

    log_event(content_id, "content_submitted", {
        "creator_id": creator_id,
        "content_type": content_type,
        "signals_used": result["signals_used"],
        "signal_scores": result["signal_scores"],
        "vote": result["vote"],
        "score": result["score"],
        "attr_result": result["attr_result"],
        "label": result["label"],
        "verified_human": badge["verified"],
    })

    return {
        "content_id": content_id,
        "content_type": content_type,
        "attr_result": result["attr_result"],
        "score": result["score"],
        "label": result["label"],
        "vote": result["vote"],
        "signal_scores": result["signal_scores"],
        "signals_used": result["signals_used"],
        "provenance_badge": badge,
    }


def _metadata_summary(metadata: dict) -> str:
    caption = str((metadata or {}).get("caption", "")).strip()
    return caption or "[image metadata submission]"


# --- Verified-human credential ----------------------------------------------

def verify_creator(creator_id: str, writing_sample: str, attestation: bool) -> dict:
    """Run the human-verification step and, on success, issue a credential.

    The step has two requirements, both of which must hold:
      1. an explicit attestation (the creator affirms they are a human and the
         sample is their own original, unassisted writing), and
      2. a fresh writing sample that the detection ensemble does NOT read as
         AI-generated (score <= VERIFY_MAX_SCORE) and that is long enough to
         judge (>= VERIFY_MIN_WORDS words).

    On success a signed 'verified_human' certificate is minted, the creator is
    recorded as verified, and an audit event is written. Raises InvalidInput /
    Forbidden with an explanatory message on failure.
    """
    if not isinstance(creator_id, str) or not creator_id.strip():
        raise InvalidInput("creator_id is required and must be a non-empty string")
    if not attestation:
        raise Forbidden("verification requires the human attestation to be affirmed")
    if not isinstance(writing_sample, str) or len(writing_sample.split()) < VERIFY_MIN_WORDS:
        raise InvalidInput(
            f"writing sample must be at least {VERIFY_MIN_WORDS} words so it can be assessed")

    analysis = analyze(content_type="text", text=writing_sample)
    if analysis["score"] > VERIFY_MAX_SCORE:
        raise Forbidden(
            "verification sample reads as AI-generated "
            f"(score {analysis['score']} > {VERIFY_MAX_SCORE}); "
            "submit a fresh sample of your own unassisted writing")

    certificate = issue_certificate(creator_id)
    evidence = {
        "sample_score": analysis["score"],
        "sample_attr_result": analysis["attr_result"],
        "attestation": True,
    }
    save_verified_creator(creator_id, certificate, evidence)
    badge = badge_for(certificate)

    log_event(creator_id, "creator_verified", {
        "creator_id": creator_id,
        "credential": certificate["credential"],
        "cert_id": certificate["cert_id"],
        "method": certificate["method"],
        "sample_score": analysis["score"],
    })

    return {
        "verified": True,
        "certificate": certificate,
        "provenance_badge": badge,
        "sample_analysis": {"score": analysis["score"], "attr_result": analysis["attr_result"]},
    }


def check_certificate(certificate: dict) -> dict:
    """Validate a presented certificate and return its display badge."""
    return {"valid": verify_certificate(certificate), "badge": badge_for(certificate)}


# --- Appeals -----------------------------------------------------------------

def submit_appeal(content_id: str, creator_id: str, creator_reasoning: str) -> dict:
    if not isinstance(content_id, str) or not content_id.strip():
        raise InvalidInput("content_id is required and must be a non-empty string")
    if not isinstance(creator_id, str) or not creator_id.strip():
        raise InvalidInput("creator_id is required and must be a non-empty string")
    if not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        raise InvalidInput("creator_reasoning is required and must be a non-empty string")

    content = get_content(content_id)
    if content is None:
        raise NotFound(f"no content found with content_id {content_id!r}")
    if content["creator_id"] != creator_id:
        raise Forbidden("creator_id does not match the original submission")

    appeal = save_appeal(content_id, creator_id, creator_reasoning)
    update_status(content_id, "under_review")

    log_event(content_id, "appeal_submitted", {
        "appeal_id": appeal["appeal_id"],
        "creator_id": creator_id,
        "appeal_reasoning": creator_reasoning,
        "original_attr_result": content["attr_result"],
        "original_score": content["score"],
        "original_label": content["label"],
        "status": "under_review",
    })

    return {"status": "under_review", "message": "Appeal recorded successfully."}
