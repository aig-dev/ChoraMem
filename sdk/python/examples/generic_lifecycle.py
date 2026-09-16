"""Framework-neutral, complete Memory Core turn lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from memory_core import AsyncMemoryClient, memory_pb2, render_memory_context


class TextModel(Protocol):
    async def __call__(
        self,
        *,
        memory_text: str,
        situation_text: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    source_ref: str
    actor_kind: int
    actor_ref: str


@dataclass(frozen=True, slots=True)
class TurnIdentity:
    scope: memory_pb2.MemoryScope
    run_ref: str
    source_group_ref: str
    situation_idempotency_key: str
    situation: SourceIdentity
    delivery_idempotency_key: str
    agent_act_idempotency_key: str
    agent_act: SourceIdentity


@dataclass(frozen=True, slots=True)
class ExplicitOutcome:
    idempotency_key: str
    text: str
    source: SourceIdentity


@dataclass(frozen=True, slots=True)
class EpisodeEvidenceBudget:
    max_bytes: int
    max_tokens: int
    token_count: Callable[[str], int]


async def run_lifecycle(
    memory: AsyncMemoryClient,
    *,
    identity: TurnIdentity,
    situation_text: str,
    run_model: TextModel,
    outcome: ExplicitOutcome,
    episode_evidence: EpisodeEvidenceBudget | None = None,
) -> str:
    situation = await memory.observe_source_event(
        memory_pb2.ObserveSourceEventRequest(
            idempotency_key=identity.situation_idempotency_key,
            source_event=memory_pb2.SourceEvent(
                scope=identity.scope,
                text=situation_text,
                source_ref=identity.situation.source_ref,
                actor_kind=identity.situation.actor_kind,
                actor_ref=identity.situation.actor_ref,
            ),
            episode_binding=memory_pb2.EpisodeBinding(
                run_ref=identity.run_ref,
                source_group_ref=identity.source_group_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_SITUATION,
            ),
        )
    )
    context = await memory.select_memory(
        memory_pb2.SelectMemoryRequest(
            scope=identity.scope,
            run_ref=identity.run_ref,
            situation_source_event_refs=[situation.source_event_ref],
            episode_evidence_max_bytes=(
                episode_evidence.max_bytes if episode_evidence is not None else 0
            ),
        )
    )
    rendered = render_memory_context(
        context,
        max_tokens=(
            episode_evidence.max_tokens if episode_evidence is not None else None
        ),
        token_count=(
            episode_evidence.token_count if episode_evidence is not None else None
        ),
    )

    delivery = None
    if rendered.text:
        delivery = await memory.record_memory_delivery(
            memory_pb2.MemoryDeliveryReceipt(
                idempotency_key=identity.delivery_idempotency_key,
                scope=identity.scope,
                run_ref=identity.run_ref,
                memory_context_ref=context.context_ref,
                delivered_memory_refs=rendered.memory_refs,
            )
        )

    # The model receives plain text only: never protobuf objects or stable refs.
    output = await run_model(
        memory_text=rendered.text,
        situation_text=situation_text,
    )
    if not isinstance(output, str) or not output.strip():
        raise TypeError("model must return nonblank text")

    agent_act = await memory.observe_source_event(
        memory_pb2.ObserveSourceEventRequest(
            idempotency_key=identity.agent_act_idempotency_key,
            source_event=memory_pb2.SourceEvent(
                scope=identity.scope,
                text=output,
                source_ref=identity.agent_act.source_ref,
                actor_kind=identity.agent_act.actor_kind,
                actor_ref=identity.agent_act.actor_ref,
            ),
            episode_binding=memory_pb2.EpisodeBinding(
                run_ref=identity.run_ref,
                source_group_ref=identity.source_group_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT,
            ),
        )
    )

    # Outcome is explicit external evidence supplied by the Harness, not an
    # interpretation fabricated from the model output.
    await memory.report_outcome(
        memory_pb2.ReportOutcomeRequest(
            idempotency_key=outcome.idempotency_key,
            scope=identity.scope,
            run_ref=identity.run_ref,
            source_group_ref=identity.source_group_ref,
            text=outcome.text,
            delivery_receipt_refs=(
                [delivery.receipt_ref] if delivery is not None else []
            ),
            related_source_event_refs=[agent_act.source_event_ref],
            source_ref=outcome.source.source_ref,
            actor_kind=outcome.source.actor_kind,
            actor_ref=outcome.source.actor_ref,
        )
    )
    return output
