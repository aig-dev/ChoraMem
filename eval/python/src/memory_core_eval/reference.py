from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Protocol

from memory_core import memory_pb2, render_memory_context


class EvalMode(str, Enum):
    NONE = "none"
    EPISODE_RAG = "episode_rag"
    RECOLLECTION_ONLY = "recollection_only"
    FULL_CORE = "full_core"


@dataclass(frozen=True, slots=True)
class HistoricalTurn:
    situation: str
    agent_act: str
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_ref: str
    history: tuple[HistoricalTurn, ...]
    query: str
    expected_output: str
    query_outcome: str | None = None


@dataclass(frozen=True, slots=True)
class HarnessProfile:
    evaluation_ref: str
    harness: str
    harness_version: str
    model: str
    profile_version: str
    prompt_version: str
    scenario_set: str
    instructions: str
    tenant_ref: str
    agent_ref: str
    relationship_ref: str
    user_ref: str
    constitution: memory_pb2.Constitution
    max_output_tokens: int
    episode_rag_top_k: int = 3

    def __post_init__(self) -> None:
        for field_name in (
            "evaluation_ref",
            "harness",
            "harness_version",
            "model",
            "profile_version",
            "prompt_version",
            "scenario_set",
            "tenant_ref",
            "agent_ref",
            "relationship_ref",
            "user_ref",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be nonblank")
        if self.episode_rag_top_k <= 0:
            raise ValueError("episode_rag_top_k must be positive")
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True, slots=True)
class EvalResult:
    evaluation_ref: str
    harness: str
    harness_version: str
    model: str
    profile_version: str
    prompt_version: str
    scenario_set: str
    max_output_tokens: int
    mode: str
    scenario_ref: str
    output: str
    matched: bool
    injected_char_count: int
    duration_ms: float
    error: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


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


class TextModel(Protocol):
    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str: ...


class EmptyModelOutput(RuntimeError):
    pass


async def _already_settled() -> None:
    return None


