from __future__ import annotations

from typing import Protocol


class TextModel(Protocol):
    """One stateless, completed plain-text result; raise on interrupted output."""

    async def complete(self, text: str) -> str: ...


class _ResponsesResource(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class _ResponsesClient(Protocol):
    responses: _ResponsesResource


class OpenAIResponsesTextModel:
    """Optional OpenAI Responses API implementation of :class:`TextModel`."""

    def __init__(
        self,
        model: str,
        *,
        client: _ResponsesClient | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model is required")
        self._model = model
        self._client = client
        self._reasoning_effort = reasoning_effort

    async def complete(self, text: str) -> str:
        client = self._client
        if client is None:
            client = _new_openai_client()
            self._client = client

        request: dict[str, object] = dict(
            model=self._model,
            input=text,
            store=False,
        )
        if self._reasoning_effort is not None:
            request["reasoning"] = {"effort": self._reasoning_effort}
        response = await client.responses.create(**request)
        status = getattr(response, "status", None)
        if status != "completed":
            raise RuntimeError(f"OpenAI response was not completed: {status}")
        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str):
            raise RuntimeError("OpenAI response did not contain plain text")
        return output_text


def _new_openai_client() -> _ResponsesClient:
    try:
        from openai import AsyncOpenAI
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "OpenAI backend requires the 'openai' optional dependency"
        ) from error
    return AsyncOpenAI()
