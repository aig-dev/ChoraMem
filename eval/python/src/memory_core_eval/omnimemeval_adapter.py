"""Thin OmniMemEval ``add/search`` adapter over the public Memory Core RPCs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Sequence
from typing import Any

import grpc

from memory_core import AsyncMemoryClient, memory_pb2 as pb, memory_pb2_grpc, render_memory_context


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()


def _episode_budget(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("episode evidence bytes must be an integer from 0 to 16384")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("episode evidence bytes must be an integer from 0 to 16384") from error
    if not 0 <= parsed <= 16_384:
        raise ValueError("episode evidence bytes must be an integer from 0 to 16384")
    return parsed


def _rpc_attempts(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("RPC attempts must be a positive integer")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("RPC attempts must be a positive integer") from error
    if parsed <= 0:
        raise ValueError("RPC attempts must be a positive integer")
    return parsed


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


class MemoryCoreClient:
    """OmniMemEval-compatible client; benchmark labels never cross this boundary."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        token: str | None = None,
        tenant_ref: str | None = None,
        agent_ref: str | None = None,
        episode_evidence_max_bytes: int | None = None,
        timeout: float | None = None,
        rpc_attempts: int | None = None,
        _stub: Any | None = None,
    ) -> None:
        self._token = _nonblank(token or os.getenv("MEMORY_CORE_TOKEN"), "MEMORY_CORE_TOKEN")
        self._tenant_ref = _nonblank(
            tenant_ref or os.getenv("MEMORY_CORE_OMNI_TENANT", "omnimemeval"),
            "MEMORY_CORE_OMNI_TENANT",
        )
        self._agent_ref = _nonblank(
            agent_ref or os.getenv("MEMORY_CORE_OMNI_AGENT_REF", "omnimemeval-agent"),
            "MEMORY_CORE_OMNI_AGENT_REF",
        )
        configured_budget = (
            episode_evidence_max_bytes
            if episode_evidence_max_bytes is not None
            else os.getenv("MEMORY_CORE_OMNI_EPISODE_EVIDENCE_BYTES", "16384")
        )
        self._episode_evidence_max_bytes = _episode_budget(configured_budget)
        configured_timeout = (
            timeout
            if timeout is not None
            else os.getenv("MEMORY_CORE_OMNI_TIMEOUT", "30")
        )
        if isinstance(configured_timeout, bool):
            raise ValueError("MEMORY_CORE_OMNI_TIMEOUT must be a positive finite number")
        try:
            self._timeout = float(configured_timeout)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "MEMORY_CORE_OMNI_TIMEOUT must be a positive finite number"
            ) from error
        if not math.isfinite(self._timeout) or self._timeout <= 0:
            raise ValueError("MEMORY_CORE_OMNI_TIMEOUT must be a positive finite number")
        self._rpc_attempts = _rpc_attempts(
            rpc_attempts
            if rpc_attempts is not None
            else os.getenv("MEMORY_CORE_OMNI_RPC_ATTEMPTS", "5")
        )
        self._pending_dialogue: dict[str, list[tuple[str, str]]] = {}
        self._pair_indexes: dict[str, int] = {}

        self._channel = None
        if _stub is not None:
            self._stub = _stub
        else:
            target, secure = AsyncMemoryClient.normalize_endpoint(
                _nonblank(endpoint or os.getenv("MEMORY_CORE_ENDPOINT"), "MEMORY_CORE_ENDPOINT")
            )
            self._channel = (
                grpc.secure_channel(target, grpc.ssl_channel_credentials())
                if secure
                else grpc.insecure_channel(target)
            )
            self._stub = memory_pb2_grpc.MemoryCoreStub(self._channel)

    def _scope(self, user_id: str, session_ref: str) -> pb.MemoryScope:
        return pb.MemoryScope(
            tenant_ref=self._tenant_ref,
            agent_ref=self._agent_ref,
            relationship_ref=_nonblank(user_id, "user_id"),
            session_ref=session_ref,
            kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
        )

    def _call(self, method: str, request: Any) -> Any:
        options = {
            "metadata": (("authorization", f"Bearer {self._token}"),),
            "timeout": self._timeout,
        }
        for attempt in range(self._rpc_attempts):
            try:
                return getattr(self._stub, method)(request, **options)
            except grpc.RpcError as error:
                if error.code() != grpc.StatusCode.ABORTED or attempt + 1 == self._rpc_attempts:
                    raise
                time.sleep(0.05 * (2 ** attempt))
        raise AssertionError("positive RPC attempt count exhausted without return")

    def add(self, messages: Sequence[dict[str, Any]], user_id: str, **_kwargs: Any) -> None:
        user_id = _nonblank(user_id, "user_id")
        incoming = []
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("messages must contain role/content dictionaries")
            role = message.get("role")
            if role == "system":
                continue
            text = _nonblank(message.get("content"), "message content")
            if role not in {"user", "assistant"}:
                raise ValueError("history must alternate user and assistant messages")
            incoming.append((role, text))

        pending = self._pending_dialogue.setdefault(user_id, [])
        for role, text in incoming:
            if not pending and role != "user":
                raise ValueError("history must alternate from a user message")
            if pending and pending[-1][0] == role:
                previous_role, previous_text = pending[-1]
                pending[-1] = (previous_role, previous_text + "\n\n" + text)
            else:
                pending.append((role, text))
            if len(pending) == 2:
                self._write_pair(user_id, pending[0][1], pending[1][1])
                pending.clear()
        if not pending:
            self._pending_dialogue.pop(user_id, None)

    def _write_pair(self, user_id: str, situation: str, agent_act: str) -> None:
        pair_index = self._pair_indexes.get(user_id, 0)
        pair_digest = _digest((("user", situation), ("assistant", agent_act)))
        pair_ref = (
            f"omni:{_digest(user_id)}:history:{pair_index}:"
            f"{pair_digest}"
        )
        scope = self._scope(user_id, f"history-{pair_index}")
        for suffix, text, episode_role, actor_kind, actor_ref in (
                (
                    "user",
                    situation,
                    pb.EPISODE_SOURCE_ROLE_SITUATION,
                    pb.SOURCE_ACTOR_KIND_USER,
                    f"user-{_digest(user_id)}",
                ),
                (
                    "assistant",
                    agent_act,
                    pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
                    pb.SOURCE_ACTOR_KIND_AGENT,
                    self._agent_ref,
                ),
            ):
            source_ref = f"{pair_ref}:{suffix}"
            self._call("ObserveSourceEvent", pb.ObserveSourceEventRequest(
                idempotency_key=source_ref,
                source_event=pb.SourceEvent(
                    scope=scope,
                    text=text,
                    source_ref=source_ref,
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                ),
                episode_binding=pb.EpisodeBinding(
                    run_ref=pair_ref,
                    source_group_ref=pair_ref,
                    role=episode_role,
                ),
            ))
        self._pair_indexes[user_id] = pair_index + 1

    def search(self, query: str, user_id: str, top_k: int, **_kwargs: Any) -> str:
        text = _nonblank(query, "query")
        if type(top_k) is not int or top_k <= 0:
            raise ValueError("top_k must be a positive integer")
        query_ref = f"omni:{_digest(user_id)}:query:{_digest(text)}"
        scope = self._scope(user_id, f"query-{_digest(text)}")
        source = self._call("ObserveSourceEvent", pb.ObserveSourceEventRequest(
            idempotency_key=query_ref,
            source_event=pb.SourceEvent(
                scope=scope,
                text=text,
                source_ref=query_ref,
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref=f"user-{_digest(user_id)}",
            ),
        ))
        context = self._call("SelectMemory", pb.SelectMemoryRequest(
            scope=scope,
            run_ref=query_ref,
            situation_source_event_refs=[source.source_event_ref],
            episode_evidence_max_bytes=self._episode_evidence_max_bytes,
        ))
        rendered = render_memory_context(context)
        if rendered.memory_refs:
            self._call("RecordMemoryDelivery", pb.MemoryDeliveryReceipt(
                idempotency_key=f"{query_ref}:delivery",
                scope=scope,
                run_ref=query_ref,
                memory_context_ref=context.context_ref,
                delivered_memory_refs=rendered.memory_refs,
            ))
        return rendered.text

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None
