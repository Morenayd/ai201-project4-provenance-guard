from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from analytics import compute_analytics
from audit_log import get_log
from content_store import get_content
from service import (
    ServiceError,
    check_certificate,
    submit,
    submit_appeal,
    verify_creator,
)

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/provenance/content/submit", methods=["POST"])
@limiter.limit("10 per hour; 3 per minute")
def submit_content():
    data = request.get_json(silent=True) or {}
    try:
        result = submit(
            creator_id=data.get("creator_id"),
            content_type=data.get("content_type", "text"),
            text=data.get("text"),
            metadata=data.get("metadata"),
        )
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    return jsonify({
        "content_id": result["content_id"],
        "content_type": result["content_type"],
        "attr_result": result["attr_result"],
        "score": result["score"],
        "label": result["label"],
        "vote": result["vote"],
        "provenance_badge": result["provenance_badge"],
    }), 200


@app.route("/provenance/creator/verify", methods=["POST"])
@limiter.limit("10 per hour; 3 per minute")
def verify_creator_endpoint():
    """Earn a 'verified human' credential via the verification step."""
    data = request.get_json(silent=True) or {}
    try:
        result = verify_creator(
            creator_id=data.get("creator_id"),
            writing_sample=data.get("writing_sample"),
            attestation=bool(data.get("attestation")),
        )
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    return jsonify(result), 200


@app.route("/provenance/creator/certificate/verify", methods=["POST"])
def check_certificate_endpoint():
    """Validate a presented provenance certificate."""
    data = request.get_json(silent=True) or {}
    certificate = data.get("certificate")
    return jsonify(check_certificate(certificate)), 200


@app.route("/provenance/content/appeal", methods=["POST"])
def appeal_content():
    data = request.get_json(silent=True) or {}
    try:
        result = submit_appeal(
            content_id=data.get("content_id"),
            creator_id=data.get("creator_id"),
            creator_reasoning=data.get("creator_reasoning"),
        )
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    return jsonify(result), 200


@app.route("/analytics", methods=["GET"])
def analytics():
    return jsonify(compute_analytics())


@app.route("/log", methods=["GET"])
def get_audit_log():
    entries = []
    for entry in get_log():
        enriched = dict(entry)
        content = get_content(entry["content_id"])
        enriched["appealed"] = content is not None and content["status"] != "classified"
        entries.append(enriched)
    return jsonify({"entries": entries})


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return jsonify({"error": f"rate limit exceeded: {e.description}"}), 429


if __name__ == "__main__":
    app.run(debug=True)
