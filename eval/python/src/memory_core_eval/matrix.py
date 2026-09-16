from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from memory_core import memory_pb2

from .cli import EvalProfile, run_suite
from .reference import (
    EvalResult,
    HarnessProfile,
    MemoryClient,
    ReferenceHarness,
    Scenario,
    TextModel,
)
from .runner_protocol import StdioTextModel


@dataclass(frozen=True, slots=True)
class HarnessDriver:
    harness: str
    version: str
    command: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.harness.strip():
            raise ValueError("harness must be nonblank")
        if not self.version.strip():
            raise ValueError("version must be nonblank")
        if not self.command or any(not part for part in self.command):
            raise ValueError("command must be non-empty")


@dataclass(frozen=True, slots=True)
class MatrixIdentity:
    evaluation_ref: str
    model: str
    tenant_ref: str
    agent_ref: str
    relationship_ref: str
    user_ref: str

    def __post_init__(self) -> None:
        for field_name in (
            "evaluation_ref",
            "model",
            "tenant_ref",
            "agent_ref",
            "relationship_ref",
            "user_ref",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be nonblank")


async def _already_settled() -> None:
    return None


async def run_matrix(
    *,
    memory: MemoryClient,
    shared_profile: EvalProfile,
    scenarios: Sequence[Scenario],
    identity: MatrixIdentity,
    drivers: Sequence[HarnessDriver],
    model_factory: Callable[[HarnessDriver], TextModel] | None = None,
    settle: Callable[[], Awaitable[None]] = _already_settled,
) -> list[EvalResult]:
    if not drivers:
        raise ValueError("matrix requires at least one harness driver")
    harness_names = [driver.harness for driver in drivers]
    if len(harness_names) != len(set(harness_names)):
        raise ValueError("matrix harness names must be unique")

    create_model = model_factory or (
        lambda driver: StdioTextModel(driver.command, model=identity.model)
    )
    results: list[EvalResult] = []
    for driver in drivers:
        harness_profile = HarnessProfile(
            evaluation_ref=identity.evaluation_ref,
            harness=driver.harness,
            harness_version=driver.version,
            model=identity.model,
            profile_version=shared_profile.profile_version,
            prompt_version=shared_profile.prompt_version,
            scenario_set=shared_profile.scenario_set,
            instructions=shared_profile.instructions,
            tenant_ref=identity.tenant_ref,
            agent_ref=identity.agent_ref,
            relationship_ref=identity.relationship_ref,
            user_ref=identity.user_ref,
            constitution=memory_pb2.Constitution(
                memory_ref=shared_profile.constitution_memory_ref,
                text=shared_profile.constitution_text,
            ),
            max_output_tokens=shared_profile.max_output_tokens,
            episode_rag_top_k=shared_profile.episode_rag_top_k,
        )
        harness = ReferenceHarness(
            memory=memory,
            model=create_model(driver),
            profile=harness_profile,
            settle=settle,
        )
        results.extend(await run_suite(harness, scenarios))
    return results
