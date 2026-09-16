"""Gold-isolated PersonaMem evidence alignment and causal funnel summaries."""
from __future__ import annotations

import ast
import csv
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from memory_core import memory_pb2 as pb

from .personamem_data import History
from .personamem_effect import EffectContext, EpisodeDocument


@dataclass(frozen=True, slots=True)
class OracleEvidence:
    question_ref: str
    persona_id: str
    messages: tuple[tuple[str, str], ...]


class EpisodeSearch(Protocol):
    def search_episodes(
        self,
        *,
        scope: pb.MemoryScope,
        query: str,
        limit: int,
    ) -> Sequence[EpisodeDocument]: ...


def load_oracle_evidence(path: Path) -> dict[str, OracleEvidence]:
    """Read benchmark labels through a diagnostics-only import boundary."""

    labels: dict[str, OracleEvidence] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            raw = row.get("related_conversation_snippet", "")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    value = ast.literal_eval(raw)
                except (SyntaxError, ValueError) as error:
                    raise ValueError(f"row {index}: invalid evidence snippet") from error
            if not isinstance(value, list) or not value:
                raise ValueError(f"row {index}: evidence snippet must be nonempty")
            messages = tuple(_message(item) for item in value)
            question_ref = f"row-{index:05d}"
            labels[question_ref] = OracleEvidence(
                question_ref=question_ref,
                persona_id=_nonblank(row.get("persona_id"), "persona_id"),
                messages=messages,
            )
    if not labels:
        raise ValueError("benchmark contains no Oracle evidence")
    return labels


def align_snippet(
    snippet: Sequence[tuple[str, str] | Mapping[str, object]],
    history: History,
) -> dict[str, object]:
    if not snippet:
        raise ValueError("evidence snippet must be nonempty")
    groups: list[dict[str, str]] = []
    for item in snippet:
        role, text = _normalized(*_message(item))
        if role == "user":
            groups.append({"user": text})
        elif groups and "assistant" not in groups[-1]:
            groups[-1]["assistant"] = text
        else:
            groups.append({"assistant": text})

    candidates: list[list[int]] = []
    for group in groups:
        primary = "user" if "user" in group else "assistant"
        found = [
            index
            for index, turn in enumerate(history.turns)
            if _contains_observed_text(
                turn.situation if primary == "user" else turn.agent_act,
                group[primary],
            )
        ]
        # Rebuilt histories sometimes retain the exact user request but replace
        # its assistant response. An exact assistant hit is corroboration when
        # available, never a prerequisite for locating user-authored evidence.
        if primary == "user" and "assistant" in group:
            corroborated = [
                index
                for index in found
                if _contains_observed_text(
                    history.turns[index].agent_act,
                    group["assistant"],
                )
            ]
            if corroborated:
                found = corroborated
        candidates.append(found)

    solutions: list[tuple[int, ...]] = []

    def search(group_index: int, previous: int, chosen: tuple[int, ...]) -> None:
        if len(solutions) >= 2:
            return
        if group_index == len(candidates):
            solutions.append(chosen)
            return
        for episode_index in candidates[group_index]:
            if episode_index > previous:
                search(group_index + 1, episode_index, chosen + (episode_index,))

    search(0, -1, ())
    if len(solutions) != 1:
        return {
            "status": "ambiguous" if solutions else "unmatched",
            "episode_indexes": [],
            "matches": len(solutions),
        }
    return {
        "status": "matched",
        "episode_indexes": list(solutions[0]),
        "matches": 1,
    }


def semantic_top40(
    provider: EpisodeSearch,
    *,
    scope: pb.MemoryScope,
    query: str,
) -> tuple[EpisodeDocument, ...]:
    query = _nonblank(query, "query")
    result = tuple(provider.search_episodes(scope=scope, query=query, limit=40))
    if len(result) > 40:
        raise ValueError("MemoryIndex returned more than the requested 40 Episodes")
    refs: set[str] = set()
    for item in result:
        if not isinstance(item, EpisodeDocument):
            raise TypeError("EpisodeSearch must return EpisodeDocument values")
        if item.memory_ref in refs:
            raise ValueError(f"duplicate Episode from MemoryIndex: {item.memory_ref}")
        refs.add(item.memory_ref)
    return result


