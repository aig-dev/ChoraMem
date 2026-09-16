"""Frozen, label-separated data for the Seed-essential delayed evaluation."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


PROTOCOL = "seed-essential-delayed-v1"


@dataclass(frozen=True, slots=True)
class DelayedSeedSession:
    session_ref: str
    situation: str
    agent_act: str
    outcome: str


@dataclass(frozen=True, slots=True)
class DelayedSeedInstance:
    instance_ref: str
    pattern_ref: str
    persona_text: str
    history_sessions: tuple[DelayedSeedSession, ...]
    current_request: str


@dataclass(frozen=True, slots=True)
class DelayedSeedLabel:
    instance_ref: str
    pattern_ref: str
    target_behavior: str


@dataclass(frozen=True, slots=True)
class DelayedSeedCounterfactual:
    instance_ref: str
    oracle_disposition: str
    anti_disposition: str


@dataclass(frozen=True, slots=True)
class DelayedSeedDataset:
    instances: tuple[DelayedSeedInstance, ...]
    counterfactuals: Mapping[str, DelayedSeedCounterfactual]
    labels: Mapping[str, DelayedSeedLabel]
    source_sha256: str


def load_delayed_seed(path: Path) -> DelayedSeedDataset:
    """Load exactly four patterns and eight probes without labels in inputs."""

    source = Path(path).resolve()
    payload = source.read_bytes()
    raw = json.loads(payload)
    if not isinstance(raw, Mapping):
        raise ValueError("delayed Seed data must be an object")
    if raw.get("schema_version") != 1 or raw.get("protocol") != PROTOCOL:
        raise ValueError("delayed Seed schema or protocol drift")
    persona = _nonblank(raw.get("persona_text"), "persona_text")
    patterns = raw.get("patterns")
    if not isinstance(patterns, list) or len(patterns) != 4:
        raise ValueError("delayed Seed data requires exactly four patterns")

    instances: list[DelayedSeedInstance] = []
    counterfactuals: dict[str, DelayedSeedCounterfactual] = {}
    labels: dict[str, DelayedSeedLabel] = {}
    pattern_refs: set[str] = set()
    for raw_pattern in patterns:
        if not isinstance(raw_pattern, Mapping):
            raise ValueError("delayed Seed pattern must be an object")
        pattern_ref = _nonblank(raw_pattern.get("pattern_ref"), "pattern_ref")
        if pattern_ref in pattern_refs:
            raise ValueError("delayed Seed pattern_ref must be unique")
        pattern_refs.add(pattern_ref)
        target = _nonblank(raw_pattern.get("target_behavior"), "target_behavior")
        oracle = _nonblank(
            raw_pattern.get("oracle_disposition"), "oracle_disposition"
        )
        anti = _nonblank(raw_pattern.get("anti_disposition"), "anti_disposition")
        if oracle == anti:
            raise ValueError("oracle and anti dispositions must differ")

        raw_sessions = raw_pattern.get("history_sessions")
        if not isinstance(raw_sessions, list) or len(raw_sessions) != 3:
            raise ValueError("each delayed Seed pattern requires three sessions")
        sessions: list[DelayedSeedSession] = []
        session_refs: set[str] = set()
        for raw_session in raw_sessions:
            if not isinstance(raw_session, Mapping):
                raise ValueError("delayed Seed session must be an object")
            session_ref = _nonblank(raw_session.get("session_ref"), "session_ref")
            if session_ref in session_refs:
                raise ValueError("session_ref must be unique within a pattern")
            session_refs.add(session_ref)
            sessions.append(
                DelayedSeedSession(
                    session_ref=session_ref,
                    situation=_nonblank(raw_session.get("situation"), "situation"),
                    agent_act=_nonblank(raw_session.get("agent_act"), "agent_act"),
                    outcome=_nonblank(raw_session.get("outcome"), "outcome"),
                )
            )

        raw_probes = raw_pattern.get("probes")
        if not isinstance(raw_probes, list) or len(raw_probes) != 2:
            raise ValueError("each delayed Seed pattern requires two probes")
        for raw_probe in raw_probes:
            if not isinstance(raw_probe, Mapping):
                raise ValueError("delayed Seed probe must be an object")
            instance_ref = _nonblank(
                raw_probe.get("instance_ref"), "instance_ref"
            )
            if instance_ref in labels:
                raise ValueError("delayed Seed instance_ref must be unique")
            request = _nonblank(
                raw_probe.get("current_request"), "current_request"
            )
            answer_input = persona + "\n" + request
            if any(value in answer_input for value in (target, oracle, anti)):
                raise ValueError("scorer-only text leaked into delayed answer input")
            instances.append(
                DelayedSeedInstance(
                    instance_ref=instance_ref,
                    pattern_ref=pattern_ref,
                    persona_text=persona,
                    history_sessions=tuple(sessions),
                    current_request=request,
                )
            )
            labels[instance_ref] = DelayedSeedLabel(
                instance_ref=instance_ref,
                pattern_ref=pattern_ref,
                target_behavior=target,
            )
            counterfactuals[instance_ref] = DelayedSeedCounterfactual(
                instance_ref=instance_ref,
                oracle_disposition=oracle,
                anti_disposition=anti,
            )

    if len(instances) != 8 or len(labels) != 8:
        raise ValueError("delayed Seed data requires exactly eight instances")
    return DelayedSeedDataset(
        instances=tuple(instances),
        counterfactuals=counterfactuals,
        labels=labels,
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    return value.strip()
