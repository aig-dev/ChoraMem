from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


INSTANCE_TYPES = ("changing", "consistent", "contrastive")


def _session(index: int) -> dict[str, object]:
    return {
        "context_factor": f"SECRET historical context {index}",
        "contextual_preference": f"SECRET historical preference {index}",
        "dialogue": [
            {"role": "user", "content": f"request {index}"},
            {"role": "assistant", "content": f"answer {index}"},
            {"role": "user", "content": f"feedback {index}"},
        ],
    }


def _rows() -> list[dict[str, object]]:
    return [
        {
            "persona_id": persona,
            "instance_type": instance_type,
            "current_request": f"current request for {persona}",
            "current_context_factor": "SECRET current context",
            "current_contextual_preference": "SECRET current preference",
            "current_checklist": ["SECRET criterion one", "SECRET criterion two"],
            "prior_interactions": [_session(index) for index in range(8)],
        }
        for persona in ("seen", "alpha", "beta")
        for instance_type in INSTANCE_TYPES
    ]


def _write_fixture(
    tmp_path: Path, *, rows: list[dict[str, object]] | None = None
) -> tuple[Path, Path]:
    data_path = tmp_path / "test.parquet"
    pq.write_table(pa.Table.from_pylist(rows or _rows()), data_path)
    manifest = {
        "schema_version": 1,
        "benchmark": "CUPID",
        "status": "frozen-before-semantic-inspection",
        "dataset": {
            "repository": "fixture/CUPID",
            "revision": "fixture-revision",
            "file": "test.parquet",
            "size_bytes": data_path.stat().st_size,
            "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
            "expected_rows": 9,
            "expected_personas": 3,
            "expected_rows_per_persona": 3,
            "expected_sessions_per_instance": 8,
            "expected_instance_types": list(INSTANCE_TYPES),
        },
        "split": {
            "unit": "persona_id",
            "salt": "split-salt",
            "expected_personas": {"dev": 1, "H1": 1, "H2": 1},
            "materialized_from_identity_columns_only": {
                "dev": {
                    "personas": 1,
                    "rows": 3,
                    "persona_ids_sha256": "47a8dea66db04a6f2a18b6e27ef53a56f2122c0733e67f3908f1485fe1d4547f",
                },
                "H1": {
                    "personas": 1,
                    "rows": 3,
                    "persona_ids_sha256": "f2c82decdd7181cf98945929a62598db7e6b477e11f6e0eb0ae97020eff151ad",
                },
                "H2": {
                    "personas": 1,
                    "rows": 3,
                    "persona_ids_sha256": "b6a98d9ce9a2d9149288fa3df42d377c3e42737afdcdaf714e33c0a100b51060",
                },
                "overlap": 0,
                "instance_types_per_split": {
                    "changing": 1,
                    "consistent": 1,
                    "contrastive": 1,
                },
            },
        },
        "exposure": {
            "exposed_personas": ["seen"],
            "rule": "Every exposed persona belongs to dev and can never enter H1 or H2.",
        },
    }
    manifest_path = tmp_path / "split.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return data_path, manifest_path


def test_load_cupid_keeps_hidden_labels_out_of_replay_instances(tmp_path: Path):
    from memory_core_eval.cupid_data import load_cupid

    data_path, manifest_path = _write_fixture(tmp_path)

    dataset = load_cupid(data_path, split_manifest=manifest_path, split="dev")

    assert len(dataset.instances) == 3
    assert {instance.persona_id for instance in dataset.instances} == {"seen"}
    consistent = next(
        instance
        for instance in dataset.instances
        if instance.instance_ref
        == "cupid-652279b8160f5788b60fd878afa5b4295b864ea8f642d05bc62bad1a32647591"
    )
    assert consistent.current_request == "current request for seen"
    assert len(consistent.history_sessions) == 8
    assert [message.role for message in consistent.history_sessions[0].messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert "SECRET" not in repr(dataset.instances)

    label = dataset.labels[consistent.instance_ref]
    assert label.persona_id == "seen"
    assert label.instance_type == "consistent"
    assert label.preference == "SECRET current preference"
    assert label.checklist == ("SECRET criterion one", "SECRET criterion two")


def test_load_cupid_uses_frozen_persona_split_and_rejects_file_drift(tmp_path: Path):
    from memory_core_eval.cupid_data import load_cupid

    data_path, manifest_path = _write_fixture(tmp_path)

    h1 = load_cupid(data_path, split_manifest=manifest_path, split="H1")
    h2 = load_cupid(data_path, split_manifest=manifest_path, split="H2")

    assert {instance.persona_id for instance in h1.instances} == {"beta"}
    assert {instance.persona_id for instance in h2.instances} == {"alpha"}
    assert len(h1.instances) == len(h2.instances) == 3

    drifted = bytearray(data_path.read_bytes())
    drifted[-1] ^= 1
    data_path.write_bytes(drifted)
    with pytest.raises(ValueError, match="SHA-256"):
        load_cupid(data_path, split_manifest=manifest_path, split="dev")


def test_load_cupid_rejects_non_alternating_dialogue(tmp_path: Path):
    from memory_core_eval.cupid_data import load_cupid

    rows = _rows()
    rows[0]["prior_interactions"][0]["dialogue"][1]["role"] = "user"
    data_path, manifest_path = _write_fixture(tmp_path, rows=rows)

    with pytest.raises(ValueError, match="alternate user and assistant"):
        load_cupid(data_path, split_manifest=manifest_path, split="dev")


def test_load_cupid_accepts_session_without_a_final_user_outcome(tmp_path: Path):
    from memory_core_eval.cupid_data import load_cupid

    rows = _rows()
    rows[0]["prior_interactions"][0]["dialogue"].append(
        {"role": "assistant", "content": "final answer without later feedback"}
    )
    data_path, manifest_path = _write_fixture(tmp_path, rows=rows)

    dataset = load_cupid(data_path, split_manifest=manifest_path, split="dev")

    changed = next(
        instance
        for instance in dataset.instances
        if instance.instance_ref
        == "cupid-3cb6358b2d5ae9c8a681f28157d92dc3821f15a6cc5762b8b90f1ea7e7d64114"
    )
    assert changed.history_sessions[0].messages[-1].role == "assistant"