def build_funnel_record(
    *,
    question_ref: str,
    persona_id: str,
    source_available: bool,
    gold_episode_refs: Sequence[str],
    indexed_episode_refs: Sequence[str],
    context: EffectContext,
    learned_basis: Mapping[str, Sequence[str]],
    answer_correct: bool,
) -> dict[str, object]:
    for name, value in (
        ("source_available", source_available),
        ("answer_correct", answer_correct),
    ):
        if type(value) is not bool:
            raise TypeError(f"{name} must be bool")
    gold = set(gold_episode_refs)
    indexed = source_available and bool(gold & set(indexed_episode_refs))
    selected = source_available and bool(gold & set(context.selected_episode_refs))
    delivered = source_available and bool(gold & set(context.delivered_episode_refs))

    def basis_hit(refs: Sequence[str]) -> bool:
        return source_available and any(
            gold & set(learned_basis.get(ref, ())) for ref in refs
        )

    return {
        "question_ref": _nonblank(question_ref, "question_ref"),
        "persona_id": _nonblank(persona_id, "persona_id"),
        "mode": context.mode.value,
        "source_available": source_available,
        "indexed": indexed,
        "selected": selected,
        "delivered": delivered,
        "answer_correct": answer_correct,
        "learned_basis_selected": basis_hit(context.selected_memory_refs),
        "learned_basis_delivered": basis_hit(context.memory_refs),
    }


def summarize_funnel(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    required = (
        "source_available",
        "indexed",
        "selected",
        "delivered",
        "answer_correct",
        "learned_basis_selected",
        "learned_basis_delivered",
    )
    if not rows:
        raise ValueError("funnel requires at least one question")
    seen: set[tuple[object, object]] = set()
    normalized = []
    for row in rows:
        key = (row.get("persona_id"), row.get("question_ref"))
        if key in seen:
            raise ValueError(f"duplicate funnel question: {key}")
        seen.add(key)
        values = {}
        for field in required:
            value = row.get(field)
            if type(value) is not bool:
                raise TypeError(f"{field} must be bool")
            values[field] = value
        if values["delivered"] and not values["selected"]:
            raise ValueError("delivered Episode evidence must have been selected")
        if values["selected"] and not values["indexed"]:
            raise ValueError("selected Episode evidence must have been indexed")
        if values["learned_basis_delivered"] and not values["learned_basis_selected"]:
            raise ValueError("delivered learned Basis must have been selected")
        if not values["source_available"] and any(
            values[field] for field in required if field != "answer_correct"
        ):
            raise ValueError("unavailable source cannot advance the evidence funnel")
        normalized.append(values)

    counts = {field: sum(row[field] for row in normalized) for field in required}
    source_count = counts["source_available"]
    indexed_count = counts["indexed"]
    selected_count = counts["selected"]
    transition_rates = {
        "source_available": source_count / len(normalized),
        "indexed_given_source": _ratio(
            sum(row["indexed"] and row["source_available"] for row in normalized),
            source_count,
        ),
        "selected_given_indexed": _ratio(
            sum(row["selected"] and row["indexed"] for row in normalized),
            indexed_count,
        ),
        "delivered_given_selected": _ratio(
            sum(row["delivered"] and row["selected"] for row in normalized),
            selected_count,
        ),
    }
    conditions = (
        "source_available",
        "indexed",
        "selected",
        "delivered",
        "learned_basis_selected",
        "learned_basis_delivered",
    )
    return {
        "questions": len(normalized),
        "counts": counts,
        "transition_rates": transition_rates,
        "answer_accuracy": statistics.mean(row["answer_correct"] for row in normalized),
        "answer_accuracy_given": {
            field: _conditional_accuracy(normalized, field, True) for field in conditions
        },
        "answer_accuracy_without": {
            field: _conditional_accuracy(normalized, field, False) for field in conditions
        },
    }


def _conditional_accuracy(rows: Sequence[Mapping[str, bool]], field: str, value: bool):
    selected = [row for row in rows if row[field] is value]
    return statistics.mean(row["answer_correct"] for row in selected) if selected else None


def _ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else None


def _message(value: tuple[str, str] | Mapping[str, object]) -> tuple[str, str]:
    if isinstance(value, Mapping):
        return (
            _nonblank(value.get("role"), "evidence role"),
            _nonblank(value.get("content"), "evidence content"),
        )
    if isinstance(value, tuple) and len(value) == 2:
        return (
            _nonblank(value[0], "evidence role"),
            _nonblank(value[1], "evidence content"),
        )
    raise ValueError("evidence message must contain role and content")


def _normalized(role: str, text: str) -> tuple[str, str]:
    if role not in {"user", "assistant"}:
        raise ValueError("evidence role must be user or assistant")
    return role, " ".join(text.split())


def _contains_observed_text(observed: str, expected_normalized: str) -> bool:
    normalized = " ".join(observed.split())
    if normalized == expected_normalized:
        return True
    padded = f" {normalized} "
    return f" {expected_normalized} " in padded


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()
