"""Reference Harness for the frozen, read-only PERMA Seed effect protocol."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb

from .perma_seed_data import PROTOCOL, PersonaData, Probe, TimelineSession
from .perma_seed_effect import (
    SeedEffectMode,
    build_seed_contexts,
    effect_mode_order,
    extract_option_token,
)


MCQ_INSTRUCTIONS = """You are answering a personalized assistant benchmark.
Use LONG_TERM_MEMORY when present, but do not quote or discuss it.
Choose the single best option for the user.
Reply with exactly one uppercase option letter and no other text."""


async def run_persona(
    *,
    persona: PersonaData,
    split: str,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    model: Any,
    output_dir: Path,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int,
) -> list[dict[str, object]]:
    """Replay one timeline once and probe three isolated contexts at each date."""

    evaluation_ref = _nonblank(evaluation_ref, "evaluation_ref")
    split = _nonblank(split, "split").upper()
    if memory_tokens < 0 or max_output_tokens <= 0:
        raise ValueError("invalid PERMA Seed token budget")

    sessions = persona.sessions
    next_session = 0
    expected_episodes = 0
    latest_state: Mapping[str, object] = {}
    results: list[dict[str, object]] = []
    for probe in persona.probes:
        while (
            next_session < len(sessions)
            and sessions[next_session].occurred_on <= probe.question_date
        ):
            session = sessions[next_session]
            scope = _scope(
                evaluation_ref,
                persona.persona_id,
                f"session:{next_session}",
            )
            pairs = await _observe_session(
                memory=memory,
                scope=scope,
                evaluation_ref=evaluation_ref,
                persona_id=persona.persona_id,
                session_index=next_session,
                session=session,
            )
            expected_episodes += pairs
            latest_state = await settle(scope, expected_episodes)
            next_session += 1

        path = _question_path(output_dir, probe.question_ref)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            _validate_resumed_result(existing, probe, split)
            results.append(existing)
            continue
        result = await _evaluate_probe(
            persona=persona,
            probe=probe,
            split=split,
            evaluation_ref=evaluation_ref,
            memory=memory,
            model=model,
            token_count=token_count,
            memory_tokens=memory_tokens,
            max_output_tokens=max_output_tokens,
            state=latest_state,
        )
        _freeze_result(path, result)
        results.append(result)
    return results


async def _observe_session(
    *,
    memory: Any,
    scope: pb.MemoryScope,
    evaluation_ref: str,
    persona_id: str,
    session_index: int,
    session: TimelineSession,
) -> int:
    messages = session.messages
    for pair_index in range(0, len(messages), 2):
        situation, agent_act = messages[pair_index:pair_index + 2]
        if situation.role != "user" or agent_act.role != "assistant":
            raise ValueError("PERMA session does not contain complete dialogue pairs")
        run_ref = (
            f"{evaluation_ref}:{persona_id}:history:{session_index}:{pair_index // 2}"
        )
        for suffix, message, actor_kind, actor_ref, role in (
            (
                "user",
                situation,
                pb.SOURCE_ACTOR_KIND_USER,
                f"user-{persona_id}",
                pb.EPISODE_SOURCE_ROLE_SITUATION,
            ),
            (
                "assistant",
                agent_act,
                pb.SOURCE_ACTOR_KIND_AGENT,
                "perma-reference-agent",
                pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
            ),
        ):
            source_ref = f"{run_ref}:{suffix}"
            await memory.observe_source_event(pb.ObserveSourceEventRequest(
                idempotency_key=source_ref,
                source_event=pb.SourceEvent(
                    scope=scope,
                    text=message.text,
                    source_ref=source_ref,
                    actor_kind=actor_kind,
                    actor_ref=actor_ref,
                ),
                episode_binding=pb.EpisodeBinding(
                    run_ref=run_ref,
                    source_group_ref=run_ref,
                    role=role,
                ),
            ))
    if session.trailing_user:
        source_ref = f"{evaluation_ref}:{persona_id}:history:{session_index}:trailing-user"
        await memory.observe_source_event(pb.ObserveSourceEventRequest(
            idempotency_key=source_ref,
            source_event=pb.SourceEvent(
                scope=scope,
                text=session.trailing_user,
                source_ref=source_ref,
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref=f"user-{persona_id}",
            ),
        ))
    return len(messages) // 2


async def _evaluate_probe(
    *,
    persona: PersonaData,
    probe: Probe,
    split: str,
    evaluation_ref: str,
    memory: Any,
    model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int,
    state: Mapping[str, object],
) -> dict[str, object]:
    digest = hashlib.sha256(probe.question_ref.encode()).hexdigest()[:20]
    identity = f"{evaluation_ref}:{persona.persona_id}:probe:{digest}"
    scope = _scope(evaluation_ref, persona.persona_id, f"probe:{digest}")
    source_ref = f"{identity}:situation"
    receipt = await memory.observe_source_event(pb.ObserveSourceEventRequest(
        idempotency_key=source_ref,
        source_event=pb.SourceEvent(
            scope=scope,
            text=probe.question,
            source_ref=source_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"user-{persona.persona_id}",
        ),
    ))
    run_ref = f"{identity}:select"
    selected = await memory.select_memory(pb.SelectMemoryRequest(
        scope=scope,
        run_ref=run_ref,
        situation_source_event_refs=[receipt.source_event_ref],
        episode_evidence_max_bytes=0,
    ))
    contexts = build_seed_contexts(
        selected,
        max_tokens=memory_tokens,
        token_count=token_count,
    )
    label = persona.labels.get(probe.question_ref)
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"missing scorer-only label: {probe.question_ref}")
    label = label.strip().upper()

    answers_by_mode: dict[SeedEffectMode, dict[str, object]] = {}
    input_text = f"QUESTION\n{probe.question}\n\nOPTIONS\n{probe.options}"
    for mode in effect_mode_order(probe.question_ref):
        context = contexts[mode]
        instructions = MCQ_INSTRUCTIONS
        if context.text:
            instructions += "\n\nLONG_TERM_MEMORY\n" + context.text
        started = time.perf_counter()
        output = await model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
        )
        predicted = extract_option_token(output)
        error = "" if predicted else "invalid option token"
        answers_by_mode[mode] = {
            "mode": mode.value,
            "correct": predicted == label,
            "predicted_option": predicted,
            "output": output,
            "error": error,
            "memory_refs": list(context.memory_refs),
            "rendered_recollection_refs": list(context.recollection_refs),
            "rendered_disposition_refs": list(context.disposition_refs),
            "selected_recollection_refs": list(context.selected_recollection_refs),
            "selected_disposition_refs": list(context.selected_disposition_refs),
            "memory_tokens": context.memory_tokens,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    seed_context = contexts[SeedEffectMode.SEED_ENABLED]
    return {
        "protocol": PROTOCOL,
        "split": split,
        "persona_id": persona.persona_id,
        "question_ref": probe.question_ref,
        "task_id": probe.task_id,
        "stage": probe.stage,
        "question_date": probe.question_date.isoformat(),
        "answers": [answers_by_mode[mode] for mode in SeedEffectMode],
        "diagnostic": {
            "active_dispositions": _active_disposition_count(state),
            "selected_disposition_refs": list(
                seed_context.selected_disposition_refs
            ),
            "rendered_disposition_refs": list(seed_context.disposition_refs),
        },
    }


def _active_disposition_count(state: Mapping[str, object]) -> int:
    records = state.get("dispositions", ())
    if not isinstance(records, list) and not isinstance(records, tuple):
        return 0
    return sum(
        1
        for record in records
        if isinstance(record, Mapping) and record.get("status") == "active"
    )


def _scope(evaluation_ref: str, persona_id: str, session: str) -> pb.MemoryScope:
    return pb.MemoryScope(
        tenant_ref="perma-seed-eval",
        agent_ref="perma-reference-agent",
        relationship_ref=f"{evaluation_ref}:persona:{persona_id}",
        session_ref=f"{evaluation_ref}:{persona_id}:{session}",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


def _question_path(output_dir: Path, question_ref: str) -> Path:
    digest = hashlib.sha256(question_ref.encode()).hexdigest()
    return output_dir / "questions" / f"{digest}.json"


def _freeze_result(path: Path, result: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_resumed_result(
    result: Mapping[str, object], probe: Probe, split: str
) -> None:
    if (
        result.get("protocol") != PROTOCOL
        or result.get("split") != split
        or result.get("persona_id") != probe.persona_id
        or result.get("question_ref") != probe.question_ref
        or result.get("task_id") != probe.task_id
        or result.get("stage") != probe.stage
    ):
        raise ValueError("PERMA Seed result drift on resume")


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()
