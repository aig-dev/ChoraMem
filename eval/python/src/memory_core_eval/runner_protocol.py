from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TextIO


class RunnerProtocolError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    model: str
    instructions: str
    input_text: str
    max_output_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("model", "instructions", "input_text"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise RunnerProtocolError(f"{field_name} must be nonblank text")
        if (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or self.max_output_tokens <= 0
        ):
            raise RunnerProtocolError("max_output_tokens must be a positive integer")

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True) + "\n"

    @classmethod
    def from_json_line(cls, line: str) -> RunnerRequest:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunnerProtocolError(f"invalid runner request JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise RunnerProtocolError("runner request must be a JSON object")
        expected = {"model", "instructions", "input_text", "max_output_tokens"}
        if set(value) != expected:
            raise RunnerProtocolError(
                f"runner request fields must equal {sorted(expected)!r}"
            )
        try:
            return cls(
                model=value["model"],
                instructions=value["instructions"],
                input_text=value["input_text"],
                max_output_tokens=value["max_output_tokens"],
            )
        except (TypeError, RunnerProtocolError) as exc:
            if isinstance(exc, RunnerProtocolError):
                raise
            raise RunnerProtocolError(f"invalid runner request: {exc}") from exc


@dataclass(frozen=True, slots=True)
class RunnerResponse:
    output_text: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        has_output = isinstance(self.output_text, str) and bool(self.output_text.strip())
        has_error = isinstance(self.error, str) and bool(self.error.strip())
        if has_output == has_error:
            raise RunnerProtocolError(
                "runner response must contain exactly one nonblank output_text or error"
            )

    def to_json_line(self) -> str:
        value = (
            {"output_text": self.output_text}
            if self.output_text is not None
            else {"error": self.error}
        )
        return json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"

    @classmethod
    def from_json_line(cls, line: str) -> RunnerResponse:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunnerProtocolError(f"invalid runner response JSON: {exc}") from exc
        if not isinstance(value, dict) or set(value) not in (
            {"output_text"},
            {"error"},
        ):
            raise RunnerProtocolError(
                "runner response must be an object with output_text or error"
            )
        return cls(
            output_text=value.get("output_text"),
            error=value.get("error"),
        )


class StdioTextModel:
    """Invoke one Harness-native text runner through a one-line NDJSON boundary."""

    def __init__(self, command: Sequence[str], *, model: str) -> None:
        if not command or any(not part for part in command):
            raise ValueError("runner command must be non-empty")
        if not model.strip():
            raise ValueError("model must be nonblank")
        self._command = tuple(command)
        self._model = model

    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        request = RunnerRequest(
            model=self._model,
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
        )
        process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(request.to_json_line().encode())
        output_lines = [line for line in stdout.decode().splitlines() if line.strip()]
        if len(output_lines) != 1:
            detail = stderr.decode().strip() or stdout.decode().strip()
            raise RunnerProtocolError(
                f"runner returned {len(output_lines)} response lines: {detail}"
            )
        response = RunnerResponse.from_json_line(output_lines[0])
        if response.error is not None:
            raise RunnerProtocolError(response.error)
        if process.returncode != 0:
            detail = stderr.decode().strip() or f"exit {process.returncode}"
            raise RunnerProtocolError(f"runner failed: {detail}")
        assert response.output_text is not None
        return response.output_text


def run_stdio_once(
    handler: Callable[[RunnerRequest], Awaitable[str]],
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    source = sys.stdin if stdin is None else stdin
    destination = sys.stdout if stdout is None else stdout
    try:
        request = RunnerRequest.from_json_line(source.readline())
        output = asyncio.run(handler(request))
        response = RunnerResponse(output_text=output)
        exit_code = 0
    except Exception as exc:
        response = RunnerResponse(error=f"{type(exc).__name__}: {exc}")
        exit_code = 1
    destination.write(response.to_json_line())
    destination.flush()
    return exit_code
