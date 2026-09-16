from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from memory_core_eval.anchor_data import (
    AnchorDataset,
    AnchorMessage,
    AnchorSession,
    BehaviorInstance,
    BehaviorLabel,
    TrajectoryInstance,
    TrajectoryLabel,
)
from memory_core_eval.anchor_runner import probe_request_sha256


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _dataset(source_file: Path) -> AnchorDataset:
    history = (
        AnchorSession(
            session_id=0,
            messages=(
                AnchorMessage(0, 0, "user", "I need a steady response."),
                AnchorMessage(0, 0, "assistant", "I will stay steady."),
            ),
        ),
    )
    trajectory_instances = tuple(
        TrajectoryInstance(
            instance_ref=f"trajectory-{index}",
            bank_id=f"bank-{index % 3}",
            persona_text=f"PERSONA_ID p-{index % 3}\nROLE companion",
            history_sessions=history,
            current_request=f"trajectory request {index}",
            options=("first", "second", "third", "fourth"),
        )
        for index in range(15)
    )
    trajectory_labels = {
        item.instance_ref: TrajectoryLabel(
            instance_ref=item.instance_ref,
            family="P-PROT",
            correct_index=0,
        )
        for item in trajectory_instances
    }
    dimensions = (
        "persona_continuity",
        "persona_continuity",
        "relationship_adaptation",
        "relationship_adaptation",
        "relationship_repair",
        "relationship_repair",
    )
    behavior_instances = tuple(
        BehaviorInstance(
            instance_ref=f"behavior-{index}",
            bank_id=f"bank-{index % 3}",
            persona_text=f"PERSONA_ID p-{index % 3}\nROLE companion",
            history_sessions=history,
            session_id=1,
            turn=0,
            current_request=f"behavior request {index}",
        )
        for index in range(6)
    )
    behavior_labels = {
        item.instance_ref: BehaviorLabel(
            instance_ref=item.instance_ref,
            dimension=dimension,
            evidence_text="The user asked the companion to remain steady.",
            rubric={
                "score_2": "The response remains steady.",
                "score_1": "The response is partly steady.",
                "score_0": "The response is not steady.",
            },
        )
        for item, dimension in zip(behavior_instances, dimensions, strict=True)
    }
    return AnchorDataset(
        trajectory_instances=trajectory_instances,
        trajectory_labels=trajectory_labels,
        behavior_instances=behavior_instances,
        behavior_labels=behavior_labels,
        source_summary={
            "repository": "SalesforceAIResearch/AnchorBench",
            "revision": "41bd0e20b9524ce484db301ac15dc14121bf06ad",
            "artifact_version": "0.1.0",
            "license": "CC-BY-NC-4.0",
            "total_banks": 3,
            "total_items": 15,
            "bank_items": {"bank-0": 5, "bank-1": 5, "bank-2": 5},
            "files": {
                source_file.name: hashlib.sha256(source_file.read_bytes()).hexdigest()
            },
        },
    )


