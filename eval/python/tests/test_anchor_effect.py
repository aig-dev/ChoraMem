from __future__ import annotations

import hashlib

import pytest

from memory_core_eval.anchor_data import BehaviorLabel, TrajectoryLabel


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _answer_arm(
    mode: str,
    *,
    output: str,
    selected: tuple[str, ...] = ("seed_test@1",),
    rendered: tuple[str, ...] = (),
    score: int | None = None,
) -> dict[str, object]:
    arm: dict[str, object] = {
        "mode": mode,
        "output": output,
        "error": "",
        "selected_recollection_refs": ["recollection_test@1"],
        "selected_disposition_refs": list(selected),
        "rendered_recollection_refs": (
            [] if mode == "none" else ["recollection_test@1"]
        ),
        "rendered_disposition_refs": list(rendered),
        "memory_tokens": 0 if mode == "none" else 12,
        "answer_request_sha256": _hash("answer:" + mode + ":" + output),
    }
    if score is not None:
        arm.update(
            {
                "judge_output": str(score),
                "score": score,
                "judge_request_sha256": _hash("judge:" + mode + ":" + output),
            }
        )
    return arm


@pytest.fixture
def valid_summary_inputs() -> dict[str, object]:
    trajectory_labels = {
        f"trajectory-{index}": TrajectoryLabel(
            instance_ref=f"trajectory-{index}",
            family="P-PROT",
            correct_index=0,
        )
        for index in range(15)
    }
    behavior_dimensions = (
        "persona_continuity",
        "persona_continuity",
        "relationship_adaptation",
        "relationship_adaptation",
        "relationship_repair",
        "relationship_repair",
    )
    behavior_labels = {
        f"behavior-{index}": BehaviorLabel(
            instance_ref=f"behavior-{index}",
            dimension=dimension,
            evidence_text="frozen evidence",
            rubric={
                "score_2": "held",
                "score_1": "partial",
                "score_0": "failed",
            },
        )
        for index, dimension in enumerate(behavior_dimensions)
    }
    trajectory_results = []
    for index, instance_ref in enumerate(trajectory_labels):
        seed_output = "A" if index == 0 else "B"
        trajectory_results.append(
            {
                "protocol": "anchor-v0-causal-dev-v1",
                "kind": "trajectory",
                "instance_ref": instance_ref,
                "bank_id": "test-bank",
                "request_sha256": _hash(instance_ref),
                "answers": [
                    _answer_arm("none", output="B"),
                    _answer_arm("recollection_only", output="B"),
                    _answer_arm(
                        "seed_enabled",
                        output=seed_output,
                        rendered=("seed_test@1",),
                    ),
                ],
                "diagnostic": {"active_dispositions": 1},
            }
        )
    behavior_results = []
    for instance_ref in behavior_labels:
        behavior_results.append(
            {
                "protocol": "anchor-v0-causal-dev-v1",
                "kind": "behavior",
                "instance_ref": instance_ref,
                "bank_id": "test-bank",
                "request_sha256": _hash(instance_ref),
                "answers": [
                    _answer_arm("none", output="none response", score=0),
                    _answer_arm(
                        "recollection_only",
                        output="recollection response",
                        score=1,
                    ),
                    _answer_arm(
                        "seed_enabled",
                        output="seed response",
                        rendered=("seed_test@1",),
                        score=2,
                    ),
                ],
                "diagnostic": {"active_dispositions": 1},
            }
        )
    manifest = {
        "protocol": "anchor-v0-causal-dev-v1",
        "counts": {"trajectory": 15, "behavior": 6},
        "instances": {
            "trajectory": {
                ref: {"request_sha256": _hash(ref)} for ref in trajectory_labels
            },
            "behavior": {
                ref: {"request_sha256": _hash(ref)} for ref in behavior_labels
            },
        },
        "seed_snapshot": {
            "internal": _hash("internal"),
            "worker": _hash("worker"),
            "api": _hash("api"),
            "gen": _hash("gen"),
        },
    }
    return {
        "trajectory_results": trajectory_results,
        "behavior_results": behavior_results,
        "trajectory_labels": trajectory_labels,
        "behavior_labels": behavior_labels,
        "lifecycle": {
            "protocol": "seed-companion-lifecycle-v1",
            "decision": {"passed": True},
        },
        "manifest": manifest,
    }


@pytest.mark.parametrize("value, expected", [("A", "A"), (" d ", "D")])
def test_parse_trajectory_option_accepts_only_one_trimmed_letter(value, expected):
    from memory_core_eval.anchor_effect import parse_trajectory_option

    assert parse_trajectory_option(value) == expected


@pytest.mark.parametrize(
    "value",
    ["answer A", "AB", "**A**", "A\nreason", 0, "", "E"],
)
def test_parse_trajectory_option_rejects_non_protocol_outputs(value):
    from memory_core_eval.anchor_effect import parse_trajectory_option

    with pytest.raises(ValueError, match="single token"):
        parse_trajectory_option(value)


@pytest.mark.parametrize("value, expected", [("0", 0), (" 1 ", 1), ("2", 2)])
def test_parse_behavior_score_accepts_only_one_trimmed_digit(value, expected):
    from memory_core_eval.anchor_effect import parse_behavior_score

    assert parse_behavior_score(value) == expected


@pytest.mark.parametrize(
    "value",
    ["score: 2", "2/2", "**2**", "1\nreason", 2, "", "3"],
)
def test_parse_behavior_score_rejects_non_protocol_outputs(value):
    from memory_core_eval.anchor_effect import parse_behavior_score

    with pytest.raises(ValueError, match="single token"):
        parse_behavior_score(value)


