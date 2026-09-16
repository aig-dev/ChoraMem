from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from memory_core import AsyncMemoryClient, memory_pb2


def scope(tenant_ref: str = "tenant-1") -> memory_pb2.MemoryScope:
    return memory_pb2.MemoryScope(
        tenant_ref=tenant_ref,
        agent_ref="agent-1",
        kind=memory_pb2.MEMORY_SCOPE_KIND_AGENT,
    )


def rpc_cases() -> list[tuple[str, str, Any, Any]]:
    return [
        (
            "observe_source_event",
            "/memory.v1.MemoryCore/ObserveSourceEvent",
            memory_pb2.ObserveSourceEventRequest(
                source_event=memory_pb2.SourceEvent(scope=scope())
            ),
            memory_pb2.SourceEventReceipt(),
        ),
        (
            "select_memory",
            "/memory.v1.MemoryCore/SelectMemory",
            memory_pb2.SelectMemoryRequest(scope=scope()),
            memory_pb2.MemoryContext(),
        ),
        (
            "record_memory_delivery",
            "/memory.v1.MemoryCore/RecordMemoryDelivery",
            memory_pb2.MemoryDeliveryReceipt(scope=scope()),
            memory_pb2.ReceiptAck(),
        ),
        (
            "report_outcome",
            "/memory.v1.MemoryCore/ReportOutcome",
            memory_pb2.ReportOutcomeRequest(scope=scope()),
            memory_pb2.OutcomeReceipt(),
        ),
    ]


@pytest.mark.asyncio
async def test_four_rpcs_use_exact_tenant_token_and_default_deadline() -> None:
    all_cases = rpc_cases()
    channel = FakeChannel({path: response for _, path, _, response in all_cases})
    tenants: list[str] = []

    async def token_provider(tenant_ref: str) -> str:
        tenants.append(tenant_ref)
        return f"token:{tenant_ref}"

    client = AsyncMemoryClient(
        channel,
        token_provider=token_provider,
        default_timeout=7.0,
    )

    for method, path, request, response in all_cases:
        assert await getattr(client, method)(request) is response
        assert channel.calls[path].kwargs[-1] == {
            "timeout": 7.0,
            "metadata": (("authorization", "Bearer token:tenant-1"),),
        }

    assert tenants == ["tenant-1"] * 4


@pytest.mark.asyncio
async def test_tenant_identity_rejects_only_blank_and_preserves_exact_value() -> None:
    channel = channel_with_default_responses()
    tenants: list[str] = []
    client = AsyncMemoryClient(
        channel,
        token_provider=lambda tenant_ref: tenants.append(tenant_ref) or "token",
    )

    exact_tenant = " tenant with spaces "
    await client.select_memory(
        memory_pb2.SelectMemoryRequest(scope=scope(exact_tenant))
    )

    assert tenants == [exact_tenant]
    for blank_tenant in ("", "   ", "\u2003"):
        with pytest.raises(ValueError, match="tenant_ref"):
            await client.select_memory(
                memory_pb2.SelectMemoryRequest(scope=scope(blank_tenant))
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["two words", "line\nbreak", "unicode\u2003space"])
async def test_bearer_token_rejects_any_unicode_whitespace(token: str) -> None:
    channel = channel_with_default_responses()
    client = AsyncMemoryClient(channel, token_provider=lambda _: token)

    with pytest.raises(ValueError, match="whitespace"):
        await client.select_memory(memory_pb2.SelectMemoryRequest(scope=scope()))

    assert channel.calls["/memory.v1.MemoryCore/SelectMemory"].requests == []


@pytest.mark.asyncio
async def test_call_deadline_is_bounded_by_default_and_auth_is_sdk_owned() -> None:
    path = "/memory.v1.MemoryCore/SelectMemory"
    channel = channel_with_default_responses()
    client = AsyncMemoryClient(
        channel,
        token_provider=lambda _: "right",
        default_timeout=9.0,
    )
    request = memory_pb2.SelectMemoryRequest(scope=scope())

    await client.select_memory(
        request,
        timeout=2.0,
        metadata=(("trace", "x"), ("authorization", "Bearer wrong")),
    )
    await client.select_memory(request, timeout=20.0)

    assert channel.calls[path].kwargs == [
        {
            "timeout": 2.0,
            "metadata": (("trace", "x"), ("authorization", "Bearer right")),
        },
        {
            "timeout": 9.0,
            "metadata": (("authorization", "Bearer right"),),
        },
    ]


