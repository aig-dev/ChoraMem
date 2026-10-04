from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from memory_core_eval.candidate_diagnostic import (
    candidate_input_sha256,
    load_candidate_probe,
    summarize_candidate_probe,
)

DATA = Path(__file__).resolve().parents[2] / "cases/memory-effect-candidates-v1.json"


def _record(case):
    text = "When unresolved options feel equally important, compare one tradeoff and leave the decision with the user."
    target = "target@1" if case.expected_formation else None
    refs = ["episode:1", "outcome:1", "episode:2", "outcome:2"]
    return {
        "case_ref": case.instance.instance_ref,
        "input_sha256": candidate_input_sha256(case),
        "started_model_calls": [{"attempt_ref": "call:1", "rpc_ref": "window", "input_sha256": "a"*64}],
        "model_calls": [{"attempt_ref": "call:1", "input_sha256": "a"*64, "output_sha256": "b"*64, "finish_reason": "stop"}],
        "candidate_session_refs": list(case.supporting_session_refs),
        "visible_basis_refs": refs,
        "target_ref": target,
        "dispositions": [] if target is None else [{"ref": target, "text": text, "status": "active", "basis_refs": refs}],
        "semantic_review": None if target is None else {"target_ref": target, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "verdict": "supported"},
    }


def test_candidate_probe_keeps_scorer_expectations_out_of_inputs(tmp_path):
    first = load_candidate_probe(DATA)
    raw = json.loads(DATA.read_text())
    raw["cases"][0]["expectation"]["target_behavior"] = "SCORER_ONLY_SENTINEL"
    raw["cases"][0]["expectation"]["supporting_session_refs"] = ["s1", "s3"]
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(raw))
    changed = load_candidate_probe(path)
    assert candidate_input_sha256(first.cases[0]) == candidate_input_sha256(changed.cases[0])
    assert "SCORER_ONLY_SENTINEL" not in repr(changed.cases[0].instance)
    assert first.source_sha256 != changed.source_sha256


@pytest.mark.parametrize("corruption", ["missing identity", "duplicate identity", "duplicate session", "missing source", "unknown support", "bad boolean"])
def test_candidate_probe_rejects_invalid_identity_or_source(tmp_path, corruption):
    raw = json.loads(DATA.read_text())
    case = raw["cases"][0]
    if corruption == "missing identity": case["case_ref"] = ""
    elif corruption == "duplicate identity": raw["cases"][1]["case_ref"] = case["case_ref"]
    elif corruption == "duplicate session": case["history_sessions"][1]["session_ref"] = "s1"
    elif corruption == "missing source": case["history_sessions"][0]["outcome"] = ""
    elif corruption == "unknown support": case["expectation"]["supporting_session_refs"] = ["missing"]
    else: case["expectation"]["formation"] = "true"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError): load_candidate_probe(path)


@pytest.mark.parametrize("corruption", ["missing result", "duplicate result", "input drift", "missing call", "unfinished call", "wrong basis", "stale review"])
def test_candidate_probe_rejects_incomplete_or_uncorrelated_evidence(corruption):
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    if corruption == "missing result": records.pop()
    elif corruption == "duplicate result": records[-1] = copy.deepcopy(records[0])
    elif corruption == "input drift": records[0]["input_sha256"] = "c"*64
    elif corruption == "missing call": records[0]["model_calls"] = []
    elif corruption == "unfinished call": records[0]["model_calls"][0]["finish_reason"] = "length"
    elif corruption == "wrong basis": records[0]["dispositions"][0]["basis_refs"].append("unseen-episode")
    else: records[0]["semantic_review"]["text_sha256"] = "c"*64
    with pytest.raises(ValueError): summarize_candidate_probe(dataset, records)


def test_candidate_probe_does_not_count_arbitrary_active_seed_as_target():
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    records[0]["target_ref"] = None
    records[0]["semantic_review"] = None
    result = summarize_candidate_probe(dataset, records)
    assert result["rows"][0]["target_formed"] is False
    assert result["rows"][0]["active_dispositions"] == 1
    assert result["positive_target_formed"] == 1


def test_candidate_probe_separates_missing_candidates_from_wrong_meaning():
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    records[0]["candidate_session_refs"] = []
    records[-1]["semantic_review"]["verdict"] = "unsupported"
    result = summarize_candidate_probe(dataset, records)
    assert result["rows"][0]["bottleneck"] == "candidate_loss"
    assert result["rows"][-1]["bottleneck"] == "target_meaning"
    assert result["positive_target_formed"] == 1
    assert result["negative_false_positives"] == 0


def test_candidate_probe_accepts_complete_no_group_no_target_observation():
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    records[1]["visible_basis_refs"] = []
    result = summarize_candidate_probe(dataset, records)
    assert result["rows"][1]["target_formed"] is False
    assert result["rows"][1]["bottleneck"] is None


def test_candidate_probe_cannot_count_a_one_episode_target_as_qualified_formation():
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    records[0]["visible_basis_refs"] = ["episode:1", "outcome:1"]
    records[0]["dispositions"][0]["basis_refs"] = ["episode:1", "outcome:1"]
    with pytest.raises(ValueError): summarize_candidate_probe(dataset, records)


def test_candidate_probe_enforces_call_ceiling_per_rpc_not_per_case():
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    records[0]["model_calls"] = [dict(records[0]["model_calls"][0], attempt_ref=f"call:{index}", rpc_ref=f"job:{index//4}") for index in range(8)]
    records[0]["started_model_calls"] = [{key:call[key] for key in ("attempt_ref", "rpc_ref", "input_sha256")} for call in records[0]["model_calls"]]
    assert summarize_candidate_probe(dataset, records)["rows"][0]["completed_calls"] == 8
    for call in records[0]["model_calls"]: call["rpc_ref"] = "one-job"
    with pytest.raises(ValueError): summarize_candidate_probe(dataset, records)

@pytest.mark.parametrize('corruption', ['missing inventory', 'omitted completion', 'duplicate completion', 'input mismatch', 'duplicate start'])
def test_candidate_probe_rejects_incomplete_started_call_inventory(corruption):
    dataset = load_candidate_probe(DATA)
    records = [_record(case) for case in dataset.cases]
    for record in records:
        record['model_calls'][0]['attempt_ref'] = 'call:1'
        record['started_model_calls'] = [{'attempt_ref': 'call:1', 'rpc_ref': 'window', 'input_sha256': 'a'*64}]
    record = records[0]
    if corruption == 'missing inventory': record.pop('started_model_calls')
    elif corruption == 'omitted completion':
        record['started_model_calls'].append({'attempt_ref': 'call:2', 'rpc_ref': 'window', 'input_sha256': 'c'*64})
    elif corruption == 'duplicate completion': record['model_calls'].append(dict(record['model_calls'][0]))
    elif corruption == 'input mismatch': record['started_model_calls'][0]['input_sha256'] = 'c'*64
    else: record['started_model_calls'].append(dict(record['started_model_calls'][0]))
    with pytest.raises(ValueError): summarize_candidate_probe(dataset, records)
