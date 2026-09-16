from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import grpc

from memory_core_worker.service import InferenceService
from memory_core_worker.source_materials import recollection_materials
from memory_core_worker.v1 import inference_pb2, inference_pb2_grpc


_TAGGED_TEXT = (
    "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT\n验证跨进程协议\n"
    "BASIS\nepisode-python-one\nepisode-python-two"
)


class SmokeTextModel:
    async def complete(self, text: str) -> str:
        required = (
            "ALLOWED_TARGET\nNEW_RECOLLECTION",
            "ALLOWED_BASIS\nepisode-python-one",
            "ALLOWED_BASIS\nepisode-python-two",
            "EPISODE episode-python-one\nSITUATION\n验证跨进程协议",
        )
        if "job-python-smoke" in text or any(value not in text for value in required):
            raise RuntimeError("Core request was not mapped to bounded tagged text")
        return _TAGGED_TEXT


class FixtureInferenceService(inference_pb2_grpc.InferenceWorkerServicer):
    def __init__(self) -> None:
        self._default = InferenceService(SmokeTextModel())

    async def ProcessConsolidationWindow(self, request, context):
        if request.job_ref != "job-python-source-material":
            return await self._default.ProcessConsolidationWindow(request, context)

        materials = recollection_materials(request)
        if len(materials) != 1:
            raise RuntimeError("source fixture expected one current USER material")
        material = materials[0]
        if (
            material.episode_ref != "episode-python-current"
            or material.source_ref != "source-python-current"
            or material.basis_refs != ("episode-python-current",)
            or "用户也喜欢安静的茶馆。" not in material.context_text
            or "锚点不可成为新记忆 Basis。" in material.context_text
            or "Outcome 不可成为新记忆 Basis。" in material.context_text
        ):
            raise RuntimeError("typed source material crossed its intended boundary")
        return inference_pb2.TaggedTextResponse(
            tagged_text=material.bind_recollection("用户喜欢茶。")
        )


async def run(address_file: Path) -> None:
    server = grpc.aio.server()
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(
        FixtureInferenceService(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    if port == 0:
        raise RuntimeError("could not bind smoke Worker")
    await server.start()
    address_file.write_text(f"127.0.0.1:{port}")
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=0)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: fake_worker_server.py ADDRESS_FILE")
    asyncio.run(run(Path(sys.argv[1])))
