from __future__ import annotations

import io
from pathlib import Path

import pytest

from memory_core_eval.cli import ConfigError
from memory_core_eval.matrix_cli import (
    MATRIX_REQUIRED_ENV,
    build_drivers,
    load_matrix_config,
    main,
)


def valid_environment(tmp_path: Path) -> dict[str, str]:
    eval_root = Path(__file__).parents[2]
    runner = tmp_path / "vercel-ai-runner.mjs"
    runner.write_text("// test runner\n", encoding="utf-8")
    return {
        "MEMORY_CORE_ENDPOINT": "http://127.0.0.1:8081",
        "MEMORY_CORE_TOKEN": "token",
        "MEMORY_EVAL_MODEL": "gpt-fixed",
        "MEMORY_EVAL_PROFILE": str(eval_root / "profiles" / "v0.json"),
        "MEMORY_EVAL_VERCEL_RUNNER": str(runner),
        "OPENAI_API_KEY": "openai-key",
        "AI_GATEWAY_API_KEY": "gateway-key",
        "MEMORY_EVAL_REF": "matrix-1",
    }


def test_matrix_config_loads_one_profile_and_two_thin_drivers(
    tmp_path: Path,
) -> None:
    config = load_matrix_config(valid_environment(tmp_path))

    assert config.identity.evaluation_ref == "matrix-1"
    assert config.identity.model == "gpt-fixed"
    assert config.profile.profile_version == "v0"
    assert config.profile.max_output_tokens == 64
    drivers = build_drivers(config)
    assert [(driver.harness, driver.version) for driver in drivers] == [
        ("openai-agents", "0.22.0"),
        ("vercel-ai", "7.0.83"),
    ]
    assert drivers[0].command[-2:] == (
        "-m",
        "memory_core_eval.openai_agents_runner",
    )
    assert drivers[1].command == ("node", str(config.vercel_runner))


def test_matrix_config_requires_both_harness_credentials() -> None:
    with pytest.raises(ConfigError) as error:
        load_matrix_config({})

    assert str(error.value) == (
        "missing required environment: " + ", ".join(MATRIX_REQUIRED_ENV)
    )


def test_matrix_main_fails_closed_before_execution_when_config_is_missing() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(environ={}, stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "missing required environment" in stderr.getvalue()