class ReferenceHarness:
    """Fixed text-only Harness for comparing Memory Core evaluation modes."""

    def __init__(
        self,
        *,
        memory: MemoryClient | None,
        model: TextModel,
        profile: HarnessProfile,
        settle: Callable[[], Awaitable[None]] = _already_settled,
    ) -> None:
        self._memory = memory
        self._model = model
        self._profile = profile
        self._settle = settle

    async def run(self, mode: EvalMode, scenario: Scenario) -> EvalResult:
        started = time.perf_counter()
        output = ""
        injected_text = ""
        error: str | None = None

        try:
            if mode in {EvalMode.RECOLLECTION_ONLY, EvalMode.FULL_CORE}:
                if self._memory is None:
                    raise RuntimeError(f"{mode.value} requires a Memory Core client")
                scope = self._scope(mode, scenario)
                await self._replay_history(mode, scenario, scope)
                await self._settle()
                situation_receipt = await self._observe(
                    mode,
                    scenario,
                    scope,
                    turn_ref="query",
                    role=memory_pb2.EPISODE_SOURCE_ROLE_SITUATION,
                    text=scenario.query,
                    actor_kind=memory_pb2.SOURCE_ACTOR_KIND_USER,
                    actor_ref=self._profile.user_ref,
                )
                context = await self._memory.select_memory(
                    memory_pb2.SelectMemoryRequest(
                        scope=scope,
                        run_ref=self._run_ref(mode, scenario, "query"),
                        situation_source_event_refs=[
                            situation_receipt.source_event_ref
                        ],
                        constitution=self._profile.constitution,
                    )
                )
                rendered = _render_core_context(mode, context)
                injected_text = rendered.text
                delivery_receipt: memory_pb2.ReceiptAck | None = None
                if rendered.text:
                    delivery_receipt = await self._memory.record_memory_delivery(
                        memory_pb2.MemoryDeliveryReceipt(
                            idempotency_key=self._identity(
                                mode, scenario, "query", "delivery"
                            ),
                            scope=scope,
                            run_ref=self._run_ref(mode, scenario, "query"),
                            memory_context_ref=context.context_ref,
                            delivered_memory_refs=rendered.memory_refs,
                        )
                    )
            else:
                scope = None
                situation_receipt = None
                delivery_receipt = None
                if mode is EvalMode.EPISODE_RAG:
                    injected_text = render_episode_rag(
                        scenario.history,
                        scenario.query,
                        top_k=self._profile.episode_rag_top_k,
                    )

            instructions = self._profile.instructions
            if injected_text:
                instructions = f"{instructions}\n\nLONG_TERM_MEMORY\n{injected_text}"
            raw_output = await self._model.complete(
                instructions=instructions,
                input_text=scenario.query,
                max_output_tokens=self._profile.max_output_tokens,
            )
            if not isinstance(raw_output, str) or not raw_output.strip():
                raise EmptyModelOutput("text model returned empty output")
            output = raw_output

            if scope is not None:
                assert self._memory is not None
                assert situation_receipt is not None
                agent_act_receipt = await self._observe(
                    mode,
                    scenario,
                    scope,
                    turn_ref="query",
                    role=memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT,
                    text=output,
                    actor_kind=memory_pb2.SOURCE_ACTOR_KIND_AGENT,
                    actor_ref=self._profile.agent_ref,
                )
                if scenario.query_outcome is not None:
                    delivery_refs = (
                        [delivery_receipt.receipt_ref]
                        if delivery_receipt is not None
                        else []
                    )
                    await self._report_outcome(
                        mode,
                        scenario,
                        scope,
                        turn_ref="query",
                        text=scenario.query_outcome,
                        delivery_receipt_refs=delivery_refs,
                        related_source_event_refs=[
                            situation_receipt.source_event_ref,
                            agent_act_receipt.source_event_ref,
                        ],
                    )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        return EvalResult(
            evaluation_ref=self._profile.evaluation_ref,
            harness=self._profile.harness,
            harness_version=self._profile.harness_version,
            model=self._profile.model,
            profile_version=self._profile.profile_version,
            prompt_version=self._profile.prompt_version,
            scenario_set=self._profile.scenario_set,
            max_output_tokens=self._profile.max_output_tokens,
            mode=mode.value,
            scenario_ref=scenario.scenario_ref,
            output=output,
            matched=error is None
            and output.strip() == scenario.expected_output.strip(),
            injected_char_count=len(injected_text),
            duration_ms=duration_ms,
            error=error,
        )

    async def _replay_history(
        self,
        mode: EvalMode,
        scenario: Scenario,
        scope: memory_pb2.MemoryScope,
    ) -> None:
        assert self._memory is not None
        for index, turn in enumerate(scenario.history):
            turn_ref = f"history-{index + 1}"
            situation = await self._observe(
                mode,
                scenario,
                scope,
                turn_ref=turn_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_SITUATION,
                text=turn.situation,
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_USER,
                actor_ref=self._profile.user_ref,
            )
            agent_act = await self._observe(
                mode,
                scenario,
                scope,
                turn_ref=turn_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT,
                text=turn.agent_act,
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_AGENT,
                actor_ref=self._profile.agent_ref,
            )
            if turn.outcome is not None:
                await self._report_outcome(
                    mode,
                    scenario,
                    scope,
                    turn_ref=turn_ref,
                    text=turn.outcome,
                    delivery_receipt_refs=(),
                    related_source_event_refs=(
                        situation.source_event_ref,
                        agent_act.source_event_ref,
                    ),
                )

    async def _observe(
        self,
        mode: EvalMode,
        scenario: Scenario,
        scope: memory_pb2.MemoryScope,
        *,
        turn_ref: str,
        role: int,
        text: str,
        actor_kind: int,
        actor_ref: str,
    ) -> memory_pb2.SourceEventReceipt:
        assert self._memory is not None
        role_name = {
            memory_pb2.EPISODE_SOURCE_ROLE_SITUATION: "situation",
            memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT: "agent-act",
        }[role]
        return await self._memory.observe_source_event(
            memory_pb2.ObserveSourceEventRequest(
                idempotency_key=self._identity(
                    mode, scenario, turn_ref, f"observe-{role_name}"
                ),
                source_event=memory_pb2.SourceEvent(
                    scope=scope,
                    text=text,
                    source_ref=self._identity(
                        mode, scenario, turn_ref, role_name
                    ),
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                ),
                episode_binding=memory_pb2.EpisodeBinding(
                    run_ref=self._run_ref(mode, scenario, turn_ref),
                    source_group_ref=self._identity(
                        mode, scenario, turn_ref, "source-group"
                    ),
                    role=role,
                ),
            )
        )

    async def _report_outcome(
        self,
        mode: EvalMode,
        scenario: Scenario,
        scope: memory_pb2.MemoryScope,
        *,
        turn_ref: str,
        text: str,
        delivery_receipt_refs: Sequence[str],
        related_source_event_refs: Sequence[str],
    ) -> memory_pb2.OutcomeReceipt:
        assert self._memory is not None
        return await self._memory.report_outcome(
            memory_pb2.ReportOutcomeRequest(
                idempotency_key=self._identity(
                    mode, scenario, turn_ref, "report-outcome"
                ),
                scope=scope,
                run_ref=self._run_ref(mode, scenario, turn_ref),
                source_group_ref=self._identity(
                    mode, scenario, turn_ref, "source-group"
                ),
                text=text,
                delivery_receipt_refs=delivery_receipt_refs,
                related_source_event_refs=related_source_event_refs,
                source_ref=self._identity(mode, scenario, turn_ref, "outcome"),
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_EXTERNAL,
                actor_ref="memory-core-eval",
            )
        )

    def _scope(
        self,
        mode: EvalMode,
        scenario: Scenario,
    ) -> memory_pb2.MemoryScope:
        isolated_ref = (
            f"{self._profile.relationship_ref}:"
            f"{self._profile.evaluation_ref}:{self._profile.harness}:"
            f"{scenario.scenario_ref}:{mode.value}"
        )
        return memory_pb2.MemoryScope(
            tenant_ref=self._profile.tenant_ref,
            agent_ref=self._profile.agent_ref,
            relationship_ref=isolated_ref,
            session_ref=self._identity(mode, scenario, "session", "scope"),
            kind=memory_pb2.MEMORY_SCOPE_KIND_RELATIONSHIP,
        )

    def _run_ref(
        self,
        mode: EvalMode,
        scenario: Scenario,
        turn_ref: str,
    ) -> str:
        return self._identity(mode, scenario, turn_ref, "run")

    def _identity(
        self,
        mode: EvalMode,
        scenario: Scenario,
        turn_ref: str,
        kind: str,
    ) -> str:
        return ":".join(
            (
                "eval",
                self._profile.evaluation_ref,
                self._profile.harness,
                scenario.scenario_ref,
                mode.value,
                turn_ref,
                kind,
            )
        )