def test_answer_prompts_keep_persona_constant_and_memory_optional():
    from memory_core_eval.anchor_effect import (
        build_behavior_prompt,
        build_trajectory_prompt,
    )

    trajectory_system, trajectory_user = build_trajectory_prompt(
        persona_text="PERSONA_ID test",
        current_request="choose carefully",
        options=("one", "two", "three", "four"),
        memory_text="DISPOSITION\nWhen this happens, do that.",
    )
    behavior_system, behavior_user = build_behavior_prompt(
        persona_text="PERSONA_ID test",
        current_request="help me now",
        memory_text="",
    )

    assert "PERSONA_ID test" in trajectory_system
    assert "LONG_TERM_MEMORY" in trajectory_system
    assert "When this happens, do that." in trajectory_system
    assert trajectory_user.endswith("Return exactly one token: A, B, C, or D.")
    assert "A) one" in trajectory_user and "D) four" in trajectory_user
    assert "PERSONA_ID test" in behavior_system
    assert "\n\nLONG_TERM_MEMORY\n" not in behavior_system
    assert behavior_user == "CURRENT_REQUEST\nhelp me now"


def test_behavior_judge_prompt_is_blind_to_arm_and_memory_metadata():
    from memory_core_eval.anchor_effect import build_behavior_judge_prompt

    system, user = build_behavior_judge_prompt(
        persona_text="PERSONA_ID test",
        evidence_text="earlier evidence",
        current_request="current request",
        response="candidate response",
        rubric={
            "score_2": "target held",
            "score_1": "partly held",
            "score_0": "target failed",
        },
    )
    combined = system + "\n" + user

    assert "target held" in combined and "earlier evidence" in combined
    assert "candidate response" in combined
    assert "seed_enabled" not in combined
    assert "memory_ref" not in combined.lower()
    assert "Return exactly one token: 0, 1, or 2." in system


def test_arm_orders_are_deterministic_complete_permutations():
    from memory_core_eval.anchor_effect import answer_arm_order, judge_arm_order
    from memory_core_eval.perma_seed_effect import SeedEffectMode

    expected = set(SeedEffectMode)
    assert set(answer_arm_order("instance-1")) == expected
    assert set(judge_arm_order("instance-1")) == expected
    assert answer_arm_order("instance-1") == answer_arm_order("instance-1")
    assert judge_arm_order("instance-1") == judge_arm_order("instance-1")


def test_summary_returns_holdout_ready_only_when_every_gate_passes(
    valid_summary_inputs,
):
    from memory_core_eval.anchor_effect import summarize_anchor_v0

    summary = summarize_anchor_v0(**valid_summary_inputs)

    assert summary["decision"] == {
        "status": "holdout_ready",
        "bottleneck": None,
    }
    assert summary["trajectory"]["positive_seed_flips"] == 1
    assert summary["behavior"]["funnel"] == {
        "formed": 6,
        "selected": 6,
        "rendered": 6,
        "behavior_changed": 6,
        "score_improved": 6,
    }


def test_summary_reports_judge_contract_before_causal_funnel(
    valid_summary_inputs,
):
    from memory_core_eval.anchor_effect import summarize_anchor_v0

    first = valid_summary_inputs["behavior_results"][0]
    first["answers"][0]["judge_output"] = "score: 0"
    first["diagnostic"]["active_dispositions"] = 0

    summary = summarize_anchor_v0(**valid_summary_inputs)

    assert summary["decision"] == {
        "status": "bottleneck",
        "bottleneck": "judge_contract",
    }


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("formed", "formed"),
        ("selected", "selected"),
        ("rendered", "rendered"),
        ("behavior_changed", "behavior_changed"),
        ("score_improved", "score_improved"),
    ],
)
def test_summary_reports_only_the_first_causal_bottleneck(
    valid_summary_inputs,
    mutation,
    expected,
):
    from memory_core_eval.anchor_effect import summarize_anchor_v0

    rows = valid_summary_inputs["behavior_results"]
    for row in rows[:2]:
        seed = next(
            arm for arm in row["answers"] if arm["mode"] == "seed_enabled"
        )
        recollection = next(
            arm
            for arm in row["answers"]
            if arm["mode"] == "recollection_only"
        )
        if mutation == "formed":
            row["diagnostic"]["active_dispositions"] = 0
        elif mutation == "selected":
            for arm in row["answers"]:
                arm["selected_disposition_refs"] = []
            seed["rendered_disposition_refs"] = []
        elif mutation == "rendered":
            seed["rendered_disposition_refs"] = []
        elif mutation == "behavior_changed":
            seed["output"] = recollection["output"]
            seed["score"] = recollection["score"]
            seed["judge_output"] = recollection["judge_output"]
        elif mutation == "score_improved":
            seed["score"] = 0
            seed["judge_output"] = "0"

    summary = summarize_anchor_v0(**valid_summary_inputs)

    assert summary["decision"] == {
        "status": "bottleneck",
        "bottleneck": expected,
    }


def test_summary_treats_trajectory_regression_as_score_bottleneck(
    valid_summary_inputs,
):
    from memory_core_eval.anchor_effect import summarize_anchor_v0

    for row in valid_summary_inputs["trajectory_results"]:
        recollection = next(
            arm
            for arm in row["answers"]
            if arm["mode"] == "recollection_only"
        )
        recollection["output"] = "A"

    summary = summarize_anchor_v0(**valid_summary_inputs)

    assert summary["decision"] == {
        "status": "bottleneck",
        "bottleneck": "score_improved",
    }
