from __future__ import annotations

import json

import pytest

from memory_core_eval.cupid_data import CupidDataset, CupidInstance, CupidLabel
from memory_core_eval.cupid_runner import freeze_result
from memory_core_eval.personamem_effect_runner import freeze_manifest


def _dataset(split: str, identities: tuple[tuple[str, str], ...]) -> CupidDataset:
    instances = tuple(
        CupidInstance(
            instance_ref="cupid-" + character * 64,
            persona_id=persona_id,
            current_request="request",
            history_sessions=(),
        )
        for character, persona_id in identities
    )
    return CupidDataset(
        split=split,
        instances=instances,
        labels={
            instance.instance_ref: CupidLabel(
                instance_ref=instance.instance_ref,
                persona_id=instance.persona_id,
                split=split,
                instance_type="consistent",
                preference="scorer-only preference",
                checklist=("criterion",),
            )
            for instance in instances
        },
    )


def _manifest(tmp_path, datasets, *, cohort="formal", persona_limit=0):
    from memory_core_eval.cupid_cli import build_run_manifest

    data = tmp_path / "test.parquet"
    split_manifest = tmp_path / "split.json"
    data.write_bytes(b"pinned CUPID fixture")
    split_manifest.write_text('{"frozen":true}\n', encoding="utf-8")
    return build_run_manifest(
        data_path=data,
        split_manifest_path=split_manifest,
        datasets=datasets,
        cohort=cohort,
        persona_limit=persona_limit,
        evaluation_ref="cupid-effect-v1",
        answer_model="MiniMax-M2.5",
        answer_model_revision="MiniMax-M2.5@api.minimaxi.com-2026-09-08",
        judge_model="MiniMax-M2.5",
        judge_model_revision="MiniMax-M2.5@api.minimaxi.com-2026-09-08",
        worker_model="MiniMax-M2.5",
        worker_model_revision="MiniMax-M2.5@api.minimaxi.com-2026-09-08",
        model_endpoint="https://api.minimaxi.com/v1",
        core_revision="core-rev",
        worker_revision="worker-rev",
        index_revision="index-rev",
        memory_tokens=2_048,
        answer_tokens=32_768,
        judge_tokens=8_192,
        settle_timeout=900,
        rpc_timeout=30,
        model_timeout=300,
        rpc_attempts=5,
        instance_workers=4,
        bootstrap_samples=2_000,
    )


def _result(instance: CupidInstance, split: str) -> dict[str, object]:
    answers = []
    for mode, score, dispositions in (
        ("none", 4, []),
        ("recollection_only", 5, []),
        ("seed_enabled", 6, ["seed@1"]),
    ):
        answers.append({
            "mode": mode,
            "output": f"{mode} answer",
            "judge_output": f"### Evaluation Score\n{score}",
            "score": score,
            "error": "",
            "answer_request_sha256": "a" * 64,
            "judge_request_sha256": "b" * 64,
            "memory_refs": dispositions,
            "selected_recollection_refs": ["rec@1"],
            "selected_disposition_refs": ["seed@1"],
            "rendered_recollection_refs": (
                [] if mode == "none" else ["rec@1"]
            ),
            "rendered_disposition_refs": dispositions,
            "memory_tokens": len(dispositions),
        })
    return {
        "protocol": "cupid-seed-generation-v1",
        "split": split,
        "persona_id": instance.persona_id,
        "instance_ref": instance.instance_ref,
        "answers": answers,
        "diagnostic": {
            "active_dispositions": 1,
            "selected_disposition_refs": ["seed@1"],
            "rendered_disposition_refs": ["seed@1"],
        },
    }


