from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path

import pytest

from memory_core import memory_pb2
from memory_core_eval.cli import (
    ConfigError,
    OpenAITextModel,
    load_eval_profile,
    load_live_config,
    load_scenarios,
    main,
    results_exit_code,
    run_suite,
    write_results,
)
from memory_core_eval.reference import (
    EvalMode,
    EvalResult,
    HarnessProfile,
    ReferenceHarness,
)


class FixedModel:
    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        del instructions, input_text, max_output_tokens
        return "乌龙茶"


def fixed_profile() -> HarnessProfile:
    return HarnessProfile(
        evaluation_ref="eval-cli",
        harness="memory-core-reference",
        harness_version="0.1.0",
        model="fixed",
        profile_version="v0",
        prompt_version="v0",
        scenario_set="v0",
        instructions="直接回答。",
        tenant_ref="tenant",
        agent_ref="agent",
        relationship_ref="relationship",
        user_ref="user",
        constitution=memory_pb2.Constitution(),
        max_output_tokens=64,
    )


def test_repository_profile_fixes_every_cross_harness_condition() -> None:
    path = Path(__file__).parents[2] / "profiles" / "v0.json"

    profile = load_eval_profile(path)

    assert profile.profile_version == "v0"
    assert profile.scenario_set == "v0"
    assert profile.scenarios.name == "v0.jsonl"
    assert profile.prompt_version == "v0"
    assert profile.instructions == "请直接完成用户要求；只使用确实提供给你的上下文。"
    assert profile.constitution_text == "保持温和、直接，不虚构没有提供的信息。"
    assert profile.max_output_tokens == 64
    assert profile.modes == (
        "none",
        "episode_rag",
        "recollection_only",
        "full_core",
    )


def test_load_scenarios_reads_the_public_jsonl_shape(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(
            {
                "scenario_ref": "tea",
                "history": [
                    {
                        "situation": "用户喜欢乌龙茶。",
                        "agent_act": "我会记住。",
                        "outcome": "用户确认。",
                    }
                ],
                "query": "喜欢什么茶？",
                "expected_output": "乌龙茶",
                "query_outcome": "回答正确。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_scenarios(path)

    assert len(loaded) == 1
    assert loaded[0].scenario_ref == "tea"
    assert loaded[0].history[0].outcome == "用户确认。"
    assert loaded[0].query_outcome == "回答正确。"


def test_repository_v0_scenarios_are_valid_and_unique() -> None:
    path = Path(__file__).parents[2] / "cases" / "v0.jsonl"

    loaded = load_scenarios(path)

    assert [case.scenario_ref for case in loaded] == [
        "relationship-tea-preference",
        "relationship-planning-boundary",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"scenario_ref": "x", "history": "not-a-list", "query": "q", "expected_output": "a"},
        {"scenario_ref": "x", "history": [{}], "query": "q", "expected_output": "a"},
    ],
)
def test_load_scenarios_fails_closed_on_malformed_cases(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        load_scenarios(path)


async def test_suite_keeps_each_mode_result_and_reports_runtime_errors(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"scenario_ref":"tea","history":[],"query":"喜欢什么茶？","expected_output":"乌龙茶"}\n',
        encoding="utf-8",
    )
    harness = ReferenceHarness(
        memory=None,
        model=FixedModel(),
        profile=fixed_profile(),
    )

    results = await run_suite(harness, load_scenarios(path))

    assert [result.mode for result in results] == [mode.value for mode in EvalMode]
    assert [result.error is None for result in results] == [True, True, False, False]
    assert results_exit_code(results) == 1


def test_unmatched_answer_is_a_score_not_a_runtime_failure() -> None:
    result = EvalResult(
        evaluation_ref="eval",
        harness="harness",
        harness_version="v0",
        model="model",
        profile_version="v0",
        prompt_version="v0",
        scenario_set="v0",
        max_output_tokens=64,
        mode="none",
        scenario_ref="case",
        output="wrong",
        matched=False,
        injected_char_count=0,
        duration_ms=1.0,
        error=None,
    )

    assert results_exit_code([result]) == 0


def test_write_results_emits_machine_readable_jsonl() -> None:
    result = EvalResult(
        evaluation_ref="eval",
        harness="harness",
        harness_version="v0",
        model="model",
        profile_version="v0",
        prompt_version="v0",
        scenario_set="v0",
        max_output_tokens=64,
        mode="none",
        scenario_ref="case",
        output="乌龙茶",
        matched=True,
        injected_char_count=0,
        duration_ms=1.0,
        error=None,
    )
    output = io.StringIO()

    write_results([result], output)

    assert json.loads(output.getvalue()) == result.as_dict()


def test_live_config_requires_every_external_dependency() -> None:
    with pytest.raises(ConfigError) as error:
        load_live_config({})

    assert str(error.value) == (
        "missing required environment: MEMORY_CORE_ENDPOINT, MEMORY_CORE_TOKEN, "
        "MEMORY_EVAL_MODEL, MEMORY_EVAL_SCENARIOS, OPENAI_API_KEY"
    )


def test_main_fails_before_live_execution_when_config_is_missing() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(environ={}, stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "missing required environment" in stderr.getvalue()


async def test_openai_text_model_can_disable_reasoning_without_changing_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeResponses:
        async def create(self, **request: object) -> object:
            requests.append(request)
            return types.SimpleNamespace(output_text="OK")

    class FakeAsyncOpenAI:
        def __init__(self, **_options: object) -> None:
            self.responses = FakeResponses()

        async def close(self) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "openai",
        types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI),
    )
    model = OpenAITextModel(
        model="reasoning-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        reasoning_effort="none",
    )

    output = await model.complete(
        instructions="Answer directly.",
        input_text="ping",
        max_output_tokens=40,
    )

    assert output == "OK"
    assert requests == [
        {
            "model": "reasoning-model",
            "instructions": "Answer directly.",
            "input": "ping",
            "max_output_tokens": 40,
            "reasoning": {"effort": "none"},
        }
    ]
