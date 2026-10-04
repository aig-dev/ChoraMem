"""Protect frozen replay identity and scorer separation, not model wording."""
from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import fields
from pathlib import Path

import pytest


DATA_PATH = Path(__file__).parents[3] / "eval/cases/memory-effect-causal-v1.json"


def _api():
    assert importlib.util.find_spec("memory_core_eval.memory_effect_data") is not None, "missing causal data loader"
    return importlib.import_module("memory_core_eval.memory_effect_data")


def _load_mutation(tmp_path, mutate):
    raw = json.loads(DATA_PATH.read_text())
    mutate(raw)
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(raw, ensure_ascii=False))
    return _api().load_memory_effect(path)


def test_all_frozen_replay_classes_are_complete_and_scorer_free():
    """A dropped case/history or a label entering replay changes the experiment."""
    data = _api().load_memory_effect(DATA_PATH)
    assert [i.instance_ref for i in data.instances] == [f"r{i}" for i in range(1, 9)]
    assert [i.instance_ref for i in data.negative_controls] == [f"n{i}" for i in range(1, 5)]
    assert [i.case_ref for i in data.feedback_scenarios] == [f"f{i}" for i in range(1, 10)]
    assert [i.instance_ref for i in data.semantic_scenarios] == [f"g{i}" for i in range(1, 9)]
    assert len(data.source_sha256) == 64
    assert {f.name for f in fields(data.instances[0])} == {
        "instance_ref", "pattern_ref", "persona_text", "history_sessions", "current_request",
    }
    for instance in data.instances + data.negative_controls:
        assert len(instance.history_sessions) == 3
        assert len({s.session_ref for s in instance.history_sessions}) == 3
        assert all(s.situation and s.agent_act and s.outcome for s in instance.history_sessions)
    assert set(data.expectations) == {f"r{i}" for i in range(1, 9)} | {f"n{i}" for i in range(1, 5)} | {f"f{i}" for i in range(1, 10)} | {f"g{i}" for i in range(1, 9)}


def test_scorer_edits_never_change_replay_input_or_its_hash(tmp_path):
    """Scorer-only rubrics and Oracle text must not alter baseline material."""
    api = _api()
    original = api.load_memory_effect(DATA_PATH)
    changed = _load_mutation(tmp_path, lambda raw: raw["trajectories"][0]["expectation"].update(
        rubric="different independent grading rubric", oracle_disposition="different ideal tendency",
    ))
    assert original.source_sha256 != changed.source_sha256
    assert original.instances == changed.instances
    assert api.memory_effect_input_sha256(original.instances[0]) == api.memory_effect_input_sha256(changed.instances[0])
    modified_input = _load_mutation(tmp_path, lambda raw: raw["trajectories"][0].update(current_request="一个不同的真实请求"))
    assert api.memory_effect_input_sha256(original.instances[0]) != api.memory_effect_input_sha256(modified_input.instances[0])


@pytest.mark.parametrize("collection,index,key,value,error", [
    ("trajectories", 0, "case_ref", "r2", "identity"),
    ("negative_controls", 0, "case_ref", "n8", "identity"),
    ("feedback_scenarios", 0, "trajectory_ref", "missing", "trajectory"),
    ("feedback_scenarios", 0, "association", "guess_from_text", "association"),
    ("feedback_scenarios", 3, "outcome_text", "fabricated silent approval", "Outcome"),
    ("trajectories", 0, "unknown_scorer_tag", "ideal", "fields"),
])
def test_loader_rejects_identity_and_untrusted_source_drift(tmp_path, collection, index, key, value, error):
    """A producer cannot relabel missing sources or invent an association."""
    with pytest.raises(ValueError, match=error):
        _load_mutation(tmp_path, lambda raw: raw[collection][index].update({key: value}))


@pytest.mark.parametrize("collection", ["trajectories", "negative_controls", "feedback_scenarios", "semantic_scenarios"])
def test_loader_rejects_incomplete_denominators(tmp_path, collection):
    with pytest.raises(ValueError, match="requires"):
        _load_mutation(tmp_path, lambda raw: raw[collection].pop())


@pytest.mark.parametrize("mutate,error", [
    (lambda raw: raw.update(schema_version=True), "schema"),
    (lambda raw: raw.update(protocol="changed"), "protocol"),
    (lambda raw: raw["trajectories"][0]["history_sessions"][0].update(outcome=""), "outcome"),
    (lambda raw: raw["trajectories"][0]["history_sessions"][1].update(session_ref="s1"), "session"),
    (lambda raw: raw["trajectories"][0]["history_sessions"].pop(), "history"),
    (lambda raw: raw["trajectories"][0]["expectation"].update(formation=1), "formation"),
    (lambda raw: raw["trajectories"][0]["correction"]["same_condition_requests"].pop(), "three"),
])
def test_loader_rejects_schema_and_incomplete_causal_histories(tmp_path, mutate, error):
    with pytest.raises(ValueError, match=error):
        _load_mutation(tmp_path, mutate)


@pytest.mark.parametrize("field", ["target_behavior", "rubric", "oracle_disposition", "anti_disposition"])
def test_scorer_text_cannot_be_copied_into_current_role_or_request(tmp_path, field):
    """Literal scorer leakage would identify the target before memory acts."""
    with pytest.raises(ValueError, match="leak"):
        _load_mutation(tmp_path, lambda raw: raw["trajectories"][0].update(
            current_request=raw["trajectories"][0]["expectation"][field],
        ))


def test_feedback_missing_and_wrong_associations_and_semantic_meanings_are_frozen():
    data = _api().load_memory_effect(DATA_PATH)
    assert data.feedback_scenarios[3].outcome_text is None
    assert data.feedback_scenarios[7].association == "trusted_late"
    assert data.feedback_scenarios[8].association == "missing_and_wrong"
    assert data.expectations["f9"]["test_both_associations"] is True
    assert data.expectations["g7"]["single_equivalent_understanding"] is True
    assert data.expectations["g8"]["forbidden_claims"] == ["K7橙叶"]
    assert all(data.expectations[f"g{i}"]["no_disposition"] for i in range(1, 9))