def test_run_manifest_freezes_dataset_models_prompts_sources_and_budgets(tmp_path):
    datasets = {
        "H1": _dataset("H1", (("a", "p1"),)),
        "H2": _dataset("H2", (("b", "p2"),)),
    }

    manifest = _manifest(tmp_path, datasets)

    assert manifest["cohort"] == "formal"
    assert manifest["persona_limit"] == 0
    assert manifest["dev_sampling"] == "sha256 whole-persona v1"
    assert manifest["split_order"] == ["H1", "H2"]
    assert manifest["modes"] == ["none", "recollection_only", "seed_enabled"]
    assert manifest["learned_memory_tokens"] == 2_048
    assert manifest["episode_evidence_max_bytes"] == 0
    assert manifest["answer_max_output_tokens"] == 32_768
    assert manifest["judge_max_output_tokens"] == 8_192
    assert manifest["answer_model"] == manifest["judge_model"]
    assert manifest["answer_model"] == manifest["worker_model"]
    assert manifest["answer_model_revision"] == (
        "MiniMax-M2.5@api.minimaxi.com-2026-09-08"
    )
    assert set(manifest["prompts_sha256"]) == {"answer", "judge"}
    assert all(len(value) == 64 for value in manifest["prompts_sha256"].values())
    assert set(manifest["harness_sources"]) >= {
        "cupid_data.py",
        "cupid_effect.py",
        "cupid_runner.py",
        "cupid_cli.py",
    }
    assert manifest["splits"]["H1"]["instance_refs"] == [
        "cupid-" + "a" * 64
    ]
    assert "api_key" not in json.dumps(manifest).lower()

    path = tmp_path / "output" / "manifest.json"
    freeze_manifest(path, manifest)
    with pytest.raises(ValueError, match="run manifest changed"):
        freeze_manifest(path, manifest | {"learned_memory_tokens": 1_024})


def test_select_cohort_limits_only_complete_dev_personas():
    from memory_core_eval.cupid_cli import select_cohort

    dev = _dataset(
        "dev",
        (("a", "p1"), ("b", "p1"), ("c", "p2"), ("d", "p2")),
    )
    formal = {
        "H1": _dataset("H1", (("e", "p3"),)),
        "H2": _dataset("H2", (("f", "p4"),)),
    }

    selected = select_cohort({"dev": dev}, cohort="dev", persona_limit=1)
    assert len({item.persona_id for item in selected["dev"].instances}) == 1
    assert len(selected["dev"].instances) == 2

    assert tuple(select_cohort(formal, cohort="formal")) == ("H1", "H2")
    with pytest.raises(ValueError, match="formal.*persona limit"):
        select_cohort(formal, cohort="formal", persona_limit=1)
    with pytest.raises(ValueError, match="cohort"):
        select_cohort({"dev": dev}, cohort="holdout")


@pytest.mark.asyncio
async def test_run_instances_resumes_only_complete_identity_matched_results(tmp_path):
    from memory_core_eval.cupid_cli import run_instances_bounded

    dataset = _dataset("dev", (("a", "p1"), ("b", "p2")))
    first, second = dataset.instances
    freeze_result(tmp_path / "instances" / f"{first.instance_ref}.json", _result(first, "dev"))
    calls = []

    async def process(instance, label):
        calls.append((instance.instance_ref, label.instance_ref))
        return _result(instance, "dev")

    rows = await run_instances_bounded(
        datasets={"dev": dataset},
        output_dir=tmp_path,
        limit=2,
        process=process,
    )

    assert calls == [(second.instance_ref, second.instance_ref)]
    assert {row["instance_ref"] for row in rows} == {
        first.instance_ref,
        second.instance_ref,
    }


@pytest.mark.asyncio
async def test_run_instances_finishes_h1_before_starting_h2(tmp_path):
    import asyncio

    from memory_core_eval.cupid_cli import run_instances_bounded

    datasets = {
        "H1": _dataset("H1", (("a", "p1"), ("b", "p2"))),
        "H2": _dataset("H2", (("c", "p3"),)),
    }
    completed = []

    async def process(instance, label):
        if label.split == "H2":
            assert set(completed) == {
                datasets["H1"].instances[0].instance_ref,
                datasets["H1"].instances[1].instance_ref,
            }
        await asyncio.sleep(0)
        completed.append(instance.instance_ref)
        return _result(instance, label.split)

    await run_instances_bounded(
        datasets=datasets,
        output_dir=tmp_path,
        limit=3,
        process=process,
    )

    assert completed[-1] == datasets["H2"].instances[0].instance_ref


