"""ANCHOR reference Harness: causal replay, one Select, three read-only arms."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb

from .anchor_data import (
    AnchorSession,
    BehaviorInstance,
    BehaviorLabel,
    TrajectoryInstance,
    TrajectoryLabel,
)
from .anchor_effect import (
    PROTOCOL,
    answer_arm_order,
    build_behavior_judge_prompt,
    build_behavior_prompt,
    build_trajectory_prompt,
    judge_arm_order,
    parse_behavior_score,
    parse_trajectory_option,
)
from .perma_seed_effect import SeedEffectContext, SeedEffectMode, build_seed_contexts


AnchorProbe = TrajectoryInstance | BehaviorInstance


@dataclass(frozen=True, slots=True)
class PreparedAnchorProbe:
    memory_context: pb.MemoryContext
    consolidation_state: Mapping[str, object]
    expected_episodes: int


def owner_scope(evaluation_ref: str, instance_ref: str) -> pb.MemoryScope:
    """Create one isolated relationship owner without label semantics."""

    evaluation = _nonblank(evaluation_ref, "evaluation_ref")
    opaque = _opaque_instance(instance_ref)
    return pb.MemoryScope(
        tenant_ref="anchor-v0-eval",
        agent_ref="anchor-reference-agent",
        relationship_ref=f"{evaluation}:instance:{opaque}",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


async def prepare_probe(
    *,
    probe: AnchorProbe,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
) -> PreparedAnchorProbe:
    """Replay the causal prefix, settle each real session, and Select once."""

    evaluation = _nonblank(evaluation_ref, "evaluation_ref")
    opaque = _opaque_instance(probe.instance_ref)
    expected_episodes = 0
    state: Mapping[str, object] = {}
    for session in probe.history_sessions:
        scope = _scope(evaluation, probe.instance_ref, f"session:{session.session_id}")
        expected_episodes += await _observe_session(
            memory=memory,
            scope=scope,
            evaluation_ref=evaluation,
            opaque_instance=opaque,
            session=session,
        )
        state = await settle(scope, expected_episodes)

    scope = _scope(evaluation, probe.instance_ref, "probe")
    source_ref = f"{evaluation}:{opaque}:probe:situation"
    receipt = await memory.observe_source_event(
        pb.ObserveSourceEventRequest(
            idempotency_key=f"{source_ref}:observe",
            source_event=pb.SourceEvent(
                scope=scope,
                text=_nonblank(probe.current_request, "current_request"),
                source_ref=source_ref,
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref=f"anchor-user:{opaque}",
            ),
        )
    )
    run_ref = f"{evaluation}:{opaque}:probe:select"
    selected = await memory.select_memory(
        pb.SelectMemoryRequest(
            scope=scope,
            run_ref=run_ref,
            situation_source_event_refs=[receipt.source_event_ref],
            episode_evidence_max_bytes=0,
        )
    )
    return PreparedAnchorProbe(
        memory_context=selected,
        consolidation_state=state,
        expected_episodes=expected_episodes,
    )


async def generate_trajectory_answers(
    *,
    instance: TrajectoryInstance,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    answer_model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int = 4,
) -> dict[str, object]:
    """Generate all three answer arms without accepting a scorer label."""

    prepared, contexts = await _prepare_contexts(
        instance=instance,
        evaluation_ref=evaluation_ref,
        memory=memory,
        settle=settle,
        token_count=token_count,
        memory_tokens=memory_tokens,
    )
    _positive_int(max_output_tokens, "max_output_tokens")
    answers: dict[SeedEffectMode, dict[str, object]] = {}
    for mode in answer_arm_order(instance.instance_ref):
        context = contexts[mode]
        instructions, input_text = build_trajectory_prompt(
            persona_text=instance.persona_text,
            current_request=instance.current_request,
            options=instance.options,
            memory_text=context.text,
        )
        output = await answer_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
        )
        parse_trajectory_option(output)
        answers[mode] = _answer_record(
            context,
            output=output,
            request_sha256=_model_request_sha256(
                instructions, input_text, max_output_tokens
            ),
        )
    return _generated_result(instance, prepared, contexts, answers, "trajectory")


def score_trajectory_answers(
    result: Mapping[str, object], label: TrajectoryLabel
) -> dict[str, object]:
    """Apply the exact label only after all answer arms have been generated."""

    scored = _mutable_result(result, kind="trajectory", instance_ref=label.instance_ref)
    answers = _mutable_answers(scored)
    if len(answers) != 3:
        raise ValueError("ANCHOR trajectory result requires three answers")
    for answer in answers:
        if "correct" in answer or "option" in answer:
            raise ValueError("ANCHOR trajectory result was already scored")
        option = parse_trajectory_option(answer.get("output"))
        answer["option"] = option
        answer["correct"] = "ABCD".index(option) == label.correct_index
    scored["family"] = label.family
    return scored


async def generate_behavior_answers(
    *,
    instance: BehaviorInstance,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    answer_model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int = 1_024,
) -> dict[str, object]:
    """Generate all three user-visible responses without accepting a rubric."""

    prepared, contexts = await _prepare_contexts(
        instance=instance,
        evaluation_ref=evaluation_ref,
        memory=memory,
        settle=settle,
        token_count=token_count,
        memory_tokens=memory_tokens,
    )
    _positive_int(max_output_tokens, "max_output_tokens")
    answers: dict[SeedEffectMode, dict[str, object]] = {}
    for mode in answer_arm_order(instance.instance_ref):
        context = contexts[mode]
        instructions, input_text = build_behavior_prompt(
            persona_text=instance.persona_text,
            current_request=instance.current_request,
            memory_text=context.text,
        )
        output = await answer_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
        )
        _nonblank(output, "ANCHOR behavior answer output")
        answers[mode] = _answer_record(
            context,
            output=output,
            request_sha256=_model_request_sha256(
                instructions, input_text, max_output_tokens
            ),
        )
    return _generated_result(instance, prepared, contexts, answers, "behavior")


async def judge_behavior_answers(
    result: Mapping[str, object],
    label: BehaviorLabel,
    *,
    judge_model: Any,
    max_judge_tokens: int = 4,
) -> dict[str, object]:
    """Blind-judge three already-complete responses with one single-token call each."""

    _positive_int(max_judge_tokens, "max_judge_tokens")
    judged = _mutable_result(result, kind="behavior", instance_ref=label.instance_ref)
    answers = _mutable_answers(judged)
    if len(answers) != 3:
        raise ValueError("ANCHOR behavior result requires three answers")
    by_mode = _answers_by_mode(answers)
    for answer in answers:
        if any(
            field in answer
            for field in ("judge_output", "judge_request_sha256", "score")
        ):
            raise ValueError("ANCHOR behavior result was already judged")
    persona = _nonblank(judged.get("persona_text"), "ANCHOR result persona")
    current_request = _nonblank(
        judged.get("current_request"), "ANCHOR result current_request"
    )
    for mode in judge_arm_order(label.instance_ref):
        answer = by_mode[mode]
        instructions, input_text = build_behavior_judge_prompt(
            persona_text=persona,
            evidence_text=label.evidence_text,
            current_request=current_request,
            response=_nonblank(answer.get("output"), "ANCHOR behavior answer"),
            rubric=label.rubric,
        )
        output = await judge_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_judge_tokens,
        )
        score = parse_behavior_score(output)
        answer["judge_output"] = output
        answer["score"] = score
        answer["judge_request_sha256"] = _model_request_sha256(
            instructions, input_text, max_judge_tokens
        )
    judged["dimension"] = label.dimension
    return judged


def probe_request_sha256(probe: AnchorProbe) -> str:
    """Bind one probe to all label-free learning and answer inputs."""

    document: dict[str, object] = {
        "kind": "trajectory" if isinstance(probe, TrajectoryInstance) else "behavior",
        "instance_ref": probe.instance_ref,
        "bank_id": probe.bank_id,
        "persona_text": probe.persona_text,
        "history": [
            {
                "session_id": session.session_id,
                "messages": [
                    {
                        "session_id": message.session_id,
                        "turn": message.turn,
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in session.messages
                ],
            }
            for session in probe.history_sessions
        ],
        "current_request": probe.current_request,
    }
    if isinstance(probe, TrajectoryInstance):
        document["options"] = list(probe.options)
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def freeze_result(path: Path, result: Mapping[str, object]) -> None:
    """Atomically freeze one complete result and reject resume drift."""

    normalized = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    destination = Path(path)
    if destination.exists():
        current = json.loads(destination.read_text(encoding="utf-8"))
        if current != normalized:
            raise ValueError("ANCHOR result drift on resume")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def load_frozen_result(
    path: Path, *, instance_ref: str
) -> dict[str, object] | None:
    """Load a completed result only when its frozen identity still matches."""

    source = Path(path)
    if not source.exists():
        return None
    value = json.loads(source.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("protocol") != PROTOCOL
        or value.get("instance_ref") != instance_ref
        or value.get("kind") not in {"trajectory", "behavior"}
    ):
        raise ValueError("ANCHOR result identity drift on resume")
    return value


async def _prepare_contexts(
    *,
    instance: AnchorProbe,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    token_count: Callable[[str], int],
    memory_tokens: int,
) -> tuple[PreparedAnchorProbe, dict[SeedEffectMode, SeedEffectContext]]:
    if type(memory_tokens) is not int or memory_tokens < 0:
        raise ValueError("memory_tokens must be a nonnegative integer")
    prepared = await prepare_probe(
        probe=instance,
        evaluation_ref=evaluation_ref,
        memory=memory,
        settle=settle,
    )
    contexts = build_seed_contexts(
        prepared.memory_context,
        max_tokens=memory_tokens,
        token_count=token_count,
    )
    return prepared, contexts


async def _observe_session(
    *,
    memory: Any,
    scope: pb.MemoryScope,
    evaluation_ref: str,
    opaque_instance: str,
    session: AnchorSession,
) -> int:
    messages = session.messages
    if not messages or len(messages) % 2:
        raise ValueError("ANCHOR history session must contain complete turns")
    episode_count = 0
    for assistant_index in range(1, len(messages), 2):
        situation = messages[assistant_index - 1]
        agent_act = messages[assistant_index]
        if (
            situation.role != "user"
            or agent_act.role != "assistant"
            or situation.session_id != session.session_id
            or agent_act.session_id != session.session_id
            or situation.turn != agent_act.turn
        ):
            raise ValueError("ANCHOR history must alternate user and assistant")
        run_ref = (
            f"{evaluation_ref}:{opaque_instance}:history:"
            f"session:{session.session_id}:turn:{situation.turn}"
        )
        situation_ref = _message_ref(
            evaluation_ref,
            opaque_instance,
            session.session_id,
            situation.turn,
            "user",
        )
        agent_act_ref = _message_ref(
            evaluation_ref,
            opaque_instance,
            session.session_id,
            agent_act.turn,
            "assistant",
        )
        await _observe_role(
            memory=memory,
            scope=scope,
            run_ref=run_ref,
            source_ref=situation_ref,
            text=situation.content,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"anchor-user:{opaque_instance}",
            role=pb.EPISODE_SOURCE_ROLE_SITUATION,
        )
        await _observe_role(
            memory=memory,
            scope=scope,
            run_ref=run_ref,
            source_ref=agent_act_ref,
            text=agent_act.content,
            actor_kind=pb.SOURCE_ACTOR_KIND_AGENT,
            actor_ref="anchor-reference-agent",
            role=pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
        )
        episode_count += 1

        next_user_index = assistant_index + 1
        if next_user_index >= len(messages):
            continue
        next_user = messages[next_user_index]
        if next_user.role != "user" or next_user.session_id != session.session_id:
            raise ValueError("ANCHOR feedback must be a same-session user turn")
        outcome_ref = _message_ref(
            evaluation_ref,
            opaque_instance,
            session.session_id,
            next_user.turn,
            "user",
        )
        await memory.report_outcome(
            pb.ReportOutcomeRequest(
                idempotency_key=f"{outcome_ref}:outcome:{situation.turn}",
                scope=scope,
                run_ref=run_ref,
                source_group_ref=run_ref,
                text=next_user.content,
                related_source_event_refs=[situation_ref, agent_act_ref],
                source_ref=outcome_ref,
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref=f"anchor-user:{opaque_instance}",
            )
        )
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
    await memory.observe_source_event(
        pb.ObserveSourceEventRequest(
            idempotency_key=f"{source_ref}:bind:{run_ref}:{role}",
            source_event=pb.SourceEvent(
                scope=scope,
                text=_nonblank(text, "source text"),
                source_ref=source_ref,
                actor_kind=actor_kind,
                actor_ref=actor_ref,
            ),
            episode_binding=pb.EpisodeBinding(
                run_ref=run_ref,
                source_group_ref=run_ref,
                role=role,
            ),
        )
    )


def _generated_result(
    instance: AnchorProbe,
    prepared: PreparedAnchorProbe,
    contexts: Mapping[SeedEffectMode, SeedEffectContext],
    answers: Mapping[SeedEffectMode, Mapping[str, object]],
    kind: str,
) -> dict[str, object]:
    seed_context = contexts[SeedEffectMode.SEED_ENABLED]
    return {
        "protocol": PROTOCOL,
        "kind": kind,
        "instance_ref": instance.instance_ref,
        "bank_id": instance.bank_id,
        "request_sha256": probe_request_sha256(instance),
        "persona_text": instance.persona_text,
        "current_request": instance.current_request,
        "answers": [dict(answers[mode]) for mode in SeedEffectMode],
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


def _answer_record(
    context: SeedEffectContext,
    *,
    output: object,
    request_sha256: str,
) -> dict[str, object]:
    if not isinstance(output, str) or not output.strip():
        raise ValueError("ANCHOR model output must be nonblank text")
    return {
        "mode": context.mode.value,
        "output": output,
        "error": "",
        "memory_refs": list(context.memory_refs),
        "selected_recollection_refs": list(context.selected_recollection_refs),
        "selected_disposition_refs": list(context.selected_disposition_refs),
        "rendered_recollection_refs": list(context.recollection_refs),
        "rendered_disposition_refs": list(context.disposition_refs),
        "memory_tokens": context.memory_tokens,
        "answer_request_sha256": request_sha256,
    }


def _mutable_result(
    result: Mapping[str, object], *, kind: str, instance_ref: str
) -> dict[str, object]:
    value = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if (
        not isinstance(value, dict)
        or value.get("protocol") != PROTOCOL
        or value.get("kind") != kind
        or value.get("instance_ref") != instance_ref
    ):
        raise ValueError("ANCHOR result and scorer label identity differ")
    return value


def _mutable_answers(result: dict[str, object]) -> list[dict[str, object]]:
    answers = result.get("answers")
    if not isinstance(answers, list) or not all(
        isinstance(answer, dict) for answer in answers
    ):
        raise ValueError("ANCHOR result requires answer records")
    return answers  # type: ignore[return-value]


def _answers_by_mode(
    answers: Sequence[Mapping[str, object]],
) -> dict[SeedEffectMode, dict[str, object]]:
    by_mode: dict[SeedEffectMode, dict[str, object]] = {}
    for raw in answers:
        if not isinstance(raw, dict):
            raise ValueError("ANCHOR answer record must be mutable")
        try:
            mode = SeedEffectMode(raw.get("mode"))
        except (TypeError, ValueError) as error:
            raise ValueError("ANCHOR answer mode is invalid") from error
        if mode in by_mode:
            raise ValueError("ANCHOR answer mode is duplicated")
        by_mode[mode] = raw
    if set(by_mode) != set(SeedEffectMode):
        raise ValueError("ANCHOR result requires exactly three modes")
    return by_mode


def _scope(evaluation_ref: str, instance_ref: str, suffix: str) -> pb.MemoryScope:
    scope = owner_scope(evaluation_ref, instance_ref)
    scope.session_ref = f"{evaluation_ref}:{_opaque_instance(instance_ref)}:{suffix}"
    return scope


def _message_ref(
    evaluation_ref: str,
    opaque_instance: str,
    session_id: int,
    turn: int,
    role: str,
) -> str:
    return (
        f"{evaluation_ref}:{opaque_instance}:history:"
        f"session:{session_id}:turn:{turn}:{role}"
    )


def _opaque_instance(instance_ref: str) -> str:
    return hashlib.sha256(_nonblank(instance_ref, "instance_ref").encode()).hexdigest()


def _active_disposition_count(state: Mapping[str, object]) -> int:
    records = state.get("dispositions", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return 0
    return sum(
        1
        for record in records
        if isinstance(record, Mapping) and record.get("status") == "active"
    )


def _model_request_sha256(
    instructions: str, input_text: str, max_output_tokens: int
) -> str:
    request = {
        "instructions": instructions,
        "input_text": input_text,
        "max_output_tokens": max_output_tokens,
    }
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()
