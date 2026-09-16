"""Read-only test phase over persona-level, chronologically consolidated memory."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from memory_core import memory_pb2 as pb, render_memory_context

from .personamem_data import History, Question, QUERY_SUFFIX
from .reference import EvalMode, HistoricalTurn, MemoryClient, TextModel, render_episode_rag


async def wait_for_consolidation(snapshot: Callable[[], dict], *, expected_episodes: int,
                                 timeout: float = 600, interval: float = 1) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        state = snapshot()
        if (state["episodes"] >= expected_episodes and state["jobs"] > 0
                and state["pending_jobs"] == 0):
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError(f"consolidation incomplete: {state}")
        await asyncio.sleep(interval)


def bounded_context(context: pb.MemoryContext, *, include_dispositions: bool,
                    token_count: Callable[[str], int], limit: int):
    if limit <= 0:
        raise ValueError("memory token budget must be positive")
    selected = context
    if not include_dispositions:
        selected = pb.MemoryContext()
        for item in context.recollections:
            selected.recollections.add().CopyFrom(item)
    return render_memory_context(selected, max_tokens=limit, token_count=token_count)


class PersonaMemHarness:
    def __init__(self, *, memory: MemoryClient, model: TextModel, scorer,
                 settle: Callable[[pb.MemoryScope, int], Awaitable[dict]],
                 evaluation_ref: str, token_count: Callable[[str], int],
                 memory_tokens: int = 2048, max_output_tokens: int = 1024,
                 batch_turns: int = 28, rag_top_k: int = 8,
                 episode_evidence_max_bytes: int = 0):
        if not evaluation_ref or memory_tokens <= 0 or max_output_tokens <= 0 or batch_turns < 2:
            raise ValueError("invalid PersonaMem evaluation configuration")
        if (type(episode_evidence_max_bytes) is not int
                or not 0 <= episode_evidence_max_bytes <= 16_384):
            raise ValueError("episode_evidence_max_bytes must be an integer from 0 to 16384")
        self.memory, self.model, self.scorer, self.settle = memory, model, scorer, settle
        self.evaluation_ref, self.token_count = evaluation_ref, token_count
        self.memory_tokens, self.max_output_tokens = memory_tokens, max_output_tokens
        self.batch_turns, self.rag_top_k = batch_turns, rag_top_k
        self.episode_evidence_max_bytes = episode_evidence_max_bytes

    def scope(self, persona_id: str, session: str = "history") -> pb.MemoryScope:
        return pb.MemoryScope(
            tenant_ref=f"personamem-{self.evaluation_ref}", agent_ref="reference-agent",
            relationship_ref=f"persona-{persona_id}", session_ref=session,
            kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
        )

    async def ingest(self, persona_id: str, history: History) -> dict:
        """Only observed dialogue is episodic evidence; the initial profile is not learned."""
        state = {}
        for index, turn in enumerate(history.turns):
            scope = self.scope(persona_id, f"window-{index // self.batch_turns}")
            prefix = f"{self.evaluation_ref}:{persona_id}:history:{index}"
            for role, text, actor in (
                (pb.EPISODE_SOURCE_ROLE_SITUATION, turn.situation, pb.SOURCE_ACTOR_KIND_USER),
                (pb.EPISODE_SOURCE_ROLE_AGENT_ACT, turn.agent_act, pb.SOURCE_ACTOR_KIND_AGENT),
            ):
                ref = f"{prefix}:{role}"
                await self.memory.observe_source_event(pb.ObserveSourceEventRequest(
                    idempotency_key=ref,
                    source_event=pb.SourceEvent(scope=scope, text=text, source_ref=ref,
                        actor_kind=actor, actor_ref="reference-agent" if actor == pb.SOURCE_ACTOR_KIND_AGENT else f"user-{persona_id}"),
                    episode_binding=pb.EpisodeBinding(run_ref=prefix, source_group_ref=prefix, role=role),
                ))
            if (index + 1) % self.batch_turns == 0 or index + 1 == len(history.turns):
                state = await self.settle(scope, index + 1)
        if history.trailing_user:
            ref = f"{self.evaluation_ref}:{persona_id}:history:unanswered"
            await self.memory.observe_source_event(pb.ObserveSourceEventRequest(
                idempotency_key=ref, source_event=pb.SourceEvent(
                    scope=self.scope(persona_id), text=history.trailing_user, source_ref=ref,
                    actor_kind=pb.SOURCE_ACTOR_KIND_USER, actor_ref=f"user-{persona_id}")))
        return state

    async def evaluate(self, question: Question, history: History) -> list[dict]:
        # Exact upstream option ordering, made repeatable by the CLI's PYTHONHASHSEED=0.
        prompt_query = question.query + QUERY_SUFFIX
        seed = hash(f"{question.persona_id}_{prompt_query}") % 2**32
        instruction, options = self.scorer.create_mcq_options(
            None, question.correct_answer, list(question.incorrect_answers), seed=seed)
        correct_option = next(k for k, value in options.items() if value == question.correct_answer)
        results = []
        for mode in EvalMode:
            started = time.perf_counter()
            context_text, refs, context_ref = "", (), None
            record = {
                "evaluation_ref": self.evaluation_ref, "question_ref": question.question_ref,
                "persona_id": question.persona_id, "mode": mode.value,
                "correct_option": correct_option, "option_mapping": options,
                "metadata": question.metadata, "query": question.query,
                "error": None, "correct": False, "output": "", "predicted_option": "",
            }
            try:
                if mode is EvalMode.EPISODE_RAG:
                    turns = history.turns + ((HistoricalTurn(history.trailing_user, ""),) if history.trailing_user else ())
                    for top_k in range(self.rag_top_k, 0, -1):
                        candidate = render_episode_rag(turns, question.query, top_k=top_k)
                        if self.token_count(candidate) <= self.memory_tokens:
                            context_text = candidate
                            break
                elif mode in {EvalMode.RECOLLECTION_ONLY, EvalMode.FULL_CORE}:
                    scope = self.scope(question.persona_id, f"query-{question.question_ref}")
                    prefix = f"{self.evaluation_ref}:{question.persona_id}:{question.question_ref}:{mode.value}"
                    # Unbound query SourceEvents are legal selection inputs, not learning Episodes.
                    # This keeps all test questions and MCQ answers out of consolidation.
                    source = await self.memory.observe_source_event(pb.ObserveSourceEventRequest(
                        idempotency_key=prefix,
                        source_event=pb.SourceEvent(scope=scope, text=question.query,
                            source_ref=prefix, actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                            actor_ref=f"user-{question.persona_id}"),
                    ))
                    context = await self.memory.select_memory(pb.SelectMemoryRequest(
                        scope=scope, run_ref=prefix,
                        situation_source_event_refs=[source.source_event_ref],
                        episode_evidence_max_bytes=(
                            self.episode_evidence_max_bytes if mode is EvalMode.FULL_CORE else 0
                        ),
                    ))
                    rendered = bounded_context(context, include_dispositions=mode is EvalMode.FULL_CORE,
                                               token_count=self.token_count, limit=self.memory_tokens)
                    context_text, refs, context_ref = rendered.text, rendered.memory_refs, context.context_ref
                    if refs:
                        await self.memory.record_memory_delivery(pb.MemoryDeliveryReceipt(
                            idempotency_key=prefix + ":delivery", scope=scope, run_ref=prefix,
                            memory_context_ref=context_ref, delivered_memory_refs=refs,
                        ))
                selected_at = time.perf_counter()
                instructions = history.background
                if context_text:
                    instructions += "\n\nLONG_TERM_MEMORY\n" + context_text
                instructions += "\n\n" + instruction
                output = await self.model.complete(instructions=instructions, input_text=prompt_query,
                                                   max_output_tokens=self.max_output_tokens)
                if not isinstance(output, str) or not output.strip():
                    raise RuntimeError("model returned empty output")
                predicted = self.scorer.extract_final_answer(None, output)
                record.update(output=output, predicted_option=predicted,
                    correct=self.scorer.check_mcq_correctness(None, predicted, question.correct_answer, options),
                    selection_ms=round((selected_at - started) * 1000, 3))
            except Exception as error:
                record["error"] = f"{type(error).__name__}: {error}"
            record.update(context_text=context_text, memory_refs=list(refs), context_ref=context_ref,
                          memory_tokens=self.token_count(context_text),
                          duration_ms=round((time.perf_counter() - started) * 1000, 3))
            results.append(record)
        return results
