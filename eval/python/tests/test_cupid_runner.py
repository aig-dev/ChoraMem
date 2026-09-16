from __future__ import annotations

import pytest

from memory_core import memory_pb2 as pb
from memory_core_eval.cupid_data import (
    CupidInstance,
    CupidLabel,
    CupidMessage,
    CupidSession,
)


class RecordingMemory:
    def __init__(self):
        self.observed = []
        self.outcomes = []
        self.selected = []

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
            recollections=[pb.Recollection(
                memory_ref="rec@1",
                text="remembered",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )],
            dispositions=[pb.Disposition(
                memory_ref="seed@1",
                text="respond this way",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )],
        )

    async def record_memory_delivery(self, _request):
        raise AssertionError("CUPID probes are read-only")


@pytest.mark.asyncio
async def test_prepare_instance_replays_feedback_causally_and_selects_once():
    from memory_core_eval.cupid_runner import prepare_instance

    instance = CupidInstance(
        instance_ref="cupid-" + "a" * 64,
        persona_id="dev-persona",
        current_request="new request",
        history_sessions=(
            CupidSession(messages=(
                CupidMessage("user", "first request"),
                CupidMessage("assistant", "first answer"),
                CupidMessage("user", "please make it warmer"),
                CupidMessage("assistant", "warmer answer"),
                CupidMessage("user", "that works"),
            )),
            CupidSession(messages=(
                CupidMessage("user", "second request"),
                CupidMessage("assistant", "second answer"),
            )),
        ),
    )
    memory = RecordingMemory()
    settled = []

    async def settle(scope, expected_episodes):
        settled.append((scope.session_ref, expected_episodes))
        return {"dispositions": [{"status": "active", "ref": "seed@1"}]}

    prepared = await prepare_instance(
        instance=instance,
        evaluation_ref="cupid-test",
        memory=memory,
        settle=settle,
    )

    assert [request.source_event.text for request in memory.observed] == [
        "first request",
        "first answer",
        "please make it warmer",
        "warmer answer",
        "second request",
        "second answer",
        "new request",
    ]
    assert [request.text for request in memory.outcomes] == [
        "please make it warmer",
        "that works",
    ]

    reused = memory.outcomes[0]
    next_situation = memory.observed[2]
    assert reused.source_ref == next_situation.source_event.source_ref
    assert reused.text == next_situation.source_event.text
    assert reused.idempotency_key != next_situation.idempotency_key
    assert reused.delivery_receipt_refs == []
    assert list(reused.related_source_event_refs) == [
        memory.observed[0].source_event.source_ref,
        memory.observed[1].source_event.source_ref,
    ]

    assert settled == [
        ("cupid-test:" + "a" * 64 + ":session:0", 2),
        ("cupid-test:" + "a" * 64 + ":session:1", 3),
    ]
    assert len(memory.selected) == 1
    request = memory.selected[0]
    assert request.episode_evidence_max_bytes == 0
    assert list(request.situation_source_event_refs) == [
        memory.observed[-1].source_event.source_ref
    ]
    assert "dev-persona" not in request.scope.relationship_ref
    assert not memory.observed[-1].HasField("episode_binding")

    assert prepared.expected_episodes == 3
    assert prepared.memory_context.context_ref == request.run_ref.join(
        ("context:", "")
    )
    assert prepared.consolidation_state["dispositions"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_prepare_instance_does_not_invent_outcome_after_terminal_assistant():
    from memory_core_eval.cupid_runner import prepare_instance

    instance = CupidInstance(
        instance_ref="cupid-" + "b" * 64,
        persona_id="dev-persona",
        current_request="probe",
        history_sessions=(CupidSession(messages=(
            CupidMessage("user", "request"),
            CupidMessage("assistant", "answer without feedback"),
        )),),
    )
    memory = RecordingMemory()

    async def settle(_scope, expected_episodes):
        assert expected_episodes == 1
        return {}

    await prepare_instance(
        instance=instance,
        evaluation_ref="cupid-test",
        memory=memory,
        settle=settle,
    )

    assert memory.outcomes == []


@pytest.mark.asyncio
async def test_evaluate_instance_generates_all_arms_before_label_is_given_to_judge():
    from memory_core_eval.cupid_runner import evaluate_instance

    instance = CupidInstance(
        instance_ref="cupid-" + "a" * 64,
        persona_id="dev-persona",
        current_request="write the new response",
        history_sessions=(CupidSession(messages=(
            CupidMessage("user", "historical request"),
            CupidMessage("assistant", "historical answer"),
            CupidMessage("user", "historical feedback"),
        )),),
    )
    label = CupidLabel(
        instance_ref=instance.instance_ref,
        persona_id=instance.persona_id,
        split="dev",
        instance_type="consistent",
        preference="SECRET warm and concrete",
        checklist=("SECRET checklist item",),
    )
    memory = RecordingMemory()

    async def settle(_scope, _expected_episodes):
        return {"dispositions": [{"status": "active", "ref": "seed@1"}]}

    answer_calls = []

    class AnswerModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            answer_calls.append((instructions, input_text, max_output_tokens))
            assert "SECRET" not in instructions + input_text
            if "DISPOSITIONS" in instructions:
                return "seed answer"
            if "RECOLLECTIONS" in instructions:
                return "recollection answer"
            return "none answer"

    judge_calls = []

    class JudgeModel:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            judge_calls.append((instructions, input_text, max_output_tokens))
            assert "SECRET warm and concrete" in instructions
            assert "SECRET checklist item" in instructions
            assert "seed_enabled" not in instructions + input_text
            assert "seed@1" not in instructions + input_text
            score = 9 if "seed answer" in input_text else 7
            if "none answer" in input_text:
                score = 5
            return f"analysis\n\n### Evaluation Score\n{score}"

    result = await evaluate_instance(
        instance=instance,
        label=label,
        evaluation_ref="cupid-test",
        memory=memory,
        settle=settle,
        answer_model=AnswerModel(),
        judge_model=JudgeModel(),
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
        max_answer_tokens=32_768,
        max_judge_tokens=8_192,
    )

    assert len(answer_calls) == 3
    assert len(judge_calls) == 3
    assert {call[2] for call in answer_calls} == {32_768}
    assert {call[2] for call in judge_calls} == {8_192}
    assert [(arm["mode"], arm["score"]) for arm in result["answers"]] == [
        ("none", 5),
        ("recollection_only", 7),
        ("seed_enabled", 9),
    ]
    assert all(
        len(arm["answer_request_sha256"]) == 64
        and len(arm["judge_request_sha256"]) == 64
        for arm in result["answers"]
    )
    assert len({
        arm["answer_request_sha256"] for arm in result["answers"]
    }) == 3
    assert len({
        arm["judge_request_sha256"] for arm in result["answers"]
    }) == 3
    assert result["answers"][2]["rendered_disposition_refs"] == ["seed@1"]
    assert result["diagnostic"] == {
        "active_dispositions": 1,
        "selected_disposition_refs": ["seed@1"],
        "rendered_disposition_refs": ["seed@1"],
    }


def test_instance_result_resume_is_atomic_and_identity_strict(tmp_path):
    from memory_core_eval.cupid_runner import freeze_result, load_frozen_result

    instance = CupidInstance(
        instance_ref="cupid-" + "c" * 64,
        persona_id="persona",
        current_request="request",
        history_sessions=(),
    )
    label = CupidLabel(
        instance_ref=instance.instance_ref,
        persona_id="persona",
        split="H1",
        instance_type="changing",
        preference="preference",
        checklist=("criterion",),
    )
    path = tmp_path / "result.json"
    result = {
        "protocol": "cupid-seed-generation-v1",
        "split": "H1",
        "persona_id": "persona",
        "instance_ref": instance.instance_ref,
        "answers": [],
    }

    assert load_frozen_result(path, instance=instance, label=label) is None
    freeze_result(path, result)
    assert load_frozen_result(path, instance=instance, label=label) == result

    changed = dict(result, split="H2")
    with pytest.raises(ValueError, match="result drift"):
        freeze_result(path, changed)


def test_owner_scope_uses_only_the_opaque_instance_identity():
    from memory_core_eval.cupid_runner import owner_scope

    scope = owner_scope("cupid-dev-v1", "cupid-" + "d" * 64)

    assert scope.tenant_ref == "cupid-seed-eval"
    assert scope.agent_ref == "cupid-reference-agent"
    assert scope.relationship_ref == "cupid-dev-v1:instance:" + "d" * 64
    assert scope.session_ref == ""
    assert scope.kind == pb.MEMORY_SCOPE_KIND_RELATIONSHIP
