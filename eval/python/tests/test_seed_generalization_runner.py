from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from memory_core import memory_pb2 as pb
from memory_core_eval.seed_generalization_data import load_seed_generalization
from memory_core_eval.seed_generalization_runner import summarize_generalization


DATA = Path(__file__).resolve().parents[2] / "seed-generalization-v1.json"


class RecordingMemory:
    def __init__(self, *, include_seed: bool) -> None:
        self.include_seed = include_seed
        self.observed: list[object] = []
        self.outcomes: list[object] = []
        self.selected: list[object] = []

    async def observe_source_event(self, request: object) -> pb.SourceEventReceipt:
        self.observed.append(request)
        source_event = request.source_event  # type: ignore[attr-defined]
        episode_binding = request.episode_binding  # type: ignore[attr-defined]
        return pb.SourceEventReceipt(
            source_event_ref=source_event.source_ref,
            episode_ref=(
                "episode:" + episode_binding.run_ref
                if request.HasField("episode_binding")  # type: ignore[attr-defined]
                else ""
            ),
        )

    async def report_outcome(self, request: object) -> pb.OutcomeReceipt:
        self.outcomes.append(request)
        return pb.OutcomeReceipt(
            outcome_event_ref=request.source_ref,  # type: ignore[attr-defined]
            episode_ref="episode:" + request.run_ref,  # type: ignore[attr-defined]
        )

    async def select_memory(self, request: object) -> pb.MemoryContext:
        self.selected.append(request)
        dispositions = []
        if self.include_seed:
            dispositions.append(
                pb.Disposition(
                    memory_ref="seed@1",
                    text="A learned response tendency.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            )
        return pb.MemoryContext(
            context_ref="context:test",
            recollections=[
                pb.Recollection(
                    memory_ref="rec@1",
                    text="A durable user fact.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
            dispositions=dispositions,
        )


class FixedModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    async def complete(self, **_kwargs: object) -> str:
        self.calls += 1
        return self.output


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pair(left: str, right: str, winner: str) -> list[dict[str, object]]:
    if winner == left:
        forward, reverse = "A", "B"
    elif winner == right:
        forward, reverse = "B", "A"
    elif winner == "tie":
        forward = reverse = "TIE"
    else:
        forward = reverse = "A"
    name = f"{left}_vs_{right}"
    return [
        {
            "comparison": name,
            "direction": "forward",
            "a_mode": left,
            "b_mode": right,
            "judge_output": forward,
            "judge_request_sha256": _hash(name + ":forward"),
        },
        {
            "comparison": name,
            "direction": "reverse",
            "a_mode": right,
            "b_mode": left,
            "judge_output": reverse,
            "judge_request_sha256": _hash(name + ":reverse"),
        },
    ]


def _positive_result(instance_ref: str, pattern_ref: str) -> dict[str, object]:
    answers = []
    for mode in ("rag", "learned_seed", "oracle_seed", "anti_seed"):
        answers.append(
            {
                "mode": mode,
                "output": mode + " answer",
                "error": "",
                "memory_tokens": 10,
                "memory_budget": 2_048,
                "selected_recollection_refs": ["rec@1"],
                "selected_disposition_refs": ["seed@1"],
                "rendered_recollection_refs": ["rec@1"],
                "rendered_disposition_refs": (
                    ["seed@1"] if mode == "learned_seed" else []
                ),
                "answer_request_sha256": _hash(instance_ref + mode),
            }
        )
    pairwise = []
    pairwise += _pair("oracle_seed", "rag", "oracle_seed")
    pairwise += _pair("learned_seed", "rag", "learned_seed")
    pairwise += _pair("oracle_seed", "learned_seed", "tie")
    pairwise += _pair("oracle_seed", "anti_seed", "oracle_seed")
    return {
        "protocol": "seed-essential-delayed-v1",
        "instance_ref": instance_ref,
        "pattern_ref": pattern_ref,
        "answers": answers,
        "pairwise": pairwise,
        "diagnostic": {
            "active_dispositions": 1,
            "active_recollections": 1,
            "selected_disposition_refs": ["seed@1"],
            "rendered_disposition_refs": ["seed@1"],
        },
    }


def _cohort() -> tuple[object, list[dict[str, object]], list[dict[str, object]]]:
    dataset = load_seed_generalization(DATA)
    by_ref = {instance.instance_ref: instance for instance in dataset.instances}
    positives = [
        _positive_result(ref, by_ref[ref].pattern_ref)
        for ref in dataset.positive_refs
    ]
    negatives = [
        {
            "protocol": "seed-generalization-v1",
            "kind": "negative",
            "instance_ref": ref,
            "diagnostic": {
                "active_dispositions": 0,
                "active_recollections": 1,
            },
        }
        for ref in dataset.negative_refs
    ]
    return dataset, positives, negatives


@pytest.mark.asyncio
async def test_positive_case_reuses_four_arm_generation_and_judging() -> None:
    from memory_core_eval.seed_generalization_runner import run_generalization_case

    dataset = load_seed_generalization(DATA)
    instance = next(
        item for item in dataset.instances if item.instance_ref == dataset.positive_refs[0]
    )
    memory = RecordingMemory(include_seed=True)

    async def settle(_scope: object, _expected: int) -> dict[str, object]:
        return {
            "dispositions": [{"ref": "seed@1", "status": "active"}],
            "recollections": [{"ref": "rec@1", "status": "active"}],
        }

    answer_model = FixedModel("A concrete answer")
    judge_model = FixedModel("A")
    result = await run_generalization_case(
        instance=instance,
        expectation=dataset.expectations[instance.instance_ref],
        evaluation_ref="generalization-test",
        memory=memory,
        settle=settle,
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
        counterfactual=dataset.counterfactuals[instance.instance_ref],
        label=dataset.labels[instance.instance_ref],
        answer_model=answer_model,
        judge_model=judge_model,
    )

    assert len(result["answers"]) == 4
    assert len(result["pairwise"]) == 8
    assert result["diagnostic"]["active_recollections"] == 1
    assert answer_model.calls == 4
    assert judge_model.calls == 8


@pytest.mark.asyncio
async def test_negative_case_records_state_without_answer_or_judge_calls() -> None:
    from memory_core_eval.seed_generalization_runner import run_generalization_case

    dataset = load_seed_generalization(DATA)
    instance = next(
        item for item in dataset.instances if item.instance_ref == dataset.negative_refs[0]
    )
    memory = RecordingMemory(include_seed=False)

    async def settle(_scope: object, _expected: int) -> dict[str, object]:
        return {
            "dispositions": [],
            "recollections": [{"ref": "rec@1", "status": "active"}],
        }

    result = await run_generalization_case(
        instance=instance,
        expectation=dataset.expectations[instance.instance_ref],
        evaluation_ref="generalization-test",
        memory=memory,
        settle=settle,
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
    )

    assert result == {
        "protocol": "seed-generalization-v1",
        "kind": "negative",
        "instance_ref": instance.instance_ref,
        "diagnostic": {
            "expected_episodes": 8,
            "active_dispositions": 0,
            "active_recollections": 1,
            "selected_disposition_refs": [],
        },
    }
    assert len(memory.observed) == 17
    assert len(memory.outcomes) == 8
    assert len(memory.selected) == 1


def test_summary_requires_positive_effect_negative_precision_and_recollections() -> None:
    dataset, positives, negatives = _cohort()

    summary = summarize_generalization(
        positive_results=positives,
        negative_results=negatives,
        dataset=dataset,
    )

    assert summary["positive"]["funnel"] == {
        "formed": 4,
        "selected": 4,
        "rendered": 4,
    }
    assert summary["negative_false_positives"] == 0
    assert summary["positive_recollection_cases"] == 4
    assert summary["decision"] == {
        "status": "preliminary_generalization",
        "bottleneck": None,
    }


def test_summary_rejects_one_negative_false_positive() -> None:
    dataset, positives, negatives = _cohort()
    negatives[0]["diagnostic"]["active_dispositions"] = 1  # type: ignore[index]

    summary = summarize_generalization(
        positive_results=positives,
        negative_results=negatives,
        dataset=dataset,
    )

    assert summary["decision"] == {
        "status": "negative_control_failed",
        "bottleneck": "false_positive_formation",
    }


def test_summary_rejects_seed_success_that_suppresses_recollection() -> None:
    dataset, positives, negatives = _cohort()
    positives[0]["diagnostic"]["active_recollections"] = 0  # type: ignore[index]

    summary = summarize_generalization(
        positive_results=positives,
        negative_results=negatives,
        dataset=dataset,
    )

    assert summary["decision"] == {
        "status": "recollection_coexistence_failed",
        "bottleneck": "active_recollections",
    }


def test_summary_marks_incomplete_cohort_as_harness_failure() -> None:
    dataset, positives, negatives = _cohort()

    summary = summarize_generalization(
        positive_results=positives[:-1],
        negative_results=negatives,
        dataset=dataset,
    )

    assert summary["decision"] == {
        "status": "data_or_harness_failure",
        "bottleneck": "cohort",
    }
