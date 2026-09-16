"""CUPID reference Harness: causal history replay and one read-only Select."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb

from .cupid_data import CupidInstance, CupidLabel, CupidSession
from .cupid_effect import (
    PROTOCOL,
    answer_arm_order,
    build_answer_prompt,
    build_judge_prompt,
    judge_arm_order,
    parse_judge_score,
)
from .perma_seed_effect import SeedEffectMode, build_seed_contexts


@dataclass(frozen=True, slots=True)
class PreparedCupidInstance:
    memory_context: pb.MemoryContext
    consolidation_state: Mapping[str, object]
    expected_episodes: int


def freeze_result(path: Path, result: Mapping[str, object]) -> None:
    """Atomically freeze one complete result and reject identity drift."""

    normalized = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != normalized:
            raise ValueError("CUPID result drift on resume")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_frozen_result(
    path: Path,
    *,
    instance: CupidInstance,
    label: CupidLabel,
) -> dict[str, object] | None:
    """Load a completed result only when its frozen identity still matches."""

    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or (
        value.get("protocol") != PROTOCOL
        or value.get("split") != label.split
        or value.get("persona_id") != instance.persona_id
        or value.get("instance_ref") != instance.instance_ref
        or label.persona_id != instance.persona_id
        or label.instance_ref != instance.instance_ref
    ):
        raise ValueError("CUPID result drift on resume")
    return value


def owner_scope(evaluation_ref: str, instance_ref: str) -> pb.MemoryScope:
    """Return the relationship owner without exposing CUPID label identity."""

    evaluation_ref = _nonblank(evaluation_ref, "evaluation_ref")
    opaque = _opaque_instance(instance_ref)
    return pb.MemoryScope(
        tenant_ref="cupid-seed-eval",
        agent_ref="cupid-reference-agent",
        relationship_ref=f"{evaluation_ref}:instance:{opaque}",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


async def evaluate_instance(
    *,
    instance: CupidInstance,
    label: CupidLabel,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    answer_model: Any,
    judge_model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_answer_tokens: int,
    max_judge_tokens: int,
) -> dict[str, object]:
    """Learn once, answer three isolated arms, then reveal labels to the Judge."""

    if (
        label.instance_ref != instance.instance_ref
        or label.persona_id != instance.persona_id
    ):
        raise ValueError("CUPID instance and scorer label identity differ")
    if memory_tokens < 0 or max_answer_tokens <= 0 or max_judge_tokens <= 0:
        raise ValueError("invalid CUPID evaluation token budget")
    prepared = await prepare_instance(
        instance=instance,
        evaluation_ref=evaluation_ref,
        memory=memory,
        settle=settle,
    )
    contexts = build_seed_contexts(
        prepared.memory_context,
        max_tokens=memory_tokens,
        token_count=token_count,
    )

    answers: dict[SeedEffectMode, dict[str, object]] = {}
    for mode in answer_arm_order(instance.instance_ref):
        context = contexts[mode]
        instructions, input_text = build_answer_prompt(
            current_request=instance.current_request,
            memory_text=context.text,
        )
        output = await answer_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_answer_tokens,
        )
        output = _nonblank(output, "CUPID answer output")
        answers[mode] = {
            "mode": mode.value,
            "output": output,
            "judge_output": "",
            "score": 0,
            "error": "",
            "memory_refs": list(context.memory_refs),
            "selected_recollection_refs": list(
                context.selected_recollection_refs
            ),
            "selected_disposition_refs": list(
                context.selected_disposition_refs
            ),
            "rendered_recollection_refs": list(context.recollection_refs),
            "rendered_disposition_refs": list(context.disposition_refs),
            "memory_tokens": context.memory_tokens,
            "answer_request_sha256": _request_sha256(
                instructions, input_text, max_answer_tokens
            ),
            "judge_request_sha256": "",
        }

    for mode in judge_arm_order(instance.instance_ref):
        answer = answers[mode]
        instructions, input_text = build_judge_prompt(
            user_request=instance.current_request,
            ai_response=str(answer["output"]),
            preference=label.preference,
            checklist=label.checklist,
        )
        judge_output = await judge_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_judge_tokens,
        )
        judge_output = _nonblank(judge_output, "CUPID judge output")
        answer["judge_output"] = judge_output
        answer["score"] = parse_judge_score(judge_output)
        answer["judge_request_sha256"] = _request_sha256(
            instructions, input_text, max_judge_tokens
        )

    seed_context = contexts[SeedEffectMode.SEED_ENABLED]
    return {
        "protocol": PROTOCOL,
        "split": label.split,
        "persona_id": instance.persona_id,
        "instance_ref": instance.instance_ref,
        "answers": [answers[mode] for mode in SeedEffectMode],
        "diagnostic": {
            "active_dispositions": _active_disposition_count(
                prepared.consolidation_state
            ),
            "selected_disposition_refs": list(
                seed_context.selected_disposition_refs
            ),
            "rendered_disposition_refs": list(seed_context.disposition_refs),
        },
    }


async def prepare_instance(
    *,
    instance: CupidInstance,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
) -> PreparedCupidInstance:
    """Replay all prior sessions, settle each window, then Select once."""

    evaluation_ref = _nonblank(evaluation_ref, "evaluation_ref")
    opaque = _opaque_instance(instance.instance_ref)
    expected_episodes = 0
    state: Mapping[str, object] = {}
    for session_index, session in enumerate(instance.history_sessions):
        scope = _scope(evaluation_ref, opaque, f"session:{session_index}")
        expected_episodes += await _observe_session(
            memory=memory,
            scope=scope,
            evaluation_ref=evaluation_ref,
            opaque_instance=opaque,
            session_index=session_index,
            session=session,
        )
        state = await settle(scope, expected_episodes)

    scope = _scope(evaluation_ref, opaque, "probe")
    source_ref = f"{evaluation_ref}:{opaque}:probe:situation"
    receipt = await memory.observe_source_event(pb.ObserveSourceEventRequest(
        idempotency_key=f"{source_ref}:observe",
        source_event=pb.SourceEvent(
            scope=scope,
            text=instance.current_request,
            source_ref=source_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"cupid-user:{opaque}",
        ),
    ))
    run_ref = f"{evaluation_ref}:{opaque}:probe:select"
    selected = await memory.select_memory(pb.SelectMemoryRequest(
        scope=scope,
        run_ref=run_ref,
        situation_source_event_refs=[receipt.source_event_ref],
        episode_evidence_max_bytes=0,
    ))
    return PreparedCupidInstance(
        memory_context=selected,
        consolidation_state=state,
        expected_episodes=expected_episodes,
    )


async def _observe_session(
    *,
    memory: Any,
    scope: pb.MemoryScope,
    evaluation_ref: str,
    opaque_instance: str,
    session_index: int,
    session: CupidSession,
) -> int:
    messages = session.messages
    episode_count = 0
    for assistant_index in range(1, len(messages), 2):
        situation_index = assistant_index - 1
        situation = messages[situation_index]
        agent_act = messages[assistant_index]
        if situation.role != "user" or agent_act.role != "assistant":
            raise ValueError("CUPID session does not alternate user and assistant")
        run_ref = (
            f"{evaluation_ref}:{opaque_instance}:history:"
            f"{session_index}:{episode_count}"
        )
        situation_ref = _message_ref(
            evaluation_ref, opaque_instance, session_index, situation_index
        )
        agent_act_ref = _message_ref(
            evaluation_ref, opaque_instance, session_index, assistant_index
        )
        await _observe_role(
            memory=memory,
            scope=scope,
            run_ref=run_ref,
            source_ref=situation_ref,
            text=situation.text,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"cupid-user:{opaque_instance}",
            role=pb.EPISODE_SOURCE_ROLE_SITUATION,
        )
        await _observe_role(
            memory=memory,
            scope=scope,
            run_ref=run_ref,
            source_ref=agent_act_ref,
            text=agent_act.text,
            actor_kind=pb.SOURCE_ACTOR_KIND_AGENT,
            actor_ref="cupid-reference-agent",
            role=pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
        )
        episode_count += 1

        feedback_index = assistant_index + 1
        if feedback_index >= len(messages):
            continue
        feedback = messages[feedback_index]
        if feedback.role != "user":
            raise ValueError("CUPID feedback must be a user message")
        feedback_ref = _message_ref(
            evaluation_ref, opaque_instance, session_index, feedback_index
        )
        await memory.report_outcome(pb.ReportOutcomeRequest(
            idempotency_key=f"{feedback_ref}:outcome:{episode_count - 1}",
            scope=scope,
            run_ref=run_ref,
            source_group_ref=run_ref,
            text=feedback.text,
            related_source_event_refs=[situation_ref, agent_act_ref],
            source_ref=feedback_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"cupid-user:{opaque_instance}",
        ))
    return episode_count


async def _observe_role(
    *,
    memory: Any,
    scope: pb.MemoryScope,
    run_ref: str,
    source_ref: str,
    text: str,
    actor_kind: int,
    actor_ref: str,
    role: int,
) -> None:
    await memory.observe_source_event(pb.ObserveSourceEventRequest(
        idempotency_key=f"{source_ref}:bind:{run_ref}:{role}",
        source_event=pb.SourceEvent(
            scope=scope,
            text=text,
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


def _message_ref(
    evaluation_ref: str,
    opaque_instance: str,
    session_index: int,
    message_index: int,
) -> str:
    return (
        f"{evaluation_ref}:{opaque_instance}:history:"
        f"{session_index}:message:{message_index}"
    )


def _scope(evaluation_ref: str, opaque_instance: str, suffix: str) -> pb.MemoryScope:
    scope = owner_scope(evaluation_ref, f"cupid-{opaque_instance}")
    scope.session_ref = f"{evaluation_ref}:{opaque_instance}:{suffix}"
    return scope


def _opaque_instance(instance_ref: str) -> str:
    value = _nonblank(instance_ref, "instance_ref")
    if not value.startswith("cupid-") or len(value) != len("cupid-") + 64:
        raise ValueError("instance_ref must be an opaque CUPID hash")
    digest = value.removeprefix("cupid-")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("instance_ref must be an opaque CUPID hash")
    return digest


def _active_disposition_count(state: Mapping[str, object]) -> int:
    records = state.get("dispositions", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return 0
    return sum(
        1
        for record in records
        if isinstance(record, Mapping) and record.get("status") == "active"
    )


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def _request_sha256(
    instructions: str, input_text: str, max_output_tokens: int
) -> str:
    request = {
        "instructions": instructions,
        "input_text": input_text,
        "max_output_tokens": max_output_tokens,
    }
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()
