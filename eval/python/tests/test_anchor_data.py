from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


_BANK_ITEMS = {
    "bard_orin_lyrae__emotional_vulnerability": 4,
    "co_jules_vega__adversarial": 8,
    "hc_nia_okonkwo__clean": 3,
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _messages(bank_id: str) -> list[dict[str, object]]:
    return [
        {
            "session_id": session_id,
            "turn": turn,
            "role": role,
            "content": f"{bank_id} session {session_id} turn {turn} {role}",
        }
        for session_id in range(3)
        for turn in range(2)
        for role in ("user", "assistant")
    ]


def _build_source(root: Path) -> dict[str, list[dict[str, object]]]:
    transcripts: dict[str, list[dict[str, object]]] = {}
    for bank_id, item_count in _BANK_ITEMS.items():
        base = root / "data/examples" / bank_id
        _write_json(
            base / "persona_card.json",
            {
                "_doc": "ignored documentation",
                "id": "test_persona",
                "domain": "companionship",
                "archetype": "steady_companion",
                "core": {
                    "name": "Test Persona",
                    "pronouns": "they/them",
                    "role": "long-term companion",
                    "style": ["warm", "plain"],
                    "values": ["agency"],
                    "boundaries": ["never fabricate awareness"],
                },
                "mutable_seed": {
                    "location": "Chengdu",
                    "active_focus": ["memory", "gardening"],
                    "current_goals": [],
                },
            },
        )
        transcript = _messages(bank_id)
        transcripts[bank_id] = transcript
        _write_jsonl(base / "transcript.jsonl", transcript)
        _write_jsonl(
            base / "items.jsonl",
            [
                {
                    "item_id": f"{bank_id}-item-{index}",
                    "family": "P-PROT" if index % 2 else "DRIFT-DELTA",
                    "stem": f"trajectory question {index}",
                    "options": ["first", "second", "third", "fourth"],
                    "correct_index": index % 4,
                    "source": {"session_id": index % 3},
                    "_bank": bank_id,
                }
                for index in range(item_count)
            ],
        )
    return transcripts


def _behavior_manifest(
    path: Path,
    transcripts: dict[str, list[dict[str, object]]],
) -> Path:
    targets = (
        (
            "persona-bard",
            "persona_continuity",
            "bard_orin_lyrae__emotional_vulnerability",
            1,
            1,
        ),
        (
            "persona-co",
            "persona_continuity",
            "co_jules_vega__adversarial",
            1,
            1,
        ),
        (
            "adapt-co",
            "relationship_adaptation",
            "co_jules_vega__adversarial",
            2,
            0,
        ),
        (
            "adapt-hc",
            "relationship_adaptation",
            "hc_nia_okonkwo__clean",
            2,
            0,
        ),
        (
            "repair-co",
            "relationship_repair",
            "co_jules_vega__adversarial",
            2,
            1,
        ),
        (
            "repair-bard",
            "relationship_repair",
            "bard_orin_lyrae__emotional_vulnerability",
            2,
            1,
        ),
    )
    checkpoints = []
    for instance_ref, dimension, bank_id, session_id, turn in targets:
        target = next(
            message
            for message in transcripts[bank_id]
            if message["session_id"] == session_id
            and message["turn"] == turn
            and message["role"] == "user"
        )
        checkpoints.append(
            {
                "instance_ref": instance_ref,
                "dimension": dimension,
                "bank_id": bank_id,
                "session_id": session_id,
                "turn": turn,
                "role": "user",
                "content_sha256": hashlib.sha256(
                    str(target["content"]).encode()
                ).hexdigest(),
                "evidence_refs": (
                    [
                        {
                            "session_id": session_id,
                            "turn": turn - 1,
                            "role": "assistant",
                        }
                    ]
                    if turn > 0
                    else []
                ),
                "rubric": {
                    "score_2": f"SECRET_RUBRIC_{instance_ref}_2",
                    "score_1": f"SECRET_RUBRIC_{instance_ref}_1",
                    "score_0": f"SECRET_RUBRIC_{instance_ref}_0",
                },
            }
        )
    _write_json(
        path,
        {
            "schema_version": 1,
            "upstream": {
                "repository": "SalesforceAIResearch/AnchorBench",
                "revision": "41bd0e20b9524ce484db301ac15dc14121bf06ad",
            },
            "checkpoints": checkpoints,
        },
    )
    return path


@pytest.fixture
def anchor_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "source"
    transcripts = _build_source(root)
    manifest = _behavior_manifest(tmp_path / "behavior.json", transcripts)

    import memory_core_eval.anchor_data as anchor_data

    monkeypatch.setattr(
        anchor_data,
        "validate_anchor_source",
        lambda _: {
            "revision": "41bd0e20b9524ce484db301ac15dc14121bf06ad",
            "total_banks": 3,
            "total_items": 15,
        },
    )
    return root, manifest, transcripts


def test_load_anchor_separates_answer_inputs_from_scorer_labels(anchor_fixture):
    from memory_core_eval.anchor_data import load_anchor

    root, manifest, _ = anchor_fixture
    dataset = load_anchor(root, behavior_manifest=manifest)

    assert len(dataset.trajectory_instances) == 15
    assert len(dataset.behavior_instances) == 6
    assert len(dataset.trajectory_labels) == 15
    assert len(dataset.behavior_labels) == 6
    assert all(
        not hasattr(instance, "correct_index")
        for instance in dataset.trajectory_instances
    )
    assert "SECRET_RUBRIC" not in repr(dataset.behavior_instances)
    assert "SECRET_RUBRIC" in repr(dataset.behavior_labels)


def test_load_anchor_trajectory_history_ends_at_its_source_session(anchor_fixture):
    from memory_core_eval.anchor_data import load_anchor

    root, manifest, _ = anchor_fixture
    dataset = load_anchor(root, behavior_manifest=manifest)
    instance = next(
        item
        for item in dataset.trajectory_instances
        if item.instance_ref.endswith("item-0")
    )

    assert [session.session_id for session in instance.history_sessions] == [0]
    assert {
        message.session_id
        for session in instance.history_sessions
        for message in session.messages
    } == {0}


def test_load_anchor_behavior_history_stops_before_target_user_turn(anchor_fixture):
    from memory_core_eval.anchor_data import load_anchor

    root, manifest, _ = anchor_fixture
    dataset = load_anchor(root, behavior_manifest=manifest)
    instance = next(
        item
        for item in dataset.behavior_instances
        if item.instance_ref == "persona-bard"
    )

    assert instance.current_request.endswith("session 1 turn 1 user")
    assert [
        (message.session_id, message.turn, message.role)
        for session in instance.history_sessions
        for message in session.messages
        if message.session_id == 1
    ] == [(1, 0, "user"), (1, 0, "assistant")]
    assert all(
        message.content != instance.current_request
        for session in instance.history_sessions
        for message in session.messages
    )


def test_load_anchor_rejects_behavior_content_hash_drift(anchor_fixture):
    from memory_core_eval.anchor_data import load_anchor

    root, manifest, _ = anchor_fixture
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["checkpoints"][0]["content_sha256"] = "0" * 64
    _write_json(manifest, value)

    with pytest.raises(ValueError, match="content SHA-256"):
        load_anchor(root, behavior_manifest=manifest)


def test_load_anchor_rejects_future_behavior_evidence(anchor_fixture):
    from memory_core_eval.anchor_data import load_anchor

    root, manifest, _ = anchor_fixture
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["checkpoints"][0]["evidence_refs"] = [
        {"session_id": 1, "turn": 1, "role": "assistant"}
    ]
    _write_json(manifest, value)

    with pytest.raises(ValueError, match="evidence must precede"):
        load_anchor(root, behavior_manifest=manifest)


def test_load_anchor_rejects_non_alternating_transcript(anchor_fixture):
    from memory_core_eval.anchor_data import load_anchor

    root, manifest, _ = anchor_fixture
    transcript = (
        root
        / "data/examples/co_jules_vega__adversarial/transcript.jsonl"
    )
    rows = [json.loads(line) for line in transcript.read_text().splitlines()]
    rows[1]["role"] = "user"
    _write_jsonl(transcript, rows)

    with pytest.raises(ValueError, match="alternate user and assistant"):
        load_anchor(root, behavior_manifest=manifest)


def test_render_persona_card_is_deterministic_shallow_tagged_text():
    from memory_core_eval.anchor_data import render_persona_card

    card = {
        "_doc": "not part of the persona",
        "id": "test_persona",
        "domain": "companionship",
        "archetype": "steady_companion",
        "core": {
            "name": "Test Persona",
            "pronouns": "they/them",
            "role": "long-term companion",
            "style": ["warm", "plain"],
            "values": ["agency"],
            "boundaries": ["never fabricate awareness"],
        },
        "mutable_seed": {
            "location": "Chengdu",
            "active_focus": ["memory", "gardening"],
            "current_goals": [],
        },
    }

    assert render_persona_card(card) == """PERSONA_ID test_persona
DOMAIN companionship
ARCHETYPE steady_companion
NAME Test Persona
PRONOUNS they/them
ROLE long-term companion

STYLE
- warm
- plain

VALUES
- agency

BOUNDARIES
- never fabricate awareness

MUTABLE_STATE
active_focus: memory | gardening
current_goals: NONE
location: Chengdu"""
