from __future__ import annotations

import pytest

from memory_core_eval.cupid_data import CupidLabel
from memory_core_eval.perma_seed_effect import SeedEffectMode


def test_answer_and_judge_prompts_keep_arm_identity_out_of_model_inputs():
    from memory_core_eval.cupid_effect import build_answer_prompt, build_judge_prompt

    answer_system, answer_user = build_answer_prompt(
        current_request="draft the update",
        memory_text="DISPOSITIONS\nrespond with concrete trade-offs",
    )
    assert "respond with concrete trade-offs" in answer_system
    assert "draft the update" in answer_user
    assert "seed_enabled" not in answer_system + answer_user
    assert "hidden preference" not in (answer_system + answer_user).lower()

    judge_system, judge_user = build_judge_prompt(
        user_request="draft the update",
        ai_response="Here is the update.",
        preference="Use concrete trade-offs.",
        checklist=("Does the response state concrete trade-offs?",),
    )
    assert 'Preference**: "Use concrete trade-offs."' in judge_system
    assert "Does the response state concrete trade-offs?" in judge_system
    assert "Here is the update." in judge_user
    assert "seed_enabled" not in judge_system + judge_user
    assert "DISPOSITIONS" not in judge_system + judge_user


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("analysis\n\n### Evaluation Score\n7", 7),
        ("analysis\n\n### **Evaluation Score**\n**10/10**", 10),
        ("analysis\n\n### Evaluation Score\n2 - Very Poor", 2),
        (
            "analysis\n\n### Evaluation Score\n"
            "3 - The response does not satisfy the preference.",
            3,
        ),
        (
            "analysis\n\n### Evaluation Score\n"
            "**1**: Unacceptable - The response misses the requirement.",
            1,
        ),
    ],
)
def test_parse_judge_score_accepts_only_one_bounded_score(output, expected):
    from memory_core_eval.cupid_effect import parse_judge_score

    assert parse_judge_score(output) == expected


@pytest.mark.parametrize(
    "output",
    [
        "### Evaluation Score\n0",
        "### Evaluation Score\n11",
        "### Evaluation Score\n7 or 8",
        "no score header",
    ],
)
def test_parse_judge_score_rejects_invalid_or_ambiguous_output(output):
    from memory_core_eval.cupid_effect import parse_judge_score

    with pytest.raises(ValueError, match="judge score"):
        parse_judge_score(output)


def test_arm_orders_are_content_blind_and_frozen():
    from memory_core_eval.cupid_effect import answer_arm_order, judge_arm_order

    first = "cupid-" + "a" * 64
    second = "cupid-" + "b" * 64
    assert [mode.value for mode in answer_arm_order(first)] == [
        "none",
        "seed_enabled",
        "recollection_only",
    ]
    assert [mode.value for mode in answer_arm_order(second)] == [
        "recollection_only",
        "none",
        "seed_enabled",
    ]
    assert [mode.value for mode in judge_arm_order(first)] == [
        "recollection_only",
        "seed_enabled",
        "none",
    ]


