from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from memory_core import memory_pb2 as pb
from memory_core_eval.delayed_seed_data import load_delayed_seed


DATA_PATH = Path(__file__).parents[2] / "seed-essential-delayed-v1.json"


def test_four_arms_share_one_budget_and_replace_the_disposition_lane():
    """Catches learned Seed leakage into RAG or counterfactual arms."""
    from memory_core_eval.delayed_seed_effect import (
        DelayedSeedMode,
        build_delayed_contexts,
    )

    dataset = load_delayed_seed(DATA_PATH)
    counterfactual = dataset.counterfactuals["overload-probe-launch"]
    selected = pb.MemoryContext(
        recollections=[
            pb.Recollection(
                memory_ref="rec@1",
                text="The user is preparing a conference.",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )
        ],
        dispositions=[
            pb.Disposition(
                memory_ref="learned@1",
                text="Learned tendency.",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )
        ],
    )

    contexts = build_delayed_contexts(
        selected,
        counterfactual=counterfactual,
        max_tokens=2_048,
        token_count=lambda text: len(text.split()),
    )

    assert set(contexts) == set(DelayedSeedMode)
    assert {item.memory_budget for item in contexts.values()} == {2_048}
    assert all(item.memory_tokens <= item.memory_budget for item in contexts.values())
    assert contexts[DelayedSeedMode.RAG].disposition_refs == ()
    assert contexts[DelayedSeedMode.LEARNED_SEED].disposition_refs == (
        "learned@1",
    )
    assert contexts[DelayedSeedMode.ORACLE_SEED].disposition_refs == (
        "oracle:overload-probe-launch",
    )
    assert contexts[DelayedSeedMode.ANTI_SEED].disposition_refs == (
        "anti:overload-probe-launch",
    )
    assert "Learned tendency." not in contexts[DelayedSeedMode.RAG].text
    assert "Learned tendency." not in contexts[DelayedSeedMode.ORACLE_SEED].text
    assert (
        counterfactual.oracle_disposition
        in contexts[DelayedSeedMode.ORACLE_SEED].text
    )
    assert counterfactual.anti_disposition in contexts[DelayedSeedMode.ANTI_SEED].text
    assert all(
        item.recollection_refs == ("rec@1",) for item in contexts.values()
    )


def test_answer_and_pairwise_prompts_keep_arm_identity_and_refs_hidden():
    """Catches scorer or arm metadata leaking into model-visible prompts."""
    from memory_core_eval.delayed_seed_effect import (
        build_answer_prompt,
        build_pairwise_prompt,
    )

    answer_system, answer_user = build_answer_prompt(
        persona_text="PERSONA_ID test\nROLE companion",
        current_request="Help me think.",
        memory_text="DISPOSITIONS\nRELATION: Ask one question.",
    )
    assert "Ask one question." in answer_system
    assert answer_user == "CURRENT_REQUEST\nHelp me think."
    assert "RECOLLECTION describes relevant past experience" in answer_system
    assert "DISPOSITION describes a learned response tendency" in answer_system
    assert "not a current instruction" in answer_system
    assert "oracle_seed" not in answer_system + answer_user
    assert "memory_ref" not in (answer_system + answer_user).lower()

    judge_system, judge_user = build_pairwise_prompt(
        persona_text="PERSONA_ID test\nROLE companion",
        current_request="Help me think.",
        target_behavior="Ask one useful question.",
        response_a="Which part is fixed?",
        response_b="Here are twelve steps.",
    )
    combined = judge_system + judge_user
    assert "Ask one useful question." in combined
    assert "Which part is fixed?" in combined
    assert "Here are twelve steps." in combined
    assert "oracle_seed" not in combined
    assert "memory_ref" not in combined.lower()
    assert "Return exactly one token: A, B, or TIE." in judge_system


@pytest.mark.parametrize(
    ("forward", "reverse", "expected"),
    [
        ("A", "B", "oracle_seed"),
        ("B", "A", "rag"),
        ("TIE", "TIE", "tie"),
        ("A", "A", "inconsistent"),
        ("A", "TIE", "inconsistent"),
    ],
)
def test_pair_consensus_requires_position_invariant_result(
    forward, reverse, expected
):
    """Catches position-biased or one-sided judgments being counted as wins."""
    from memory_core_eval.delayed_seed_effect import (
        DelayedSeedMode,
        pair_consensus,
    )

    assert pair_consensus(
        forward,
        reverse,
        left=DelayedSeedMode.ORACLE_SEED,
        right=DelayedSeedMode.RAG,
    ) == expected


@pytest.mark.parametrize("value", ["A because", "AB", "", "oracle", "C"])
def test_pairwise_parser_rejects_non_contract_output(value):
    """Catches prose or arm labels being silently coerced into Judge outcomes."""
    from memory_core_eval.delayed_seed_effect import parse_pairwise_token

    with pytest.raises(ValueError, match="A, B, or TIE"):
        parse_pairwise_token(value)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _pair(left: str, right: str, winner: str) -> list[dict[str, object]]:
    if winner == left:
        outputs = ("A", "B")
    elif winner == right:
        outputs = ("B", "A")
    elif winner == "tie":
        outputs = ("TIE", "TIE")
    else:
        outputs = ("A", "A")
    name = f"{left}_vs_{right}"
    return [
        {
            "comparison": name,
            "direction": "forward",
            "a_mode": left,
            "b_mode": right,
            "judge_output": outputs[0],
            "judge_request_sha256": _hash(name + ":forward"),
        },
        {
            "comparison": name,
            "direction": "reverse",
            "a_mode": right,
            "b_mode": left,
            "judge_output": outputs[1],
            "judge_request_sha256": _hash(name + ":reverse"),
        },
    ]


