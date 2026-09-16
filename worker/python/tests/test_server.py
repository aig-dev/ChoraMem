from __future__ import annotations

import pytest

from memory_core_worker.server import Settings, parse_settings


def test_settings_read_explicit_worker_environment() -> None:
    settings = Settings.from_environment(
        {
            "MEMORY_WORKER_GRPC_ADDR": "0.0.0.0:19091",
            "MEMORY_WORKER_OPENAI_MODEL": "test-model",
        }
    )

    assert settings.grpc_address == "0.0.0.0:19091"
    assert settings.openai_model == "test-model"
    assert settings.max_model_input_bytes == 256 * 1024
    assert settings.reasoning_effort is None


def test_settings_read_optional_reasoning_effort() -> None:
    settings = Settings.from_environment(
        {
            "MEMORY_WORKER_OPENAI_MODEL": "test-model",
            "MEMORY_WORKER_REASONING_EFFORT": "none",
        }
    )

    assert settings.reasoning_effort == "none"


def test_settings_require_an_explicit_model() -> None:
    with pytest.raises(ValueError, match="MEMORY_WORKER_OPENAI_MODEL"):
        Settings.from_environment({})


def test_cli_model_can_be_used_without_model_environment() -> None:
    settings = parse_settings(
        ["--listen", "127.0.0.1:29091", "--model", "cli-model"],
        environment={},
    )

    assert settings == Settings(
        grpc_address="127.0.0.1:29091",
        openai_model="cli-model",
        max_model_input_bytes=256 * 1024,
        reasoning_effort=None,
    )


def test_settings_reject_non_positive_input_budget() -> None:
    with pytest.raises(ValueError, match="MEMORY_WORKER_MAX_INPUT_BYTES"):
        Settings.from_environment(
            {
                "MEMORY_WORKER_OPENAI_MODEL": "test-model",
                "MEMORY_WORKER_MAX_INPUT_BYTES": "0",
            }
        )
