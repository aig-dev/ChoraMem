"""Execution and strict summary for Seed generalization holdouts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from memory_core import memory_pb2 as pb

from .delayed_seed_data import (
    DelayedSeedCounterfactual,
    DelayedSeedInstance,
    DelayedSeedLabel,
)
from .delayed_seed_effect import summarize_delayed_seed
from .delayed_seed_runner import (
    generate_delayed_answers,
    judge_delayed_answers,
    prepare_delayed_instance,
)
from .seed_generalization_data import (
    PROTOCOL,
    GeneralizationDataset,
    GeneralizationExpectation,
)


async def run_generalization_case(
    *,
    instance: DelayedSeedInstance,
    expectation: GeneralizationExpectation,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    token_count: Callable[[str], int],
    memory_tokens: int,
    counterfactual: DelayedSeedCounterfactual | None = None,
    label: DelayedSeedLabel | None = None,
    answer_model: Any = None,
    judge_model: Any = None,
    max_output_tokens: int = 1_024,
) -> dict[str, object]:
    if expectation.expected_formation:
        if (
            counterfactual is None
            or label is None
            or answer_model is None
            or judge_model is None
        ):
            raise ValueError("positive generalization case requires answer labels and models")
        generated = await generate_delayed_answers(
            instance=instance,
            counterfactual=counterfactual,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=answer_model,
            token_count=token_count,
            memory_tokens=memory_tokens,
            max_output_tokens=max_output_tokens,
        )
        return await judge_delayed_answers(
            generated,
            label,
            judge_model=judge_model,
            max_judge_tokens=max_output_tokens,
        )

    prepared = await prepare_delayed_instance(
        instance=instance,
        evaluation_ref=evaluation_ref,
        memory=memory,
        settle=settle,
    )
    final_state = (
        prepared.consolidation_states[-1]
        if prepared.consolidation_states
        else {}
    )
    return {
        "protocol": PROTOCOL,
        "kind": "negative",
        "instance_ref": instance.instance_ref,
        "diagnostic": {
            "expected_episodes": prepared.expected_episodes,
            "active_dispositions": _active_count(final_state, "dispositions"),
            "active_recollections": _active_count(final_state, "recollections"),
            "selected_disposition_refs": [
                item.memory_ref for item in prepared.memory_context.dispositions
            ],
        },
    }


def summarize_generalization(
    *,
    positive_results: Sequence[Mapping[str, object]],
    negative_results: Sequence[Mapping[str, object]],
    dataset: GeneralizationDataset,
) -> dict[str, object]:
    if not _complete_refs(positive_results, dataset.positive_refs) or not _complete_refs(
        negative_results, dataset.negative_refs
    ):
        return _harness_failure("cohort")
    try:
        positive = summarize_delayed_seed(
            positive_results,
            labels=dataset.labels,
            expected_instances=4,
            dominance_wins=3,
        )
        positive_recollection_cases = sum(
            _diagnostic_count(result, "active_recollections") > 0
            for result in positive_results
        )
        negative_rows = []
        for result in negative_results:
            if result.get("protocol") != PROTOCOL or result.get("kind") != "negative":
                raise ValueError("negative generalization result identity drift")
            negative_rows.append(
                {
                    "instance_ref": result["instance_ref"],
                    "active_dispositions": _diagnostic_count(
                        result, "active_dispositions"
                    ),
                    "active_recollections": _diagnostic_count(
                        result, "active_recollections"
                    ),
                }
            )
    except (KeyError, TypeError, ValueError):
        return _harness_failure("result_contract")

    false_positives = sum(row["active_dispositions"] > 0 for row in negative_rows)
    if false_positives:
        decision = {
            "status": "negative_control_failed",
            "bottleneck": "false_positive_formation",
        }
    elif positive_recollection_cases != len(dataset.positive_refs):
        decision = {
            "status": "recollection_coexistence_failed",
            "bottleneck": "active_recollections",
        }
    elif positive["decision"]["status"] != "preliminary_learned_advantage":
        decision = {
            "status": "positive_generalization_failed",
            "bottleneck": positive["decision"].get("bottleneck")
            or positive["decision"]["status"],
        }
    else:
        decision = {"status": "preliminary_generalization", "bottleneck": None}

    return {
        "protocol": PROTOCOL,
        "instances": len(dataset.instances),
        "positive": positive,
        "negative_rows": negative_rows,
        "negative_false_positives": false_positives,
        "positive_recollection_cases": positive_recollection_cases,
        "decision": decision,
    }


def _complete_refs(
    results: Sequence[Mapping[str, object]], expected: Sequence[str]
) -> bool:
    refs = [result.get("instance_ref") for result in results]
    return len(refs) == len(expected) and len(set(refs)) == len(refs) and set(refs) == set(expected)


def _diagnostic_count(result: Mapping[str, object], name: str) -> int:
    diagnostic = result.get("diagnostic")
    if not isinstance(diagnostic, Mapping):
        raise ValueError("generalization result requires diagnostic")
    value = diagnostic.get(name)
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _active_count(state: Mapping[str, object], name: str) -> int:
    records = state.get(name, ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return 0
    return sum(
        1
        for record in records
        if isinstance(record, Mapping) and record.get("status") == "active"
    )


def _harness_failure(bottleneck: str) -> dict[str, object]:
    return {
        "protocol": PROTOCOL,
        "decision": {
            "status": "data_or_harness_failure",
            "bottleneck": bottleneck,
        },
    }
