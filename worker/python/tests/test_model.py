from __future__ import annotations

from types import SimpleNamespace

import pytest

from memory_core_worker.model import OpenAIResponsesTextModel


class RecordingResponses:
    def __init__(self, output_text: object, status: str | None = "completed") -> None:
        self.output_text = output_text
        self.status = status
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text, status=self.status)


class FakeOpenAIClient:
    def __init__(self, output_text: object, status: str | None = "completed") -> None:
        self.responses = RecordingResponses(output_text, status)


@pytest.mark.asyncio
async def test_openai_backend_uses_one_plain_text_response_call() -> None:
    tagged_text = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT 简洁回答\nBASIS\nepisode-1\nepisode-2"
    client = FakeOpenAIClient(tagged_text)
    model = OpenAIResponsesTextModel("test-model", client=client)

    result = await model.complete("RULE\n只返回 tagged text\nWINDOW\n原始窗口")

    assert result == tagged_text
    assert client.responses.calls == [
        {
            "model": "test-model",
            "input": "RULE\n只返回 tagged text\nWINDOW\n原始窗口",
            "store": False,
        }
    ]


@pytest.mark.asyncio
async def test_openai_backend_passes_explicit_reasoning_effort() -> None:
    client = FakeOpenAIClient("NO_CHANGE")
    model = OpenAIResponsesTextModel(
        "test-model", client=client, reasoning_effort="none"
    )

    await model.complete("WINDOW\n原始窗口")

    assert client.responses.calls == [
        {
            "model": "test-model",
            "input": "WINDOW\n原始窗口",
            "store": False,
            "reasoning": {"effort": "none"},
        }
    ]


@pytest.mark.asyncio
async def test_openai_backend_rejects_a_non_text_result() -> None:
    model = OpenAIResponsesTextModel("test-model", client=FakeOpenAIClient(None))

    with pytest.raises(RuntimeError, match="plain text"):
        await model.complete("WINDOW\n原始窗口")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress", "queued", None])
async def test_unfinished_response_is_an_error_even_when_partial_text_looks_valid(status) -> None:
    model = OpenAIResponsesTextModel(
        "test-model", client=FakeOpenAIClient("NO_CHANGE", status=status)
    )

    with pytest.raises(RuntimeError, match="not completed"):
        await model.complete("WINDOW\n原始窗口")