def _cohort(
    *,
    learned_vs_rag: str,
    oracle_vs_rag: str,
    oracle_vs_anti: str = "oracle_seed",
    active: bool = True,
    selected: bool = True,
    rendered: bool = True,
) -> tuple[list[dict[str, object]], object]:
    dataset = load_delayed_seed(DATA_PATH)
    results = []
    for instance in dataset.instances:
        answers = []
        for mode in ("rag", "learned_seed", "oracle_seed", "anti_seed"):
            learned_refs = ["seed@1"] if selected else []
            rendered_refs = ["seed@1"] if rendered and mode == "learned_seed" else []
            answers.append(
                {
                    "mode": mode,
                    "output": f"{mode} answer",
                    "error": "",
                    "memory_tokens": 10,
                    "memory_budget": 2_048,
                    "selected_recollection_refs": ["rec@1"],
                    "selected_disposition_refs": learned_refs,
                    "rendered_recollection_refs": ["rec@1"],
                    "rendered_disposition_refs": rendered_refs,
                    "answer_request_sha256": _hash(instance.instance_ref + mode),
                }
            )
        pairwise = []
        pairwise += _pair("oracle_seed", "rag", oracle_vs_rag)
        pairwise += _pair("learned_seed", "rag", learned_vs_rag)
        pairwise += _pair("oracle_seed", "learned_seed", "oracle_seed")
        pairwise += _pair("oracle_seed", "anti_seed", oracle_vs_anti)
        results.append(
            {
                "protocol": "seed-essential-delayed-v1",
                "instance_ref": instance.instance_ref,
                "pattern_ref": instance.pattern_ref,
                "answers": answers,
                "pairwise": pairwise,
                "diagnostic": {
                    "active_dispositions": 1 if active else 0,
                    "selected_disposition_refs": ["seed@1"] if selected else [],
                    "rendered_disposition_refs": ["seed@1"] if rendered else [],
                },
            }
        )
    return results, dataset


def test_summary_reports_preliminary_learned_advantage_only_for_strict_dominance():
    """Catches a few wins or position-sensitive wins being called an advantage."""
    from memory_core_eval.delayed_seed_effect import summarize_delayed_seed

    results, dataset = _cohort(
        learned_vs_rag="learned_seed",
        oracle_vs_rag="oracle_seed",
    )

    summary = summarize_delayed_seed(results, labels=dataset.labels)

    assert summary["funnel"] == {"formed": 8, "selected": 8, "rendered": 8}
    assert summary["comparisons"]["learned_seed_vs_rag"] == {
        "learned_seed": 8,
        "rag": 0,
        "tie": 0,
        "inconsistent": 0,
    }
    assert summary["decision"] == {
        "status": "preliminary_learned_advantage",
        "bottleneck": None,
    }


def test_summary_separates_learning_bottleneck_from_no_architecture_value():
    """Catches Oracle and Learned failures being collapsed into one diagnosis."""
    from memory_core_eval.delayed_seed_effect import summarize_delayed_seed

    results, dataset = _cohort(
        learned_vs_rag="tie",
        oracle_vs_rag="oracle_seed",
    )
    learning = summarize_delayed_seed(results, labels=dataset.labels)
    assert learning["decision"] == {
        "status": "learning_or_selection_bottleneck",
        "bottleneck": "learned_effect",
    }

    results, dataset = _cohort(
        learned_vs_rag="tie",
        oracle_vs_rag="tie",
    )
    architecture = summarize_delayed_seed(results, labels=dataset.labels)
    assert architecture["decision"] == {
        "status": "no_detectable_architecture_advantage",
        "bottleneck": "oracle_vs_rag",
    }


def test_summary_reports_first_learning_funnel_break_and_manipulation_failure():
    """Catches semantic-effect tuning when Seed formation or the control failed first."""
    from memory_core_eval.delayed_seed_effect import summarize_delayed_seed

    results, dataset = _cohort(
        learned_vs_rag="tie",
        oracle_vs_rag="oracle_seed",
        active=False,
        selected=False,
        rendered=False,
    )
    formed = summarize_delayed_seed(results, labels=dataset.labels)
    assert formed["decision"]["bottleneck"] == "formed"

    results, dataset = _cohort(
        learned_vs_rag="learned_seed",
        oracle_vs_rag="oracle_seed",
        oracle_vs_anti="tie",
    )
    manipulation = summarize_delayed_seed(results, labels=dataset.labels)
    assert manipulation["decision"] == {
        "status": "manipulation_check_failed",
        "bottleneck": "oracle_vs_anti",
    }


def test_small_cohort_uses_its_frozen_dominance_rule_for_funnel_diagnosis():
    """Catches the eight-case default mislabeling a complete four-case funnel."""
    from memory_core_eval.delayed_seed_effect import summarize_delayed_seed

    results, dataset = _cohort(
        learned_vs_rag="tie",
        oracle_vs_rag="oracle_seed",
    )
    results = results[:4]
    labels = {
        result["instance_ref"]: dataset.labels[result["instance_ref"]]
        for result in results
    }

    summary = summarize_delayed_seed(
        results,
        labels=labels,
        expected_instances=4,
        dominance_wins=3,
    )

    assert summary["funnel"] == {"formed": 4, "selected": 4, "rendered": 4}
    assert summary["decision"] == {
        "status": "learning_or_selection_bottleneck",
        "bottleneck": "learned_effect",
    }
