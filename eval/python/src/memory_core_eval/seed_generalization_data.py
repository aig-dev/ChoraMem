"""Frozen unseen-pattern and naturalized long-history Seed evaluation data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from .delayed_seed_data import (
    DelayedSeedCounterfactual,
    DelayedSeedInstance,
    DelayedSeedLabel,
    DelayedSeedSession,
)


PROTOCOL = "seed-generalization-v1"
_SCHEDULE = (
    ("signal", 0),
    ("distractor", 0),
    ("distractor", 1),
    ("signal", 1),
    ("distractor", 2),
    ("distractor", 3),
    ("distractor", 4),
    ("signal", 2),
)


@dataclass(frozen=True, slots=True)
class GeneralizationExpectation:
    expected_formation: bool
    signal_session_refs: frozenset[str]
    distractor_session_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class GeneralizationDataset:
    protocol: str
    instances: tuple[DelayedSeedInstance, ...]
    expectations: Mapping[str, GeneralizationExpectation]
    positive_refs: tuple[str, ...]
    negative_refs: tuple[str, ...]
    labels: Mapping[str, DelayedSeedLabel]
    counterfactuals: Mapping[str, DelayedSeedCounterfactual]
    source_sha256: str
    benchmark: str = "Memory Core Seed generalization naturalized synthetic v1"


def load_seed_generalization(path: Path) -> GeneralizationDataset:
    source = Path(path).resolve()
    payload = source.read_bytes()
    raw = json.loads(payload)
    if not isinstance(raw, Mapping):
        raise ValueError("Seed generalization data must be an object")
    if raw.get("schema_version") != 1 or raw.get("protocol") != PROTOCOL:
        raise ValueError("Seed generalization schema or protocol drift")
    persona = _nonblank(raw.get("persona_text"), "persona_text")
    positives = raw.get("positive_cases")
    negatives = raw.get("negative_cases")
    if (
        not isinstance(positives, list)
        or not isinstance(negatives, list)
        or len(positives) != 4
        or len(negatives) != 4
    ):
        raise ValueError("Seed generalization requires four positive and four negative cases")
    distractors = _sessions(raw.get("distractor_sessions"), "distractor_sessions", 5)

    instances: list[DelayedSeedInstance] = []
    expectations: dict[str, GeneralizationExpectation] = {}
    labels: dict[str, DelayedSeedLabel] = {}
    counterfactuals: dict[str, DelayedSeedCounterfactual] = {}
    positive_refs: list[str] = []
    negative_refs: list[str] = []

    for expected_formation, cases in ((True, positives), (False, negatives)):
        for raw_case in cases:
            if not isinstance(raw_case, Mapping):
                raise ValueError("Seed generalization case must be an object")
            instance_ref = _nonblank(raw_case.get("instance_ref"), "instance_ref")
            if instance_ref in expectations:
                raise ValueError("instance_ref must be unique")
            pattern_ref = _nonblank(
                raw_case.get("pattern_ref" if expected_formation else "control_ref"),
                "pattern_ref",
            )
            signals = _sessions(raw_case.get("signal_sessions"), "signal_sessions", 3)
            history = tuple(
                signals[index] if kind == "signal" else distractors[index]
                for kind, index in _SCHEDULE
            )
            refs = [session.session_ref for session in history]
            if len(set(refs)) != len(refs):
                raise ValueError("session_ref must be unique within a case")
            current_request = _nonblank(
                raw_case.get("current_request"), "current_request"
            )
            instance = DelayedSeedInstance(
                instance_ref=instance_ref,
                pattern_ref=pattern_ref,
                persona_text=persona,
                history_sessions=history,
                current_request=current_request,
            )
            instances.append(instance)
            expectations[instance_ref] = GeneralizationExpectation(
                expected_formation=expected_formation,
                signal_session_refs=frozenset(
                    session.session_ref for session in signals
                ),
                distractor_session_refs=frozenset(
                    session.session_ref for session in distractors
                ),
            )
            if not expected_formation:
                negative_refs.append(instance_ref)
                continue

            target = _nonblank(raw_case.get("target_behavior"), "target_behavior")
            oracle = _nonblank(
                raw_case.get("oracle_disposition"), "oracle_disposition"
            )
            anti = _nonblank(raw_case.get("anti_disposition"), "anti_disposition")
            if oracle == anti:
                raise ValueError("oracle and anti dispositions must differ")
            answer_input = persona + "\n" + current_request
            if any(value in answer_input for value in (target, oracle, anti)):
                raise ValueError("scorer-only text leaked into answer input")
            positive_refs.append(instance_ref)
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

    return GeneralizationDataset(
        protocol=PROTOCOL,
        instances=tuple(instances),
        expectations=expectations,
        positive_refs=tuple(positive_refs),
        negative_refs=tuple(negative_refs),
        labels=labels,
        counterfactuals=counterfactuals,
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _sessions(value: object, name: str, count: int) -> tuple[DelayedSeedSession, ...]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{name} must contain exactly {count} sessions")
    result: list[DelayedSeedSession] = []
    refs: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError(f"{name} session must be an object")
        ref = _nonblank(raw.get("session_ref"), "session_ref")
        if ref in refs:
            raise ValueError(f"{name} session_ref must be unique")
        refs.add(ref)
        result.append(
            DelayedSeedSession(
                session_ref=ref,
                situation=_nonblank(raw.get("situation"), "situation"),
                agent_act=_nonblank(raw.get("agent_act"), "agent_act"),
                outcome=_nonblank(raw.get("outcome"), "outcome"),
            )
        )
    return tuple(result)


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    return value.strip()
