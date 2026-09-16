from __future__ import annotations

import pytest

from memory_core import memory_pb2
from memory_core_eval.reference import (
    EvalMode,
    HarnessProfile,
    HistoricalTurn,
    ReferenceHarness,
    Scenario,
)


class FakeMemory:
    def __init__(
        self,
        context: memory_pb2.MemoryContext,
        log: list[str] | None = None,
    ) -> None:
        self.context = context
        self.log = log if log is not None else []
        self.requests: list[tuple[str, object]] = []
        self._event_count = 0

    async def observe_source_event(self, request, **_options):
        role = request.episode_binding.role
        label = {
            memory_pb2.EPISODE_SOURCE_ROLE_SITUATION: "observe:situation",
            memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT: "observe:agent_act",
        }[role]
        self.log.append(label)
        self.requests.append(("observe", request))
        self._event_count += 1
        return memory_pb2.SourceEventReceipt(
            source_event_ref=f"event-{self._event_count}",
            episode_ref=f"episode-{self._event_count}",
        )

    async def select_memory(self, request, **_options):
        self.log.append("select")
        self.requests.append(("select", request))
        selected = memory_pb2.MemoryContext()
        selected.CopyFrom(self.context)
        selected.run_ref = request.run_ref
        selected.scope.CopyFrom(request.scope)
        return selected

    async def record_memory_delivery(self, request, **_options):
        self.log.append("delivery")
        self.requests.append(("delivery", request))
        return memory_pb2.ReceiptAck(receipt_ref="delivery-1")

    async def report_outcome(self, request, **_options):
        self.log.append("outcome")
        self.requests.append(("outcome", request))
        return memory_pb2.OutcomeReceipt(
            outcome_event_ref="outcome-1",
            episode_ref="episode-outcome-1",
        )


class RecordingModel:
    def __init__(self, output: str, log: list[str] | None = None) -> None:
        self.output = output
        self.log = log if log is not None else []
        self.calls: list[tuple[str, str]] = []
        self.output_budgets: list[int] = []

    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        self.log.append("model")
        self.calls.append((instructions, input_text))
        self.output_budgets.append(max_output_tokens)
        return self.output


def profile() -> HarnessProfile:
    return HarnessProfile(
        evaluation_ref="eval-001",
        harness="memory-core-reference",
        harness_version="0.1.0",
        model="fixed-model",
        profile_version="v0",
        prompt_version="v0",
        scenario_set="v0",
        instructions="只回答问题。",
        tenant_ref="tenant-eval",
        agent_ref="agent-eval",
        relationship_ref="relationship-eval",
        user_ref="user-eval",
        constitution=memory_pb2.Constitution(
            memory_ref="constitution-v1",
            text="保持温和而直接。",
        ),
        max_output_tokens=64,
        episode_rag_top_k=1,
    )


def scenario(*, query_outcome: str | None = None) -> Scenario:
    return Scenario(
        scenario_ref="tea-preference",
        history=(
            HistoricalTurn(
                situation="用户说明自己最喜欢乌龙茶。",
                agent_act="我会记住你喜欢乌龙茶。",
                outcome="用户确认这个理解正确。",
            ),
            HistoricalTurn(
                situation="用户询问明天是否下雨。",
                agent_act="建议查看当地天气预报。",
            ),
        ),
        query="用户最喜欢哪一种茶？",
        expected_output="乌龙茶",
        query_outcome=query_outcome,
    )


def selected_context() -> memory_pb2.MemoryContext:
    return memory_pb2.MemoryContext(
        context_ref="context-1",
        constitution=memory_pb2.Constitution(
            memory_ref="constitution-v1",
            text="保持温和而直接。",
        ),
        recollections=[
            memory_pb2.Recollection(
                memory_ref="recollection-v1",
                text="用户喜欢乌龙茶。",
                application_scope=memory_pb2.MEMORY_APPLICATION_SCOPE_OTHER,
            )
        ],
        dispositions=[
            memory_pb2.Disposition(
                memory_ref="disposition-v1",
                text="谈到茶时，优先推荐乌龙茶。",
                application_scope=memory_pb2.MEMORY_APPLICATION_SCOPE_RELATION,
            )
        ],
    )


