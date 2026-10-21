import asyncio
import socket
from contextlib import suppress

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
import pytest

from memory_core_worker.server import serve


@pytest.mark.asyncio
async def test_health_probe_does_not_invoke_model():
    class Model:
        async def complete(self, text):
            raise AssertionError("health check must not invoke model")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    address = f"127.0.0.1:{port}"
    task = asyncio.create_task(serve(Model(), address, 262144))
    try:
        async with grpc.aio.insecure_channel(address) as channel:
            await asyncio.wait_for(channel.channel_ready(), 2)
            response = await health_pb2_grpc.HealthStub(channel).Check(
                health_pb2.HealthCheckRequest(service="memory.inference.v1.InferenceWorker"), timeout=2,
            )
            assert response.status == health_pb2.HealthCheckResponse.SERVING
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
