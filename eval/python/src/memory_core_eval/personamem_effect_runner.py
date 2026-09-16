"""Paired PersonaMem-v2 effect evaluation and causal bottleneck diagnosis."""
from __future__ import annotations

import hashlib
import json
import random
import statistics
import time
import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb
from memory_core.client import AsyncMemoryClient
from memoryindex.v1 import memory_index_pb2 as index_pb
from memoryindex.v1 import memory_index_pb2_grpc as index_rpc

from .personamem_data import History, QUERY_SUFFIX, Question
from .personamem_diagnostics import (
    OracleEvidence,
    align_snippet,
    build_funnel_record,
    semantic_top40,
    summarize_funnel,
)
from .personamem_effect import (
    EffectContext,
    EffectMode,
    EpisodeDocument,
    build_effect_context,
)


class CachedTextModel:
    """Content-addressed answer cache so identical arms cannot drift randomly."""

    def __init__(self, delegate: Any, directory: Path, *, model_revision: str) -> None:
        self._delegate = delegate
        self._directory = directory
        self._model_revision = _nonblank(model_revision, "model_revision")
        self._locks: dict[str, asyncio.Lock] = {}
        directory.mkdir(parents=True, exist_ok=True)

    async def complete(
        self,
        *,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        request = {
            "model_revision": self._model_revision,
            "instructions": instructions,
            "input_text": input_text,
            "max_output_tokens": max_output_tokens,
        }
        digest = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        path = self._directory / f"{digest}.json"
        lock = self._locks.setdefault(digest, asyncio.Lock())
        async with lock:
            if path.exists():
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("request") != request:
                    raise ValueError("answer cache hash collision or request drift")
                output = cached.get("output")
                if not isinstance(output, str) or not output.strip():
                    raise ValueError("answer cache contains an empty output")
                return output

            output = await self._delegate.complete(
                instructions=instructions,
                input_text=input_text,
                max_output_tokens=max_output_tokens,
            )
            if not isinstance(output, str) or not output.strip():
                raise RuntimeError("model returned empty output")
            record = request | {"request": request, "output": output}
            temporary = path.with_suffix(".json.part")
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
            return output


class GrpcEpisodeSearch:
    """Read one owner's exact Episode order from the configured MemoryIndex."""

    def __init__(
        self,
        *,
        documents: Sequence[EpisodeDocument],
        owner_document_count: int,
        endpoint: str | None = None,
        token: str = "",
        timeout: float = 30,
        stub: Any | None = None,
    ) -> None:
        if owner_document_count < len(documents) or owner_document_count <= 0:
            raise ValueError("owner_document_count must cover all captured Episodes")
        if timeout <= 0:
            raise ValueError("MemoryIndex timeout must be positive")
        self._documents = {item.memory_ref: item for item in documents}
        if len(self._documents) != len(documents):
            raise ValueError("captured Episode refs must be unique")
        self._owner_document_count = owner_document_count
        self._token = token.strip()
        self._timeout = timeout
        self._channel = None
        if stub is None:
            if endpoint is None:
                raise ValueError("MemoryIndex endpoint is required")
            import grpc

            target, secure = AsyncMemoryClient.normalize_endpoint(endpoint)
            self._channel = (
                grpc.secure_channel(target, grpc.ssl_channel_credentials())
                if secure
                else grpc.insecure_channel(target)
            )
            stub = index_rpc.MemoryIndexStub(self._channel)
        self._stub = stub

    def search_episodes(
        self,
        *,
        scope: pb.MemoryScope,
        query: str,
        limit: int,
    ) -> tuple[EpisodeDocument, ...]:
        if limit <= 0:
            raise ValueError("Episode search limit must be positive")
        metadata = (("x-agent-rpc-token", self._token),) if self._token else ()
        response = self._stub.Search(index_pb.SearchRequest(query=index_pb.Query(
            scope=index_pb.Scope(
                tenant_ref=scope.tenant_ref,
                agent_ref=scope.agent_ref,
                relationship_ref=scope.relationship_ref,
            ),
            text=_nonblank(query, "query"),
            # The public port cannot filter by kind. Read the full owner ordering,
            # then take its first N Episodes instead of accepting a mixed top-N.
            limit=self._owner_document_count,
        )), timeout=self._timeout, metadata=metadata)
        result: list[EpisodeDocument] = []
        seen: set[str] = set()
        for candidate in response.candidates:
            if candidate.kind != index_pb.MEMORY_KIND_EPISODE:
                continue
            if candidate.ref in seen:
                raise ValueError(f"duplicate Episode from MemoryIndex: {candidate.ref}")
            seen.add(candidate.ref)
            document = self._documents.get(candidate.ref)
            if document is None:
                raise ValueError(f"unknown Episode from MemoryIndex: {candidate.ref}")
            result.append(document)
            if len(result) == limit:
                break
        expected = min(limit, len(self._documents))
        if len(result) < expected:
            raise RuntimeError(
                "MemoryIndex did not return a complete Episode top-40; "
                f"received {len(result)} of {expected} required Episodes"
            )
        return tuple(result)

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()


async def ingest_history(
    *,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[dict[str, object]]],
    evaluation_ref: str,
    persona_id: str,
    history: History,
    batch_turns: int,
) -> tuple[dict[str, object], tuple[EpisodeDocument, ...]]:
    """Persist only complete observed pairs and retain their actual Core Episode refs."""

    evaluation_ref = _nonblank(evaluation_ref, "evaluation_ref")
    persona_id = _nonblank(persona_id, "persona_id")
    if batch_turns <= 0:
        raise ValueError("batch_turns must be positive")
    documents: list[EpisodeDocument] = []
    state: dict[str, object] = {}
    for index, turn in enumerate(history.turns):
        scope = _scope(evaluation_ref, persona_id, f"window-{index // batch_turns}")
        run_ref = f"{evaluation_ref}:{persona_id}:history:{index}"
        receipts = []
        for role, text, actor, actor_ref in (
            (
                pb.EPISODE_SOURCE_ROLE_SITUATION,
                turn.situation,
                pb.SOURCE_ACTOR_KIND_USER,
                f"user-{persona_id}",
            ),
            (
                pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
                turn.agent_act,
                pb.SOURCE_ACTOR_KIND_AGENT,
                "reference-agent",
            ),
        ):
            source_ref = f"{run_ref}:{role}"
            receipts.append(await memory.observe_source_event(pb.ObserveSourceEventRequest(
                idempotency_key=source_ref,
                source_event=pb.SourceEvent(
                    scope=scope,
                    text=text,
                    source_ref=source_ref,
                    actor_kind=actor,
                    actor_ref=actor_ref,
                ),
                episode_binding=pb.EpisodeBinding(
                    run_ref=run_ref,
                    source_group_ref=run_ref,
                    role=role,
                ),
            )))
        episode_ref = receipts[-1].episode_ref
        if not isinstance(episode_ref, str) or not episode_ref.strip():
            raise RuntimeError("Core did not return the materialized Episode ref")
        if any(item.memory_ref == episode_ref for item in documents):
            raise RuntimeError(f"Core returned duplicate Episode ref: {episode_ref}")
        documents.append(EpisodeDocument(
            episode_ref,
            f"SITUATION [user]\n{turn.situation}\nAGENT_ACT [agent]\n{turn.agent_act}",
        ))
        if (index + 1) % batch_turns == 0 or index + 1 == len(history.turns):
            state = await settle(scope, index + 1)

    if history.trailing_user:
        scope = _scope(evaluation_ref, persona_id, "history")
        source_ref = f"{evaluation_ref}:{persona_id}:history:unanswered"
        await memory.observe_source_event(pb.ObserveSourceEventRequest(
            idempotency_key=source_ref,
            source_event=pb.SourceEvent(
                scope=scope,
                text=history.trailing_user,
                source_ref=source_ref,
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref=f"user-{persona_id}",
            ),
        ))
    return state, tuple(documents)


