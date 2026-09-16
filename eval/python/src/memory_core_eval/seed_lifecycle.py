"""Fixed companion lifecycle proving the Disposition causal boundary."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb, render_memory_context

from .perma_seed_effect import extract_option_token


class LifecycleKind(str, Enum):
    POSITIVE_COMPLETE = "positive_complete"
    NO_DELIVERY = "no_delivery"
    NO_OUTCOME = "no_outcome"
    NEGATIVE_COMPLETE = "negative_complete"


@dataclass(frozen=True, slots=True)
class LifecycleCase:
    kind: LifecycleKind
    record_delivery: bool
    outcome_text: str | None


FORMATION_TURNS = (
    (
        "A product launch had so many moving pieces that I froze. Once we named what mattered most before discussing solutions, I could think again.",
        "We named the most important concern before considering possible solutions.",
    ),
    (
        "Planning my move overwhelmed me. Saying the priorities aloud before looking at solutions made the next step manageable.",
        "We clarified the priorities together before choosing a next step.",
    ),
    (
        "Preparing a conference agenda left me stuck among too many competing tasks. Once we identified the one outcome that mattered most, I could move forward.",
        "We identified the most important outcome before working through the agenda.",
    ),
)

QUERY = "This complicated plan has left me overwhelmed and unable to tell where to begin."
DELAYED_QUERY = QUERY
ACTION_OPTIONS = {
    "A": (
        "Let's pause before solutions and name the top priority first: "
        "what matters most here?"
    ),
    "B": "Here is a complete ten-step solution to follow immediately.",
}

_MODEL_INSTRUCTIONS = """You are the companion in a fixed behavior-choice test.
Use LONG_TERM_MEMORY when present. Choose the response that best fits this user now.
Reply with exactly one uppercase option letter and no other text."""


def lifecycle_cases() -> tuple[LifecycleCase, ...]:
    positive = "Yes, naming priorities before solutions helped me start."
    return (
        LifecycleCase(LifecycleKind.POSITIVE_COMPLETE, True, positive),
        LifecycleCase(LifecycleKind.NO_DELIVERY, False, positive),
        LifecycleCase(LifecycleKind.NO_OUTCOME, True, None),
        LifecycleCase(
            LifecycleKind.NEGATIVE_COMPLETE,
            True,
            (
                "That made things harder: sorting priorities increased the overwhelm, "
                "while one concrete next step would have been manageable."
            ),
        ),
    )


async def run_lifecycle_case(
    *,
    case: LifecycleCase,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int,
) -> dict[str, object]:
    """Run one isolated formation/activation/feedback/control chain."""

    evaluation_ref = _validate_run_inputs(
        evaluation_ref=evaluation_ref,
        case=case,
        memory_tokens=memory_tokens,
        max_output_tokens=max_output_tokens,
    )
    lane = case.kind.value
    before, formed_episode_count = await _form_lifecycle_seed(
        evaluation_ref=evaluation_ref,
        lane=lane,
        memory=memory,
        settle=settle,
        expected_episodes_before=0,
    )
    return await _run_feedback_case(
        case=case,
        evaluation_ref=evaluation_ref,
        lane=lane,
        before=before,
        expected_episodes=formed_episode_count + 1,
        memory=memory,
        settle=settle,
        model=model,
        token_count=token_count,
        memory_tokens=memory_tokens,
        max_output_tokens=max_output_tokens,
    )


async def _form_lifecycle_seed(
    *,
    evaluation_ref: str,
    lane: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    expected_episodes_before: int,
) -> tuple[Mapping[str, object], int]:
    before: Mapping[str, object] = {}
    for offset, (situation, agent_act) in enumerate(FORMATION_TURNS, start=1):
        scope = _scope(evaluation_ref, lane, f"formation-{offset}")
        await _observe_complete_turn(
            memory=memory,
            scope=scope,
            identity=_identity(evaluation_ref, lane, f"formation-{offset}"),
            situation=situation,
            agent_act=agent_act,
        )
        before = await settle(scope, expected_episodes_before + offset)
        if offset >= 2 and _active_dispositions(before):
            return before, expected_episodes_before + offset
    raise RuntimeError("formation produced no active Disposition")


async def _run_feedback_case(
    *,
    case: LifecycleCase,
    evaluation_ref: str,
    lane: str,
    before: Mapping[str, object],
    expected_episodes: int,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    model: Any,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int,
) -> dict[str, object]:
    formed = _active_dispositions(before)
    if not formed:
        raise RuntimeError("feedback chain requires an active Disposition")

    feedback_scope = _scope(evaluation_ref, lane, f"{case.kind.value}-feedback")
    feedback_identity = _identity(
        evaluation_ref, lane, f"{case.kind.value}-feedback"
    )
    run_ref = feedback_identity + ":run"
    group_ref = feedback_identity + ":source-group"
    situation_ref = feedback_identity + ":situation"
    situation = await memory.observe_source_event(pb.ObserveSourceEventRequest(
        idempotency_key=situation_ref,
        source_event=pb.SourceEvent(
            scope=feedback_scope,
            text=QUERY,
            source_ref=situation_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref="seed-lifecycle-user",
        ),
        episode_binding=pb.EpisodeBinding(
            run_ref=run_ref,
            source_group_ref=group_ref,
            role=pb.EPISODE_SOURCE_ROLE_SITUATION,
        ),
    ))
    selected = await memory.select_memory(pb.SelectMemoryRequest(
        scope=feedback_scope,
        run_ref=run_ref,
        situation_source_event_refs=[situation.source_event_ref],
        episode_evidence_max_bytes=0,
    ))
    rendered = render_memory_context(
        selected,
        max_tokens=memory_tokens,
        token_count=token_count,
    )
    selected_dispositions = tuple(
        item.memory_ref for item in selected.dispositions if item.text.strip()
    )
    rendered_refs = set(rendered.memory_refs)
    rendered_dispositions = tuple(
        ref for ref in selected_dispositions if ref in rendered_refs
    )
    if not rendered_dispositions:
        raise RuntimeError("relevant probe rendered no Disposition")

    delivery_ref = ""
    if case.record_delivery:
        delivery = await memory.record_memory_delivery(pb.MemoryDeliveryReceipt(
            idempotency_key=feedback_identity + ":delivery",
            scope=feedback_scope,
            run_ref=run_ref,
            memory_context_ref=selected.context_ref,
            delivered_memory_refs=rendered.memory_refs,
        ))
        delivery_ref = delivery.receipt_ref

    instructions = _MODEL_INSTRUCTIONS
    if rendered.text:
        instructions += "\n\nLONG_TERM_MEMORY\n" + rendered.text
    output = await model.complete(
        instructions=instructions,
        input_text=_choice_input(QUERY),
        max_output_tokens=max_output_tokens,
    )
    option = extract_option_token(output)
    if option not in ACTION_OPTIONS:
        raise RuntimeError("lifecycle model did not return a valid option token")
    agent_act_text = ACTION_OPTIONS[option]
    agent_ref = feedback_identity + ":agent-act"
    agent_act = await memory.observe_source_event(pb.ObserveSourceEventRequest(
        idempotency_key=agent_ref,
        source_event=pb.SourceEvent(
            scope=feedback_scope,
            text=agent_act_text,
            source_ref=agent_ref,
            actor_kind=pb.SOURCE_ACTOR_KIND_AGENT,
            actor_ref="seed-lifecycle-agent",
        ),
        episode_binding=pb.EpisodeBinding(
            run_ref=run_ref,
            source_group_ref=group_ref,
            role=pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
        ),
    ))

    outcome_ref = ""
    if case.outcome_text is not None:
        outcome = await memory.report_outcome(pb.ReportOutcomeRequest(
            idempotency_key=feedback_identity + ":outcome-report",
            scope=feedback_scope,
            run_ref=run_ref,
            source_group_ref=group_ref,
            text=case.outcome_text,
            delivery_receipt_refs=[delivery_ref] if delivery_ref else [],
            related_source_event_refs=[
                situation.source_event_ref,
                agent_act.source_event_ref,
            ],
            source_ref=feedback_identity + ":outcome",
            actor_kind=pb.SOURCE_ACTOR_KIND_USER,
            actor_ref="seed-lifecycle-user",
        ))
        outcome_ref = outcome.outcome_event_ref

    target_lineages = {_seed_lineage(ref) for ref in rendered_dispositions}
    after = await settle(feedback_scope, expected_episodes)
    reenacted = bool(
        _basis_delta(
            before,
            after,
            {"reenactment"},
            target_lineages=target_lineages,
        )
    )
    revision_or_inhibition = bool(
        _basis_delta(
            before,
            after,
            {"revision", "inhibition"},
            target_lineages=target_lineages,
        )
    )

    delayed_scope = _scope(evaluation_ref, lane, f"{case.kind.value}-delayed")
    delayed_identity = _identity(
        evaluation_ref, lane, f"{case.kind.value}-delayed"
    )
    delayed_situation = await memory.observe_source_event(
        pb.ObserveSourceEventRequest(
            idempotency_key=delayed_identity + ":situation",
            source_event=pb.SourceEvent(
                scope=delayed_scope,
                text=DELAYED_QUERY,
                source_ref=delayed_identity + ":situation",
                actor_kind=pb.SOURCE_ACTOR_KIND_USER,
                actor_ref="seed-lifecycle-user",
            ),
        )
    )
    delayed = await memory.select_memory(pb.SelectMemoryRequest(
        scope=delayed_scope,
        run_ref=delayed_identity + ":run",
        situation_source_event_refs=[delayed_situation.source_event_ref],
        episode_evidence_max_bytes=0,
    ))
    delayed_rendered = render_memory_context(
        delayed,
        max_tokens=memory_tokens,
        token_count=token_count,
    )
    delayed_dispositions = tuple(
        item.memory_ref
        for item in delayed.dispositions
        if item.text.strip() and item.memory_ref in set(delayed_rendered.memory_refs)
    )
    delayed_instructions = _MODEL_INSTRUCTIONS
    if delayed_rendered.text:
        delayed_instructions += "\n\nLONG_TERM_MEMORY\n" + delayed_rendered.text
    delayed_output = await model.complete(
        instructions=delayed_instructions,
        input_text=_choice_input(DELAYED_QUERY),
        max_output_tokens=max_output_tokens,
    )
    delayed_option = extract_option_token(delayed_output)

    return {
        "protocol": "seed-companion-lifecycle-v1",
        "kind": case.kind.value,
        "formed": bool(formed),
        "formed_seed_refs": sorted(formed),
        "selected_disposition_refs": list(selected_dispositions),
        "rendered": bool(rendered_dispositions),
        "rendered_disposition_refs": list(rendered_dispositions),
        "delivered_memory_refs": list(rendered.memory_refs) if delivery_ref else [],
        "delivery_receipt_ref": delivery_ref,
        "agent_act_option": option,
        "agent_act_text": agent_act_text,
        "agent_act_source_ref": agent_act.source_event_ref,
        "agent_act_expressed_tendency": option == "A",
        "outcome_event_ref": outcome_ref,
        "reenacted": reenacted,
        "revision_or_inhibition": revision_or_inhibition,
        "future_selection_changed": (
            tuple(
                ref
                for ref in rendered_dispositions
                if _seed_lineage(ref) in target_lineages
            )
            != tuple(
                ref
                for ref in delayed_dispositions
                if _seed_lineage(ref) in target_lineages
            )
        ),
        "delayed_disposition_refs": list(delayed_dispositions),
        "delayed_option": delayed_option,
        "before": dict(before),
        "after": dict(after),
    }


async def run_lifecycle_suite(
    *,
    evaluation_ref: str,
    memory: Any,
    settle: Callable[[pb.MemoryScope, int], Awaitable[Mapping[str, object]]],
    model: Any,
    output_dir: Path,
    token_count: Callable[[str], int],
    memory_tokens: int,
    max_output_tokens: int,
) -> dict[str, object]:
    """Run and freeze all causal cases before the expensive external lane."""

    output_dir = output_dir.resolve()
    summary_path = output_dir / "summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if (
            summary.get("protocol") != "seed-companion-lifecycle-v1"
            or summary.get("decision", {}).get("passed") is not True
        ):
            raise ValueError("existing lifecycle summary is not a passing frozen run")
        return summary

    evaluation_ref = _nonblank(evaluation_ref, "evaluation_ref")
    if type(memory_tokens) is not int or memory_tokens < 0:
        raise ValueError("memory_tokens must be nonnegative")
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be positive")

    cases_by_kind = {case.kind: case for case in lifecycle_cases()}
    ordered_cases = tuple(
        cases_by_kind[kind]
        for kind in (
            LifecycleKind.NO_DELIVERY,
            LifecycleKind.POSITIVE_COMPLETE,
            LifecycleKind.NO_OUTCOME,
            LifecycleKind.NEGATIVE_COMPLETE,
        )
    )
    lane = "shared-seed"
    before, formed_episode_count = await _form_lifecycle_seed(
        evaluation_ref=evaluation_ref,
        lane=lane,
        memory=memory,
        settle=settle,
        expected_episodes_before=0,
    )

    results: list[dict[str, object]] = []
    for offset, case in enumerate(ordered_cases, start=1):
        path = output_dir / f"{case.kind.value}.json"
        if path.exists():
            raise ValueError(
                "partial lifecycle output cannot be resumed; use a fresh evaluation_ref"
            )
        result = await _run_feedback_case(
            case=case,
            evaluation_ref=evaluation_ref,
            lane=lane,
            before=before,
            expected_episodes=formed_episode_count + offset,
            memory=memory,
            settle=settle,
            model=model,
            token_count=token_count,
            memory_tokens=memory_tokens,
            max_output_tokens=max_output_tokens,
        )
        _freeze_json(path, result)
        results.append(result)
        before = result["after"]
    summary = summarize_lifecycle(results)
    _freeze_json(summary_path, summary)
    return summary


def summarize_lifecycle(results: Sequence[Mapping[str, object]]) -> dict[str, object]:
    expected = {kind.value for kind in LifecycleKind}
    by_kind: dict[str, Mapping[str, object]] = {}
    reasons: list[str] = []
    for result in results:
        kind = _nonblank(result.get("kind"), "kind")
        if kind not in expected or kind in by_kind:
            raise ValueError("lifecycle results must contain unique known cases")
        by_kind[kind] = result
    if set(by_kind) != expected:
        reasons.append("all four lifecycle cases are required")
    for kind, result in by_kind.items():
        for field in ("formed", "rendered", "agent_act_expressed_tendency"):
            if result.get(field) is not True:
                reasons.append(f"{kind} did not prove {field}")

    positive = by_kind.get(LifecycleKind.POSITIVE_COMPLETE.value)
    if positive is not None and positive.get("reenacted") is not True:
        reasons.append("positive complete chain did not reenact")
    no_delivery = by_kind.get(LifecycleKind.NO_DELIVERY.value)
    if no_delivery is not None and (
        no_delivery.get("reenacted") is not False
        or no_delivery.get("revision_or_inhibition") is not False
    ):
        reasons.append("undelivered control changed the Seed")
    no_outcome = by_kind.get(LifecycleKind.NO_OUTCOME.value)
    if no_outcome is not None and no_outcome.get("revision_or_inhibition") is not False:
        reasons.append("outcome-less control revised or inhibited the Seed")
    negative = by_kind.get(LifecycleKind.NEGATIVE_COMPLETE.value)
    if negative is not None and (
        negative.get("revision_or_inhibition") is not True
        or negative.get("future_selection_changed") is not True
    ):
        reasons.append("negative complete chain did not change future selection")
    return {
        "protocol": "seed-companion-lifecycle-v1",
        "cases": list(by_kind),
        "decision": {"passed": not reasons, "reasons": reasons},
    }


async def _observe_complete_turn(
    *,
    memory: Any,
    scope: pb.MemoryScope,
    identity: str,
    situation: str,
    agent_act: str,
) -> None:
    run_ref = identity + ":run"
    group_ref = identity + ":source-group"
    for suffix, text, actor_kind, actor_ref, role in (
        (
            "situation",
            situation,
            pb.SOURCE_ACTOR_KIND_USER,
            "seed-lifecycle-user",
            pb.EPISODE_SOURCE_ROLE_SITUATION,
        ),
        (
            "agent-act",
            agent_act,
            pb.SOURCE_ACTOR_KIND_AGENT,
            "seed-lifecycle-agent",
            pb.EPISODE_SOURCE_ROLE_AGENT_ACT,
        ),
    ):
        source_ref = identity + ":" + suffix
        await memory.observe_source_event(pb.ObserveSourceEventRequest(
            idempotency_key=source_ref,
            source_event=pb.SourceEvent(
                scope=scope,
                text=text,
                source_ref=source_ref,
                actor_kind=actor_kind,
                actor_ref=actor_ref,
            ),
            episode_binding=pb.EpisodeBinding(
                run_ref=run_ref,
                source_group_ref=group_ref,
                role=role,
            ),
        ))


def _active_dispositions(state: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    records = state.get("dispositions", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise TypeError("state dispositions must be a sequence")
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("state disposition must be a mapping")
        if record.get("status") != "active":
            continue
        ref = _nonblank(record.get("ref"), "Disposition ref")
        result[ref] = _nonblank(record.get("text"), "Disposition text")
    return result


def _active_ref_set(state: Mapping[str, object]) -> set[str]:
    return set(_active_dispositions(state))


def _basis_delta(
    before: Mapping[str, object],
    after: Mapping[str, object],
    roles: set[str],
    *,
    target_lineages: set[str] | None = None,
) -> set[tuple[str, str, str]]:
    delta = _seed_basis(after, roles) - _seed_basis(before, roles)
    if target_lineages is None:
        return delta
    return {item for item in delta if _seed_lineage(item[0]) in target_lineages}


def _seed_basis(
    state: Mapping[str, object], roles: set[str]
) -> set[tuple[str, str, str]]:
    basis = state.get("basis", {})
    if not isinstance(basis, Mapping):
        raise TypeError("state basis must be a mapping")
    records = basis.get("seed_basis_links", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise TypeError("seed basis links must be a sequence")
    result: set[tuple[str, str, str]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("seed basis link must be a mapping")
        role = record.get("role")
        if role not in roles:
            continue
        result.add((
            _nonblank(record.get("ref"), "seed basis ref"),
            _nonblank(record.get("episode_ref"), "seed basis Episode ref"),
            str(role),
        ))
    return result


def _choice_input(query: str) -> str:
    return (
        f"SITUATION\n{query}\n\nOPTIONS\n"
        f"A. {ACTION_OPTIONS['A']}\nB. {ACTION_OPTIONS['B']}"
    )


def _seed_lineage(version_ref: str) -> str:
    head, separator, suffix = version_ref.rpartition("@")
    return head if separator and head and suffix.isdigit() else version_ref


def _freeze_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _scope(evaluation_ref: str, lane: str, session: str) -> pb.MemoryScope:
    lane = _nonblank(lane, "lifecycle lane")
    relationship = f"{evaluation_ref}:{lane}"
    return pb.MemoryScope(
        tenant_ref="seed-lifecycle-eval",
        agent_ref="seed-lifecycle-agent",
        relationship_ref=relationship,
        session_ref=f"{relationship}:{session}",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


def _identity(evaluation_ref: str, lane: str, step: str) -> str:
    return f"seed-lifecycle:{evaluation_ref}:{lane}:{step}"


def _validate_run_inputs(
    *,
    evaluation_ref: str,
    case: LifecycleCase,
    memory_tokens: int,
    max_output_tokens: int,
) -> str:
    evaluation_ref = _nonblank(evaluation_ref, "evaluation_ref")
    if not isinstance(case, LifecycleCase):
        raise TypeError("case must be LifecycleCase")
    if type(memory_tokens) is not int or memory_tokens < 0:
        raise ValueError("memory_tokens must be nonnegative")
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be positive")
    return evaluation_ref


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()
