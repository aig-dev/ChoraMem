from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import grpc

from .v1 import memory_pb2, memory_pb2_grpc


TokenProvider = Callable[[str], str | Awaitable[str]]


class AsyncMemoryClient:
    """Authenticated async client for the four public Memory Core RPCs."""

    def __init__(
        self,
        channel: Any,
        *,
        token_provider: TokenProvider,
        default_timeout: float = 10.0,
        _owns_channel: bool = False,
    ) -> None:
        if default_timeout <= 0:
            raise ValueError("default_timeout must be positive")
        self._channel = channel
        self._stub = memory_pb2_grpc.MemoryCoreStub(channel)
        self._token_provider = token_provider
        self._default_timeout = default_timeout
        self._owns_channel = _owns_channel
        self._closed = False

    @classmethod
    def connect(
        cls,
        endpoint: str,
        *,
        token_provider: TokenProvider,
        default_timeout: float = 10.0,
    ) -> AsyncMemoryClient:
        target, secure = cls.normalize_endpoint(endpoint)
        if secure:
            channel = grpc.aio.secure_channel(
                target,
                grpc.ssl_channel_credentials(),
            )
        else:
            channel = grpc.aio.insecure_channel(target)
        return cls(
            channel,
            token_provider=token_provider,
            default_timeout=default_timeout,
            _owns_channel=True,
        )

    @staticmethod
    def normalize_endpoint(endpoint: str) -> tuple[str, bool]:
        value = endpoint.strip()
        parsed = urlsplit(value if "://" in value else f"http://{value}")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("endpoint must be an http(s) host and optional port")
        port = parsed.port or (443 if parsed.scheme == "https" else None)
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        target = f"{host}:{port}" if port else host
        return target, parsed.scheme == "https"

    async def observe_source_event(
        self,
        request: memory_pb2.ObserveSourceEventRequest,
        **options: Any,
    ) -> memory_pb2.SourceEventReceipt:
        return await self._call(
            self._stub.ObserveSourceEvent,
            request,
            request.source_event.scope.tenant_ref,
            options,
        )

    async def select_memory(
        self,
        request: memory_pb2.SelectMemoryRequest,
        **options: Any,
    ) -> memory_pb2.MemoryContext:
        return await self._call(
            self._stub.SelectMemory,
            request,
            request.scope.tenant_ref,
            options,
        )

    async def record_memory_delivery(
        self,
        request: memory_pb2.MemoryDeliveryReceipt,
        **options: Any,
    ) -> memory_pb2.ReceiptAck:
        return await self._call(
            self._stub.RecordMemoryDelivery,
            request,
            request.scope.tenant_ref,
            options,
        )

    async def report_outcome(
        self,
        request: memory_pb2.ReportOutcomeRequest,
        **options: Any,
    ) -> memory_pb2.OutcomeReceipt:
        return await self._call(
            self._stub.ReportOutcome,
            request,
            request.scope.tenant_ref,
            options,
        )

    async def _call(
        self,
        rpc: Any,
        request: Any,
        tenant_ref: str,
        options: dict[str, Any],
    ) -> Any:
        if self._closed:
            raise RuntimeError("Memory Core client is closed")
        if not isinstance(tenant_ref, str) or not tenant_ref.strip():
            raise ValueError("request requires a nonblank tenant_ref")

        timeout = _bounded_timeout(
            self._default_timeout,
            options.pop("timeout", None),
        )
        token = self._token_provider(tenant_ref)
        if inspect.isawaitable(token):
            token = await token
        _validate_bearer_token(token)

        metadata: Sequence[tuple[str, str]] = options.pop("metadata", ())
        caller_metadata = tuple(
            (key, value)
            for key, value in metadata
            if key.lower() != "authorization"
        )
        options["metadata"] = caller_metadata + (
            ("authorization", f"Bearer {token}"),
        )
        options["timeout"] = timeout
        return await rpc(request, **options)

    async def close(self) -> None:
        if self._owns_channel and not self._closed:
            self._closed = True
            await self._channel.close()

    async def __aenter__(self) -> AsyncMemoryClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


def _bounded_timeout(default_timeout: float, requested_timeout: Any) -> float:
    if requested_timeout is None:
        return default_timeout
    if not isinstance(requested_timeout, (int, float)) or requested_timeout <= 0:
        raise ValueError("timeout must be positive")
    return min(default_timeout, float(requested_timeout))


def _validate_bearer_token(token: Any) -> None:
    if not isinstance(token, str) or not token:
        raise ValueError("token provider returned a blank token")
    if any(character.isspace() for character in token):
        raise ValueError("Bearer token must not contain Unicode whitespace")