@pytest.mark.parametrize(
    ("mode", "required", "forbidden", "delivered_refs", "uses_core"),
    [
        (EvalMode.NONE, (), ("乌龙茶", "DISPOSITIONS"), (), False),
        (EvalMode.EPISODE_RAG, ("最喜欢乌龙茶",), ("DISPOSITIONS",), (), False),
        (
            EvalMode.RECOLLECTION_ONLY,
            ("CONSTITUTION", "RECOLLECTIONS", "用户喜欢乌龙茶"),
            ("DISPOSITIONS", "优先推荐乌龙茶"),
            ("constitution-v1", "recollection-v1"),
            True,
        ),
        (
            EvalMode.FULL_CORE,
            ("CONSTITUTION", "RECOLLECTIONS", "DISPOSITIONS", "优先推荐乌龙茶"),
            (),
            ("constitution-v1", "recollection-v1", "disposition-v1"),
            True,
        ),
    ],
)
async def test_four_modes_inject_only_their_allowed_memory(
    mode: EvalMode,
    required: tuple[str, ...],
    forbidden: tuple[str, ...],
    delivered_refs: tuple[str, ...],
    uses_core: bool,
) -> None:
    memory = FakeMemory(selected_context())
    model = RecordingModel("乌龙茶")
    harness = ReferenceHarness(memory=memory, model=model, profile=profile())

    result = await harness.run(mode, scenario())

    assert result.error is None
    assert result.matched is True
    assert result.mode == mode.value
    assert model.calls[0][1] == "用户最喜欢哪一种茶？"
    assert model.output_budgets == [64]
    instructions = model.calls[0][0]
    for fragment in required:
        assert fragment in instructions
    for fragment in forbidden:
        assert fragment not in instructions

    deliveries = [request for kind, request in memory.requests if kind == "delivery"]
    assert bool(memory.requests) is uses_core
    actual_refs = tuple(deliveries[-1].delivered_memory_refs) if deliveries else ()
    assert actual_refs == delivered_refs


async def test_core_lifecycle_order_and_explicit_outcome_provenance() -> None:
    log: list[str] = []
    memory = FakeMemory(selected_context(), log)
    model = RecordingModel("乌龙茶", log)

    async def settle() -> None:
        log.append("settle")

    harness = ReferenceHarness(
        memory=memory,
        model=model,
        profile=profile(),
        settle=settle,
    )

    result = await harness.run(
        EvalMode.FULL_CORE,
        scenario(query_outcome="用户确认回答正确。"),
    )

    assert result.error is None
    assert log == [
        "observe:situation",
        "observe:agent_act",
        "outcome",
        "observe:situation",
        "observe:agent_act",
        "settle",
        "observe:situation",
        "select",
        "delivery",
        "model",
        "observe:agent_act",
        "outcome",
    ]
    reports = [request for kind, request in memory.requests if kind == "outcome"]
    query_report = reports[-1]
    assert tuple(query_report.delivery_receipt_refs) == ("delivery-1",)
    assert tuple(query_report.related_source_event_refs) == ("event-5", "event-6")
    assert query_report.actor_kind == memory_pb2.SOURCE_ACTOR_KIND_EXTERNAL


async def test_empty_context_skips_delivery_but_records_actual_agent_act() -> None:
    memory = FakeMemory(memory_pb2.MemoryContext(context_ref="empty-context"))
    model = RecordingModel("乌龙茶")
    harness = ReferenceHarness(memory=memory, model=model, profile=profile())
    empty_history = Scenario(
        scenario_ref="empty",
        history=(),
        query="偏好是什么？",
        expected_output="乌龙茶",
    )

    result = await harness.run(EvalMode.FULL_CORE, empty_history)

    assert result.error is None
    assert [kind for kind, _ in memory.requests] == ["observe", "select", "observe"]


