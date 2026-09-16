from __future__ import annotations

from pathlib import Path

import pytest

from memory_core_eval.seed_generalization_data import (
    PROTOCOL,
    load_seed_generalization,
)


DATA = Path(__file__).resolve().parents[2] / "seed-generalization-v1.json"
ANCHOR_COMPANION_DATA = (
    Path(__file__).resolve().parents[2] / "anchor-companion-v1.json"
)


def test_frozen_generalization_data_has_long_positive_and_negative_cases() -> None:
    dataset = load_seed_generalization(DATA)

    assert dataset.protocol == PROTOCOL == "seed-generalization-v1"
    assert len(dataset.instances) == 8
    assert len(dataset.positive_refs) == 4
    assert len(dataset.negative_refs) == 4
    assert set(dataset.positive_refs).isdisjoint(dataset.negative_refs)
    assert set(dataset.expectations) == {
        instance.instance_ref for instance in dataset.instances
    }

    for instance in dataset.instances:
        expectation = dataset.expectations[instance.instance_ref]
        assert len(instance.history_sessions) == 8
        assert len(set(session.session_ref for session in instance.history_sessions)) == 8
        assert len(expectation.signal_session_refs) == 3
        assert expectation.signal_session_refs.issubset(
            {session.session_ref for session in instance.history_sessions}
        )
        assert expectation.distractor_session_refs == (
            {session.session_ref for session in instance.history_sessions}
            - expectation.signal_session_refs
        )
        assert len(expectation.distractor_session_refs) == 5
        signal_positions = [
            index
            for index, session in enumerate(instance.history_sessions)
            if session.session_ref in expectation.signal_session_refs
        ]
        assert all(
            right - left > 1
            for left, right in zip(signal_positions, signal_positions[1:])
        )


def test_positive_answer_inputs_do_not_contain_scorer_or_counterfactual_text() -> None:
    dataset = load_seed_generalization(DATA)

    assert set(dataset.labels) == set(dataset.positive_refs)
    assert set(dataset.counterfactuals) == set(dataset.positive_refs)
    for instance in dataset.instances:
        answer_input = instance.persona_text + "\n" + instance.current_request
        if instance.instance_ref in dataset.positive_refs:
            label = dataset.labels[instance.instance_ref]
            counterfactual = dataset.counterfactuals[instance.instance_ref]
            assert label.target_behavior not in answer_input
            assert counterfactual.oracle_disposition not in answer_input
            assert counterfactual.anti_disposition not in answer_input
        else:
            assert instance.instance_ref not in dataset.labels
            assert instance.instance_ref not in dataset.counterfactuals


def test_source_hash_is_stable_and_nonempty() -> None:
    dataset = load_seed_generalization(DATA)

    assert len(dataset.source_sha256) == 64
    assert int(dataset.source_sha256, 16) > 0


def test_loader_rejects_post_freeze_shape_drift(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        '{"schema_version":1,"protocol":"seed-generalization-v1",'
        '"persona_text":"persona","positive_cases":[],"negative_cases":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="four positive and four negative"):
        load_seed_generalization(invalid)


def test_anchor_companion_overlay_changes_only_behavior_probes() -> None:
    from memory_core_eval.anchor_companion_data import load_anchor_companion

    base = load_seed_generalization(DATA)
    companion = load_anchor_companion(ANCHOR_COMPANION_DATA)
    base_by_ref = {item.instance_ref: item for item in base.instances}
    companion_by_ref = {item.instance_ref: item for item in companion.instances}

    assert companion.benchmark == "Memory Core ANCHOR companion-disposition v1"
    assert set(companion_by_ref) == set(base_by_ref)
    assert companion.positive_refs == base.positive_refs
    assert companion.negative_refs == base.negative_refs
    for ref in base_by_ref:
        assert companion_by_ref[ref].persona_text == base_by_ref[ref].persona_text
        assert companion_by_ref[ref].history_sessions == base_by_ref[ref].history_sessions
    for ref in base.negative_refs:
        assert companion_by_ref[ref].current_request == base_by_ref[ref].current_request
    for ref in base.positive_refs:
        assert companion_by_ref[ref].current_request != base_by_ref[ref].current_request

    writing = companion_by_ref["holdout-writing-voice"].current_request
    depleted = companion_by_ref["holdout-depleted-start"].current_request
    assert "The last key clicked" in writing
    assert "without turning tonight into a project" not in depleted