def learned_basis_map(memory_state: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    """Map active Recollection/Disposition version refs to their canonical Episodes."""

    active: set[str] = set()
    for key in ("recollections", "dispositions"):
        records = memory_state.get(key, ())
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise TypeError(f"{key} must be a sequence")
        for record in records:
            if isinstance(record, Mapping) and record.get("status") == "active":
                active.add(_nonblank(record.get("ref"), f"{key} ref"))

    by_memory: dict[str, set[str]] = defaultdict(set)
    basis = memory_state.get("basis", {})
    if not isinstance(basis, Mapping):
        raise TypeError("basis must be a mapping")
    for table in ("recollection_basis_links", "seed_basis_links"):
        links = basis.get(table, ())
        if not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
            raise TypeError(f"{table} must be a sequence")
        for link in links:
            if not isinstance(link, Mapping):
                raise TypeError(f"{table} entry must be a mapping")
            memory_ref = _nonblank(link.get("ref"), "basis memory ref")
            if memory_ref in active:
                by_memory[memory_ref].add(_nonblank(link.get("episode_ref"), "basis Episode ref"))
    return {
        memory_ref: tuple(sorted(episodes))
        for memory_ref, episodes in sorted(by_memory.items())
    }


def freeze_question_result(path: Path, result: Mapping[str, object]) -> None:
    """Checkpoint one complete paired question without permitting resume drift."""

    normalized = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != normalized:
            raise ValueError("question result changed across resume")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def wait_for_effect_ready(
    snapshot: Callable[[], dict[str, object]],
    *,
    expected_episodes: int,
    timeout: float = 900,
    interval: float = 1,
) -> dict[str, object]:
    """Wait until canonical consolidation and its disposable index projection agree."""

    if expected_episodes < 0 or timeout <= 0 or interval < 0:
        raise ValueError("invalid effect readiness wait configuration")
    deadline = time.monotonic() + timeout
    while True:
        state = snapshot()
        required = (
            "episodes",
            "consolidated_episodes",
            "jobs",
            "pending_jobs",
            "pending_index_operations",
        )
        if any(type(state.get(field)) is not int for field in required):
            raise TypeError("effect readiness snapshot requires integer counters")
        if (
            state["episodes"] >= expected_episodes
            and state["consolidated_episodes"] >= expected_episodes
            and state["jobs"] > 0
            and state["pending_jobs"] == 0
            and state["pending_index_operations"] == 0
        ):
            return state
        if time.monotonic() >= deadline:
            raise TimeoutError(f"effect preparation incomplete: {state}")
        await asyncio.sleep(interval)


async def evaluate_question(
    *,
    memory: Any,
    episode_search: Any,
    model: Any,
    scorer: Any,
    evaluation_ref: str,
    scope: pb.MemoryScope,
    question: Question,
    history: History,
    oracle: OracleEvidence,
    episode_documents: Sequence[EpisodeDocument],
    learned_basis: Mapping[str, Sequence[str]],
    token_count: Callable[[str], int],
    core_memory_tokens: int,
    max_output_tokens: int,
    episode_evidence_max_bytes: int,
) -> dict[str, object]:
    """Evaluate one question in six isolated contexts without answer writeback."""

    if oracle.question_ref != question.question_ref or oracle.persona_id != question.persona_id:
        raise ValueError("Oracle evidence does not belong to the question")
    if len(episode_documents) != len(history.turns):
        raise ValueError("Episode documents must map one-to-one to observed history turns")

    alignment = align_snippet(oracle.messages, history)
    source_available = alignment["status"] == "matched"
    gold_refs = tuple(
        episode_documents[index].memory_ref
        for index in alignment["episode_indexes"]
    )

    semantic_episodes = semantic_top40(
        episode_search,
        scope=scope,
        query=question.query,
    )

    identity = _identity(evaluation_ref, question.persona_id, question.question_ref)
    source_ref = identity + ":query"
    source = await memory.observe_source_event(pb.ObserveSourceEventRequest(
        idempotency_key=source_ref,
        source_event=pb.SourceEvent(
            scope=scope,
            text=question.query,
            source_ref=source_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"user-{question.persona_id}",
        ),
    ))
    select_run_ref = identity + ":select"
    selected = await memory.select_memory(pb.SelectMemoryRequest(
        scope=scope,
        run_ref=select_run_ref,
        situation_source_event_refs=[source.source_event_ref],
        episode_evidence_max_bytes=episode_evidence_max_bytes,
    ))

    contexts = {
        EffectMode.NONE: build_effect_context(EffectMode.NONE),
        EffectMode.FULL_HISTORY: build_effect_context(
            EffectMode.FULL_HISTORY,
            history=history,
        ),
        EffectMode.ORACLE_EPISODE: build_effect_context(
            EffectMode.ORACLE_EPISODE,
            oracle_episodes=_oracle_documents(oracle),
        ),
        EffectMode.SEMANTIC_TOP40: build_effect_context(
            EffectMode.SEMANTIC_TOP40,
            semantic_episodes=semantic_episodes,
        ),
        EffectMode.CURRENT_CORE: build_effect_context(
            EffectMode.CURRENT_CORE,
            core_context=selected,
            token_count=token_count,
            core_memory_tokens=core_memory_tokens,
        ),
        EffectMode.LEARNED_CORE: build_effect_context(
            EffectMode.LEARNED_CORE,
            core_context=selected,
            token_count=token_count,
            core_memory_tokens=core_memory_tokens,
        ),
    }

    prompt_query = question.query + QUERY_SUFFIX
    # This is the option order used by the existing pinned PersonaMem harness.
    seed = hash(f"{question.persona_id}_{prompt_query}") % 2**32
    mcq_instruction, options = scorer.create_mcq_options(
        None,
        question.correct_answer,
        list(question.incorrect_answers),
        seed=seed,
    )
    correct_option = next(
        option for option, answer in options.items() if answer == question.correct_answer
    )

    answers_by_mode: dict[EffectMode, dict[str, object]] = {}
    for mode in effect_mode_order(
        evaluation_ref,
        question.persona_id,
        question.question_ref,
    ):
        context = contexts[mode]
        if mode in {EffectMode.CURRENT_CORE, EffectMode.LEARNED_CORE} and context.memory_refs:
            await memory.record_memory_delivery(pb.MemoryDeliveryReceipt(
                idempotency_key=f"{identity}:delivery:{mode.value}",
                scope=scope,
                run_ref=select_run_ref,
                memory_context_ref=selected.context_ref,
                delivered_memory_refs=context.memory_refs,
            ))

        instruction_parts = []
        if context.text:
            instruction_parts.append("LONG_TERM_MEMORY\n" + context.text)
        instruction_parts.append(mcq_instruction)
        instructions = "\n\n".join(instruction_parts)
        started = time.perf_counter()
        output = await model.complete(
            instructions=instructions,
            input_text=prompt_query,
            max_output_tokens=max_output_tokens,
        )
        if not isinstance(output, str) or not output.strip():
            raise RuntimeError("model returned empty output")
        predicted = scorer.extract_final_answer(None, output)
        correct = scorer.check_mcq_correctness(
            None,
            predicted,
            question.correct_answer,
            options,
        )
        answers_by_mode[mode] = {
            "evaluation_ref": evaluation_ref,
            "question_ref": question.question_ref,
            "persona_id": question.persona_id,
            "mode": mode.value,
            "correct": bool(correct),
            "correct_option": correct_option,
            "predicted_option": predicted,
            "option_mapping": options,
            "output": output,
            "context_text": context.text,
            "memory_refs": list(context.memory_refs),
            "memory_tokens": token_count(context.text),
            "oracle_label_used": mode is EffectMode.ORACLE_EPISODE,
            "metadata": question.metadata,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": None,
        }

    answers = [answers_by_mode[mode] for mode in EffectMode]

    current_answer = next(
        row for row in answers if row["mode"] == EffectMode.CURRENT_CORE.value
    )
    diagnostic = build_funnel_record(
        question_ref=question.question_ref,
        persona_id=question.persona_id,
        source_available=source_available,
        gold_episode_refs=gold_refs,
        indexed_episode_refs=[item.memory_ref for item in semantic_episodes],
        context=contexts[EffectMode.CURRENT_CORE],
        learned_basis=learned_basis,
        answer_correct=bool(current_answer["correct"]),
    )
    diagnostic["oracle_alignment"] = alignment
    return {
        "question_ref": question.question_ref,
        "persona_id": question.persona_id,
        "answers": answers,
        "diagnostic": diagnostic,
    }


def summarize_effect_results(
    results: Sequence[Mapping[str, object]],
    *,
    bootstrap_samples: int = 2_000,
) -> dict[str, object]:
    """Reject unpaired/leaky results, then summarize effects and the causal funnel."""

    if not results:
        raise ValueError("at least one completed question is required")
    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")

    expected_modes = tuple(mode.value for mode in EffectMode)
    expected_mode_set = set(expected_modes)
    questions: dict[tuple[str, str], dict[str, Mapping[str, object]]] = {}
    diagnostics: list[Mapping[str, object]] = []
    diagnostics_by_question: dict[tuple[str, str], Mapping[str, object]] = {}

    for result in results:
        persona_id = _nonblank(result.get("persona_id"), "persona_id")
        question_ref = _nonblank(result.get("question_ref"), "question_ref")
        key = (persona_id, question_ref)
        if key in questions:
            raise ValueError(f"duplicate question result: {key}")

        raw_answers = result.get("answers")
        if not isinstance(raw_answers, Sequence) or isinstance(raw_answers, (str, bytes)):
            raise ValueError("each question requires all six modes")
        by_mode: dict[str, Mapping[str, object]] = {}
        for answer in raw_answers:
            if not isinstance(answer, Mapping):
                raise TypeError("answer result must be a mapping")
            mode = answer.get("mode")
            if mode in by_mode:
                raise ValueError("each question requires six modes exactly once")
            if not isinstance(mode, str):
                raise ValueError("answer mode must be a string")
            by_mode[mode] = answer
        if len(by_mode) != 6 or set(by_mode) != expected_mode_set:
            raise ValueError("each question requires a complete set of six modes")

        for mode, answer in by_mode.items():
            if (
                answer.get("question_ref") != question_ref
                or answer.get("persona_id") != persona_id
            ):
                raise ValueError("answer identity does not match its question")
            oracle_used = answer.get("oracle_label_used")
            should_use_oracle = mode == EffectMode.ORACLE_EPISODE.value
            if oracle_used is not should_use_oracle:
                raise ValueError("Oracle evidence leaked outside the Oracle mode")
            if type(answer.get("correct")) is not bool:
                raise TypeError("correct must be bool")
            if not isinstance(answer.get("memory_tokens"), (int, float)):
                raise TypeError("memory_tokens must be numeric")
            if answer.get("error") not in {None, ""}:
                raise ValueError("cannot summarize failed question groups")

        diagnostic = result.get("diagnostic")
        if not isinstance(diagnostic, Mapping):
            raise TypeError("diagnostic must be a mapping")
        if diagnostic.get("question_ref") != question_ref or diagnostic.get("persona_id") != persona_id:
            raise ValueError("diagnostic identity does not match its question")
        if diagnostic.get("mode") != EffectMode.CURRENT_CORE.value:
            raise ValueError("funnel diagnostic must describe current_core")
        if diagnostic.get("answer_correct") is not by_mode[EffectMode.CURRENT_CORE.value]["correct"]:
            raise ValueError("funnel correctness must match current_core")

        questions[key] = by_mode
        diagnostics.append(diagnostic)
        diagnostics_by_question[key] = diagnostic

    modes: dict[str, dict[str, object]] = {}
    for mode in expected_modes:
        records = [answers[mode] for answers in questions.values()]
        groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for record in records:
            metadata = record.get("metadata", {})
            if isinstance(metadata, Mapping) and "pref_type" in metadata:
                groups[str(metadata["pref_type"])].append(record)
        modes[mode] = {
            "n": len(records),
            "correct": sum(record["correct"] for record in records),
            "accuracy": statistics.mean(record["correct"] for record in records),
            "invalid_answers": sum(not record.get("predicted_option") for record in records),
            "mean_memory_tokens": statistics.mean(record["memory_tokens"] for record in records),
            "groups": {
                name: {
                    "n": len(group),
                    "accuracy": statistics.mean(record["correct"] for record in group),
                }
                for name, group in sorted(groups.items())
            },
        }

    comparisons = (
        (EffectMode.ORACLE_EPISODE.value, EffectMode.NONE.value),
        (EffectMode.FULL_HISTORY.value, EffectMode.NONE.value),
        (EffectMode.SEMANTIC_TOP40.value, EffectMode.ORACLE_EPISODE.value),
        (EffectMode.CURRENT_CORE.value, EffectMode.SEMANTIC_TOP40.value),
        (EffectMode.LEARNED_CORE.value, EffectMode.CURRENT_CORE.value),
    )
    paired = {
        f"{left}-minus-{right}": _clustered_difference(
            questions,
            left,
            right,
            bootstrap_samples=bootstrap_samples,
        )
        for left, right in comparisons
    }
    causal_keys = tuple(
        key
        for key, diagnostic in diagnostics_by_question.items()
        if diagnostic["source_available"] is True
    )
    unavailable_keys = tuple(key for key in questions if key not in causal_keys)
    stage_effects = {
        "oracle_episode-minus-none|source_available": _conditioned_difference(
            questions,
            diagnostics_by_question,
            EffectMode.ORACLE_EPISODE.value,
            EffectMode.NONE.value,
            condition="source_available",
            bootstrap_samples=bootstrap_samples,
        ),
        "oracle_episode-minus-semantic_top40|source_available": _conditioned_difference(
            questions,
            diagnostics_by_question,
            EffectMode.ORACLE_EPISODE.value,
            EffectMode.SEMANTIC_TOP40.value,
            condition="source_available",
            bootstrap_samples=bootstrap_samples,
        ),
        "semantic_top40-minus-current_core|indexed": _conditioned_difference(
            questions,
            diagnostics_by_question,
            EffectMode.SEMANTIC_TOP40.value,
            EffectMode.CURRENT_CORE.value,
            condition="indexed",
            bootstrap_samples=bootstrap_samples,
        ),
        "current_core-minus-learned_core|delivered": _conditioned_difference(
            questions,
            diagnostics_by_question,
            EffectMode.CURRENT_CORE.value,
            EffectMode.LEARNED_CORE.value,
            condition="delivered",
            bootstrap_samples=bootstrap_samples,
        ),
    }
    return {
        "questions": len(questions),
        "personas": len({persona for persona, _ in questions}),
        "modes": modes,
        "paired": paired,
        "uncertainty": "question-weighted difference; 95% paired persona-cluster bootstrap; seed=0",
        "funnel": summarize_funnel(diagnostics),
        "causal_subset": {
            "questions": len(causal_keys),
            "modes": _subset_accuracies(questions, causal_keys, expected_modes),
            "stage_effects": stage_effects,
        },
        "excluded_from_causal_decision": len(unavailable_keys),
        "source_unavailable": {
            "questions": len(unavailable_keys),
            "modes": _subset_accuracies(questions, unavailable_keys, expected_modes),
        },
        "decision": _decide_conditioned_bottleneck(stage_effects),
    }


def freeze_manifest(path: Path, config: Mapping[str, object]) -> None:
    """Write a manifest once and reject configuration drift on resume."""

    normalized = json.loads(json.dumps(config, ensure_ascii=False, sort_keys=True))
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != normalized:
            raise ValueError("run manifest changed; use a new output directory")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def effect_mode_order(
    evaluation_ref: str,
    persona_id: str,
    question_ref: str,
) -> tuple[EffectMode, ...]:
    """Deterministically interleave arms so provider drift cannot favor one mode."""

    identity = ":".join((
        _nonblank(evaluation_ref, "evaluation_ref"),
        _nonblank(persona_id, "persona_id"),
        _nonblank(question_ref, "question_ref"),
    ))
    return tuple(sorted(
        EffectMode,
        key=lambda mode: hashlib.sha256(
            f"{identity}:{mode.value}".encode()
        ).digest(),
    ))


def _oracle_documents(oracle: OracleEvidence) -> tuple[EpisodeDocument, ...]:
    documents: list[EpisodeDocument] = []
    messages = oracle.messages
    index = 0
    while index < len(messages):
        role, content = messages[index]
        parts = [f"SITUATION [user]\n{content}"] if role == "user" else [
            f"AGENT_ACT [agent]\n{content}"
        ]
        if role == "user" and index + 1 < len(messages) and messages[index + 1][0] == "assistant":
            parts.append(f"AGENT_ACT [agent]\n{messages[index + 1][1]}")
            index += 1
        digest = hashlib.sha256(
            f"{oracle.question_ref}:{index}:{'|'.join(parts)}".encode()
        ).hexdigest()[:16]
        documents.append(EpisodeDocument(f"oracle:{digest}", "\n".join(parts)))
        index += 1
    return tuple(documents)


def _clustered_difference(
    questions: Mapping[tuple[str, str], Mapping[str, Mapping[str, object]]],
    left: str,
    right: str,
    *,
    bootstrap_samples: int,
) -> dict[str, object]:
    by_persona: dict[str, list[int]] = defaultdict(list)
    for (persona, _), answers in questions.items():
        by_persona[persona].append(
            int(answers[left]["correct"]) - int(answers[right]["correct"])
        )
    personas = sorted(by_persona)
    point_values = [value for persona in personas for value in by_persona[persona]]
    rng = random.Random(0)
    samples = sorted(
        statistics.mean(
            value
            for persona in rng.choices(personas, k=len(personas))
            for value in by_persona[persona]
        )
        for _ in range(bootstrap_samples)
    )
    lower = min(int(bootstrap_samples * 0.025), bootstrap_samples - 1)
    upper = min(int(bootstrap_samples * 0.975), bootstrap_samples - 1)
    return {
        "difference": statistics.mean(point_values),
        "ci95": [samples[lower], samples[upper]],
    }


def _conditioned_difference(
    questions: Mapping[tuple[str, str], Mapping[str, Mapping[str, object]]],
    diagnostics: Mapping[tuple[str, str], Mapping[str, object]],
    left: str,
    right: str,
    *,
    condition: str,
    bootstrap_samples: int,
) -> dict[str, object]:
    selected = {
        key: answers
        for key, answers in questions.items()
        if diagnostics[key].get(condition) is True
    }
    if not selected:
        return {"n": 0, "difference": None, "ci95": None}
    return {
        "n": len(selected),
        **_clustered_difference(
            selected,
            left,
            right,
            bootstrap_samples=bootstrap_samples,
        ),
    }


def _subset_accuracies(
    questions: Mapping[tuple[str, str], Mapping[str, Mapping[str, object]]],
    keys: Sequence[tuple[str, str]],
    modes: Sequence[str],
) -> dict[str, dict[str, object]]:
    return {
        mode: {
            "n": len(keys),
            "accuracy": (
                statistics.mean(questions[key][mode]["correct"] for key in keys)
                if keys
                else None
            ),
        }
        for mode in modes
    }


def _decide_conditioned_bottleneck(
    effects: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    answer_comparison = "oracle_episode-minus-none|source_available"
    answer_gap = effects[answer_comparison]["difference"]
    if answer_gap is None:
        return {
            "bottleneck": "evaluation_source_coverage",
            "gap": None,
            "comparison": answer_comparison,
        }
    if not _effect_is_reliably_positive(effects[answer_comparison]):
        return {
            "bottleneck": "answer_model",
            "gap": float(answer_gap),
            "comparison": answer_comparison,
        }
    candidates = (
        ("retrieval", "oracle_episode-minus-semantic_top40|source_available"),
        ("selection_assembly", "semantic_top40-minus-current_core|indexed"),
        ("consolidation", "current_core-minus-learned_core|delivered"),
    )
    available = [
        (name, comparison, effects[comparison]["difference"])
        for name, comparison in candidates
        if _effect_is_reliably_positive(effects[comparison])
    ]
    if not available:
        measured = [
            (comparison, effects[comparison]["difference"])
            for _, comparison in candidates
            if effects[comparison]["difference"] is not None
        ]
        if measured:
            comparison, gap = max(measured, key=lambda item: float(item[1]))
            return {
                "bottleneck": "no_measured_core_bottleneck",
                "gap": float(gap),
                "comparison": comparison,
            }
        return {
            "bottleneck": "evaluation_source_coverage",
            "gap": None,
            "comparison": answer_comparison,
        }
    bottleneck, comparison, gap = max(available, key=lambda item: float(item[2]))
    return {
        "bottleneck": bottleneck,
        "gap": float(gap),
        "comparison": comparison,
    }


def _effect_is_reliably_positive(effect: Mapping[str, object]) -> bool:
    difference = effect.get("difference")
    interval = effect.get("ci95")
    if difference is None or interval is None:
        return False
    if (
        not isinstance(interval, Sequence)
        or isinstance(interval, (str, bytes))
        or len(interval) != 2
    ):
        raise ValueError("effect ci95 must contain two bounds")
    return float(difference) > 0 and float(interval[0]) > 0


def _identity(evaluation_ref: str, persona_id: str, question_ref: str) -> str:
    raw = ":".join((
        _nonblank(evaluation_ref, "evaluation_ref"),
        _nonblank(persona_id, "persona_id"),
        _nonblank(question_ref, "question_ref"),
    ))
    return "personamem-effect:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _scope(evaluation_ref: str, persona_id: str, session_ref: str) -> pb.MemoryScope:
    return pb.MemoryScope(
        tenant_ref=f"personamem-{evaluation_ref}",
        agent_ref="reference-agent",
        relationship_ref=f"persona-{persona_id}",
        session_ref=session_ref,
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()
