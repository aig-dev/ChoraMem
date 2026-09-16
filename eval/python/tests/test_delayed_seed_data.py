from __future__ import annotations

from collections import Counter
from dataclasses import fields
from pathlib import Path


DATA_PATH = Path(__file__).parents[2] / "seed-essential-delayed-v1.json"


def test_frozen_dataset_has_eight_label_separated_delayed_instances():
    """Catches missing cases, scorer leakage, or sessions without a full causal chain."""
    from memory_core_eval.delayed_seed_data import load_delayed_seed

    dataset = load_delayed_seed(DATA_PATH)

    assert len(dataset.instances) == 8
    assert len(dataset.labels) == 8
    assert len(dataset.source_sha256) == 64
    assert Counter(item.pattern_ref for item in dataset.instances) == {
        "one_question_when_overloaded": 2,
        "one_commitment_when_avoiding": 2,
        "permission_before_advice": 2,
        "three_beat_checkin": 2,
    }
    assert {field.name for field in fields(dataset.instances[0])} == {
        "instance_ref",
        "pattern_ref",
        "persona_text",
        "history_sessions",
        "current_request",
    }
    assert {field.name for field in fields(next(iter(dataset.labels.values())))} == {
        "instance_ref",
        "pattern_ref",
        "target_behavior",
    }
    assert {
        field.name for field in fields(next(iter(dataset.counterfactuals.values())))
    } == {
        "instance_ref",
        "oracle_disposition",
        "anti_disposition",
    }
    assert all(len(item.history_sessions) == 3 for item in dataset.instances)
    assert all(
        session.session_ref.strip()
        and session.situation.strip()
        and session.agent_act.strip()
        and session.outcome.strip()
        for item in dataset.instances
        for session in item.history_sessions
    )
    assert all(
        dataset.labels[item.instance_ref].instance_ref == item.instance_ref
        and dataset.labels[item.instance_ref].pattern_ref == item.pattern_ref
        for item in dataset.instances
    )


def test_probe_inputs_do_not_carry_or_repeat_scorer_only_fields():
    """Catches exact target/oracle/anti leakage into delayed answer inputs."""
    from memory_core_eval.delayed_seed_data import load_delayed_seed

    dataset = load_delayed_seed(DATA_PATH)

    for instance in dataset.instances:
        label = dataset.labels[instance.instance_ref]
        counterfactual = dataset.counterfactuals[instance.instance_ref]
        answer_input = instance.persona_text + "\n" + instance.current_request
        assert label.target_behavior not in answer_input
        assert counterfactual.oracle_disposition not in answer_input
        assert counterfactual.anti_disposition not in answer_input
        assert "oracle" not in answer_input.lower()
        assert "anti_seed" not in answer_input.lower()


def test_loader_rejects_an_incomplete_history_session(tmp_path):
    """Catches fixtures that cannot establish Situation-AgentAct-Outcome causality."""
    import json

    import pytest

    from memory_core_eval.delayed_seed_data import load_delayed_seed

    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    raw["patterns"][0]["history_sessions"][0]["outcome"] = ""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="outcome"):
        load_delayed_seed(path)
