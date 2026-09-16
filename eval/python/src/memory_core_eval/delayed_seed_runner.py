"""Reference runner for the Seed-essential delayed four-arm evaluation."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb

from .delayed_seed_data import (
    DelayedSeedCounterfactual,
    DelayedSeedInstance,
    DelayedSeedLabel,
    DelayedSeedSession,
    PROTOCOL,
)
from .delayed_seed_effect import (
    PAIRWISE_COMPARISONS,
    DelayedSeedContext,
    DelayedSeedMode,
    answer_arm_order,
    build_answer_prompt,
    build_delayed_contexts,
    build_pairwise_prompt,
    parse_pairwise_token,
)


@dataclass(frozen=True, slots=True)
class PreparedDelayedInstance:
    memory_context: pb.MemoryContext
    consolidation_states: tuple[Mapping[str, object], ...]
    expected_episodes: int


async def prepare_delayed_instance(
    *,
    instance: DelayedSeedInstance,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
) -> PreparedDelayedInstance:
    """Replay three causal sessions one at a time, then issue one read-only probe."""

    evaluation = _nonblank(evaluation_ref, "evaluation_ref")
    opaque = _opaque(instance.instance_ref)
    expected_episodes = 0
    states: list[Mapping[str, object]] = []
    for index, session in enumerate(instance.history_sessions):
        scope = _scope(
            evaluation,
            opaque,
            f"history:{index}:{_opaque(session.session_ref)[:16]}",
        )
        await _observe_session(
            session=session,
            session_index=index,
            evaluation_ref=evaluation,
            opaque_instance=opaque,
            scope=scope,
            memory=memory,
        )
        expected_episodes += 1
        states.append(await settle(scope, expected_episodes))

    probe_scope = _scope(evaluation, opaque, "probe")
    source_ref = f"{evaluation}:{opaque}:probe:situation"
    receipt = await memory.observe_source_event(
        pb.ObserveSourceEventRequest(
            idempotency_key=f"{source_ref}:observe",
            source_event=pb.SourceEvent(
                scope=probe_scope,
                text=_nonblank(instance.current_request, "current_request"),
                source_ref=source_ref,
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref=f"delayed-user:{opaque}",
            ),
        )
    )
    run_ref = f"{evaluation}:{opaque}:probe:select"
    selected = await memory.select_memory(
        pb.SelectMemoryRequest(
            scope=probe_scope,
            run_ref=run_ref,
            situation_source_event_refs=[receipt.source_event_ref],
            episode_evidence_max_bytes=0,
        )
    )
    return PreparedDelayedInstance(
        memory_context=selected,
        consolidation_states=tuple(states),
        expected_episodes=expected_episodes,
    )


async def generate_delayed_answers(
    *,
    instance: DelayedSeedInstance,
    counterfactual: DelayedSeedCounterfactual,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    answer_model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int = 1_024,
) -> dict[str, object]:
    """Generate all four arms without accepting or loading a scorer label."""

    if counterfactual.instance_ref != instance.instance_ref:
        raise ValueError("delayed Seed counterfactual identity drift")
    _positive_int(max_output_tokens, "max_output_tokens")
    prepared = await prepare_delayed_instance(
        instance=instance,
        evaluation_ref=evaluation_ref,
        memory=memory,
        settle=settle,
    )
    contexts = build_delayed_contexts(
        prepared.memory_context,
        counterfactual=counterfactual,
        max_tokens=memory_tokens,
        token_count=token_count,
    )
    answers: dict[DelayedSeedMode, dict[str, object]] = {}
    for mode in answer_arm_order(instance.instance_ref):
        context = contexts[mode]
        instructions, input_text = build_answer_prompt(
            persona_text=instance.persona_text,
            current_request=instance.current_request,
            memory_text=context.text,
        )
        output = await answer_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
        )
        answers[mode] = _answer_record(
            context,
            output=output,
            request_sha256=_request_sha256(
                instructions, input_text, max_output_tokens
            ),
        )

    learned = contexts[DelayedSeedMode.LEARNED_SEED]
    final_state = prepared.consolidation_states[-1] if prepared.consolidation_states else {}
    return {
        "protocol": PROTOCOL,
        "instance_ref": instance.instance_ref,
        "pattern_ref": instance.pattern_ref,
        "request_sha256": delayed_instance_request_sha256(instance),
        "persona_text": instance.persona_text,
        "current_request": instance.current_request,
        "answers": [answers[mode] for mode in DelayedSeedMode],
        "diagnostic": {
            "expected_episodes": prepared.expected_episodes,
            "settled_active_dispositions": [
                _active_disposition_count(state)
                for state in prepared.consolidation_states
            ],
            "active_dispositions": _active_disposition_count(final_state),
            "active_recollections": _active_recollection_count(final_state),
            "selected_disposition_refs": list(
                learned.selected_disposition_refs
            ),
            "rendered_disposition_refs": list(learned.disposition_refs),
        },
    }


async def judge_delayed_answers(
    result: Mapping[str, object],
    label: DelayedSeedLabel,
    *,
    judge_model: Any,
    max_judge_tokens: int = 1_024,
) -> dict[str, object]:
    """Apply the scorer-only target after generation, in both response orders."""

    _positive_int(max_judge_tokens, "max_judge_tokens")
    judged = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if (
        not isinstance(judged, dict)
        or judged.get("protocol") != PROTOCOL
        or judged.get("instance_ref") != label.instance_ref
        or judged.get("pattern_ref") != label.pattern_ref
    ):
        raise ValueError("delayed Seed result and scorer label identity drift")
    if "pairwise" in judged:
        raise ValueError("delayed Seed result was already judged")
    answers = _answers_by_mode(judged.get("answers"))
    persona = _nonblank(judged.get("persona_text"), "persona_text")
    current_request = _nonblank(
        judged.get("current_request"), "current_request"
    )

    jobs: list[tuple[str, str, DelayedSeedMode, DelayedSeedMode]] = []
    for left, right in PAIRWISE_COMPARISONS:
        comparison = f"{left.value}_vs_{right.value}"
        jobs.extend(
            (
                (comparison, "forward", left, right),
                (comparison, "reverse", right, left),
            )
        )
    jobs.sort(
        key=lambda job: hashlib.sha256(
            f"delayed-seed-judge-v1:{label.instance_ref}:{job[0]}:{job[1]}".encode()
        ).digest()
    )

    records: list[dict[str, object]] = []
    for comparison, direction, a_mode, b_mode in jobs:
        instructions, input_text = build_pairwise_prompt(
            persona_text=persona,
            current_request=current_request,
            target_behavior=label.target_behavior,
            response_a=_nonblank(answers[a_mode].get("output"), "response_a"),
            response_b=_nonblank(answers[b_mode].get("output"), "response_b"),
        )
        output = await judge_model.complete(
            instructions=instructions,
            input_text=input_text,
            max_output_tokens=max_judge_tokens,
        )
        token = parse_pairwise_token(output)
        records.append(
            {
                "comparison": comparison,
                "direction": direction,
                "a_mode": a_mode.value,
                "b_mode": b_mode.value,
                "judge_output": token,
                "judge_request_sha256": _request_sha256(
                    instructions, input_text, max_judge_tokens
                ),
            }
        )
    judged["pairwise"] = records
    return judged


def freeze_result(path: Path, result: Mapping[str, object]) -> None:
    """Atomically freeze one complete result and reject overwrite drift."""

    normalized = json.loads(json.dumps(result, ensure_ascii=False, sort_keys=True))
    destination = Path(path)
    if destination.exists():
        current = json.loads(destination.read_text(encoding="utf-8"))
        if current != normalized:
            raise ValueError("delayed Seed result drift on resume")
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
    """Load a complete result only when its protocol and identity still match."""

    source = Path(path)
    if not source.exists():
        return None
    value = json.loads(source.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("protocol") != PROTOCOL
        or value.get("instance_ref") != instance_ref
    ):
        raise ValueError("delayed Seed result identity drift on resume")
    return value


async def _observe_session(
    *,
    session: DelayedSeedSession,
    session_index: int,
    evaluation_ref: str,
    opaque_instance: str,
    scope: pb.MemoryScope,
    memory: Any,
) -> None:
    run_ref = f"{evaluation_ref}:{opaque_instance}:history:{session_index}:run"
    situation_ref = f"{run_ref}:situation"
    agent_act_ref = f"{run_ref}:agent-act"
    for source_ref, text, actor_kind, actor_ref, role in (
        (
            situation_ref,
            session.situation,
            pb.SOURCE_ACTOR_KIND_USER,
            f"delayed-user:{opaque_instance}",
            pb.EPISODE_SOURCE_ROLE_SITUATION,
        ),
        (
            agent_act_ref,
            session.agent_act,
            pb.SOURCE_ACTOR_KIND_AGENT,
            "delayed-reference-agent",
            pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
        ),
    ):
        await memory.observe_source_event(
            pb.ObserveSourceEventRequest(
                idempotency_key=f"{source_ref}:observe",
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
    outcome_ref = f"{run_ref}:outcome"
    await memory.report_outcome(
        pb.ReportOutcomeRequest(
            idempotency_key=f"{outcome_ref}:report",
            scope=scope,
            run_ref=run_ref,
            source_group_ref=run_ref,
            text=_nonblank(session.outcome, "outcome"),
            related_source_event_refs=[situation_ref, agent_act_ref],
            source_ref=outcome_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref=f"delayed-user:{opaque_instance}",
        )
    )


def _scope(evaluation_ref: str, opaque: str, suffix: str) -> pb.MemoryScope:
    return pb.MemoryScope(
        tenant_ref="seed-essential-delayed-v1-eval",
        agent_ref="delayed-reference-agent",
        relationship_ref=f"{evaluation_ref}:instance:{opaque}",
        session_ref=f"{evaluation_ref}:{opaque}:{suffix}",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


def _answer_record(
    context: DelayedSeedContext, *, output: object, request_sha256: str
) -> dict[str, object]:
    answer = _nonblank(output, "answer output")
    return {
        "mode": context.mode.value,
        "output": answer,
        "error": "",
        "memory_refs": list(context.memory_refs),
        "selected_recollection_refs": list(context.selected_recollection_refs),
        "selected_disposition_refs": list(context.selected_disposition_refs),
        "rendered_recollection_refs": list(context.recollection_refs),
        "rendered_disposition_refs": list(context.disposition_refs),
        "memory_tokens": context.memory_tokens,
        "memory_budget": context.memory_budget,
        "answer_request_sha256": request_sha256,
    }


def _answers_by_mode(value: object) -> dict[DelayedSeedMode, Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("delayed Seed result requires four answer arms")
    answers: dict[DelayedSeedMode, Mapping[str, object]] = {}
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("delayed Seed answer must be an object")
        try:
            mode = DelayedSeedMode(raw.get("mode"))
        except (TypeError, ValueError) as error:
            raise ValueError("delayed Seed answer mode is invalid") from error
        if mode in answers:
            raise ValueError("delayed Seed answer mode is duplicated")
        _nonblank(raw.get("output"), "answer output")
        answers[mode] = raw
    if set(answers) != set(DelayedSeedMode):
        raise ValueError("delayed Seed result requires exactly four modes")
    return answers


def _active_disposition_count(state: Mapping[str, object]) -> int:
    records = state.get("dispositions", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return 0
    return sum(
        1
        for record in records
        if isinstance(record, Mapping) and record.get("status") == "active"
    )


def _active_recollection_count(state: Mapping[str, object]) -> int:
    records = state.get("recollections", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return 0
    return sum(
        1
        for record in records
        if isinstance(record, Mapping) and record.get("status") == "active"
    )


def delayed_instance_request_sha256(instance: DelayedSeedInstance) -> str:
    """Hash only the label-free learning and answer inputs for one instance."""
    value = {
        "instance_ref": instance.instance_ref,
        "pattern_ref": instance.pattern_ref,
        "persona_text": instance.persona_text,
        "history_sessions": [
            {
                "session_ref": session.session_ref,
                "situation": session.situation,
                "agent_act": session.agent_act,
                "outcome": session.outcome,
            }
            for session in instance.history_sessions
        ],
        "current_request": instance.current_request,
    }
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _request_sha256(
    instructions: str, input_text: str, max_output_tokens: int
) -> str:
    value = {
        "instructions": instructions,
        "input_text": input_text,
        "max_output_tokens": max_output_tokens,
    }
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _opaque(value: str) -> str:
    return hashlib.sha256(_nonblank(value, "reference").encode()).hexdigest()


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()
