from __future__ import annotations

from dataclasses import dataclass

import pytest

from memory_core_eval.openai_agents_runner import run_openai_agents_request
from memory_core_eval.runner_protocol import RunnerProtocolError, RunnerRequest


class FakeModelSettings:
    def __init__(self, *, max_tokens: int) -> None:
        self.max_tokens = max_tokens


class FakeAgent:
    last: FakeAgent | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        FakeAgent.last = self


@dataclass
class FakeResult:
    final_output: object


class FakeRunner:
    output: object = "oolong"
    calls: list[tuple[object, object, dict[str, object]]] = []

    @classmethod
    async def run(cls, agent: object, input_text: object, **options: object) -> FakeResult:
        cls.calls.append((agent, input_text, options))
        return FakeResult(final_output=cls.output)


class FakeSDK:
    Agent = FakeAgent
    ModelSettings = FakeModelSettings
    Runner = FakeRunner


def request() -> RunnerRequest:
    return RunnerRequest(
        model="gpt-fixed",
        instructions="Only answer from memory.",
        input_text="What tea?",
        max_output_tokens=64,
    )


@pytest.fixture(autouse=True)
def reset_fakes() -> None:
    FakeAgent.last = None
    FakeRunner.calls = []
    FakeRunner.output = "oolong"


async def test_openai_agents_runner_is_a_single_turn_text_bridge() -> None:
    output = await run_openai_agents_request(request(), sdk=FakeSDK)

    assert output == "oolong"
    assert FakeAgent.last is not None
    assert FakeAgent.last.kwargs["name"] == "Memory Core Eval"
    assert FakeAgent.last.kwargs["model"] == "gpt-fixed"
    assert FakeAgent.last.kwargs["instructions"] == "Only answer from memory."
    settings = FakeAgent.last.kwargs["model_settings"]
    assert isinstance(settings, FakeModelSettings)
    assert settings.max_tokens == 64
    assert FakeRunner.calls[0][1] == "What tea?"
    assert FakeRunner.calls[0][2] == {"max_turns": 1}


async def test_openai_agents_runner_rejects_non_text_output() -> None:
    FakeRunner.output = {"structured": "not allowed"}

    with pytest.raises(RunnerProtocolError, match="non-empty text"):
        await run_openai_agents_request(request(), sdk=FakeSDK)
