from __future__ import annotations

from pathlib import Path

import pytest

from memory_core import memory_pb2 as pb
from memory_core_eval.delayed_seed_data import (
    DelayedSeedCounterfactual,
    DelayedSeedLabel,
    load_delayed_seed,
)


DATA_PATH = Path(__file__).parents[2] / "seed-essential-delayed-v1.json"


class RecordingMemory:
    def __init__(self):
        self.observed = []
        self.outcomes = []
        self.selected = []
        self.deliveries = []

    async def observe_source_event(self, request):
        self.observed.append(request)
        return pb.SourceEventReceipt(
            source_event_ref=request.source_event.source_ref,
            episode_ref=(
                "episode:" + request.episode_binding.run_ref
                if request.HasField("episode_binding")
                else ""
            ),
        )

    async def report_outcome(self, request):
        self.outcomes.append(request)
        return pb.OutcomeReceipt(
            outcome_event_ref=request.source_ref,
            episode_ref="episode:" + request.run_ref,
        )

    async def select_memory(self, request):
        self.selected.append(request)
        return pb.MemoryContext(
            context_ref="context:" + request.run_ref,
            run_ref=request.run_ref,
            scope=request.scope,
            recollections=[
                pb.Recollection(
                    memory_ref="rec@1",
                    text="A remembered event.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
            dispositions=[
                pb.Disposition(
                    memory_ref="seed@1",
                    text="A learned response tendency.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
        )

    async def record_memory_delivery(self, request):
        self.deliveries.append(request)
        raise AssertionError("delayed probes must remain read-only")


@pytest.mark.asyncio
async def test_prepare_replays_three_settled_causal_sessions_then_selects_once():
    """Catches batching sessions, missing Outcomes, or a writable probe."""
    from memory_core_eval.delayed_seed_runner import prepare_delayed_instance

    instance = load_delayed_seed(DATA_PATH).instances[0]
    memory = RecordingMemory()
    settled = []

    async def settle(scope, expected_episodes):
        settled.append((scope.session_ref, expected_episodes))
        return {
            "dispositions": [
                {"ref": "seed@1", "status": "active"}
            ]
            if expected_episodes >= 2
            else []
        }

    prepared = await prepare_delayed_instance(
        instance=instance,
        evaluation_ref="delayed-test",
        memory=memory,
        settle=settle,
    )

    assert len(memory.observed) == 7
    assert [request.source_event.text for request in memory.observed[:6]] == [
        text
        for session in instance.history_sessions
        for text in (session.situation, session.agent_act)
    ]
    assert memory.observed[-1].source_event.text == instance.current_request
    assert not memory.observed[-1].HasField("episode_binding")
    assert [request.text for request in memory.outcomes] == [
        session.outcome for session in instance.history_sessions
    ]
    for index, outcome in enumerate(memory.outcomes):
        assert list(outcome.related_source_event_refs) == [
            memory.observed[index * 2].source_event.source_ref,
            memory.observed[index * 2 + 1].source_event.source_ref,
        ]
        assert outcome.actor_kind == pb.SOURCE_ACTOR_KIND_USER
    assert [expected for _, expected in settled] == [1, 2, 3]
    assert len({session for session, _ in settled}) == 3
    assert len(memory.selected) == 1
    assert memory.selected[0].episode_evidence_max_bytes == 0
    assert list(memory.selected[0].situation_source_event_refs) == [
        memory.observed[-1].source_event.source_ref
    ]
    assert memory.deliveries == []
    assert prepared.expected_episodes == 3
    assert len(prepared.consolidation_states) == 3
    assert prepared.memory_context.dispositions[0].memory_ref == "seed@1"


@pytest.mark.asyncio
async def test_generation_finishes_four_label_free_arms_before_pairwise_judging():
    """Catches scorer labels reaching answer generation or early judging."""
    from memory_core_eval.delayed_seed_runner import (
        generate_delayed_answers,
        judge_delayed_answers,
    )

    dataset = load_delayed_seed(DATA_PATH)
    instance = dataset.instances[0]
    counterfactual = DelayedSeedCounterfactual(
        instance_ref=instance.instance_ref,
        oracle_disposition="When overloaded, ask one useful question.",
        anti_disposition="When overloaded, give twelve steps.",
    )
    label = DelayedSeedLabel(
        instance_ref=instance.instance_ref,
        pattern_ref=instance.pattern_ref,
        target_behavior="SECRET ask exactly one question before advice.",
    )
    memory = RecordingMemory()
    events = []

    async def settle(_scope, expected_episodes):
        return {
            "dispositions": [
                {"ref": "seed@1", "status": "active"}
            ]
            if expected_episodes >= 2
            else [],
            "recollections": [{"ref": "rec@1", "status": "active"}],
        }

    class AnswerModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            events.append("answer")
            assert "SECRET" not in instructions + input_text
            assert max_output_tokens == 1_024
            return f"answer {len(events)}"

    generated = await generate_delayed_answers(
        instance=instance,
        counterfactual=counterfactual,
        evaluation_ref="delayed-test",
        memory=memory,
        settle=settle,
        answer_model=AnswerModel(),
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
        max_output_tokens=1_024,
    )
    assert events == ["answer"] * 4
    assert "pairwise" not in generated
    assert {answer["mode"] for answer in generated["answers"]} == {
        "rag",
        "learned_seed",
        "oracle_seed",
        "anti_seed",
    }
    assert generated["diagnostic"]["active_recollections"] == 1

    class JudgeModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            assert events == ["answer"] * 4 or events[-1] == "judge"
            events.append("judge")
            assert "SECRET" in instructions + input_text
            assert "seed@1" not in instructions + input_text
            assert "oracle_seed" not in instructions + input_text
            assert max_output_tokens == 1_024
            return "A"

    judged = await judge_delayed_answers(
        generated,
        label,
        judge_model=JudgeModel(),
        max_judge_tokens=1_024,
    )

    assert events[:4] == ["answer"] * 4
    assert events[4:] == ["judge"] * 8
    assert len(judged["pairwise"]) == 8
    assert all(len(item["judge_request_sha256"]) == 64 for item in judged["pairwise"])


@pytest.mark.asyncio
async def test_judge_rejects_prose_instead_of_a_single_pairwise_token():
    """Catches malformed Judge output being converted into a winner."""
    from memory_core_eval.delayed_seed_runner import (
        generate_delayed_answers,
        judge_delayed_answers,
    )

    dataset = load_delayed_seed(DATA_PATH)
    instance = dataset.instances[0]
    memory = RecordingMemory()

    async def settle(_scope, _expected):
        return {"dispositions": [{"ref": "seed@1", "status": "active"}]}

    class AnswerModel:
        async def complete(self, **_kwargs):
            return "valid answer"

    generated = await generate_delayed_answers(
        instance=instance,
        counterfactual=dataset.counterfactuals[instance.instance_ref],
        evaluation_ref="delayed-test",
        memory=memory,
        settle=settle,
        answer_model=AnswerModel(),
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
    )

    class BadJudge:
        async def complete(self, **_kwargs):
            return "A because it is better"

    with pytest.raises(ValueError, match="A, B, or TIE"):
        await judge_delayed_answers(
            generated,
            dataset.labels[instance.instance_ref],
            judge_model=BadJudge(),
        )


def test_frozen_result_round_trip_rejects_identity_or_content_drift(tmp_path):
    """Catches partial resume or silent overwrite of a completed instance."""
    from memory_core_eval.delayed_seed_runner import freeze_result, load_frozen_result

    path = tmp_path / "instance.json"
    result = {
        "protocol": "seed-essential-delayed-v1",
        "instance_ref": "probe-1",
        "pattern_ref": "pattern-1",
        "answers": [],
        "pairwise": [],
    }
    freeze_result(path, result)

    assert load_frozen_result(path, instance_ref="probe-1") == result
    with pytest.raises(ValueError, match="drift"):
        load_frozen_result(path, instance_ref="probe-2")
    with pytest.raises(ValueError, match="drift"):
        freeze_result(path, result | {"pattern_ref": "changed"})