@pytest.mark.asyncio
async def test_validation_and_token_provider_fail_before_transport() -> None:
    path = "/memory.v1.MemoryCore/SelectMemory"
    channel = channel_with_default_responses()

    client = AsyncMemoryClient(channel, token_provider=lambda _: "token")
    with pytest.raises(ValueError, match="tenant_ref"):
        await client.select_memory(memory_pb2.SelectMemoryRequest())
    with pytest.raises(ValueError, match="timeout"):
        await client.select_memory(
            memory_pb2.SelectMemoryRequest(scope=scope()),
            timeout=0,
        )

    def fail(_: str) -> str:
        raise RuntimeError("identity unavailable")

    failing = AsyncMemoryClient(channel, token_provider=fail)
    with pytest.raises(RuntimeError, match="identity unavailable"):
        await failing.select_memory(
            memory_pb2.SelectMemoryRequest(scope=scope())
        )

    assert channel.calls[path].requests == []


def test_endpoint_normalization() -> None:
    assert AsyncMemoryClient.normalize_endpoint("http://localhost:50051/") == (
        "localhost:50051",
        False,
    )
    assert AsyncMemoryClient.normalize_endpoint("https://memory.example.com") == (
        "memory.example.com:443",
        True,
    )
    assert AsyncMemoryClient.normalize_endpoint("localhost:50051") == (
        "localhost:50051",
        False,
    )
    with pytest.raises(ValueError):
        AsyncMemoryClient.normalize_endpoint("https://example.com/path")


@pytest.mark.asyncio
async def test_caller_owned_close_is_noop() -> None:
    channel = channel_with_default_responses()
    client = AsyncMemoryClient(channel, token_provider=lambda _: "token")

    await client.close()
    await client.select_memory(memory_pb2.SelectMemoryRequest(scope=scope()))

    assert channel.close_count == 0


@pytest.mark.asyncio
async def test_connected_client_owns_and_closes_its_channel(monkeypatch: Any) -> None:
    channel = channel_with_default_responses()
    monkeypatch.setattr(
        "memory_core.client.grpc.aio.insecure_channel",
        lambda _: channel,
    )
    client = AsyncMemoryClient.connect(
        "localhost:50051",
        token_provider=lambda _: "token",
    )

    await client.close()
    await client.close()

    assert channel.close_count == 1
    with pytest.raises(RuntimeError, match="closed"):
        await client.select_memory(
            memory_pb2.SelectMemoryRequest(scope=scope())
        )


def test_active_sources_do_not_expose_retired_contract() -> None:
    root = Path(__file__).parents[1]
    active = [root / "src", root / "README.md", root / "examples"]
    text = "\n".join(
        path.read_text()
        for item in active
        for path in ([item] if item.is_file() else item.rglob("*.py"))
    )
    for retired in (
        "AssemblePersona",
        "assemble_persona",
        "PersonaContext",
        "ContextDelivery",
        "record_context_delivery",
    ):
        assert retired not in text


class FakeUnaryCall:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.requests: list[Any] = []
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, request: Any, **kwargs: Any) -> Any:
        self.requests.append(request)
        self.kwargs.append(kwargs)
        return self.response


class FakeChannel:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: dict[str, FakeUnaryCall] = {}
        self.close_count = 0

    def unary_unary(self, path: str, **_: Any) -> Callable[..., Any]:
        if path not in self.calls:
            self.calls[path] = FakeUnaryCall(self.responses[path])
        return self.calls[path]

    async def close(self) -> None:
        self.close_count += 1


def channel_with_default_responses() -> FakeChannel:
    return FakeChannel({path: response for _, path, _, response in rpc_cases()})