@pytest.fixture
def manifest_inputs(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_file = source_root / "source.txt"
    source_file.write_text("frozen source", encoding="utf-8")
    behavior_manifest = tmp_path / "anchor-behavior.json"
    behavior_manifest.write_text(
        json.dumps({"schema_version": 1, "checkpoints": list(range(6))}),
        encoding="utf-8",
    )
    repository_root = tmp_path / "repository"
    for name in ("internal", "worker", "api", "gen"):
        directory = repository_root / name
        directory.mkdir(parents=True)
        (directory / "frozen.txt").write_text(name, encoding="utf-8")
    runtime = {
        "answer_model": "MiniMax-M2.5",
        "answer_model_revision": "MiniMax-M2.5@api.minimaxi.com-2026-09-08",
        "answer_endpoint": "https://api.minimaxi.com/v1",
        "worker_model": "MiniMax-M2.5",
        "worker_output_tokens": 8_192,
        "core_revision": "core-rev",
        "worker_revision": "worker-rev",
        "index_revision": (
            "chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:"
            "chunk220-overlap32:episode-alpha-v1:"
            "exact-all-scoped-semantic-tiebreak-v1"
        ),
        "core_timing_profile": (
            "quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2"
        ),
        "memory_tokens": 2_048,
        "trajectory_output_tokens": 1_024,
        "behavior_output_tokens": 1_024,
        "judge_output_tokens": 1_024,
        "settle_timeout_seconds": 900.0,
        "rpc_timeout_seconds": 30.0,
        "model_timeout_seconds": 300.0,
        "rpc_attempts": 5,
        "probe_workers": 1,
        "worker_semantic_cache": True,
    }
    return {
        "dataset": _dataset(source_file),
        "source_root": source_root,
        "behavior_manifest": behavior_manifest,
        "repository_root": repository_root,
        "evaluation_ref": "anchor-v0-test",
        "runtime": runtime,
    }


def test_run_manifest_freezes_source_prompts_seed_paths_and_six_checkpoints(
    manifest_inputs,
):
    from memory_core_eval.anchor_cli import build_run_manifest

    manifest = build_run_manifest(**manifest_inputs)

    assert manifest["protocol"] == "anchor-v0-causal-dev-v1"
    assert manifest["counts"] == {"trajectory": 15, "behavior": 6}
    assert set(manifest["seed_snapshot"]) == {"internal", "worker", "api", "gen"}
    assert set(manifest["prompt_sha256"]) == {
        "trajectory_answer",
        "behavior_answer",
        "behavior_judge",
    }
    assert len(manifest["checkpoints"]["sha256"]) == 64
    assert len(manifest["persona_sha256"]) == 3
    assert manifest["runtime"]["memory_tokens"] == 2_048
    assert manifest["runtime"]["trajectory_output_tokens"] == 1_024
    assert manifest["runtime"]["judge_output_tokens"] == 1_024
    assert manifest["runtime"]["worker_output_tokens"] == 8_192
    assert manifest["runtime"]["worker_semantic_cache"] is True
    assert manifest["runtime"]["core_timing_profile"] == (
        "quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2"
    )
    assert "local_memory_index.py" in manifest["harness_sources"]
    assert set(manifest["instances"]["trajectory"]) == {
        f"trajectory-{index}" for index in range(15)
    }


def test_run_manifest_rejects_model_or_source_drift(manifest_inputs):
    from memory_core_eval.anchor_cli import build_run_manifest

    bad_runtime = dict(manifest_inputs["runtime"])
    bad_runtime["answer_model"] = "floating-model"
    with pytest.raises(ValueError, match="MiniMax-M2.5"):
        build_run_manifest(**(manifest_inputs | {"runtime": bad_runtime}))

    (manifest_inputs["source_root"] / "source.txt").write_text(
        "mutated", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="source.*drift"):
        build_run_manifest(**manifest_inputs)


def test_seed_snapshot_changes_when_a_protected_path_changes(manifest_inputs):
    from memory_core_eval.anchor_cli import protected_seed_snapshot

    root = manifest_inputs["repository_root"]
    before = protected_seed_snapshot(root)
    (root / "worker" / "frozen.txt").write_text("changed", encoding="utf-8")
    after = protected_seed_snapshot(root)

    assert before["worker"] != after["worker"]
    assert before["internal"] == after["internal"]


def test_empty_database_gate_uses_one_read_only_query():
    from memory_core_eval.anchor_cli import assert_isolated_database

    calls = []

    class Probe:
        def read(self, sql, params):
            calls.append((sql, params))
            return [{"source_events": 0, "episodes": 0, "consolidation_jobs": 0}]

    assert_isolated_database(Probe())
    assert len(calls) == 1
    assert calls[0][0].lstrip().startswith("SELECT")

    class DirtyProbe:
        def read(self, sql, params):
            return [{"source_events": 1, "episodes": 0, "consolidation_jobs": 0}]

    with pytest.raises(ValueError, match="not empty"):
        assert_isolated_database(DirtyProbe())


def test_install_source_command_installs_validates_and_prints(
    monkeypatch, tmp_path, capsys
):
    import memory_core_eval.anchor_cli as anchor_cli

    calls = []

    def install(path):
        calls.append(("install", path))
        path.mkdir(parents=True)
        return path

    def validate(path):
        calls.append(("validate", path))
        return {"revision": "frozen", "total_items": 15}

    monkeypatch.setattr(anchor_cli, "install_anchor_source", install)
    monkeypatch.setattr(anchor_cli, "validate_anchor_source", validate)

    output = tmp_path / "source"
    assert anchor_cli.main(["install-source", "--output", str(output)]) == 0
    assert calls == [("install", output), ("validate", output)]
    assert json.loads(capsys.readouterr().out)["total_items"] == 15


def test_live_runtime_is_fixed_and_contains_no_api_secret():
    from memory_core_eval.anchor_cli import runtime_from_environment

    environment = {
        "MEMORY_EVAL_MODEL": "MiniMax-M2.5",
        "MEMORY_EVAL_MODEL_REVISION": (
            "MiniMax-M2.5@api.minimaxi.com-2026-09-08"
        ),
        "MEMORY_WORKER_OPENAI_MODEL": "MiniMax-M2.5",
        "MEMORY_EVAL_WORKER_OUTPUT_TOKENS": "8192",
        "OPENAI_BASE_URL": "https://api.minimaxi.com/v1",
        "MEMORY_EVAL_CORE_REVISION": "core-rev",
        "MEMORY_EVAL_WORKER_REVISION": "worker-rev",
        "MEMORY_EVAL_INDEX_REVISION": (
            "chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:"
            "chunk220-overlap32:episode-alpha-v1:"
            "exact-all-scoped-semantic-tiebreak-v1"
        ),
        "MEMORY_EVAL_CORE_TIMING_PROFILE": (
            "quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2"
        ),
        "OPENAI_API_KEY": "must-not-enter-manifest",
    }
    runtime = runtime_from_environment(
        environment,
        settle_timeout=900,
        rpc_timeout=30,
        model_timeout=300,
        probe_workers=1,
    )

    assert runtime["answer_model_revision"].endswith("2026-09-08")
    assert runtime["worker_output_tokens"] == 8_192
    assert runtime["core_timing_profile"].startswith("quiet5s-")
    assert "must-not-enter-manifest" not in json.dumps(runtime)

    environment["MEMORY_EVAL_MODEL"] = "floating-model"
    with pytest.raises(ValueError, match="MiniMax-M2.5"):
        runtime_from_environment(
            environment,
            settle_timeout=900,
            rpc_timeout=30,
            model_timeout=300,
            probe_workers=1,
        )

    environment["MEMORY_EVAL_MODEL"] = "MiniMax-M2.5"
    environment["MEMORY_EVAL_WORKER_OUTPUT_TOKENS"] = "1024"
    with pytest.raises(ValueError, match="worker_output_tokens=8192"):
        runtime_from_environment(
            environment,
            settle_timeout=900,
            rpc_timeout=30,
            model_timeout=300,
            probe_workers=1,
        )


@pytest.mark.asyncio
async def test_wait_for_index_projection_requires_zero_before_consolidation():
    from memory_core_eval.anchor_cli import wait_for_index_projection

    class Probe:
        def __init__(self):
            self.values = iter((2, 1, 0))
            self.calls = 0

        def pending_index_operations(self, scope):
            assert scope is sentinel
            self.calls += 1
            return next(self.values)

    sentinel = object()
    probe = Probe()

    await wait_for_index_projection(
        probe,
        sentinel,
        timeout=1,
        interval=0.001,
    )

    assert probe.calls == 3


@pytest.mark.asyncio
async def test_wait_for_index_projection_fails_closed_on_timeout():
    from memory_core_eval.anchor_cli import (
        AnchorStageError,
        wait_for_index_projection,
    )

    class Probe:
        def pending_index_operations(self, scope):
            del scope
            return 1

    with pytest.raises(AnchorStageError, match="did not settle"):
        await wait_for_index_projection(
            Probe(),
            object(),
            timeout=0.001,
            interval=0.001,
        )


@pytest.mark.asyncio
async def test_settle_memory_state_reuses_one_timeout_for_both_waits(monkeypatch):
    import memory_core_eval.anchor_cli as anchor_cli

    calls = []
    sentinel = object()

    class Probe:
        def pending_index_operations(self, scope):
            assert scope is sentinel
            return 0

        def snapshot(self, scope):
            assert scope is sentinel
            return {"episodes": [{"ref": "episode-1"}]}

        def memory_state(self, scope):
            assert scope is sentinel
            return {"state": "settled"}

    async def wait_for_projection(probe, scope, *, timeout):
        assert isinstance(probe, Probe)
        assert scope is sentinel
        calls.append(("projection", timeout))

    async def wait_for_ready(snapshot, *, expected_episodes, timeout):
        assert snapshot() == {
            "episodes": [{"ref": "episode-1"}],
            "pending_index_operations": 0,
        }
        assert expected_episodes == 1
        calls.append(("ready", timeout))

    monkeypatch.setattr(anchor_cli, "wait_for_index_projection", wait_for_projection)
    monkeypatch.setattr(anchor_cli, "wait_for_effect_ready", wait_for_ready)

    result = await anchor_cli.settle_memory_state(
        Probe(),
        sentinel,
        expected_episodes=1,
        timeout=37.0,
    )

    assert result == {"state": "settled"}
    assert calls == [("projection", 37.0), ("ready", 37.0)]


@pytest.mark.asyncio
async def test_smoke_runs_complete_15_plus_6_cohort_without_api_key(
    manifest_inputs, tmp_path, monkeypatch
):
    from memory_core_eval.anchor_cli import run_smoke

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    summary = await run_smoke(
        **manifest_inputs,
        output_dir=tmp_path / "smoke",
    )

    assert summary["counts"] == {"trajectory": 15, "behavior": 6}
    assert summary["records"] == {"answers": 63, "judges": 18}
    assert summary["decision"] == {
        "status": "holdout_ready",
        "bottleneck": None,
    }


@pytest.mark.asyncio
async def test_execute_dev_freezes_judge_contract_failure_as_only_bottleneck(
    manifest_inputs, tmp_path
):
    from memory_core_eval.anchor_cli import (
        AnchorStageError,
        build_run_manifest,
        execute_dev,
    )

    dataset = manifest_inputs["dataset"]
    manifest = build_run_manifest(**manifest_inputs)

    async def lifecycle():
        return {
            "protocol": "seed-companion-lifecycle-v1",
            "decision": {"passed": True, "reasons": []},
        }

    async def process_trajectory(instance):
        return _valid_result(instance, "trajectory")

    async def process_behavior(instance):
        if instance.instance_ref == "behavior-0":
            raise AnchorStageError("judge_contract", "judge returned prose")
        return _valid_result(instance, "behavior")

    summary = await execute_dev(
        manifest=manifest,
        dataset=dataset,
        output_dir=tmp_path / "run",
        process_trajectory=process_trajectory,
        process_behavior=process_behavior,
        lifecycle=lifecycle,
    )

    assert summary["decision"] == {
        "status": "bottleneck",
        "bottleneck": "judge_contract",
    }
    failed = json.loads(
        next((tmp_path / "run" / "behavior").glob("*.json")).read_text()
    )
    failures = [
        json.loads(path.read_text())
        for path in (tmp_path / "run" / "behavior").glob("*.json")
        if "error_stage" in json.loads(path.read_text())
    ]
    assert failed["protocol"] == "anchor-v0-causal-dev-v1"
    assert failures == [
        {
            "error": "judge returned prose",
            "error_stage": "judge_contract",
            "error_type": "AnchorStageError",
            "instance_ref": "behavior-0",
            "kind": "behavior",
            "protocol": "anchor-v0-causal-dev-v1",
            "request_sha256": probe_request_sha256(
                dataset.behavior_instances[0]
            ),
        }
    ]


@pytest.mark.asyncio
async def test_resume_reuses_exact_results_and_rejects_partial_artifacts(
    manifest_inputs, tmp_path
):
    from memory_core_eval.anchor_cli import build_run_manifest, execute_dev

    dataset = manifest_inputs["dataset"]
    manifest = build_run_manifest(**manifest_inputs)
    calls = {"trajectory": 0, "behavior": 0, "lifecycle": 0}

    async def lifecycle():
        calls["lifecycle"] += 1
        return {
            "protocol": "seed-companion-lifecycle-v1",
            "decision": {"passed": True, "reasons": []},
        }

    async def process_trajectory(instance):
        calls["trajectory"] += 1
        return _valid_result(instance, "trajectory")

    async def process_behavior(instance):
        calls["behavior"] += 1
        return _valid_result(instance, "behavior")

    output = tmp_path / "resume"
    await execute_dev(
        manifest=manifest,
        dataset=dataset,
        output_dir=output,
        process_trajectory=process_trajectory,
        process_behavior=process_behavior,
        lifecycle=lifecycle,
    )
    await execute_dev(
        manifest=manifest,
        dataset=dataset,
        output_dir=output,
        process_trajectory=process_trajectory,
        process_behavior=process_behavior,
        lifecycle=lifecycle,
    )
    assert calls == {"trajectory": 15, "behavior": 6, "lifecycle": 1}

    (output / "trajectory" / "orphan.json.part").write_text(
        "partial", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="partial"):
        await execute_dev(
            manifest=manifest,
            dataset=dataset,
            output_dir=output,
            process_trajectory=process_trajectory,
            process_behavior=process_behavior,
            lifecycle=lifecycle,
        )


def _valid_result(instance, kind: str) -> dict[str, object]:
    def arm(mode: str, *, output: str, score: int | None = None):
        value: dict[str, object] = {
            "mode": mode,
            "output": output,
            "error": "",
            "memory_refs": [] if mode == "none" else ["recollection_test@1"],
            "selected_recollection_refs": ["recollection_test@1"],
            "selected_disposition_refs": ["seed_test@1"],
            "rendered_recollection_refs": (
                [] if mode == "none" else ["recollection_test@1"]
            ),
            "rendered_disposition_refs": (
                ["seed_test@1"] if mode == "seed_enabled" else []
            ),
            "memory_tokens": 0 if mode == "none" else 4,
            "answer_request_sha256": _sha(f"answer:{instance.instance_ref}:{mode}"),
        }
        if score is not None:
            value.update(
                {
                    "judge_output": str(score),
                    "score": score,
                    "judge_request_sha256": _sha(
                        f"judge:{instance.instance_ref}:{mode}"
                    ),
                }
            )
        return value

    if kind == "trajectory":
        answers = [
            arm("none", output="B"),
            arm("recollection_only", output="B"),
            arm("seed_enabled", output="A"),
        ]
    else:
        answers = [
            arm("none", output="not steady", score=0),
            arm("recollection_only", output="partly steady", score=1),
            arm("seed_enabled", output="steady", score=2),
        ]
    return {
        "protocol": "anchor-v0-causal-dev-v1",
        "kind": kind,
        "instance_ref": instance.instance_ref,
        "bank_id": instance.bank_id,
        "request_sha256": probe_request_sha256(instance),
        "answers": answers,
        "diagnostic": {
            "active_dispositions": 1,
            "selected_disposition_refs": ["seed_test@1"],
            "rendered_disposition_refs": ["seed_test@1"],
        },
    }
