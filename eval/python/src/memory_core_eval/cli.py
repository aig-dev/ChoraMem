from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from memory_core import AsyncMemoryClient, memory_pb2

from .reference import (
    EvalMode,
    EvalResult,
    HarnessProfile,
    HistoricalTurn,
    ReferenceHarness,
    Scenario,
)


REFERENCE_HARNESS = "memory-core-reference"
REFERENCE_HARNESS_VERSION = "0.1.0"
REFERENCE_PROMPT_VERSION = "v0"
REFERENCE_INSTRUCTIONS = "请直接完成用户要求；只使用确实提供给你的上下文。"
REFERENCE_CONSTITUTION = "保持温和、直接，不虚构没有提供的信息。"
REFERENCE_SETTLE_SECONDS = 5.0
REQUIRED_LIVE_ENV = (
    "MEMORY_CORE_ENDPOINT",
    "MEMORY_CORE_TOKEN",
    "MEMORY_EVAL_MODEL",
    "MEMORY_EVAL_SCENARIOS",
    "OPENAI_API_KEY",
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvalProfile:
    profile_version: str
    scenario_set: str
    scenarios: Path
    prompt_version: str
    instructions: str
    constitution_memory_ref: str
    constitution_text: str
    max_output_tokens: int
    episode_rag_top_k: int
    modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LiveConfig:
    endpoint: str
    token: str
    model: str
    scenarios: Path
    api_key: str
    base_url: str | None
    evaluation_ref: str
    tenant_ref: str
    agent_ref: str
    relationship_ref: str
    user_ref: str


def load_eval_profile(path: Path) -> EvalProfile:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot load eval profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("eval profile must be a JSON object")

    try:
        scenario_file = _required_text(value, "scenario_file")
        scenarios = (path.parent / scenario_file).resolve()
        if not scenarios.is_file():
            raise ValueError(f"scenario file does not exist: {scenarios}")
        max_output_tokens = value.get("max_output_tokens")
        if not isinstance(max_output_tokens, int) or isinstance(
            max_output_tokens, bool
        ) or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        episode_rag_top_k = value.get("episode_rag_top_k")
        if not isinstance(episode_rag_top_k, int) or isinstance(
            episode_rag_top_k, bool
        ) or episode_rag_top_k <= 0:
            raise ValueError("episode_rag_top_k must be a positive integer")
        raw_modes = value.get("modes")
        if not isinstance(raw_modes, list) or not all(
            isinstance(mode, str) for mode in raw_modes
        ):
            raise ValueError("modes must be a list of strings")
        modes = tuple(raw_modes)
        expected_modes = tuple(mode.value for mode in EvalMode)
        if modes != expected_modes:
            raise ValueError(f"modes must equal {expected_modes!r}")
        return EvalProfile(
            profile_version=_required_text(value, "profile_version"),
            scenario_set=_required_text(value, "scenario_set"),
            scenarios=scenarios,
            prompt_version=_required_text(value, "prompt_version"),
            instructions=_required_text(value, "instructions"),
            constitution_memory_ref=_required_text(
                value, "constitution_memory_ref"
            ),
            constitution_text=_required_text(value, "constitution_text"),
            max_output_tokens=max_output_tokens,
            episode_rag_top_k=episode_rag_top_k,
            modes=modes,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid eval profile {path}: {exc}") from exc


def load_live_config(environ: Mapping[str, str]) -> LiveConfig:
    missing = [key for key in REQUIRED_LIVE_ENV if not environ.get(key, "").strip()]
    if missing:
        raise ConfigError("missing required environment: " + ", ".join(missing))

    scenarios = Path(environ["MEMORY_EVAL_SCENARIOS"]).expanduser()
    if not scenarios.is_file():
        raise ConfigError(f"scenario file does not exist: {scenarios}")
    base_url = environ.get("OPENAI_BASE_URL", "").strip() or None
    return LiveConfig(
        endpoint=environ["MEMORY_CORE_ENDPOINT"].strip(),
        token=environ["MEMORY_CORE_TOKEN"].strip(),
        model=environ["MEMORY_EVAL_MODEL"].strip(),
        scenarios=scenarios,
        api_key=environ["OPENAI_API_KEY"].strip(),
        base_url=base_url,
        evaluation_ref=environ.get("MEMORY_EVAL_REF", "").strip()
        or uuid.uuid4().hex,
        tenant_ref=environ.get("MEMORY_EVAL_TENANT_REF", "memory-core-eval").strip(),
        agent_ref=environ.get("MEMORY_EVAL_AGENT_REF", "reference-agent").strip(),
        relationship_ref=environ.get(
            "MEMORY_EVAL_RELATIONSHIP_REF", "reference-relationship"
        ).strip(),
        user_ref=environ.get("MEMORY_EVAL_USER_REF", "reference-user").strip(),
    )


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    scenarios: list[Scenario] = []
    seen_refs: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, raw_line in enumerate(source, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
                if not isinstance(value, dict):
                    raise ValueError("case must be an object")
                scenario_ref = _required_text(value, "scenario_ref")
                if scenario_ref in seen_refs:
                    raise ValueError(f"duplicate scenario_ref {scenario_ref!r}")
                history_value = value.get("history")
                if not isinstance(history_value, list):
                    raise ValueError("history must be a list")
                history = tuple(_load_turn(turn) for turn in history_value)
                query_outcome = _optional_text(value, "query_outcome")
                scenario = Scenario(
                    scenario_ref=scenario_ref,
                    history=history,
                    query=_required_text(value, "query"),
                    expected_output=_required_text(value, "expected_output"),
                    query_outcome=query_outcome,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid scenario at line {line_number}: {exc}") from exc
            seen_refs.add(scenario_ref)
            scenarios.append(scenario)
    if not scenarios:
        raise ValueError("scenario file contains no cases")
    return tuple(scenarios)


def _load_turn(value: object) -> HistoricalTurn:
    if not isinstance(value, dict):
        raise ValueError("history item must be an object")
    return HistoricalTurn(
        situation=_required_text(value, "situation"),
        agent_act=_required_text(value, "agent_act"),
        outcome=_optional_text(value, "outcome"),
    )


def _required_text(value: Mapping[str, object], key: str) -> str:
    text = value.get(key)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} must be nonblank text")
    return text.strip()


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    text = value.get(key)
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} must be nonblank text when present")
    return text.strip()


async def run_suite(
    harness: ReferenceHarness,
    scenarios: Sequence[Scenario],
) -> list[EvalResult]:
    results: list[EvalResult] = []
    for scenario in scenarios:
        for mode in EvalMode:
            results.append(await harness.run(mode, scenario))
    return results


def write_results(results: Sequence[EvalResult], output: TextIO) -> None:
    for result in results:
        output.write(
            json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        )


def results_exit_code(results: Sequence[EvalResult]) -> int:
    return 1 if any(result.error is not None for result in results) else 0


class OpenAITextModel:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None,
        reasoning_effort: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ConfigError(
                "live evaluation requires the eval package's 'live' extra"
            ) from exc
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        request: dict[str, object] = {
            "model": self._model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_output_tokens,
        }
        if self._reasoning_effort is not None:
            request["reasoning"] = {"effort": self._reasoning_effort}
        response = await self._client.responses.create(**request)
        output = getattr(response, "output_text", "")
        return output if isinstance(output, str) else ""

    async def close(self) -> None:
        await self._client.close()


async def _run_live(config: LiveConfig, output: TextIO) -> int:
    scenarios = load_scenarios(config.scenarios)
    model = OpenAITextModel(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )
    try:
        async with AsyncMemoryClient.connect(
            config.endpoint,
            token_provider=lambda _tenant_ref: config.token,
        ) as memory:
            profile = HarnessProfile(
                evaluation_ref=config.evaluation_ref,
                harness=REFERENCE_HARNESS,
                harness_version=REFERENCE_HARNESS_VERSION,
                model=config.model,
                profile_version="legacy-live",
                prompt_version=REFERENCE_PROMPT_VERSION,
                scenario_set="custom",
                instructions=REFERENCE_INSTRUCTIONS,
                tenant_ref=config.tenant_ref,
                agent_ref=config.agent_ref,
                relationship_ref=config.relationship_ref,
                user_ref=config.user_ref,
                constitution=memory_pb2.Constitution(
                    memory_ref="eval-constitution-v0",
                    text=REFERENCE_CONSTITUTION,
                ),
                max_output_tokens=64,
            )
            harness = ReferenceHarness(
                memory=memory,
                model=model,
                profile=profile,
                settle=lambda: asyncio.sleep(REFERENCE_SETTLE_SECONDS),
            )
            results = await run_suite(harness, scenarios)
            write_results(results, output)
            return results_exit_code(results)
    finally:
        await model.close()


def main(
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    environment = os.environ if environ is None else environ
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        config = load_live_config(environment)
        return asyncio.run(_run_live(config, output))
    except KeyboardInterrupt:
        errors.write("evaluation interrupted\n")
        return 130
    except Exception as exc:
        errors.write(f"evaluation failed: {exc}\n")
        return 2 if isinstance(exc, ConfigError) else 1


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
