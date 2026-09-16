from __future__ import annotations

import asyncio
import inspect

import pytest

from memory_core import memory_pb2
from memory_core_eval.memoryagentbench import (
    MEMORY_AGENT_BENCH_COMMIT,
    MemoryAgentBenchAdapter,
)
from memory_core_eval.reference import HarnessProfile


class FakeMemory:
    def __init__(self, context: memory_pb2.MemoryContext) -> None:
        self.context = context
        self.calls: list[tuple[str, object]] = []
        self._event_count = 0

    async def observe_source_event(self, request, **_options):
        self.calls.append(("observe", request))
        self._event_count += 1
        return memory_pb2.SourceEventReceipt(
            source_event_ref=f"source-{self._event_count}",
            episode_ref=f"episode-{self._event_count}",
        )

    async def select_memory(self, request, **_options):
        self.calls.append(("select", request))
        selected = memory_pb2.MemoryContext()
        selected.CopyFrom(self.context)
        selected.scope.CopyFrom(request.scope)
        selected.run_ref = request.run_ref
        return selected

    async def record_memory_delivery(self, request, **_options):
        self.calls.append(("delivery", request))
        return memory_pb2.ReceiptAck(receipt_ref="delivery-1")

    async def report_outcome(self, request, **_options):
        raise AssertionError(f"benchmark adapter must not fabricate Outcome: {request}")


class RecordingModel:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[tuple[str, str]] = []
        self.output_budgets: list[int] = []

    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        self.calls.append((instructions, input_text))
        self.output_budgets.append(max_output_tokens)
        return self.output


def profile() -> HarnessProfile:
    return HarnessProfile(
        evaluation_ref="mab-run-1",
        harness="memory-agent-bench",
        harness_version=MEMORY_AGENT_BENCH_COMMIT,
        model="fixed-model",
        profile_version="memoryagentbench-v0",
        prompt_version="v0",
        scenario_set="memoryagentbench",
        instructions="根据记忆回答问题。",
        tenant_ref="tenant",
        agent_ref="agent",
        relationship_ref="benchmark-relationship",
        user_ref="benchmark-user",
        constitution=memory_pb2.Constitution(),
        max_output_tokens=64,
    )


def context() -> memory_pb2.MemoryContext:
    return memory_pb2.MemoryContext(
        context_ref="context-1",
        recollections=[
            memory_pb2.Recollection(
                memory_ref="recollection-1",
                text="Alice 的生日是 4 月 2 日。",
                application_scope=memory_pb2.MEMORY_APPLICATION_SCOPE_OTHER,
            )
        ],
    )


def adapter(
    memory: FakeMemory,
    model: RecordingModel,
) -> MemoryAgentBenchAdapter:
    return MemoryAgentBenchAdapter(
        memory_factory=lambda: memory,
        model_factory=lambda: model,
        profile=profile(),
        benchmark_context_ref="context-7",
        count_tokens=len,
        settle_seconds=0,
    )


def test_send_message_signature_matches_the_pinned_upstream_contract() -> None:
    assert MEMORY_AGENT_BENCH_COMMIT == "fe1735de8cf8b9908e1e3d3b5612afc815698062"
    parameters = inspect.signature(MemoryAgentBenchAdapter.send_message).parameters

    assert list(parameters) == [
        "self",
        "message",
        "memorizing",
        "query_id",
        "context_id",
    ]
    assert parameters["memorizing"].default is False
    assert parameters["query_id"].default is None
    assert parameters["context_id"].default is None


def test_memorizing_records_a_complete_episode_without_calling_the_model() -> None:
    memory = FakeMemory(context())
    model = RecordingModel("April 2")
    benchmark = adapter(memory, model)

    result = benchmark.send_message("Alice's birthday is April 2.", memorizing=True)

    assert result == "Memorized"
    assert [kind for kind, _ in memory.calls] == ["observe", "observe"]
    situation = memory.calls[0][1]
    agent_act = memory.calls[1][1]
    assert situation.source_event.text == "Alice's birthday is April 2."
    assert situation.source_event.actor_kind == memory_pb2.SOURCE_ACTOR_KIND_USER
    assert situation.episode_binding.role == memory_pb2.EPISODE_SOURCE_ROLE_SITUATION
    assert agent_act.source_event.text == "Memorized"
    assert agent_act.source_event.actor_kind == memory_pb2.SOURCE_ACTOR_KIND_AGENT
    assert agent_act.source_event.actor_ref == "agent"
    assert agent_act.episode_binding.role == memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT
    assert agent_act.episode_binding.run_ref == situation.episode_binding.run_ref
    assert (
        agent_act.episode_binding.source_group_ref
        == situation.episode_binding.source_group_ref
    )
    assert model.calls == []


