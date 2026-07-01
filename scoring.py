"""Confidence scoring logic for Provenance Guard.

Combines the signal scores into one confidence score and maps that score
to an attribution result + transparency label, per planning.md's
Uncertainty Representation thresholds and Transparency Label Design.

The combination is an ENSEMBLE of three or more signals. Two things happen:

1. Weighted average (`combine_weighted`) - the actual decision. Each signal
   carries a documented weight reflecting how much we trust it (see
   SIGNAL_WEIGHTS). Weights are renormalized over whichever signals actually
   ran, so a signal that couldn't be computed for a given input (e.g. no
   caption on an image) doesn't silently count as 0.5.

2. Majority vote (`tally_votes`) - a transparency overlay, NOT the decision.
   Each signal casts a human/uncertain/ai vote based on its own score band.
   Reporting the tally next to the weighted score shows the user *how much
   the signals agreed*, which a single blended number hides. A 0.6 from two
   signals that both say "ai" is a different situation from a 0.6 where one
   screams "ai" and another says "human", and the vote surfaces that.
"""

# (upper_bound, attr_result, label), in ascending order. The "likely" bands
# are a distinct claim from "highly likely" (predominantly one source but
# possibly lightly edited by the other), not just a weaker version of it.
THRESHOLDS = [
    (0.34, "highly_likely_human", "Highly likely human: This content appears to be mostly human-written."),
    (0.44, "likely_human", "Likely human: This content appears to be human-written, possibly with light AI editing."),
    (0.55, "uncertain", "Uncertain: This content may be human-written or AI-generated."),
    (0.70, "likely_ai", "Likely AI: This content appears to be AI-generated, possibly with light human editing."),
    (1.00, "highly_likely_ai", "Highly likely AI: This content appears to be mostly AI-generated."),
]

# Documented ensemble weights per signal. Rationale:
#   - llm_classification (0.5): the strongest signal by a wide margin in
#     paired testing (see README Detection signals); holistic judgment that
#     the pure-Python signals can't replicate. Given half the total weight.
#   - stylometric (0.3): free, deterministic, but a narrower dynamic range and
#     weak on short or clean text, so it can inform but shouldn't dominate.
#   - structural_repetition (0.2): newest and noisiest; catches templated
#     phrase/opener repetition the other two miss, but on its own is the least
#     reliable, so it gets the smallest vote.
#   - metadata_provenance (0.6): for the image modality, metadata tags are
#     the most direct provenance evidence available, so the metadata signal
#     leads and the caption-derived text signals fill in the rest.
SIGNAL_WEIGHTS = {
    "llm_classification": 0.5,
    "stylometric": 0.3,
    "structural_repetition": 0.2,
    "metadata_provenance": 0.6,
}

# Score bands each signal uses to cast its transparency vote. Deliberately
# the same human/uncertain/ai cut points as the middle of THRESHOLDS.
VOTE_HUMAN_MAX = 0.44
VOTE_AI_MIN = 0.56


def combine_scores(scores: list) -> float:
    """Average a list of 0-1 signal scores into a single confidence score.

    Kept for backward compatibility (plain, unweighted mean). New callers
    should prefer `combine_weighted`, which applies the documented ensemble
    weights.
    """
    if not scores:
        raise ValueError("combine_scores requires at least one signal score")
    return round(sum(scores) / len(scores), 4)


def combine_weighted(signal_scores: dict, weights: dict = None) -> float:
    """Weighted ensemble average of named signal scores.

    `signal_scores` maps signal name -> score in [0, 1]. Weights are looked
    up in `weights` (default SIGNAL_WEIGHTS) and renormalized over exactly the
    signals present, so an absent signal is excluded rather than counted as a
    neutral 0.5. A signal with no configured weight defaults to 1.0.
    """
    if not signal_scores:
        raise ValueError("combine_weighted requires at least one signal score")
    weights = weights or SIGNAL_WEIGHTS
    total_weight = sum(weights.get(name, 1.0) for name in signal_scores)
    if total_weight == 0:
        return round(sum(signal_scores.values()) / len(signal_scores), 4)
    weighted_sum = sum(score * weights.get(name, 1.0) for name, score in signal_scores.items())
    return round(weighted_sum / total_weight, 4)


def vote_from_score(score: float) -> str:
    """Map a single signal's score to its human/uncertain/ai vote."""
    if score <= VOTE_HUMAN_MAX:
        return "human"
    if score >= VOTE_AI_MIN:
        return "ai"
    return "uncertain"


def tally_votes(signal_scores: dict) -> dict:
    """Count per-signal votes into {counts, majority, agreement}.

    - counts: {"human": n, "uncertain": n, "ai": n}
    - majority: the vote with the most signals (ties resolve to "uncertain")
    - agreement: fraction of signals that cast the majority vote, in [0, 1] -
      a direct measure of how much the ensemble agreed.
    """
    counts = {"human": 0, "uncertain": 0, "ai": 0}
    votes = {name: vote_from_score(score) for name, score in signal_scores.items()}
    for vote in votes.values():
        counts[vote] += 1

    total = len(votes) or 1
    top = max(counts.values())
    leaders = [label for label, count in counts.items() if count == top]
    majority = leaders[0] if len(leaders) == 1 else "uncertain"

    return {
        "votes": votes,
        "counts": counts,
        "majority": majority,
        "agreement": round(top / total, 4),
    }


def score_to_attribution(score: float) -> tuple:
    """Map a combined confidence score to (attr_result, label) per the thresholds."""
    for upper_bound, attr_result, label in THRESHOLDS:
        if score <= upper_bound:
            return attr_result, label
    return THRESHOLDS[-1][1], THRESHOLDS[-1][2]
