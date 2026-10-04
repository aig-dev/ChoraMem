"""Read-only G4 diagnostics: candidate access is separate from target meaning.

Scorer expectations never enter the replay instance or its input hash. Live
records must come from captured calls and canonical Store observations; these
validators do not manufacture a committed Seed or establish natural-language
truth. Semantic review is explicit and bound to the exact observed text.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .delayed_seed_data import DelayedSeedInstance, DelayedSeedSession
from .delayed_seed_runner import delayed_instance_request_sha256


PROTOCOL = "memory-effect-candidates-v1"


@dataclass(frozen=True, slots=True)
class CandidateCase:
    instance: DelayedSeedInstance
    expected_formation: bool
    supporting_session_refs: frozenset[str]
    target_behavior: str  # Scorer-only; never passed to the reference Harness.


@dataclass(frozen=True, slots=True)
class CandidateProbe:
    cases: tuple[CandidateCase, ...]
    source_sha256: str


def load_candidate_probe(path: Path) -> CandidateProbe:
    payload = Path(path).read_bytes()
    raw = json.loads(payload)
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1 or raw.get("protocol") != PROTOCOL:
        raise ValueError("candidate probe protocol drift")
    cases = []
    seen = set()
    for value in _sequence(raw.get("cases"), "cases"):
        value = _mapping(value, "case")
        ref = _text(value.get("case_ref"), "case_ref")
        if ref in seen:
            raise ValueError("duplicate case_ref")
        seen.add(ref)
        sessions = []
        session_refs = set()
        for row in _sequence(value.get("history_sessions"), "history_sessions"):
            row = _mapping(row, "session")
            session_ref = _text(row.get("session_ref"), "session_ref")
            if session_ref in session_refs:
                raise ValueError("duplicate session_ref")
            session_refs.add(session_ref)
            sessions.append(DelayedSeedSession(
                session_ref=session_ref,
                situation=_text(row.get("situation"), "situation"),
                agent_act=_text(row.get("agent_act"), "agent_act"),
                outcome=_text(row.get("outcome"), "outcome"),
            ))
        if not 2 <= len(sessions) <= 32:
            raise ValueError("candidate probe requires 2-32 independent sessions")
        expectation = _mapping(value.get("expectation"), "expectation")
        formation = expectation.get("formation")
        if type(formation) is not bool:
            raise ValueError("formation must be boolean")
        support = _refs(expectation.get("supporting_session_refs"), "supporting_session_refs")
        if not set(support) <= session_refs or (formation and len(support) < 2):
            raise ValueError("invalid supporting sessions")
        cases.append(CandidateCase(
            instance=DelayedSeedInstance(
                instance_ref=ref, pattern_ref=ref,
                persona_text=_text(value.get("persona_text"), "persona_text"),
                history_sessions=tuple(sessions),
                current_request=_text(value.get("current_request"), "current_request"),
            ),
            expected_formation=formation, supporting_session_refs=frozenset(support),
            target_behavior=_text(expectation.get("target_behavior"), "target_behavior"),
        ))
    if not cases:
        raise ValueError("empty candidate probe")
    return CandidateProbe(tuple(cases), hashlib.sha256(payload).hexdigest())


def candidate_input_sha256(case: CandidateCase) -> str:
    return delayed_instance_request_sha256(case.instance)


def summarize_candidate_probe(
    dataset: CandidateProbe, records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Reject incomplete evidence; count only an explicitly reviewed exact target."""
    by_ref = {}
    for record in records:
        record = _mapping(record, "record")
        ref = _text(record.get("case_ref"), "case_ref")
        if ref in by_ref:
            raise ValueError("duplicate case result")
        by_ref[ref] = record
    if set(by_ref) != {case.instance.instance_ref for case in dataset.cases}:
        raise ValueError("incomplete case result set")
    rows = []
    for case in dataset.cases:
        record = by_ref[case.instance.instance_ref]
        if record.get("input_sha256") != candidate_input_sha256(case):
            raise ValueError("case input drift")
        calls = _sequence(record.get("model_calls"), "model_calls")
        if not calls:
            raise ValueError("missing model completions")
        rpc_counts = Counter()
        for call in calls:
            call = _mapping(call, "model call")
            if call.get("finish_reason") != "stop" or any(
                not _hash(call.get(key)) for key in ("input_sha256", "output_sha256")
            ):
                raise ValueError("incomplete model completion")
            rpc_counts[_text(call.get("rpc_ref", "window"), "rpc_ref")] += 1
        if max(rpc_counts.values()) > 4:
            raise ValueError("Worker RPC call ceiling exceeded")
        candidates = set(_refs(record.get("candidate_session_refs"), "candidate_session_refs"))
        visible = _refs(record.get("visible_basis_refs"), "visible_basis_refs")
        if len(visible) % 2:
            raise ValueError("visible Basis must contain complete Episode/Outcome pairs")
        dispositions = _sequence(record.get("dispositions"), "dispositions")
        active = {}
        for value in dispositions:
            value = _mapping(value, "Disposition")
            if value.get("status") != "active":
                continue
            ref = _text(value.get("ref"), "Disposition ref")
            if ref in active:
                raise ValueError("duplicate active Disposition")
            active[ref] = value
        target_ref = record.get("target_ref")
        formed = False
        if target_ref is not None:
            if len(visible) < 4:
                raise ValueError("formed target requires two visible complete pairs")
            target_ref = _text(target_ref, "target_ref")
            if target_ref not in active:
                raise ValueError("reviewed target is not active")
            target = active[target_ref]
            if set(_refs(target.get("basis_refs"), "committed Basis")) != set(visible):
                raise ValueError("committed Basis differs from model-visible group")
            text = _text(target.get("text"), "tendency text")
            review = _mapping(record.get("semantic_review"), "semantic_review")
            if review.get("target_ref") != target_ref or review.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError("stale semantic review")
            if review.get("verdict") not in {"supported", "unsupported"}:
                raise ValueError("missing explicit semantic verdict")
            formed = review["verdict"] == "supported"
        elif record.get("semantic_review") is not None:
            raise ValueError("review lacks an exact target")
        missing = sorted(case.supporting_session_refs - candidates)
        if case.expected_formation:
            bottleneck = "candidate_loss" if missing else (None if formed else "target_meaning")
        else:
            bottleneck = "false_positive_formation" if active else None
        rows.append({"case_ref": case.instance.instance_ref, "expected_formation": case.expected_formation,
                     "missing_candidate_sessions": missing, "active_dispositions": len(active),
                     "target_formed": formed, "bottleneck": bottleneck,
                     "completed_calls": len(calls)})
    return {"protocol": PROTOCOL, "data_sha256": dataset.source_sha256, "rows": rows,
            "positive_target_formed": sum(row["target_formed"] for row in rows if row["expected_formation"]),
            "negative_false_positives": sum(row["active_dispositions"] > 0 for row in rows if not row["expected_formation"])}


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(name + " must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(name + " must be an array")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " is required")
    return value.strip()


def _refs(value: object, name: str) -> list[str]:
    refs = [_text(ref, name) for ref in _sequence(value, name)]
    if len(refs) != len(set(refs)):
        raise ValueError("duplicate " + name)
    return refs


def _hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
