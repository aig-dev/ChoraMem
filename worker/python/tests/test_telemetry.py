import asyncio
import json

import pytest

from memory_core_worker.service import InferenceService
from memory_core_worker.telemetry import LangfuseTelemetry
from memory_core_worker.v1.inference_pb2 import ProcessConsolidationWindowRequest


class Model:
    async def complete(self, text):
        await asyncio.sleep(0)
        return "NO_CHANGE"


@pytest.mark.asyncio
async def test_concurrent_window_traces_are_isolated_and_contain_no_private_text():
    batches = []
    telemetry = LangfuseTelemetry("http://localhost:3001", "public", "secret", "model", sender=batches.append)
    service = InferenceService(Model(), telemetry=telemetry)
    await asyncio.gather(*[
        service.ProcessConsolidationWindow(ProcessConsolidationWindowRequest(
            job_ref=f"private-job-{index}", window_text="private source text",
            allowed_basis_refs=["private-basis"], allowed_target_refs=["private-target"],
        ), None) for index in range(2)
    ])
    assert len(batches) == 2
    traces = []
    for batch in batches:
        payload = json.dumps(batch)
        for private in ("private-job", "private source text", "private-basis", "private-target", "NO_CHANGE", "secret"):
            assert private not in payload
        trace = next(event["body"] for event in batch if event["type"] == "trace-create")
        traces.append(trace["id"])
        generations = [event["body"] for event in batch if event["type"] == "generation-create"]
        assert generations and all(generation["traceId"] == trace["id"] for generation in generations)
        assert all("input" not in generation and "output" not in generation for generation in generations)
        assert trace["metadata"]["worker_result"] == "no_change"
        assert {"lane": "primary", "status": "no_change", "count": 0} in trace["metadata"]["diagnostics"]
    assert len(set(traces)) == 2


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_change_worker_result(caplog):
    def broken(batch):
        raise RuntimeError("private exporter credentials")
    telemetry = LangfuseTelemetry("http://localhost:3001", "public", "secret", "model", sender=broken)
    result = await InferenceService(Model(), telemetry=telemetry).ProcessConsolidationWindow(
        ProcessConsolidationWindowRequest(job_ref="job", window_text="source"), None,
    )
    assert result.tagged_text == ""
    assert "telemetry_unavailable" in caplog.text
    assert "private exporter credentials" not in caplog.text


@pytest.mark.asyncio
async def test_model_failure_is_recorded_without_raw_exception():
    class Broken:
        async def complete(self, text):
            raise RuntimeError("private provider error")
    batches = []
    telemetry = LangfuseTelemetry("http://localhost:3001", "public", "secret", "model", sender=batches.append)
    with pytest.raises(RuntimeError, match="may be retried"):
        await InferenceService(Broken(), telemetry=telemetry).ProcessConsolidationWindow(
            ProcessConsolidationWindowRequest(job_ref="job", window_text="source"), None,
        )
    assert "private provider error" not in json.dumps(batches)
    assert any(event["body"].get("level") == "ERROR" for event in batches[0])


def test_telemetry_requires_explicit_enable_and_complete_configuration():
    assert LangfuseTelemetry.from_environment({}) is None
    assert LangfuseTelemetry.from_environment({"MEMORY_WORKER_LANGFUSE_ENABLED": "true"}) is None
    assert LangfuseTelemetry.from_environment({"MEMORY_WORKER_LANGFUSE_ENABLED": "true", "LANGFUSE_HOST": "http://[malformed"}) is None
