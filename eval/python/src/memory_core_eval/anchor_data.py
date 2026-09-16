"""Leakage-safe ANCHOR public dev and Memory Core behavior data loader."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .anchor_source import (
    BANK_ITEM_COUNTS,
    EXPECTED_ANCHOR_REPOSITORY,
    EXPECTED_ANCHOR_REVISION,
    validate_anchor_source,
)


BEHAVIOR_DIMENSIONS = (
    "persona_continuity",
    "relationship_adaptation",
    "relationship_repair",
)


@dataclass(frozen=True, slots=True)
class AnchorMessage:
    session_id: int
    turn: int
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class AnchorSession:
    session_id: int
    messages: tuple[AnchorMessage, ...]


@dataclass(frozen=True, slots=True)
class TrajectoryInstance:
    instance_ref: str
    bank_id: str
    persona_text: str
    history_sessions: tuple[AnchorSession, ...]
    current_request: str
    options: tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class TrajectoryLabel:
    instance_ref: str
    family: str
    correct_index: int


@dataclass(frozen=True, slots=True)
class BehaviorInstance:
    instance_ref: str
    bank_id: str
    persona_text: str
    history_sessions: tuple[AnchorSession, ...]
    session_id: int
    turn: int
    current_request: str


@dataclass(frozen=True, slots=True)
class BehaviorLabel:
    instance_ref: str
    dimension: str
    evidence_text: str
    rubric: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class AnchorDataset:
    trajectory_instances: tuple[TrajectoryInstance, ...]
    trajectory_labels: Mapping[str, TrajectoryLabel]
    behavior_instances: tuple[BehaviorInstance, ...]
    behavior_labels: Mapping[str, BehaviorLabel]
    source_summary: Mapping[str, object]


def load_anchor(root: Path, *, behavior_manifest: Path) -> AnchorDataset:
    """Load answer inputs and scorer-only labels into separate objects."""

    source_root = Path(root).resolve()
    source_summary = validate_anchor_source(source_root)
    if source_summary.get("revision") != EXPECTED_ANCHOR_REVISION:
        raise ValueError("ANCHOR source revision drift")

    bank_messages: dict[str, tuple[AnchorMessage, ...]] = {}
    bank_sessions: dict[str, tuple[AnchorSession, ...]] = {}
    persona_texts: dict[str, str] = {}
    trajectory_instances: list[TrajectoryInstance] = []
    trajectory_labels: dict[str, TrajectoryLabel] = {}

    for bank_id, expected_items in BANK_ITEM_COUNTS.items():
        base = source_root / "data/examples" / bank_id
        persona_texts[bank_id] = render_persona_card(
            _read_object(base / "persona_card.json", f"{bank_id} persona card")
        )
        messages = _load_transcript(base / "transcript.jsonl", bank_id)
        sessions = _group_sessions(messages)
        bank_messages[bank_id] = messages
        bank_sessions[bank_id] = sessions
        session_ids = {session.session_id for session in sessions}

        items = _read_jsonl(base / "items.jsonl", f"{bank_id} items")
        if len(items) != expected_items:
            raise ValueError(f"ANCHOR item count drift: {bank_id}")
        for item in items:
            instance_ref = _nonblank(item.get("item_id"), "ANCHOR item_id")
            if instance_ref in trajectory_labels:
                raise ValueError("ANCHOR trajectory item identity is duplicated")
            item_bank = item.get("_bank")
            if item_bank is not None and item_bank != bank_id:
                raise ValueError("ANCHOR trajectory item bank drift")
            source = item.get("source")
            if not isinstance(source, Mapping):
                raise ValueError("ANCHOR trajectory item requires source")
            source_session = _nonnegative_int(
                source.get("session_id"), "ANCHOR source session_id"
            )
            if source_session not in session_ids:
                raise ValueError("ANCHOR trajectory source session is missing")
            options = _four_options(item.get("options"))
            correct_index = _nonnegative_int(
                item.get("correct_index"), "ANCHOR correct_index"
            )
            if correct_index >= len(options):
                raise ValueError("ANCHOR correct_index is outside its options")
            family = _nonblank(item.get("family"), "ANCHOR family")
            trajectory_instances.append(
                TrajectoryInstance(
                    instance_ref=instance_ref,
                    bank_id=bank_id,
                    persona_text=persona_texts[bank_id],
                    history_sessions=tuple(
                        session
                        for session in sessions
                        if session.session_id <= source_session
                    ),
                    current_request=_nonblank(item.get("stem"), "ANCHOR stem"),
                    options=options,
                )
            )
            trajectory_labels[instance_ref] = TrajectoryLabel(
                instance_ref=instance_ref,
                family=family,
                correct_index=correct_index,
            )

    if len(trajectory_instances) != 15 or len(trajectory_labels) != 15:
        raise ValueError("ANCHOR public dev requires exactly 15 trajectory items")

    manifest = _read_object(Path(behavior_manifest), "ANCHOR behavior manifest")
    _validate_behavior_manifest_header(manifest)
    checkpoints = manifest.get("checkpoints")
    if not isinstance(checkpoints, list) or len(checkpoints) != 6:
        raise ValueError("ANCHOR behavior manifest requires six checkpoints")
    behavior_instances: list[BehaviorInstance] = []
    behavior_labels: dict[str, BehaviorLabel] = {}
    dimensions: Counter[str] = Counter()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, Mapping):
            raise ValueError("ANCHOR behavior checkpoint must be an object")
        instance_ref = _nonblank(
            checkpoint.get("instance_ref"), "ANCHOR behavior instance_ref"
        )
        if instance_ref in behavior_labels:
            raise ValueError("ANCHOR behavior checkpoint identity is duplicated")
        dimension = _nonblank(
            checkpoint.get("dimension"), "ANCHOR behavior dimension"
        )
        if dimension not in BEHAVIOR_DIMENSIONS:
            raise ValueError("ANCHOR behavior dimension is unknown")
        bank_id = _nonblank(checkpoint.get("bank_id"), "ANCHOR behavior bank_id")
        if bank_id not in bank_messages:
            raise ValueError("ANCHOR behavior checkpoint bank is unknown")
        session_id = _nonnegative_int(
            checkpoint.get("session_id"), "ANCHOR behavior session_id"
        )
        turn = _nonnegative_int(checkpoint.get("turn"), "ANCHOR behavior turn")
        if checkpoint.get("role") != "user":
            raise ValueError("ANCHOR behavior target must be a user turn")
        messages = bank_messages[bank_id]
        positions = {
            (message.session_id, message.turn, message.role): index
            for index, message in enumerate(messages)
        }
        target_key = (session_id, turn, "user")
        if target_key not in positions:
            raise ValueError("ANCHOR behavior target turn is missing")
        target_index = positions[target_key]
        target = messages[target_index]
        expected_hash = _sha256(checkpoint.get("content_sha256"))
        if hashlib.sha256(target.content.encode()).hexdigest() != expected_hash:
            raise ValueError("ANCHOR behavior content SHA-256 drift")

        evidence_refs = checkpoint.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            raise ValueError("ANCHOR behavior evidence_refs must be a list")
        evidence: list[AnchorMessage] = []
        seen_evidence: set[tuple[int, int, str]] = set()
        for raw_ref in evidence_refs:
            if not isinstance(raw_ref, Mapping):
                raise ValueError("ANCHOR behavior evidence ref must be an object")
            evidence_key = (
                _nonnegative_int(raw_ref.get("session_id"), "evidence session_id"),
                _nonnegative_int(raw_ref.get("turn"), "evidence turn"),
                _role(raw_ref.get("role"), "evidence role"),
            )
            if evidence_key in seen_evidence:
                raise ValueError("ANCHOR behavior evidence ref is duplicated")
            seen_evidence.add(evidence_key)
            evidence_index = positions.get(evidence_key)
            if evidence_index is None:
                raise ValueError("ANCHOR behavior evidence turn is missing")
            if evidence_index >= target_index:
                raise ValueError("ANCHOR behavior evidence must precede target")
            evidence.append(messages[evidence_index])

        rubric = checkpoint.get("rubric")
        if not isinstance(rubric, Mapping) or set(rubric) != {
            "score_2",
            "score_1",
            "score_0",
        }:
            raise ValueError("ANCHOR behavior rubric requires score_2/1/0")
        normalized_rubric = {
            key: _nonblank(rubric.get(key), f"ANCHOR behavior {key}")
            for key in ("score_2", "score_1", "score_0")
        }
        behavior_instances.append(
            BehaviorInstance(
                instance_ref=instance_ref,
                bank_id=bank_id,
                persona_text=persona_texts[bank_id],
                history_sessions=_group_sessions(messages[:target_index]),
                session_id=session_id,
                turn=turn,
                current_request=target.content,
            )
        )
        behavior_labels[instance_ref] = BehaviorLabel(
            instance_ref=instance_ref,
            dimension=dimension,
            evidence_text=_render_evidence(evidence),
            rubric=normalized_rubric,
        )
        dimensions[dimension] += 1

    if dimensions != Counter({dimension: 2 for dimension in BEHAVIOR_DIMENSIONS}):
        raise ValueError("ANCHOR behavior manifest requires two checkpoints per dimension")

    return AnchorDataset(
        trajectory_instances=tuple(trajectory_instances),
        trajectory_labels=trajectory_labels,
        behavior_instances=tuple(behavior_instances),
        behavior_labels=behavior_labels,
        source_summary=dict(source_summary),
    )


def render_persona_card(card: Mapping[str, object]) -> str:
    """Render the pinned persona as deterministic, shallow tagged text."""

    if not isinstance(card, Mapping):
        raise TypeError("ANCHOR persona card must be an object")
    core = card.get("core")
    mutable = card.get("mutable_seed")
    if not isinstance(core, Mapping) or not isinstance(mutable, Mapping):
        raise ValueError("ANCHOR persona card requires core and mutable_seed")
    lines = [
        f"PERSONA_ID {_one_line(card.get('id'), 'persona id')}",
        f"DOMAIN {_one_line(card.get('domain'), 'persona domain')}",
        f"ARCHETYPE {_one_line(card.get('archetype'), 'persona archetype')}",
        f"NAME {_one_line(core.get('name'), 'persona name')}",
        f"PRONOUNS {_one_line(core.get('pronouns'), 'persona pronouns')}",
        f"ROLE {_one_line(core.get('role'), 'persona role')}",
        "",
        "STYLE",
        *[f"- {item}" for item in _string_list(core.get("style"), "persona style")],
        "",
        "VALUES",
        *[f"- {item}" for item in _string_list(core.get("values"), "persona values")],
        "",
        "BOUNDARIES",
        *[
            f"- {item}"
            for item in _string_list(core.get("boundaries"), "persona boundaries")
        ],
        "",
        "MUTABLE_STATE",
    ]
    for key in sorted(mutable):
        normalized_key = _one_line(key, "persona mutable key")
        raw_value = mutable[key]
        if isinstance(raw_value, str):
            rendered = _one_line(raw_value, f"persona mutable {normalized_key}")
        elif isinstance(raw_value, list):
            values = _string_list(
                raw_value,
                f"persona mutable {normalized_key}",
                allow_empty=True,
            )
            rendered = " | ".join(values) if values else "NONE"
        else:
            raise ValueError("ANCHOR persona mutable values must be strings or lists")
        lines.append(f"{normalized_key}: {rendered}")
    return "\n".join(lines)


def _validate_behavior_manifest_header(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("ANCHOR behavior manifest schema drift")
    upstream = manifest.get("upstream")
    if not isinstance(upstream, Mapping) or upstream != {
        "repository": EXPECTED_ANCHOR_REPOSITORY,
        "revision": EXPECTED_ANCHOR_REVISION,
    }:
        raise ValueError("ANCHOR behavior manifest upstream drift")


def _load_transcript(path: Path, bank_id: str) -> tuple[AnchorMessage, ...]:
    rows = _read_jsonl(path, f"{bank_id} transcript")
    messages: list[AnchorMessage] = []
    previous_session = -1
    session_index = 0
    for row in rows:
        session_id = _nonnegative_int(row.get("session_id"), "session_id")
        turn = _nonnegative_int(row.get("turn"), "turn")
        role = _role(row.get("role"), "role")
        content = _nonblank(row.get("content"), "transcript content")
        if session_id != previous_session:
            if session_id != previous_session + 1:
                raise ValueError("ANCHOR transcript session order is not contiguous")
            previous_session = session_id
            session_index = 0
        expected_turn = session_index // 2
        expected_role = "user" if session_index % 2 == 0 else "assistant"
        if turn != expected_turn or role != expected_role:
            raise ValueError("ANCHOR transcript must alternate user and assistant")
        messages.append(
            AnchorMessage(
                session_id=session_id,
                turn=turn,
                role=role,
                content=content,
            )
        )
        session_index += 1
    sessions = _group_sessions(tuple(messages))
    if not sessions or any(len(session.messages) % 2 for session in sessions):
        raise ValueError("ANCHOR transcript sessions must contain complete turns")
    return tuple(messages)


def _group_sessions(messages: Sequence[AnchorMessage]) -> tuple[AnchorSession, ...]:
    grouped: list[AnchorSession] = []
    current_id: int | None = None
    current: list[AnchorMessage] = []
    for message in messages:
        if current_id is None:
            current_id = message.session_id
        if message.session_id != current_id:
            grouped.append(AnchorSession(current_id, tuple(current)))
            current_id = message.session_id
            current = []
        current.append(message)
    if current_id is not None:
        grouped.append(AnchorSession(current_id, tuple(current)))
    return tuple(grouped)


def _render_evidence(messages: Sequence[AnchorMessage]) -> str:
    return "\n\n".join(
        (
            f"[session={message.session_id} turn={message.turn} "
            f"role={message.role}]\n{message.content}"
        )
        for message in messages
    )


def _four_options(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("ANCHOR trajectory item requires four options")
    options = tuple(_nonblank(option, "ANCHOR option") for option in value)
    return options  # type: ignore[return-value]


def _sha256(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("ANCHOR content SHA-256 is invalid")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError("ANCHOR content SHA-256 is invalid")
    return value


def _role(value: object, label: str) -> str:
    if value not in {"user", "assistant"}:
        raise ValueError(f"{label} must be user or assistant")
    return str(value)


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _one_line(value: object, label: str) -> str:
    text = _nonblank(value, label)
    if "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be one line")
    return text


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def _string_list(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a string list")
    return tuple(_one_line(item, label) for item in value)


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _read_jsonl(path: Path, label: str) -> list[Mapping[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
    values: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"{label} contains a blank row")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {line_number} is invalid JSON") from error
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} row {line_number} must be an object")
        values.append(value)
    return values
