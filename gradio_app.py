"""Interactive Gradio interface for Provenance Guard.

Thin UI over service.py - it calls the exact same pipeline the Flask API
does, so the two front-ends can't disagree about a verdict. Five tabs mirror
the system's capabilities:

  - Analyze content   : run the ensemble on text or image metadata
  - Verify creator    : earn the "verified human" credential
  - Appeal            : contest a classification
  - Analytics         : detection patterns, appeal rate, signal agreement
  - Audit log         : the structured event trail

Run with:  ./venv/bin/python gradio_app.py
"""
import json

import gradio as gr
import pandas as pd

import service
from analytics import ATTR_ORDER, compute_analytics
from audit_log import get_log
from content_store import get_content

VOTE_EMOJI = {"human": "🧑 human", "uncertain": "❓ uncertain", "ai": "🤖 ai"}

EXAMPLE_METADATA = json.dumps({
    "software": "Adobe Photoshop 25.0 (Firefly)",
    "prompt": "a serene mountain lake at sunset, hyperrealistic",
    "steps": "40",
    "caption": "A calm alpine lake mirrors the orange sky at dusk."
}, indent=2)


def _badge_md(badge: dict) -> str:
    if badge.get("verified"):
        return f"### {badge['icon']} {badge['label']}\n{badge['detail']}"
    return f"**{badge['label']}** — {badge['detail']}"


def _verdict_md(result: dict) -> str:
    vote = result["vote"]
    lines = [
        f"## {result['label']}",
        f"**Attribution:** `{result['attr_result']}`  |  "
        f"**Ensemble score:** `{result['score']}` (0 = human, 1 = AI)",
        "",
        "### Ensemble signals (weighted)",
        "| Signal | Score | Vote |",
        "| --- | --- | --- |",
    ]
    for name, sc in result["signal_scores"].items():
        v = vote["votes"].get(name, "")
        lines.append(f"| `{name}` | {sc} | {VOTE_EMOJI.get(v, v)} |")
    lines += [
        "",
        f"**Vote tally:** {vote['counts']}  →  majority **{vote['majority']}**, "
        f"agreement **{vote['agreement']}**",
    ]
    return "\n".join(lines)


# --- Tab handlers ------------------------------------------------------------

def analyze_handler(content_type, creator_id, text, metadata_json):
    try:
        if content_type == "Image metadata":
            metadata = json.loads(metadata_json) if metadata_json.strip() else {}
            result = service.submit(creator_id, content_type="image_metadata", metadata=metadata)
        else:
            result = service.submit(creator_id, content_type="text", text=text)
    except json.JSONDecodeError as exc:
        return "❌ Metadata must be valid JSON: " + str(exc), "", {}
    except service.ServiceError as exc:
        return f"❌ {exc}", "", {}

    verdict = _verdict_md(result)
    badge = _badge_md(result["provenance_badge"])
    footer = f"\n\n---\n*content_id:* `{result['content_id']}` (use it to file an appeal)"
    return verdict + footer, badge, result["signals_used"]


def toggle_inputs(content_type):
    is_text = content_type == "Text"
    return gr.update(visible=is_text), gr.update(visible=not is_text)


def verify_handler(creator_id, writing_sample, attestation):
    try:
        result = service.verify_creator(creator_id, writing_sample, attestation)
    except service.ServiceError as exc:
        return f"❌ Verification failed: {exc}", {}
    badge = _badge_md(result["provenance_badge"])
    msg = (
        f"{badge}\n\n"
        f"Sample read as `{result['sample_analysis']['attr_result']}` "
        f"(score {result['sample_analysis']['score']}). "
        "Keep the certificate below; it is signed and tamper-evident."
    )
    return msg, result["certificate"]


def appeal_handler(content_id, creator_id, reasoning):
    try:
        result = service.submit_appeal(content_id, creator_id, reasoning)
    except service.ServiceError as exc:
        return f"❌ {exc}"
    return f"✅ {result['message']} Status is now **{result['status']}**."


def analytics_handler():
    a = compute_analytics()
    attr = a["detection_patterns"]["by_attribution"]
    df = pd.DataFrame({"attribution": ATTR_ORDER, "count": [attr[k] for k in ATTR_ORDER]})

    summary = "\n".join([
        f"**Total submissions:** {a['total_submissions']}",
        "",
        "**By content type:** " + (", ".join(
            f"{k}: {v}" for k, v in a["detection_patterns"]["by_content_type"].items()) or "—"),
        "",
        f"**Appeal rate:** {a['appeals']['appeal_rate']:.0%} "
        f"({a['appeals']['appealed']} of {a['appeals']['total_submissions']})",
        "",
        f"**Signal agreement (mean):** {a['signal_agreement']['mean_agreement']:.0%} "
        f"across {a['signal_agreement']['samples']} submissions — how often the ensemble's "
        "signals actually corroborated each other.",
        "",
        f"**Mean confidence score:** {a['confidence']['mean_score']}",
        "",
        f"**Verified-human creators:** {a['verified_creators']['total_verified']}  |  "
        f"verified submissions: {a['verified_creators']['verified_submissions']} "
        f"({a['verified_creators']['verified_submission_share']:.0%})",
    ])
    return summary, df


