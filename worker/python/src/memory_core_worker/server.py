from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os

import grpc

from .model import OpenAIResponsesTextModel, TextModel
from .service import DEFAULT_MAX_MODEL_INPUT_BYTES, InferenceService
from .v1 import inference_pb2_grpc


@dataclass(frozen=True, slots=True)
class Settings:
    grpc_address: str
    openai_model: str
    max_model_input_bytes: int
    reasoning_effort: str | None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] = os.environ) -> Settings:
        return _validated_settings(
            grpc_address=environment.get(
                "MEMORY_WORKER_GRPC_ADDR", "127.0.0.1:8082"
            ),
            openai_model=environment.get("MEMORY_WORKER_OPENAI_MODEL", ""),
            max_model_input_bytes=environment.get(
                "MEMORY_WORKER_MAX_INPUT_BYTES", str(DEFAULT_MAX_MODEL_INPUT_BYTES)
            ),
            reasoning_effort=environment.get("MEMORY_WORKER_REASONING_EFFORT", ""),
        )


async def serve(
    model: TextModel, grpc_address: str, max_model_input_bytes: int
) -> None:
    server = grpc.aio.server()
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(
        InferenceService(model, max_model_input_bytes=max_model_input_bytes), server
    )
    if server.add_insecure_port(grpc_address) == 0:
        raise RuntimeError(f"could not bind gRPC server to {grpc_address}")
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=5)


def parse_settings(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] = os.environ,
) -> Settings:
    parser = argparse.ArgumentParser(
        description="Memory Core stateless reference inference Worker"
    )
    parser.add_argument(
        "--listen",
        default=environment.get("MEMORY_WORKER_GRPC_ADDR", "127.0.0.1:8082"),
    )
    parser.add_argument(
        "--model", default=environment.get("MEMORY_WORKER_OPENAI_MODEL", "")
    )
    parser.add_argument(
        "--max-input-bytes",
        default=environment.get(
            "MEMORY_WORKER_MAX_INPUT_BYTES", str(DEFAULT_MAX_MODEL_INPUT_BYTES)
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        default=environment.get("MEMORY_WORKER_REASONING_EFFORT", ""),
    )
    args = parser.parse_args(argv)
    return _validated_settings(
        grpc_address=args.listen,
        openai_model=args.model,
        max_model_input_bytes=args.max_input_bytes,
        reasoning_effort=args.reasoning_effort,
    )


def main(argv: Sequence[str] | None = None) -> None:
    settings = parse_settings(argv)
    model = OpenAIResponsesTextModel(
        settings.openai_model,
        reasoning_effort=settings.reasoning_effort,
    )
    try:
        asyncio.run(
            serve(model, settings.grpc_address, settings.max_model_input_bytes)
        )
    except KeyboardInterrupt:
        pass


def _validated_settings(
    *,
    grpc_address: str,
    openai_model: str,
    max_model_input_bytes: str,
    reasoning_effort: str,
) -> Settings:
    grpc_address = grpc_address.strip()
    openai_model = openai_model.strip()
    if not grpc_address:
        raise ValueError("MEMORY_WORKER_GRPC_ADDR must not be empty")
    if not openai_model:
        raise ValueError("MEMORY_WORKER_OPENAI_MODEL is required")
    try:
        parsed_max_input_bytes = int(max_model_input_bytes)
    except ValueError as error:
        raise ValueError("MEMORY_WORKER_MAX_INPUT_BYTES must be a positive integer") from error
    if parsed_max_input_bytes <= 0:
        raise ValueError("MEMORY_WORKER_MAX_INPUT_BYTES must be a positive integer")
    return Settings(
        grpc_address=grpc_address,
        openai_model=openai_model,
        max_model_input_bytes=parsed_max_input_bytes,
        reasoning_effort=reasoning_effort.strip() or None,
    )