def test_verified_summary_requires_exact_results_and_passed_formal_lifecycle(tmp_path):
    from memory_core_eval.cupid_cli import verified_summary

    datasets = {
        "H1": _dataset("H1", (("a", "p1"),)),
        "H2": _dataset("H2", (("b", "p2"),)),
    }
    manifest = _manifest(tmp_path, datasets)
    freeze_manifest(tmp_path / "manifest.json", manifest)
    for dataset in datasets.values():
        instance = dataset.instances[0]
        freeze_result(
            tmp_path / "instances" / f"{instance.instance_ref}.json",
            _result(instance, dataset.split),
        )
    lifecycle_path = tmp_path / "lifecycle" / "summary.json"
    lifecycle_path.parent.mkdir(parents=True)
    lifecycle_path.write_text(json.dumps({
        "protocol": "seed-companion-lifecycle-v1",
        "decision": {"passed": True, "reasons": []},
    }), encoding="utf-8")

    summary = verified_summary(tmp_path, manifest=manifest, datasets=datasets)

    assert summary["instances"] == 2
    assert summary["decision"] == {"passed": True, "reasons": []}
    assert summary["lifecycle"]["decision"]["passed"] is True
    assert len(summary["manifest_sha256"]) == 64

    (tmp_path / "instances" / f"{datasets['H2'].instances[0].instance_ref}.json").unlink()
    with pytest.raises(ValueError, match="complete frozen cohort"):
        verified_summary(tmp_path, manifest=manifest, datasets=datasets)


