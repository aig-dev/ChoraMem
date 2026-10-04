"""Frozen synthetic causal probes; labels never enter replay values.

This data validates an experiment contract, not the truth of a user's words or
the semantic accuracy of a Worker. Scorer text and Oracle remain separate from
the immutable existing DelayedSeedInstance values consumed by the Harness.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from .delayed_seed_data import DelayedSeedCounterfactual, DelayedSeedInstance, DelayedSeedSession


PROTOCOL = "memory-effect-causal-v1"


@dataclass(frozen=True, slots=True)
class EffectCorrection:
    source_text: str
    same_condition_requests: tuple[str, ...]
    different_condition_request: str


@dataclass(frozen=True, slots=True)
class EffectFeedbackScenario:
    case_ref: str
    trajectory_ref: str
    current_request: str
    agent_act: str  # Declared scripted act for Worker semantics, not a model answer.
    outcome_text: str | None
    association: str
    followup_requests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryEffectDataset:
    instances: tuple[DelayedSeedInstance, ...]
    negative_controls: tuple[DelayedSeedInstance, ...]
    feedback_scenarios: tuple[EffectFeedbackScenario, ...]
    semantic_scenarios: tuple[DelayedSeedInstance, ...]
    counterfactuals: Mapping[str, DelayedSeedCounterfactual]
    corrections: Mapping[str, EffectCorrection]
    expectations: Mapping[str, Mapping[str, object]]
    source_sha256: str


def load_memory_effect(path: Path) -> MemoryEffectDataset:
    payload = Path(path).read_bytes()
    raw = _object(json.loads(payload), "data")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        raise ValueError("memory effect schema drift")
    if raw.get("protocol") != PROTOCOL:
        raise ValueError("memory effect protocol drift")
    _fields(raw, {"schema_version", "protocol", "persona_text", "trajectories", "negative_controls", "feedback_scenarios", "semantic_scenarios"})
    persona = _text(raw.get("persona_text"), "persona_text")
    expected: dict[str, Mapping[str, object]] = {}
    counterfactuals: dict[str, DelayedSeedCounterfactual] = {}
    corrections: dict[str, EffectCorrection] = {}
    groups: dict[str, tuple[DelayedSeedInstance, ...]] = {}
    for name, prefix, size in (("trajectories", "r", 8), ("negative_controls", "n", 4), ("semantic_scenarios", "g", 8)):
        rows = _cases(raw.get(name), name, prefix, size)
        instances = []
        for row in rows:
            keys = {"case_ref", "history_sessions", "current_request", "expectation"}
            _fields(row, keys | ({"correction"} if name == "trajectories" else set()))
            ref = row["case_ref"]
            expectation = _object(row.get("expectation"), "expectation")
            expected[ref] = MappingProxyType(dict(expectation))
            request = _text(row.get("current_request"), "current_request")
            sessions = _sessions(row.get("history_sessions"), exact_three=name != "semantic_scenarios")
            if name == "semantic_scenarios":
                _fields(expectation, {"meaning", "required_claims", "forbidden_claims", "no_disposition"}, {"single_equivalent_understanding"})
                _text(expectation.get("meaning"), "meaning")
                _texts(expectation.get("required_claims"), "required_claims", nonempty=True)
                _texts(expectation.get("forbidden_claims"), "forbidden_claims")
                if expectation.get("no_disposition") is not True:
                    raise ValueError("semantic no_disposition expectation drift")
            else:
                expectation_keys = {"formation", "target_behavior", "rubric"}
                _fields(expectation, expectation_keys | ({"oracle_disposition", "anti_disposition"} if name == "trajectories" else set()))
                if type(expectation.get("formation")) is not bool or expectation["formation"] != (name == "trajectories"):
                    raise ValueError("formation expectation drift")
                for key in expectation_keys - {"formation"}:
                    _text(expectation.get(key), key)
                if name == "trajectories":
                    oracle = _text(expectation.get("oracle_disposition"), "oracle_disposition")
                    anti = _text(expectation.get("anti_disposition"), "anti_disposition")
                    if oracle == anti:
                        raise ValueError("Oracle and anti must differ")
                    counterfactuals[ref] = DelayedSeedCounterfactual(ref, oracle, anti)
                    correction = _object(row.get("correction"), "correction")
                    _fields(correction, {"source_text", "same_condition_requests", "different_condition_request"})
                    same = _texts(correction.get("same_condition_requests"), "same_condition_requests")
                    if len(same) != 3 or len(set(same)) != 3:
                        raise ValueError("correction requires three distinct same-condition probes")
                    corrections[ref] = EffectCorrection(_text(correction.get("source_text"), "correction source_text"), same, _text(correction.get("different_condition_request"), "different_condition_request"))
                for key, value in expectation.items():
                    if isinstance(value, str) and value in persona + "\n" + request:
                        raise ValueError(f"scorer {key} leaked into answer input")
            instances.append(DelayedSeedInstance(ref, ref, persona, sessions, request))
        groups[name] = tuple(instances)

    feedback = []
    for row in _cases(raw.get("feedback_scenarios"), "feedback_scenarios", "f", 9):
        _fields(row, {"case_ref", "trajectory_ref", "current_request", "agent_act", "outcome_text", "association", "followup_requests", "expectation"})
        ref = row["case_ref"]
        trajectory = _text(row.get("trajectory_ref"), "trajectory_ref")
        if trajectory not in counterfactuals:
            raise ValueError("unknown feedback trajectory")
        association = row.get("association")
        if association not in {"current", "none", "trusted_late", "missing_and_wrong"}:
            raise ValueError("untrusted association")
        outcome = row.get("outcome_text")
        if (association == "none") != (outcome is None):
            raise ValueError("missing Outcome cannot be fabricated or labelled approval")
        if outcome is not None:
            outcome = _text(outcome, "outcome_text")
        expectation = _object(row.get("expectation"), "expectation")
        _fields(expectation, {"meaning", "allowed_operations"}, {"locality", "existing_target_only", "test_both_associations"})
        _text(expectation.get("meaning"), "meaning")
        operations = _texts(expectation.get("allowed_operations"), "allowed_operations", nonempty=True)
        if not set(operations) <= {"KEEP", "REENACT", "NO_CHANGE", "INHIBIT", "TEXT", "ADAPT"}:
            raise ValueError("unknown expected operation")
        expected[ref] = MappingProxyType(dict(expectation))
        feedback.append(EffectFeedbackScenario(ref, trajectory, _text(row.get("current_request"), "current_request"), _text(row.get("agent_act"), "agent_act"), outcome, association, _texts(row.get("followup_requests"), "followup_requests")))

    return MemoryEffectDataset(groups["trajectories"], groups["negative_controls"], tuple(feedback), groups["semantic_scenarios"], MappingProxyType(counterfactuals), MappingProxyType(corrections), MappingProxyType(expected), hashlib.sha256(payload).hexdigest())


def memory_effect_input_sha256(instance: DelayedSeedInstance) -> str:
    """Bind actual replay sources/current input, independent of scorer edits."""
    values = {
        "protocol": PROTOCOL, "instance_ref": instance.instance_ref,
        "persona_text": instance.persona_text, "current_request": instance.current_request,
        "history_sessions": [{"session_ref": s.session_ref, "situation": s.situation, "agent_act": s.agent_act, "outcome": s.outcome} for s in instance.history_sessions],
    }
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cases(value: object, name: str, prefix: str, count: int) -> list[Mapping]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{name} requires {count} cases")
    rows = [_object(row, name) for row in value]
    if [row.get("case_ref") for row in rows] != [f"{prefix}{i}" for i in range(1, count + 1)]:
        raise ValueError(f"{name} identity drift or duplicate")
    return rows


def _sessions(value: object, *, exact_three: bool) -> tuple[DelayedSeedSession, ...]:
    if not isinstance(value, list) or (len(value) != 3 if exact_three else not 1 <= len(value) <= 3):
        raise ValueError("history requires three complete sessions" if exact_three else "history requires 1-3 complete sessions")
    sessions = []
    seen = set()
    for row in value:
        row = _object(row, "session")
        _fields(row, {"session_ref", "situation", "agent_act", "outcome"})
        ref = _text(row.get("session_ref"), "session_ref")
        if ref in seen:
            raise ValueError("duplicate session_ref")
        seen.add(ref)
        sessions.append(DelayedSeedSession(ref, _text(row.get("situation"), "situation"), _text(row.get("agent_act"), "agent_act"), _text(row.get("outcome"), "outcome")))
    return tuple(sessions)


def _fields(value: Mapping, required: set[str], optional: set[str] = frozenset()) -> None:
    if not required <= value.keys() or not value.keys() <= required | optional:
        raise ValueError("unexpected or missing fields")


def _object(value: object, name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(name + " must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be nonblank text")
    return value


def _texts(value: object, name: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(name + " must be an array")
    return tuple(_text(item, name) for item in value)
