from __future__ import annotations

import json
import sys

import pytest

from memory_core_eval.runner_protocol import (
    RunnerProtocolError,
    RunnerRequest,
    StdioTextModel,
)


def request() -> RunnerRequest:
    return RunnerRequest(
        model="fixed-model",
        instructions="Only answer from memory.",
        input_text="What tea?",
        max_output_tokens=64,
    )


def test_runner_request_is_one_flat_json_object() -> None:
    encoded = request().to_json_line()

    assert json.loads(encoded) == {
        "model": "fixed-model",
        "instructions": "Only answer from memory.",
        "input_text": "What tea?",
        "max_output_tokens": 64,
    }
    assert RunnerRequest.from_json_line(encoded) == request()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"model": "m", "instructions": "i", "input_text": "q", "max_output_tokens": 0},
        {"model": "m", "instructions": "i", "input_text": "q", "max_output_tokens": True},
    ],
)
def test_runner_request_fails_closed_on_invalid_shape(payload: dict[str, object]) -> None:
    with pytest.raises(RunnerProtocolError):
        RunnerRequest.from_json_line(json.dumps(payload))


async def test_stdio_text_model_sends_budget_and_returns_plain_text() -> None:
    child = (
        "import json,sys; "
        "r=json.loads(sys.stdin.readline()); "
        "print(json.dumps({'output_text': "
        "r['model'] + ':' + str(r['max_output_tokens']) + ':' + r['input_text']}))"
    )
    model = StdioTextModel((sys.executable, "-c", child), model="fixed-model")

    output = await model.complete(
        instructions="Only answer from memory.",
        input_text="What tea?",
        max_output_tokens=64,
    )

    assert output == "fixed-model:64:What tea?"


async def test_stdio_text_model_surfaces_runner_error() -> None:
    child = "import json; print(json.dumps({'error': 'provider failed'}))"
    model = StdioTextModel((sys.executable, "-c", child), model="fixed-model")

    with pytest.raises(RunnerProtocolError, match="provider failed"):
        await model.complete(
            instructions="i",
            input_text="q",
            max_output_tokens=64,
        )