def test_verified_summary_rejects_an_unregistered_result_file(tmp_path):
    from memory_core_eval.cupid_cli import verified_summary

    datasets = {
        "H1": _dataset("H1", (("a", "p1"),)),
        "H2": _dataset("H2", (("b", "p2"),)),
    }
    manifest = _manifest(tmp_path, datasets)
    for dataset in datasets.values():
        instance = dataset.instances[0]
        freeze_result(
            tmp_path / "instances" / f"{instance.instance_ref}.json",
            _result(instance, dataset.split),
        )
    (tmp_path / "instances" / ("cupid-" + "c" * 64 + ".json")).write_text(
        "{}", encoding="utf-8"
    )
    lifecycle_path = tmp_path / "lifecycle" / "summary.json"
    lifecycle_path.parent.mkdir(parents=True)
    lifecycle_path.write_text(json.dumps({
        "protocol": "seed-companion-lifecycle-v1",
        "decision": {"passed": True, "reasons": []},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="unregistered CUPID result"):
        verified_summary(tmp_path, manifest=manifest, datasets=datasets)


def test_verified_summary_enforces_the_frozen_memory_budget(tmp_path):
    from memory_core_eval.cupid_cli import verified_summary

    datasets = {"dev": _dataset("dev", (("a", "p1"),))}
    manifest = _manifest(tmp_path, datasets, cohort="dev")
    instance = datasets["dev"].instances[0]
    result = _result(instance, "dev")
    result["answers"][2]["memory_tokens"] = 2_049
    freeze_result(
        tmp_path / "instances" / f"{instance.instance_ref}.json", result
    )

    with pytest.raises(ValueError, match="memory budget"):
        verified_summary(tmp_path, manifest=manifest, datasets=datasets)


def test_verified_summary_replays_the_frozen_dev_persona_limit(tmp_path):
    from memory_core_eval.cupid_cli import select_cohort, verified_summary

    full = _dataset(
        "dev",
        (("a", "p1"), ("b", "p1"), ("c", "p2"), ("d", "p2")),
    )
    selected = select_cohort({"dev": full}, cohort="dev", persona_limit=1)
    manifest = _manifest(
        tmp_path,
        selected,
        cohort="dev",
        persona_limit=1,
    )
    for instance in selected["dev"].instances:
        freeze_result(
            tmp_path / "instances" / f"{instance.instance_ref}.json",
            _result(instance, "dev"),
        )

    summary = verified_summary(
        tmp_path,
        manifest=manifest,
        datasets={"dev": full},
    )

    assert summary["instances"] == len(selected["dev"].instances)


@pytest.mark.asyncio
async def test_execute_cohort_runs_passing_lifecycle_before_formal_instances(tmp_path):
    from memory_core_eval.cupid_cli import execute_cohort

    datasets = {
        "H1": _dataset("H1", (("a", "p1"),)),
        "H2": _dataset("H2", (("b", "p2"),)),
    }
    manifest = _manifest(tmp_path, datasets)
    events = []

    async def lifecycle():
        events.append("lifecycle")
        return {
            "protocol": "seed-companion-lifecycle-v1",
            "decision": {"passed": True, "reasons": []},
        }

    async def process(instance, _label):
        assert events[0] == "lifecycle"
        events.append(instance.instance_ref)
        return _result(instance, "H1" if instance.persona_id == "p1" else "H2")

    summary = await execute_cohort(
        manifest=manifest,
        datasets=datasets,
        output_dir=tmp_path,
        instance_workers=2,
        process=process,
        lifecycle=lifecycle,
    )

    assert events[0] == "lifecycle"
    assert summary["decision"]["passed"] is True
    assert json.loads((tmp_path / "summary.json").read_text()) == summary


@pytest.mark.asyncio
async def test_execute_cohort_stops_before_instances_when_lifecycle_fails(tmp_path):
    from memory_core_eval.cupid_cli import execute_cohort

    datasets = {
        "H1": _dataset("H1", (("a", "p1"),)),
        "H2": _dataset("H2", (("b", "p2"),)),
    }
    manifest = _manifest(tmp_path, datasets)

    async def lifecycle():
        return {
            "protocol": "seed-companion-lifecycle-v1",
            "decision": {"passed": False, "reasons": ["causal gap"]},
        }

    async def process(_instance, _label):
        raise AssertionError("formal instances must not run after a failed lifecycle")

    with pytest.raises(RuntimeError, match="lifecycle gate failed"):
        await execute_cohort(
            manifest=manifest,
            datasets=datasets,
            output_dir=tmp_path,
            instance_workers=2,
            process=process,
            lifecycle=lifecycle,
        )


def test_prepare_command_prints_structure_without_scorer_labels(monkeypatch, capsys, tmp_path):
    import memory_core_eval.cupid_cli as cupid_cli

    datasets = {
        "dev": _dataset("dev", (("a", "p0"),)),
        "H1": _dataset("H1", (("b", "p1"),)),
        "H2": _dataset("H2", (("c", "p2"),)),
    }
    calls = []

    def fake_load(path, *, split_manifest, split):
        calls.append((path, split_manifest, split))
        return datasets[split]

    monkeypatch.setattr(cupid_cli, "load_cupid", fake_load)

    assert cupid_cli.main([
        "prepare",
        "--data",
        str(tmp_path / "test.parquet"),
        "--split-manifest",
        str(tmp_path / "split.json"),
    ]) == 0

    output = capsys.readouterr().out
    assert [call[2] for call in calls] == ["dev", "H1", "H2"]
    assert json.loads(output) == {
        "dev": {"instances": 1, "personas": 1},
        "H1": {"instances": 1, "personas": 1},
        "H2": {"instances": 1, "personas": 1},
    }
    assert "scorer-only preference" not in output


def test_run_command_dispatches_only_the_selected_dev_personas(
    monkeypatch, tmp_path
):
    import memory_core_eval.cupid_cli as cupid_cli

    dev = _dataset(
        "dev",
        (("a", "p0"), ("b", "p0"), ("c", "p1"), ("d", "p1")),
    )
    loaded_splits = []
    dispatched = {}

    def fake_load(_path, *, split_manifest, split):
        loaded_splits.append(split)
        assert split_manifest == tmp_path / "split.json"
        return dev

    async def fake_run_live(args, datasets):
        dispatched["args"] = args
        dispatched["datasets"] = datasets
        return 0

    monkeypatch.setattr(cupid_cli, "load_cupid", fake_load)
    monkeypatch.setattr(cupid_cli, "run_live", fake_run_live)

    assert cupid_cli.main([
        "run",
        "--cohort",
        "dev",
        "--persona-limit",
        "1",
        "--data",
        str(tmp_path / "test.parquet"),
        "--split-manifest",
        str(tmp_path / "split.json"),
        "--output",
        str(tmp_path / "out"),
    ]) == 0

    assert loaded_splits == ["dev"]
    assert tuple(dispatched["datasets"]) == ("dev",)
    assert len({
        instance.persona_id
        for instance in dispatched["datasets"]["dev"].instances
    }) == 1
