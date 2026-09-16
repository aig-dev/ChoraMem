from __future__ import annotations

import hashlib

import pytest

from memory_core import memory_pb2 as pb
from memory_core_eval.anchor_data import (
    AnchorMessage,
    AnchorSession,
    BehaviorInstance,
    BehaviorLabel,
    TrajectoryInstance,
    TrajectoryLabel,
)


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
                    memory_ref="recollection_test@1",
                    text="The user corrected an earlier misunderstanding.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
            dispositions=[
                pb.Disposition(
                    memory_ref="seed_test@1",
                    text="Acknowledge corrections directly before continuing.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
        )

    async def record_memory_delivery(self, request):
        self.deliveries.append(request)
        raise AssertionError("ANCHOR probes must never record Delivery")


def _history() -> tuple[AnchorSession, ...]:
    return (
        AnchorSession(
            session_id=0,
            messages=(
                AnchorMessage(0, 0, "user", "first request"),
                AnchorMessage(0, 0, "assistant", "first answer"),
                AnchorMessage(0, 1, "user", "please correct that"),
                AnchorMessage(0, 1, "assistant", "corrected answer"),
            ),
        ),
        AnchorSession(
            session_id=1,
            messages=(
                AnchorMessage(1, 0, "user", "second request"),
                AnchorMessage(1, 0, "assistant", "second answer"),
            ),
        ),
    )


def _behavior_instance() -> BehaviorInstance:
    return BehaviorInstance(
        instance_ref="behavior-test",
        bank_id="test-bank",
        persona_text="PERSONA_ID test\nROLE companion",
        history_sessions=_history(),
        session_id=2,
        turn=0,
        current_request="respond to the correction now",
    )


def _trajectory_instance() -> TrajectoryInstance:
    return TrajectoryInstance(
        instance_ref="trajectory-test",
        bank_id="test-bank",
        persona_text="PERSONA_ID test\nROLE companion",
        history_sessions=_history(),
        current_request="which option is current?",
        options=("first", "second", "third", "fourth"),
    )


async def _settle(_scope, _expected_episodes):
    return {
        "dispositions": [
            {"status": "active", "ref": "seed_test@1"},
            {"status": "inhibited", "ref": "seed_old@2"},
        ]
    }


@pytest.mark.asyncio
async def test_prepare_probe_replays_causal_roles_and_selects_exactly_once():
    from memory_core_eval.anchor_runner import prepare_probe

    memory = RecordingMemory()
    settled = []

    async def settle(scope, expected_episodes):
        settled.append((scope.session_ref, expected_episodes))
        return await _settle(scope, expected_episodes)

    prepared = await prepare_probe(
        probe=_behavior_instance(),
        evaluation_ref="anchor-test",
        memory=memory,
        settle=settle,
    )

    assert [request.source_event.text for request in memory.observed] == [
        "first request",
        "first answer",
        "please correct that",
        "corrected answer",
        "second request",
        "second answer",
        "respond to the correction now",
    ]
    assert [request.text for request in memory.outcomes] == ["please correct that"]
    assert (
        memory.outcomes[0].source_ref
        == memory.observed[2].source_event.source_ref
    )
    assert list(memory.outcomes[0].related_source_event_refs) == [
        memory.observed[0].source_event.source_ref,
        memory.observed[1].source_event.source_ref,
    ]
    assert [expected for _, expected in settled] == [2, 3]
    assert len(memory.selected) == 1
    assert memory.selected[0].episode_evidence_max_bytes == 0
    assert list(memory.selected[0].situation_source_event_refs) == [
        memory.observed[-1].source_event.source_ref
    ]
    assert not memory.observed[-1].HasField("episode_binding")
    assert prepared.expected_episodes == 3
    assert prepared.consolidation_state["dispositions"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_prepare_probe_does_not_invent_cross_session_outcomes():
    from memory_core_eval.anchor_runner import prepare_probe

    memory = RecordingMemory()
    instance = BehaviorInstance(
        instance_ref="terminal-test",
        bank_id="test-bank",
        persona_text="PERSONA_ID test",
        history_sessions=(
            AnchorSession(
                0,
                (
                    AnchorMessage(0, 0, "user", "request"),
                    AnchorMessage(0, 0, "assistant", "terminal answer"),
                ),
            ),
            AnchorSession(
                1,
                (
                    AnchorMessage(1, 0, "user", "later session"),
                    AnchorMessage(1, 0, "assistant", "later answer"),
                ),
            ),
        ),
        session_id=2,
        turn=0,
        current_request="probe",
    )

    await prepare_probe(
        probe=instance,
        evaluation_ref="anchor-test",
        memory=memory,
        settle=_settle,
    )

    assert memory.outcomes == []


@pytest.mark.asyncio
async def test_behavior_generation_finishes_all_arms_before_label_is_available():
    from memory_core_eval.anchor_runner import (
        generate_behavior_answers,
        judge_behavior_answers,
    )

    memory = RecordingMemory()
    events: list[str] = []

    class AnswerModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            events.append("answer")
            assert "SECRET_RUBRIC" not in instructions + input_text
            assert max_output_tokens == 1_024
            if "DISPOSITIONS" in instructions:
                return "seed response"
            if "RECOLLECTIONS" in instructions:
                return "recollection response"
            return "none response"

    generated = await generate_behavior_answers(
        instance=_behavior_instance(),
        evaluation_ref="anchor-test",
        memory=memory,
        settle=_settle,
        answer_model=AnswerModel(),
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
        max_output_tokens=1_024,
    )

    assert events == ["answer", "answer", "answer"]
    assert all("score" not in arm for arm in generated["answers"])

    label = BehaviorLabel(
        instance_ref="behavior-test",
        dimension="relationship_repair",
        evidence_text="frozen evidence",
        rubric={
            "score_2": "SECRET_RUBRIC target held",
            "score_1": "SECRET_RUBRIC partly held",
            "score_0": "SECRET_RUBRIC failed",
        },
    )

    class JudgeModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            events.append("judge")
            combined = instructions + input_text
            assert "SECRET_RUBRIC" in combined
            assert "seed_enabled" not in combined
            assert "seed_test@1" not in combined
            assert max_output_tokens == 4
            if "seed response" in input_text:
                return "2"
            if "recollection response" in input_text:
                return "1"
            return "0"

    judged = await judge_behavior_answers(
        generated,
        label,
        judge_model=JudgeModel(),
        max_judge_tokens=4,
    )

    assert events == ["answer", "answer", "answer", "judge", "judge", "judge"]
    assert [(arm["mode"], arm["score"]) for arm in judged["answers"]] == [
        ("none", 0),
        ("recollection_only", 1),
        ("seed_enabled", 2),
    ]
    assert memory.deliveries == []
    assert judged["diagnostic"] == {
        "active_dispositions": 1,
        "selected_disposition_refs": ["seed_test@1"],
        "rendered_disposition_refs": ["seed_test@1"],
    }


@pytest.mark.asyncio
async def test_trajectory_label_is_applied_only_after_three_answers_exist():
    from memory_core_eval.anchor_runner import (
        generate_trajectory_answers,
        score_trajectory_answers,
    )

    calls = []

    class AnswerModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            calls.append(instructions + input_text)
            assert "SECRET_GOLD" not in instructions + input_text
            assert max_output_tokens == 4
            return "A" if "DISPOSITIONS" in instructions else "B"

    generated = await generate_trajectory_answers(
        instance=_trajectory_instance(),
        evaluation_ref="anchor-test",
        memory=RecordingMemory(),
        settle=_settle,
        answer_model=AnswerModel(),
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
        max_output_tokens=4,
    )
    assert len(calls) == 3
    assert all("correct" not in arm for arm in generated["answers"])

    scored = score_trajectory_answers(
        generated,
        TrajectoryLabel(
            instance_ref="trajectory-test",
            family="SECRET_GOLD",
            correct_index=0,
        ),
    )

    assert [(arm["mode"], arm["correct"]) for arm in scored["answers"]] == [
        ("none", False),
        ("recollection_only", False),
        ("seed_enabled", True),
    ]


def test_owner_scope_hashes_public_instance_identity():
    from memory_core_eval.anchor_runner import owner_scope

    scope = owner_scope("anchor-dev", "public-item-id")
    digest = hashlib.sha256(b"public-item-id").hexdigest()

    assert scope.tenant_ref == "anchor-v0-eval"
    assert scope.agent_ref == "anchor-reference-agent"
    assert scope.relationship_ref == f"anchor-dev:instance:{digest}"
    assert scope.session_ref == ""
    assert scope.kind == pb.MEMORY_SCOPE_KIND_RELATIONSHIP


def test_result_freeze_and_resume_are_atomic_and_identity_strict(tmp_path):
    from memory_core_eval.anchor_runner import freeze_result, load_frozen_result

    path = tmp_path / "result.json"
    result = {
        "protocol": "anchor-v0-causal-dev-v1",
        "kind": "behavior",
        "instance_ref": "behavior-test",
        "answers": [],
    }

    assert load_frozen_result(path, instance_ref="behavior-test") is None
    freeze_result(path, result)
    assert load_frozen_result(path, instance_ref="behavior-test") == result
    assert not path.with_suffix(".json.part").exists()

    with pytest.raises(ValueError, match="result drift"):
        freeze_result(path, dict(result, kind="trajectory"))

    with pytest.raises(ValueError, match="identity drift"):
        load_frozen_result(path, instance_ref="different-test")
