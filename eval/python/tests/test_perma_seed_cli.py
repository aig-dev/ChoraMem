import hashlib
import json

import pytest

import memory_core_eval.perma_seed_cli as perma_seed_cli
from memory_core_eval.perma_seed_cli import (
    build_run_manifest,
    main,
    verified_summary,
)
from memory_core_eval.perma_seed_data import PERSONA_SPLITS
from memory_core_eval.personamem_effect_runner import freeze_manifest


def _write_complete_questions(output):
    question_dir = output / "questions"
    question_dir.mkdir(parents=True)
    for split, personas in PERSONA_SPLITS.items():
        for persona_id in personas:
            for offset in range(20):
                stage = 2 if offset < 10 else 3
                question_ref = f"{persona_id}:task-{offset % 10}:{stage}"
                result = {
                    "protocol": "perma-seed-mcq-v2",
                    "split": split,
                    "persona_id": persona_id,
                    "question_ref": question_ref,
                    "stage": stage,
                    "answers": [
                        {"mode": "none", "correct": False, "error": ""},
                        {
                            "mode": "recollection_only",
                            "correct": False,
                            "error": "",
                        },
                        {
                            "mode": "seed_enabled",
                            "correct": True,
                            "error": "",
                            "rendered_disposition_refs": ["seed@1"],
                        },
                    ],
                }
                name = hashlib.sha256(question_ref.encode()).hexdigest() + ".json"
                (question_dir / name).write_text(json.dumps(result), encoding="utf-8")


def _write_lifecycle_summary(output):
    path = output / "lifecycle" / "summary.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "protocol": "seed-companion-lifecycle-v1",
                "decision": {"passed": True, "reasons": []},
            }
        ),
        encoding="utf-8",
    )


def test_run_manifest_freezes_all_controls_and_rejects_resume_drift(tmp_path):
    data_manifests = {
        "H1": {"data_revision": "data-rev", "split": "H1"},
        "H2": {"data_revision": "data-rev", "split": "H2"},
    }
    manifest = build_run_manifest(
        data_manifests=data_manifests,
        evaluation_ref="perma-seed-final-v1",
        answer_model="MiniMax-M2.5",
        answer_model_revision="api.minimaxi.com-2026-09-08",
        worker_model="MiniMax-M2.5",
        core_revision="core-rev",
        worker_revision="worker-rev",
        index_revision="index-rev",
        memory_tokens=2_048,
        output_tokens=32_768,
        settle_timeout=900,
        rpc_timeout=30,
        answer_timeout=300,
        answer_reasoning_split=True,
        answer_max_retries=0,
        rpc_attempts=5,
        persona_workers=5,
        bootstrap_samples=2_000,
    )

    assert manifest["modes"] == ["none", "recollection_only", "seed_enabled"]
    assert manifest["test_write_policy"] == (
        "unbound query SourceEvent only; no Delivery, AgentAct, or Outcome"
    )
    assert manifest["episode_evidence_max_bytes"] == 0
    assert manifest["memory_tokens"] == 2_048
    assert manifest["answer_request_timeout_seconds"] == 300
    assert manifest["answer_reasoning_split"] is True
    assert manifest["answer_max_retries"] == 0
    assert manifest["rpc_attempts"] == 5
    path = tmp_path / "manifest.json"
    freeze_manifest(path, manifest)
    with pytest.raises(ValueError, match="run manifest changed"):
        freeze_manifest(path, manifest | {"memory_tokens": 1_024})


def test_verified_summary_requires_all_100_frozen_question_groups(tmp_path):
    _write_complete_questions(tmp_path)
    _write_lifecycle_summary(tmp_path)
    manifest = {"protocol": "perma-seed-mcq-v2", "bootstrap_samples": 200}

    summary = verified_summary(tmp_path, manifest)
    assert summary["questions"] == 100
    assert summary["decision"]["passed"] is True

    next((tmp_path / "questions").glob("*.json")).unlink()
    with pytest.raises(ValueError, match="ten Type 2 and ten Type 3"):
        verified_summary(tmp_path, manifest)


def test_verified_summary_rejects_missing_or_failed_lifecycle_gate(tmp_path):
    _write_complete_questions(tmp_path)
    manifest = {"protocol": "perma-seed-mcq-v2", "bootstrap_samples": 200}

    with pytest.raises(ValueError, match="lifecycle"):
        verified_summary(tmp_path, manifest)

    _write_lifecycle_summary(tmp_path)
    path = tmp_path / "lifecycle" / "summary.json"
    failed = json.loads(path.read_text())
    failed["decision"] = {"passed": False, "reasons": ["causal gap"]}
    path.write_text(json.dumps(failed), encoding="utf-8")
    with pytest.raises(ValueError, match="lifecycle"):
        verified_summary(tmp_path, manifest)


def test_main_dispatches_lifecycle_without_starting_persona_questions(
    monkeypatch, tmp_path
):
    dispatched = {}

    def fake_prepare_split(root, *, split, endpoint):
        return (), {"split": split, "root": str(root), "endpoint": endpoint}

    async def fake_run(args, personas_by_split, data_manifests):
        dispatched["command"] = args.command
        dispatched["personas"] = personas_by_split
        dispatched["manifests"] = data_manifests
        return 0

    monkeypatch.setattr(perma_seed_cli, "prepare_split", fake_prepare_split)
    monkeypatch.setattr(perma_seed_cli, "run", fake_run)

    assert main(["lifecycle", "--data", str(tmp_path)]) == 0
    assert dispatched["command"] == "lifecycle"
    assert dispatched["personas"] == {"H1": (), "H2": ()}
    assert set(dispatched["manifests"]) == {"H1", "H2"}
