from __future__ import annotations

from pathlib import Path

from memory_core import memory_pb2
from memory_core_eval.cli import load_eval_profile, load_scenarios
from memory_core_eval.matrix import (
    HarnessDriver,
    MatrixIdentity,
    run_matrix,
)


class FakeMemory:
    def __init__(self) -> None:
        self.requests: list[tuple[str, object]] = []
        self.events = 0

    async def observe_source_event(self, request, **_options):
        self.requests.append(("observe", request))
        self.events += 1
        return memory_pb2.SourceEventReceipt(
            source_event_ref=f"event-{self.events}",
            episode_ref=f"episode-{self.events}",
        )

    async def select_memory(self, request, **_options):
        self.requests.append(("select", request))
        return memory_pb2.MemoryContext(
            context_ref=f"context-{len(self.requests)}",
            run_ref=request.run_ref,
            scope=request.scope,
        )

    async def record_memory_delivery(self, request, **_options):
        self.requests.append(("delivery", request))
        return memory_pb2.ReceiptAck(receipt_ref="delivery")

    async def report_outcome(self, request, **_options):
        self.requests.append(("outcome", request))
        return memory_pb2.OutcomeReceipt(
            outcome_event_ref="outcome",
            episode_ref="episode-outcome",
        )


class FixedModel:
    def __init__(self, harness: str) -> None:
        self.harness = harness
        self.calls: list[tuple[str, str, int]] = []

    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        self.calls.append((instructions, input_text, max_output_tokens))
        return "乌龙茶" if "茶" in input_text else "ASK_FIRST"


async def test_matrix_runs_both_harnesses_under_one_fixed_profile() -> None:
    eval_root = Path(__file__).parents[2]
    profile = load_eval_profile(eval_root / "profiles" / "v0.json")
    scenarios = load_scenarios(profile.scenarios)
    memory = FakeMemory()
    drivers = (
        HarnessDriver("openai-agents", "0.22", ("python", "runner.py")),
        HarnessDriver("vercel-ai", "7.0.83", ("node", "runner.mjs")),
    )
    models: dict[str, FixedModel] = {}

    def model_factory(driver: HarnessDriver) -> FixedModel:
        model = FixedModel(driver.harness)
        models[driver.harness] = model
        return model

    results = await run_matrix(
        memory=memory,
        shared_profile=profile,
        scenarios=scenarios,
        identity=MatrixIdentity(
            evaluation_ref="matrix-1",
            model="gpt-fixed",
            tenant_ref="tenant",
            agent_ref="agent",
            relationship_ref="relationship",
            user_ref="user",
        ),
        drivers=drivers,
        model_factory=model_factory,
    )

    assert len(results) == len(drivers) * len(scenarios) * 4
    assert {result.harness for result in results} == {
        "openai-agents",
        "vercel-ai",
    }
    assert {result.mode for result in results} == set(profile.modes)
    assert {result.model for result in results} == {"gpt-fixed"}
    assert {result.profile_version for result in results} == {profile.profile_version}
    assert {result.prompt_version for result in results} == {profile.prompt_version}
    assert {result.scenario_set for result in results} == {profile.scenario_set}
    assert {result.max_output_tokens for result in results} == {64}
    assert all(result.error is None for result in results)
    assert all(result.matched for result in results)
    assert all(
        budget == 64
        for model in models.values()
        for _, _, budget in model.calls
    )

    source_refs = [
        request.source_event.source_ref
        for kind, request in memory.requests
        if kind == "observe"
    ]
    assert any(":openai-agents:" in source_ref for source_ref in source_refs)
    assert any(":vercel-ai:" in source_ref for source_ref in source_refs)
    assert len(source_refs) == len(set(source_refs))
