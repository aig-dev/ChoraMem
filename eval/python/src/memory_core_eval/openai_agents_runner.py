from __future__ import annotations

from typing import Any

from .runner_protocol import (
    RunnerProtocolError,
    RunnerRequest,
    run_stdio_once,
)


async def run_openai_agents_request(
    request: RunnerRequest,
    *,
    sdk: Any | None = None,
) -> str:
    agents_sdk = _load_sdk() if sdk is None else sdk
    agent = agents_sdk.Agent(
        name="Memory Core Eval",
        instructions=request.instructions,
        model=request.model,
        model_settings=agents_sdk.ModelSettings(
            max_tokens=request.max_output_tokens,
        ),
    )
    result = await agents_sdk.Runner.run(
        agent,
        request.input_text,
        max_turns=1,
    )
    output = getattr(result, "final_output", None)
    if not isinstance(output, str) or not output.strip():
        raise RunnerProtocolError(
            "OpenAI Agents runner requires a non-empty text final_output"
        )
    return output


def _load_sdk() -> Any:
    try:
        import agents
    except ImportError as exc:
        raise RunnerProtocolError(
            "OpenAI Agents runner requires the eval package's 'openai-agents' extra"
        ) from exc
    return agents


def main() -> int:
    return run_stdio_once(run_openai_agents_request)


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