def audit_handler():
    rows = []
    for entry in get_log():
        content = get_content(entry["content_id"])
        appealed = content is not None and content["status"] != "classified"
        payload = entry.get("payload", {})
        rows.append({
            "created_at": entry["created_at"],
            "event_type": entry["event_type"],
            "content_id": entry["content_id"][:8],
            "attr_result": payload.get("attr_result", payload.get("credential", "")),
            "score": payload.get("score", ""),
            "appealed": appealed,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["created_at", "event_type", "content_id", "attr_result", "score", "appealed"])


# --- Layout ------------------------------------------------------------------

def build_ui():
    with gr.Blocks(title="Provenance Guard") as demo:
        gr.Markdown(
            "# 🛡️ Provenance Guard\n"
            "Transparency labels for creative-sharing platforms: is this content "
            "human-written or AI-generated? Every verdict is an **ensemble** of "
            "independent signals, combined with documented weights and shown with a "
            "transparency vote."
        )

        with gr.Tab("Analyze content"):
            content_type = gr.Radio(["Text", "Image metadata"], value="Text",
                                    label="Content type")
            creator_id = gr.Textbox(label="Creator ID", value="creator-demo")
            text_in = gr.Textbox(label="Text", lines=8,
                                 placeholder="Paste the text to analyze…", visible=True)
            metadata_in = gr.Code(label="Image metadata (JSON)", language="json",
                                  value=EXAMPLE_METADATA, visible=False)
            analyze_btn = gr.Button("Analyze", variant="primary")
            verdict_out = gr.Markdown()
            badge_out = gr.Markdown()
            with gr.Accordion("Raw signal outputs", open=False):
                signals_out = gr.JSON()

            content_type.change(toggle_inputs, content_type, [text_in, metadata_in])
            analyze_btn.click(analyze_handler,
                              [content_type, creator_id, text_in, metadata_in],
                              [verdict_out, badge_out, signals_out])

        with gr.Tab("Verify creator"):
            gr.Markdown(
                "Earn a **verified human** credential. Submit a fresh sample of your own "
                "unassisted writing (≥ 40 words) and affirm the attestation. The sample must "
                "not read as AI-generated. On success you receive a signed, tamper-evident "
                "certificate that stamps a badge onto your future submissions."
            )
            v_creator = gr.Textbox(label="Creator ID", value="creator-demo")
            v_sample = gr.Textbox(label="Writing sample", lines=8)
            v_attest = gr.Checkbox(
                label="I affirm I am a human and this is my own original, unassisted writing.")
            v_btn = gr.Button("Request verification", variant="primary")
            v_msg = gr.Markdown()
            with gr.Accordion("Provenance certificate", open=False):
                v_cert = gr.JSON()
            v_btn.click(verify_handler, [v_creator, v_sample, v_attest], [v_msg, v_cert])

        with gr.Tab("Appeal"):
            gr.Markdown("Contest a classification. You must be the original submitter.")
            a_content = gr.Textbox(label="Content ID")
            a_creator = gr.Textbox(label="Creator ID", value="creator-demo")
            a_reason = gr.Textbox(label="Why is the classification wrong?", lines=4)
            a_btn = gr.Button("Submit appeal", variant="primary")
            a_msg = gr.Markdown()
            a_btn.click(appeal_handler, [a_content, a_creator, a_reason], a_msg)

        with gr.Tab("Analytics"):
            gr.Markdown("Detection patterns, appeal rate, and signal-agreement rate.")
            an_btn = gr.Button("Refresh", variant="primary")
            an_summary = gr.Markdown()
            an_plot = gr.BarPlot(x="attribution", y="count",
                                 title="Detection patterns by attribution")
            an_btn.click(analytics_handler, None, [an_summary, an_plot])
            demo.load(analytics_handler, None, [an_summary, an_plot])

        with gr.Tab("Audit log"):
            log_btn = gr.Button("Refresh", variant="primary")
            log_table = gr.Dataframe(label="Recent events (newest first)")
            log_btn.click(audit_handler, None, log_table)
            demo.load(audit_handler, None, log_table)

    return demo


if __name__ == "__main__":
    build_ui().launch()