def test_query_runs_select_delivery_model_and_actual_agent_act_without_outcome() -> None:
    memory = FakeMemory(context())
    model = RecordingModel("April 2")
    benchmark = adapter(memory, model)
    benchmark.send_message("Alice's birthday is April 2.", memorizing=True)

    result = benchmark.send_message(
        "When is Alice's birthday?",
        memorizing=False,
        query_id=11,
        context_id=7,
    )

    assert list(result) == [
        "output",
        "input_len",
        "output_len",
        "memory_construction_time",
        "query_time_len",
    ]
    assert result["output"] == "April 2"
    assert result["output_len"] == len("April 2")
    assert result["memory_construction_time"] >= 0
    assert result["query_time_len"] >= 0
    assert [kind for kind, _ in memory.calls] == [
        "observe",
        "observe",
        "observe",
        "select",
        "delivery",
        "observe",
    ]

    delivery = next(request for kind, request in memory.calls if kind == "delivery")
    assert tuple(delivery.delivered_memory_refs) == ("recollection-1",)
    assert "Alice 的生日" in model.calls[0][0]
    assert model.calls[0][1] == "When is Alice's birthday?"
    assert model.output_budgets == [64]
    agent_act = memory.calls[-1][1]
    assert agent_act.source_event.text == "April 2"
    assert agent_act.episode_binding.role == memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT
    assert result["input_len"] == len(model.calls[0][0] + "\n" + model.calls[0][1])


def test_query_with_empty_context_does_not_write_delivery() -> None:
    memory = FakeMemory(memory_pb2.MemoryContext(context_ref="empty"))
    model = RecordingModel("unknown")
    benchmark = adapter(memory, model)

    result = benchmark.send_message("What is remembered?", query_id=1, context_id=7)

    assert result["output"] == "unknown"
    assert [kind for kind, _ in memory.calls] == ["observe", "select", "observe"]


def test_empty_model_output_does_not_record_agent_act() -> None:
    memory = FakeMemory(context())
    model = RecordingModel("  ")
    benchmark = adapter(memory, model)

    with pytest.raises(TypeError, match="non-empty text"):
        benchmark.send_message("When is Alice's birthday?", query_id=1, context_id=7)

    observed_roles = [
        request.episode_binding.role
        for kind, request in memory.calls
        if kind == "observe"
    ]
    assert observed_roles == [memory_pb2.EPISODE_SOURCE_ROLE_SITUATION]


def test_benchmark_agent_act_preserves_the_model_output_exactly() -> None:
    memory = FakeMemory(memory_pb2.MemoryContext(context_ref="empty"))
    model = RecordingModel("  April 2\n")
    benchmark = adapter(memory, model)

    result = benchmark.send_message("When is it?", query_id=1, context_id=7)

    assert result["output"] == "  April 2\n"
    agent_act = memory.calls[-1][1]
    assert agent_act.source_event.text == "  April 2\n"


def test_all_async_resources_stay_on_one_owned_event_loop() -> None:
    created: list[LoopBoundMemory] = []

    def memory_factory() -> LoopBoundMemory:
        memory = LoopBoundMemory(context())
        created.append(memory)
        return memory

    benchmark = MemoryAgentBenchAdapter(
        memory_factory=memory_factory,
        model_factory=lambda: RecordingModel("April 2"),
        profile=profile(),
        benchmark_context_ref="context-7",
        count_tokens=len,
        settle_seconds=0,
    )

    benchmark.send_message("Alice's birthday is April 2.", memorizing=True)
    benchmark.send_message("When is it?", query_id=1, context_id=7)
    benchmark.close()

    assert len(created) == 1
    assert created[0].closed is True


class LoopBoundMemory(FakeMemory):
    def __init__(self, selected: memory_pb2.MemoryContext) -> None:
        super().__init__(selected)
        self.loop = asyncio.get_running_loop()
        self.closed = False

    def _check_loop(self) -> None:
        assert asyncio.get_running_loop() is self.loop

    async def observe_source_event(self, request, **options):
        self._check_loop()
        return await super().observe_source_event(request, **options)

    async def select_memory(self, request, **options):
        self._check_loop()
        return await super().select_memory(request, **options)

    async def record_memory_delivery(self, request, **options):
        self._check_loop()
        return await super().record_memory_delivery(request, **options)

    async def close(self) -> None:
        self._check_loop()
        self.closed = True