def _render_core_context(mode: EvalMode, context: memory_pb2.MemoryContext):
    if mode is EvalMode.FULL_CORE:
        return render_memory_context(context)
    filtered = memory_pb2.MemoryContext(
        context_ref=context.context_ref,
        run_ref=context.run_ref,
        scope=context.scope,
        constitution=context.constitution,
        recollections=context.recollections,
    )
    return render_memory_context(filtered)


def render_episode_rag(
    history: Sequence[HistoricalTurn],
    query: str,
    *,
    top_k: int,
    ngram_size: int = 3,
) -> str:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if ngram_size <= 0:
        raise ValueError("ngram_size must be positive")
    query_ngrams = _character_ngrams(query, ngram_size)
    if not query_ngrams:
        return ""

    ranked: list[tuple[float, int, HistoricalTurn]] = []
    for index, turn in enumerate(history):
        document = "\n".join(
            part
            for part in (turn.situation, turn.agent_act, turn.outcome or "")
            if part.strip()
        )
        document_ngrams = _character_ngrams(document, ngram_size)
        union = query_ngrams | document_ngrams
        score = len(query_ngrams & document_ngrams) / len(union) if union else 0.0
        if score > 0:
            ranked.append((score, index, turn))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked[:top_k]
    if not selected:
        return ""

    episodes: list[str] = []
    for _, _, turn in selected:
        lines = [
            f"SITUATION: {turn.situation.strip()}",
            f"AGENT_ACT: {turn.agent_act.strip()}",
        ]
        if turn.outcome and turn.outcome.strip():
            lines.append(f"OUTCOME: {turn.outcome.strip()}")
        episodes.append("\n".join(lines))
    return "EPISODES\n" + "\n\n".join(episodes)


def _character_ngrams(text: str, size: int) -> set[str]:
    normalized = "".join(character.casefold() for character in text if character.isalnum())
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {
        normalized[index : index + size]
        for index in range(len(normalized) - size + 1)
    }