def _cohort(*, h2_seed_score: int = 6):
    labels = {}
    results = []
    for split, personas in {"H1": ("p1", "p2"), "H2": ("p3", "p4")}.items():
        for persona in personas:
            for instance_type in ("consistent", "contrastive", "changing"):
                instance_ref = f"{split}:{persona}:{instance_type}"
                labels[instance_ref] = CupidLabel(
                    instance_ref=instance_ref,
                    persona_id=persona,
                    split=split,
                    instance_type=instance_type,
                    preference="scorer only",
                    checklist=("criterion",),
                )
                seed_score = h2_seed_score if split == "H2" else 6
                results.append({
                    "protocol": "cupid-seed-generation-v1",
                    "split": split,
                    "persona_id": persona,
                    "instance_ref": instance_ref,
                    "answers": [
                        {
                            "mode": SeedEffectMode.NONE.value,
                            "output": "none answer",
                            "judge_output": "### Evaluation Score\n4",
                            "score": 4,
                            "error": "",
                            "answer_request_sha256": "a" * 64,
                            "judge_request_sha256": "b" * 64,
                            "memory_tokens": 0,
                            "selected_recollection_refs": ["rec@1"],
                            "selected_disposition_refs": ["seed@1"],
                            "rendered_recollection_refs": [],
                            "rendered_disposition_refs": [],
                        },
                        {
                            "mode": SeedEffectMode.RECOLLECTION_ONLY.value,
                            "output": "recollection answer",
                            "judge_output": "### Evaluation Score\n5",
                            "score": 5,
                            "error": "",
                            "answer_request_sha256": "c" * 64,
                            "judge_request_sha256": "d" * 64,
                            "memory_tokens": 10,
                            "selected_recollection_refs": ["rec@1"],
                            "selected_disposition_refs": ["seed@1"],
                            "rendered_recollection_refs": ["rec@1"],
                            "rendered_disposition_refs": [],
                        },
                        {
                            "mode": SeedEffectMode.SEED_ENABLED.value,
                            "output": "seed answer",
                            "judge_output": f"### Evaluation Score\n{seed_score}",
                            "score": seed_score,
                            "error": "",
                            "answer_request_sha256": "e" * 64,
                            "judge_request_sha256": "f" * 64,
                            "memory_tokens": 20,
                            "selected_recollection_refs": ["rec@1"],
                            "selected_disposition_refs": ["seed@1"],
                            "rendered_recollection_refs": ["rec@1"],
                            "rendered_disposition_refs": ["seed@1"],
                        },
                    ],
                    "diagnostic": {"active_dispositions": 1},
                })
    return results, labels


def test_formal_summary_requires_positive_seed_effect_on_both_holdouts():
    from memory_core_eval.cupid_effect import summarize_cupid_effect

    results, labels = _cohort()

    summary = summarize_cupid_effect(
        results,
        labels=labels,
        bootstrap_samples=200,
        formal=True,
    )

    assert summary["arms"] == {
        "none": {"mean_score": 4.0},
        "recollection_only": {"mean_score": 5.0},
        "seed_enabled": {"mean_score": 6.0},
    }
    assert summary["splits"]["H1"]["seed-minus-recollection"] == 1.0
    assert summary["splits"]["H2"]["seed-minus-recollection"] == 1.0
    assert summary["paired"]["seed-minus-recollection"]["ci95"] == [1.0, 1.0]
    assert summary["instance_types"]["changing"]["seed-minus-recollection"] == 1.0
    assert summary["funnel"]["H1"] == {
        "instances": 6,
        "formed": 6,
        "selected": 6,
        "rendered": 6,
        "score_changed": 6,
    }
    assert summary["decision"] == {"passed": True, "reasons": []}


def test_formal_summary_fails_when_one_unseen_split_has_no_increment():
    from memory_core_eval.cupid_effect import summarize_cupid_effect

    results, labels = _cohort(h2_seed_score=5)

    summary = summarize_cupid_effect(
        results,
        labels=labels,
        bootstrap_samples=200,
        formal=True,
    )

    assert summary["decision"]["passed"] is False
    assert "split H2 did not improve" in summary["decision"]["reasons"]
    assert summary["paired"]["seed-minus-recollection"]["ci95"][0] == 0.0


def test_summary_rejects_incomplete_or_failed_arms():
    from memory_core_eval.cupid_effect import summarize_cupid_effect

    results, labels = _cohort()
    results[0]["answers"][0]["error"] = "provider failed"
    with pytest.raises(ValueError, match="failed answer or judge arm"):
        summarize_cupid_effect(results, labels=labels, formal=True)


def test_summary_rejects_unbound_or_tampered_answer_and_judge_records():
    from memory_core_eval.cupid_effect import summarize_cupid_effect

    results, labels = _cohort()
    results[0]["answers"][0]["judge_output"] = ""
    with pytest.raises(ValueError, match="complete answer and Judge"):
        summarize_cupid_effect(results, labels=labels, formal=True)

    results, labels = _cohort()
    results[0]["answers"][0]["score"] = 8
    with pytest.raises(ValueError, match="Judge score drift"):
        summarize_cupid_effect(results, labels=labels, formal=True)

    results, labels = _cohort()
    results[0]["answers"][0]["answer_request_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match="request hash"):
        summarize_cupid_effect(results, labels=labels, formal=True)
