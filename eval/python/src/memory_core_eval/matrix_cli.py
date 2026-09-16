from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from memory_core import AsyncMemoryClient

from .cli import (
    ConfigError,
    EvalProfile,
    load_eval_profile,
    load_scenarios,
    results_exit_code,
    write_results,
)
from .matrix import HarnessDriver, MatrixIdentity, run_matrix


OPENAI_AGENTS_VERSION = "0.22.0"
VERCEL_AI_VERSION = "7.0.83"
MATRIX_SETTLE_SECONDS = 5.0
MATRIX_REQUIRED_ENV = (
    "MEMORY_CORE_ENDPOINT",
    "MEMORY_CORE_TOKEN",
    "MEMORY_EVAL_MODEL",
    "MEMORY_EVAL_PROFILE",
    "MEMORY_EVAL_VERCEL_RUNNER",
    "OPENAI_API_KEY",
    "AI_GATEWAY_API_KEY",
)


@dataclass(frozen=True, slots=True)
class MatrixConfig:
    endpoint: str
    token: str
    profile: EvalProfile
    identity: MatrixIdentity
    vercel_runner: Path
    node_command: str
    settle_seconds: float


def load_matrix_config(environ: Mapping[str, str]) -> MatrixConfig:
    missing = [key for key in MATRIX_REQUIRED_ENV if not environ.get(key, "").strip()]
    if missing:
        raise ConfigError("missing required environment: " + ", ".join(missing))

    profile = load_eval_profile(Path(environ["MEMORY_EVAL_PROFILE"]).expanduser())
    vercel_runner = Path(environ["MEMORY_EVAL_VERCEL_RUNNER"]).expanduser().resolve()
    if not vercel_runner.is_file():
        raise ConfigError(f"Vercel AI runner does not exist: {vercel_runner}")
    try:
        settle_seconds = float(
            environ.get("MEMORY_EVAL_SETTLE_SECONDS", str(MATRIX_SETTLE_SECONDS))
        )
    except ValueError as exc:
        raise ConfigError("MEMORY_EVAL_SETTLE_SECONDS must be numeric") from exc
    if settle_seconds < 0:
        raise ConfigError("MEMORY_EVAL_SETTLE_SECONDS must not be negative")

    return MatrixConfig(
        endpoint=environ["MEMORY_CORE_ENDPOINT"].strip(),
        token=environ["MEMORY_CORE_TOKEN"].strip(),
        profile=profile,
        identity=MatrixIdentity(
            evaluation_ref=environ.get("MEMORY_EVAL_REF", "").strip()
            or uuid.uuid4().hex,
            model=environ["MEMORY_EVAL_MODEL"].strip(),
            tenant_ref=environ.get("MEMORY_EVAL_TENANT_REF", "memory-core-eval").strip(),
            agent_ref=environ.get("MEMORY_EVAL_AGENT_REF", "matrix-agent").strip(),
            relationship_ref=environ.get(
                "MEMORY_EVAL_RELATIONSHIP_REF", "matrix-relationship"
            ).strip(),
            user_ref=environ.get("MEMORY_EVAL_USER_REF", "matrix-user").strip(),
        ),
        vercel_runner=vercel_runner,
        node_command=environ.get("MEMORY_EVAL_NODE", "node").strip(),
        settle_seconds=settle_seconds,
    )


def build_drivers(config: MatrixConfig) -> tuple[HarnessDriver, HarnessDriver]:
    return (
        HarnessDriver(
            harness="openai-agents",
            version=OPENAI_AGENTS_VERSION,
            command=(
                sys.executable,
                "-m",
                "memory_core_eval.openai_agents_runner",
            ),
        ),
        HarnessDriver(
            harness="vercel-ai",
            version=VERCEL_AI_VERSION,
            command=(config.node_command, str(config.vercel_runner)),
        ),
    )


async def _run_live(config: MatrixConfig, output: TextIO) -> int:
    scenarios = load_scenarios(config.profile.scenarios)
    async with AsyncMemoryClient.connect(
        config.endpoint,
        token_provider=lambda _tenant_ref: config.token,
    ) as memory:
        results = await run_matrix(
            memory=memory,
            shared_profile=config.profile,
            scenarios=scenarios,
            identity=config.identity,
            drivers=build_drivers(config),
            settle=lambda: asyncio.sleep(config.settle_seconds),
        )
    write_results(results, output)
    return results_exit_code(results)


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
        config = load_matrix_config(environment)
        return asyncio.run(_run_live(config, output))
    except KeyboardInterrupt:
        errors.write("evaluation interrupted\n")
        return 130
    except Exception as exc:
        errors.write(f"evaluation failed: {exc}\n")
        return 2 if isinstance(exc, ConfigError) else 1


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
