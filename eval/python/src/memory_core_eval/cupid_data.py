"""Pinned CUPID inputs with a hard boundary between replay text and labels."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class CupidMessage:
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class CupidSession:
    messages: tuple[CupidMessage, ...]


@dataclass(frozen=True, slots=True)
class CupidInstance:
    instance_ref: str
    persona_id: str
    current_request: str
    history_sessions: tuple[CupidSession, ...]


@dataclass(frozen=True, slots=True)
class CupidLabel:
    instance_ref: str
    persona_id: str
    split: str
    instance_type: str
    preference: str
    checklist: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CupidDataset:
    split: str
    instances: tuple[CupidInstance, ...]
    labels: Mapping[str, CupidLabel]


def load_cupid(
    path: Path, *, split_manifest: Path, split: str
) -> CupidDataset:
    """Load one frozen persona split without putting scorer labels on instances."""

    path = path.resolve()
    manifest = _read_manifest(split_manifest)
    split_name = _split_name(split)
    dataset_spec = _mapping(manifest.get("dataset"), "dataset")
    _verify_file(path, dataset_spec)

    try:
        import pyarrow.parquet as parquet
    except ImportError as error:  # pragma: no cover - exercised by package install
        raise RuntimeError("CUPID evaluation requires the 'cupid' extra") from error

    rows = parquet.read_table(path).to_pylist()
    expected_rows = _positive_int(dataset_spec.get("expected_rows"), "expected_rows")
    if len(rows) != expected_rows:
        raise ValueError(f"CUPID row count drift: got {len(rows)}, want {expected_rows}")

    expected_types = tuple(
        _nonblank(value, "expected instance type")
        for value in _sequence(
            dataset_spec.get("expected_instance_types"),
            "expected_instance_types",
        )
    )
    expected_sessions = _positive_int(
        dataset_spec.get("expected_sessions_per_instance"),
        "expected_sessions_per_instance",
    )
    expected_per_persona = _positive_int(
        dataset_spec.get("expected_rows_per_persona"),
        "expected_rows_per_persona",
    )

    normalized: list[tuple[str, str, CupidInstance, tuple[str, ...]]] = []
    identities: set[tuple[str, str]] = set()
    persona_counts: Counter[str] = Counter()
    type_counts_by_persona: dict[str, set[str]] = {}
    for raw in rows:
        row = _mapping(raw, "CUPID row")
        persona_id = _nonblank(row.get("persona_id"), "persona_id")
        instance_type = _nonblank(row.get("instance_type"), "instance_type")
        identity = (persona_id, instance_type)
        if identity in identities:
            raise ValueError(f"duplicate CUPID instance: {identity!r}")
        identities.add(identity)
        persona_counts[persona_id] += 1
        type_counts_by_persona.setdefault(persona_id, set()).add(instance_type)

        sessions = _sessions(row.get("prior_interactions"), expected_sessions)
        instance_ref = _instance_ref(persona_id, instance_type)
        instance = CupidInstance(
            instance_ref=instance_ref,
            persona_id=persona_id,
            current_request=_nonblank(row.get("current_request"), "current_request"),
            history_sessions=sessions,
        )
        checklist = tuple(
            _nonblank(item, "current checklist item")
            for item in _sequence(row.get("current_checklist"), "current_checklist")
        )
        if not checklist:
            raise ValueError("current_checklist must not be empty")
        normalized.append((
            persona_id,
            instance_type,
            instance,
            (
                _nonblank(
                    row.get("current_contextual_preference"),
                    "current_contextual_preference",
                ),
                *checklist,
            ),
        ))

    expected_personas = _positive_int(
        dataset_spec.get("expected_personas"), "expected_personas"
    )
    if len(persona_counts) != expected_personas:
        raise ValueError(
            f"CUPID persona count drift: got {len(persona_counts)}, "
            f"want {expected_personas}"
        )
    expected_type_set = set(expected_types)
    for persona_id, count in persona_counts.items():
        if count != expected_per_persona:
            raise ValueError(
                f"persona {persona_id!r} has {count} rows; want {expected_per_persona}"
            )
        if type_counts_by_persona[persona_id] != expected_type_set:
            raise ValueError(f"persona {persona_id!r} instance types drifted")

    assignments = _split_personas(set(persona_counts), manifest)
    _verify_materialized_splits(
        assignments,
        normalized,
        manifest,
    )
    selected_personas = assignments[split_name]
    instances: list[CupidInstance] = []
    labels: dict[str, CupidLabel] = {}
    for persona_id, instance_type, instance, scorer in normalized:
        if persona_id not in selected_personas:
            continue
        preference, *checklist = scorer
        instances.append(instance)
        labels[instance.instance_ref] = CupidLabel(
            instance_ref=instance.instance_ref,
            persona_id=persona_id,
            split=split_name,
            instance_type=instance_type,
            preference=preference,
            checklist=tuple(checklist),
        )
    instances.sort(key=lambda item: item.instance_ref)
    return CupidDataset(
        split=split_name,
        instances=tuple(instances),
        labels=MappingProxyType(labels),
    )


def _sessions(value: object, expected_count: int) -> tuple[CupidSession, ...]:
    raw_sessions = _sequence(value, "prior_interactions")
    if len(raw_sessions) != expected_count:
        raise ValueError(
            f"CUPID session count drift: got {len(raw_sessions)}, want {expected_count}"
        )
    sessions: list[CupidSession] = []
    for raw_session in raw_sessions:
        session = _mapping(raw_session, "prior interaction")
        raw_dialogue = _sequence(session.get("dialogue"), "dialogue")
        if not raw_dialogue:
            raise ValueError("CUPID dialogue must not be empty")
        messages: list[CupidMessage] = []
        for index, raw_message in enumerate(raw_dialogue):
            message = _mapping(raw_message, "dialogue message")
            role = _nonblank(message.get("role"), "dialogue role")
            expected_role = "user" if index % 2 == 0 else "assistant"
            if role != expected_role:
                raise ValueError("CUPID dialogue must alternate user and assistant")
            messages.append(CupidMessage(
                role=role,
                text=_nonblank(message.get("content"), "dialogue content"),
            ))
        sessions.append(CupidSession(tuple(messages)))
    return tuple(sessions)


def _split_personas(
    personas: set[str], manifest: Mapping[str, Any]
) -> dict[str, frozenset[str]]:
    split_spec = _mapping(manifest.get("split"), "split")
    if split_spec.get("unit") != "persona_id":
        raise ValueError("CUPID split unit must be persona_id")
    salt = _nonblank(split_spec.get("salt"), "split salt")
    expected = _mapping(split_spec.get("expected_personas"), "split counts")
    counts = {
        "dev": _positive_int(expected.get("dev"), "dev persona count"),
        "H1": _positive_int(expected.get("H1"), "H1 persona count"),
        "H2": _positive_int(expected.get("H2"), "H2 persona count"),
    }
    exposure = _mapping(manifest.get("exposure"), "exposure")
    exposed = {
        _nonblank(value, "exposed persona")
        for value in _sequence(exposure.get("exposed_personas"), "exposed_personas")
    }
    if not exposed <= personas:
        raise ValueError("exposed persona is absent from CUPID data")
    if counts["dev"] < len(exposed) or sum(counts.values()) != len(personas):
        raise ValueError("frozen CUPID split counts do not cover the persona set")

    ordered = sorted(
        personas - exposed,
        key=lambda persona: (
            hashlib.sha256(f"{salt}:{persona}".encode()).digest(),
            persona,
        ),
    )
    dev_remaining = counts["dev"] - len(exposed)
    h1_end = dev_remaining + counts["H1"]
    assignments = {
        "dev": frozenset(exposed | set(ordered[:dev_remaining])),
        "H1": frozenset(ordered[dev_remaining:h1_end]),
        "H2": frozenset(ordered[h1_end:]),
    }
    if any(len(assignments[name]) != counts[name] for name in assignments):
        raise ValueError("frozen CUPID split assignment is incomplete")
    return assignments


def _verify_materialized_splits(
    assignments: Mapping[str, frozenset[str]],
    rows: Sequence[tuple[str, str, CupidInstance, tuple[str, ...]]],
    manifest: Mapping[str, Any],
) -> None:
    materialized = _mapping(
        _mapping(manifest.get("split"), "split").get(
            "materialized_from_identity_columns_only"
        ),
        "materialized split",
    )
    if set(assignments["dev"]) & set(assignments["H1"]):
        raise ValueError("CUPID split overlap")
    if set(assignments["dev"]) & set(assignments["H2"]):
        raise ValueError("CUPID split overlap")
    if set(assignments["H1"]) & set(assignments["H2"]):
        raise ValueError("CUPID split overlap")

    for split_name, personas in assignments.items():
        frozen = _mapping(materialized.get(split_name), f"materialized {split_name}")
        selected = [row for row in rows if row[0] in personas]
        if len(personas) != _positive_int(frozen.get("personas"), "personas"):
            raise ValueError(f"CUPID {split_name} persona identity drift")
        if len(selected) != _positive_int(frozen.get("rows"), "rows"):
            raise ValueError(f"CUPID {split_name} row identity drift")
        digest = hashlib.sha256(
            ("\n".join(sorted(personas)) + "\n").encode()
        ).hexdigest()
        if digest != frozen.get("persona_ids_sha256"):
            raise ValueError(f"CUPID {split_name} persona identity digest drift")


def _instance_ref(persona_id: str, instance_type: str) -> str:
    digest = hashlib.sha256(
        f"cupid-instance-v1:{persona_id}\0{instance_type}".encode()
    ).hexdigest()
    return f"cupid-{digest}"


def _verify_file(path: Path, dataset_spec: Mapping[str, Any]) -> None:
    if not path.is_file():
        raise ValueError(f"CUPID data file is missing: {path}")
    expected_size = _positive_int(dataset_spec.get("size_bytes"), "size_bytes")
    if path.stat().st_size != expected_size:
        raise ValueError("CUPID file size drift")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != dataset_spec.get("sha256"):
        raise ValueError("CUPID file SHA-256 drift")


def _read_manifest(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("invalid CUPID split manifest") from error
    manifest = _mapping(value, "manifest")
    if manifest.get("schema_version") != 1 or manifest.get("benchmark") != "CUPID":
        raise ValueError("invalid CUPID split manifest")
    return manifest


def _split_name(value: object) -> str:
    name = _nonblank(value, "split")
    if name.lower() == "dev":
        return "dev"
    name = name.upper()
    if name not in {"H1", "H2"}:
        raise ValueError("CUPID split must be dev, H1, or H2")
    return name


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be an array")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()
