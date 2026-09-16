from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ..rendering import render_memory_context
from ..v1 import memory_pb2


class MemoryClient(Protocol):
    async def observe_source_event(
        self,
        request: memory_pb2.ObserveSourceEventRequest,
        **options: Any,
    ) -> memory_pb2.SourceEventReceipt: ...

    async def select_memory(
        self,
        request: memory_pb2.SelectMemoryRequest,
        **options: Any,
    ) -> memory_pb2.MemoryContext: ...

    async def record_memory_delivery(
        self,
        request: memory_pb2.MemoryDeliveryReceipt,
        **options: Any,
    ) -> memory_pb2.ReceiptAck: ...

    async def report_outcome(
        self,
        request: memory_pb2.ReportOutcomeRequest,
        **options: Any,
    ) -> memory_pb2.OutcomeReceipt: ...


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Trusted source identity supplied by the Harness, never inferred."""

    source_ref: str
    actor_kind: int
    actor_ref: str


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    """Stable identities required for one observed Agent turn."""

    scope: memory_pb2.MemoryScope
    run_ref: str
    source_group_ref: str
    situation_idempotency_key: str
    situation: SourceProvenance
    delivery_idempotency_key: str
    agent_act_idempotency_key: str
    agent_act: SourceProvenance


@dataclass(frozen=True, slots=True)
class OutcomeIdentity:
    """Stable identity for one explicit, source-bound Outcome."""

    scope: memory_pb2.MemoryScope
    run_ref: str
    source_group_ref: str
    idempotency_key: str
    source: SourceProvenance


@dataclass(frozen=True, slots=True)
class TurnResult:
    runner_agent: Any
    runner_result: Any
    memory_context: memory_pb2.MemoryContext
    situation_receipt: memory_pb2.SourceEventReceipt
    delivery_receipt: memory_pb2.ReceiptAck | None
    agent_act_receipt: memory_pb2.SourceEventReceipt


class OpenAIAgentsAdapter:
    """Sequence the public causal lifecycle around an OpenAI Agents turn."""

    def __init__(
        self,
        memory: MemoryClient,
        *,
        runner: Any | None = None,
        episode_evidence_max_bytes: int = 0,
        max_tokens: int | None = None,
        token_count: Callable[[str], int] | None = None,
    ) -> None:
        if (
            type(episode_evidence_max_bytes) is not int
            or not 0 <= episode_evidence_max_bytes <= 16_384
        ):
            raise ValueError(
                "episode_evidence_max_bytes must be an integer from 0 to 16384"
            )
        # The renderer owns total-budget validation; exercise it now so invalid
        # adapter configuration fails before any turn performs I/O.
        render_memory_context(
            memory_pb2.MemoryContext(),
            max_tokens=max_tokens,
            token_count=token_count,
        )
        if episode_evidence_max_bytes > 0 and max_tokens is None:
            raise ValueError(
                "episode evidence requires max_tokens and token_count"
            )
        self._memory = memory
        self._runner = runner
        self._episode_evidence_max_bytes = episode_evidence_max_bytes
        self._max_tokens = max_tokens
        self._token_count = token_count

    async def run_turn(
        self,
        agent: Any,
        user_text: str,
        *,
        identity: TurnIdentity,
        constitution: memory_pb2.Constitution | None = None,
        **runner_options: Any,
    ) -> TurnResult:
        frozen_constitution = None
        if constitution is not None:
            frozen_constitution = memory_pb2.Constitution()
            frozen_constitution.CopyFrom(constitution)
        situation_request = _event_request(
            identity.situation_idempotency_key,
            identity,
            user_text,
            identity.situation,
            memory_pb2.EPISODE_SOURCE_ROLE_SITUATION,
        )
        if frozen_constitution is not None:
            situation_request.source_event.constitution.CopyFrom(frozen_constitution)
        situation_receipt = await self._memory.observe_source_event(situation_request)
        select_request = memory_pb2.SelectMemoryRequest(
            scope=identity.scope,
            run_ref=identity.run_ref,
            situation_source_event_refs=[situation_receipt.source_event_ref],
            episode_evidence_max_bytes=self._episode_evidence_max_bytes,
        )
        if frozen_constitution is not None:
            select_request.constitution.CopyFrom(frozen_constitution)
        memory_context = await self._memory.select_memory(select_request)

        rendered = render_memory_context(
            memory_context,
            max_tokens=self._max_tokens,
            token_count=self._token_count,
        )
        delivery_receipt: memory_pb2.ReceiptAck | None = None
        runner_input: Any = user_text
        if rendered.text:
            runner_input = [
                {"role": "developer", "content": rendered.text},
                {"role": "user", "content": user_text},
            ]
            delivery_receipt = await self._memory.record_memory_delivery(
                memory_pb2.MemoryDeliveryReceipt(
                    idempotency_key=identity.delivery_idempotency_key,
                    scope=identity.scope,
                    run_ref=identity.run_ref,
                    memory_context_ref=memory_context.context_ref,
                    delivered_memory_refs=rendered.memory_refs,
                )
            )

        runner = self._runner if self._runner is not None else _official_runner()
        runner_result = await runner.run(agent, runner_input, **runner_options)
        final_output = getattr(runner_result, "final_output", None)
        if not isinstance(final_output, str) or not final_output.strip():
            raise TypeError(
                "OpenAI Agents adapter requires a non-empty text final_output"
            )

        agent_act_receipt = await self._memory.observe_source_event(
            _event_request(
                identity.agent_act_idempotency_key,
                identity,
                final_output,
                identity.agent_act,
                memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT,
            )
        )
        return TurnResult(
            runner_agent=agent,
            runner_result=runner_result,
            memory_context=memory_context,
            situation_receipt=situation_receipt,
            delivery_receipt=delivery_receipt,
            agent_act_receipt=agent_act_receipt,
        )

    async def report_outcome(
        self,
        text: str,
        *,
        identity: OutcomeIdentity,
        delivery_receipt_refs: Sequence[str] = (),
        related_source_event_refs: Sequence[str] = (),
        constitution: memory_pb2.Constitution | None = None,
    ) -> memory_pb2.OutcomeReceipt:
        return await self._memory.report_outcome(
            memory_pb2.ReportOutcomeRequest(
                idempotency_key=identity.idempotency_key,
                scope=identity.scope,
                run_ref=identity.run_ref,
                source_group_ref=identity.source_group_ref,
                text=text,
                delivery_receipt_refs=delivery_receipt_refs,
                related_source_event_refs=related_source_event_refs,
                source_ref=identity.source.source_ref,
                actor_kind=identity.source.actor_kind,
                actor_ref=identity.source.actor_ref,
                constitution=constitution,
            )
        )


def _event_request(
    idempotency_key: str,
    identity: TurnIdentity,
    text: str,
    source: SourceProvenance,
    role: int,
) -> memory_pb2.ObserveSourceEventRequest:
    return memory_pb2.ObserveSourceEventRequest(
        idempotency_key=idempotency_key,
        source_event=memory_pb2.SourceEvent(
            scope=identity.scope,
            text=text,
            source_ref=source.source_ref,
            actor_kind=source.actor_kind,
            actor_ref=source.actor_ref,
        ),
        episode_binding=memory_pb2.EpisodeBinding(
            run_ref=identity.run_ref,
            source_group_ref=identity.source_group_ref,
            role=role,
        ),
    )


def _official_runner() -> Any:
    try:
        from agents import Runner
    except ImportError as exc:  # pragma: no cover - optional dependency boundary
        raise RuntimeError(
            "OpenAI Agents adapter requires the 'openai-agents' optional dependency"
        ) from exc
    return Runner
