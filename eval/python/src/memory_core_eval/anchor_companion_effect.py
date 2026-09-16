"""Strict causal conclusion for ANCHOR companion-disposition probes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


EXPECTED_CASES = 4


def summarize_anchor_companion(
    generalization: Mapping[str, object],
) -> dict[str, object]:
    """Only judge learned effect for cases where both Oracle controls work."""

    false_positives = _count(
        generalization.get("negative_false_positives"),
        "negative_false_positives",
    )
    positive = generalization.get("positive")
    if not isinstance(positive, Mapping):
        raise ValueError("ANCHOR companion summary requires positive results")
    funnel = positive.get("funnel")
    if not isinstance(funnel, Mapping):
        raise ValueError("ANCHOR companion summary requires a funnel")
    normalized_funnel = {
        stage: _count(funnel.get(stage), stage)
        for stage in ("formed", "selected", "rendered")
    }
    rows = positive.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("ANCHOR companion summary requires result rows")
    if len(rows) != EXPECTED_CASES:
        raise ValueError("ANCHOR companion summary requires four cases")

    if false_positives:
        decision = _decision(
            "engineering_bottleneck", "false_positive_formation", False
        )
        return _result(normalized_funnel, (), decision)
    for stage in ("formed", "selected", "rendered"):
        if normalized_funnel[stage] != EXPECTED_CASES:
            decision = _decision("engineering_bottleneck", stage, False)
            return _result(normalized_funnel, (), decision)

    manipulable = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("ANCHOR companion row must be an object")
        consensus = raw.get("consensus")
        if not isinstance(consensus, Mapping):
            raise ValueError("ANCHOR companion row requires consensus")
        if (
            consensus.get("oracle_seed_vs_anti_seed") == "oracle_seed"
            and consensus.get("oracle_seed_vs_rag") == "oracle_seed"
        ):
            manipulable.append(consensus)

    learned_counts = {
        "learned_seed": 0,
        "rag": 0,
        "tie": 0,
        "inconsistent": 0,
    }
    for consensus in manipulable:
        winner = consensus.get("learned_seed_vs_rag")
        if winner not in learned_counts:
            raise ValueError("ANCHOR companion learned consensus is invalid")
        learned_counts[winner] += 1

    if len(manipulable) != EXPECTED_CASES:
        decision = _decision("evaluation_ceiling", "oracle_vs_rag", False)
    elif learned_counts["learned_seed"] >= 3 and learned_counts["rag"] == 0:
        decision = _decision(
            "preliminary_learned_companion_advantage", None, False
        )
    else:
        decision = _decision(
            "neutral_disposition_architecture_bottleneck",
            "learned_effect",
            True,
        )
    return _result(normalized_funnel, learned_counts, decision)


def _result(
    funnel: Mapping[str, int],
    learned_counts: Mapping[str, int] | tuple[()],
    decision: Mapping[str, object],
) -> dict[str, object]:
    counts = (
        dict(learned_counts)
        if learned_counts
        else {"learned_seed": 0, "rag": 0, "tie": 0, "inconsistent": 0}
    )
    return {
        "protocol": "anchor-companion-v1",
        "funnel": dict(funnel),
        "manipulable_cases": sum(counts.values()),
        "learned_on_manipulable": counts,
        "decision": dict(decision),
    }


def _decision(status: str, bottleneck: str | None, architecture: bool) -> dict[str, object]:
    return {
        "status": status,
        "bottleneck": bottleneck,
        "architecture_verdict": architecture,
    }


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value