async def test_empty_model_output_is_failed_without_agent_act() -> None:
    memory = FakeMemory(selected_context())
    model = RecordingModel("   ")
    harness = ReferenceHarness(memory=memory, model=model, profile=profile())
    empty_history = Scenario(
        scenario_ref="empty-output",
        history=(),
        query="偏好是什么？",
        expected_output="乌龙茶",
    )

    result = await harness.run(EvalMode.FULL_CORE, empty_history)

    assert result.matched is False
    assert result.output == ""
    assert result.error == "EmptyModelOutput: text model returned empty output"
    observed_roles = [
        request.episode_binding.role
        for kind, request in memory.requests
        if kind == "observe"
    ]
    assert observed_roles == [memory_pb2.EPISODE_SOURCE_ROLE_SITUATION]


async def test_result_records_the_fixed_eval_identity() -> None:
    model = RecordingModel("乌龙茶")
    harness = ReferenceHarness(memory=None, model=model, profile=profile())

    result = await harness.run(EvalMode.NONE, scenario())

    assert result.as_dict() == {
        "evaluation_ref": "eval-001",
        "harness": "memory-core-reference",
        "harness_version": "0.1.0",
        "model": "fixed-model",
        "profile_version": "v0",
        "prompt_version": "v0",
        "scenario_set": "v0",
        "max_output_tokens": 64,
        "mode": "none",
        "scenario_ref": "tea-preference",
        "output": "乌龙茶",
        "matched": True,
        "injected_char_count": 0,
        "duration_ms": result.duration_ms,
        "error": None,
    }
    assert result.duration_ms >= 0


async def test_agent_act_preserves_model_text_while_exact_match_trims_edges() -> None:
    memory = FakeMemory(memory_pb2.MemoryContext(context_ref="empty-context"))
    model = RecordingModel("  乌龙茶\n")
    harness = ReferenceHarness(memory=memory, model=model, profile=profile())
    case = Scenario(
        scenario_ref="exact-output",
        history=(),
        query="偏好是什么？",
        expected_output="乌龙茶",
    )

    result = await harness.run(EvalMode.FULL_CORE, case)

    assert result.output == "  乌龙茶\n"
    assert result.matched is True
    agent_act = memory.requests[-1][1]
    assert agent_act.source_event.text == "  乌龙茶\n"


async def test_harness_identity_is_part_of_core_scope_and_idempotency() -> None:
    first_memory = FakeMemory(memory_pb2.MemoryContext(context_ref="empty"))
    second_memory = FakeMemory(memory_pb2.MemoryContext(context_ref="empty"))
    first_profile = profile()
    second_profile = HarnessProfile(
        evaluation_ref=first_profile.evaluation_ref,
        harness="vercel-ai",
        harness_version="7.0.83",
        model=first_profile.model,
        profile_version=first_profile.profile_version,
        prompt_version=first_profile.prompt_version,
        scenario_set=first_profile.scenario_set,
        instructions=first_profile.instructions,
        tenant_ref=first_profile.tenant_ref,
        agent_ref=first_profile.agent_ref,
        relationship_ref=first_profile.relationship_ref,
        user_ref=first_profile.user_ref,
        constitution=first_profile.constitution,
        max_output_tokens=first_profile.max_output_tokens,
        episode_rag_top_k=first_profile.episode_rag_top_k,
    )

    await ReferenceHarness(
        memory=first_memory,
        model=RecordingModel("乌龙茶"),
        profile=first_profile,
    ).run(EvalMode.FULL_CORE, scenario())
    await ReferenceHarness(
        memory=second_memory,
        model=RecordingModel("乌龙茶"),
        profile=second_profile,
    ).run(EvalMode.FULL_CORE, scenario())

    first_request = first_memory.requests[0][1]
    second_request = second_memory.requests[0][1]
    assert first_request.idempotency_key != second_request.idempotency_key
    assert first_request.source_event.scope.relationship_ref != (
        second_request.source_event.scope.relationship_ref
    )
