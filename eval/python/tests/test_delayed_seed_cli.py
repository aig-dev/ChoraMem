from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_core_eval.delayed_seed_data import load_delayed_seed


DATA_PATH = Path(__file__).parents[2] / "seed-essential-delayed-v1.json"


@pytest.fixture
def delayed_inputs(tmp_path):
    repository = tmp_path / "repository"
    for name in ("internal", "worker", "api", "gen"):
        directory = repository / name
        directory.mkdir(parents=True)
        (directory / "frozen.txt").write_text(name, encoding="utf-8")
    return {
        "dataset": load_delayed_seed(DATA_PATH),
        "data_path": DATA_PATH,
        "repository_root": repository,
        "evaluation_ref": "seed-delayed-test",
        "runtime": {
            "answer_model": "MiniMax-M2.5",
            "answer_model_revision": (
                "MiniMax-M2.5@api.minimaxi.com-2026-09-08"
            ),
            "answer_endpoint": "https://api.minimaxi.com/v1",
            "worker_model": "MiniMax-M2.5",
            "worker_output_tokens": 8_192,
            "core_revision": "core-rev",
            "worker_revision": "worker-rev",
            "index_revision": "index-rev",
            "core_timing_profile": (
                "quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2"
            ),
            "memory_tokens": 2_048,
            "answer_output_tokens": 1_024,
            "judge_output_tokens": 1_024,
            "settle_timeout_seconds": 900.0,
            "rpc_timeout_seconds": 30.0,
            "model_timeout_seconds": 300.0,
            "rpc_attempts": 5,
            "worker_semantic_cache": True,
        },
    }


def test_manifest_freezes_data_prompts_protected_paths_and_all_instances(
    delayed_inputs,
):
    from memory_core_eval.delayed_seed_cli import build_run_manifest

    manifest = build_run_manifest(**delayed_inputs)

    assert manifest["protocol"] == "seed-essential-delayed-v1"
    assert manifest["counts"] == {"patterns": 4, "instances": 8}
    assert manifest["data"]["sha256"] == delayed_inputs["dataset"].source_sha256
    assert set(manifest["seed_snapshot"]) == {"internal", "worker", "api", "gen"}
    assert set(manifest["prompt_sha256"]) == {"answer", "pairwise_judge"}
    assert set(manifest["instances"]) == {
        instance.instance_ref for instance in delayed_inputs["dataset"].instances
    }
    assert manifest["modes"] == [
        "rag",
        "learned_seed",
        "oracle_seed",
        "anti_seed",
    ]
    assert manifest["comparisons_per_instance"] == 8
    assert "OPENAI_API_KEY" not in json.dumps(manifest)


def test_live_environment_runtime_can_be_passed_directly_to_manifest(delayed_inputs):
    """Regression: derived runtime fields must not fail a second normalization."""
    from memory_core_eval.delayed_seed_cli import (
        build_run_manifest,
        runtime_from_environment,
    )
    from memory_core_eval.local_memory_index import PROVIDER_REVISION_REF

    environment = {
        "MEMORY_EVAL_MODEL": "MiniMax-M2.5",
        "MEMORY_EVAL_MODEL_REVISION": (
            "MiniMax-M2.5@api.minimaxi.com-2026-09-08"
        ),
        "OPENAI_BASE_URL": "https://api.minimaxi.com/v1",
        "MEMORY_WORKER_OPENAI_MODEL": "MiniMax-M2.5",
        "MEMORY_EVAL_WORKER_OUTPUT_TOKENS": "8192",
        "MEMORY_EVAL_CORE_REVISION": "core-rev",
        "MEMORY_EVAL_WORKER_REVISION": "worker-rev",
        "MEMORY_EVAL_INDEX_REVISION": PROVIDER_REVISION_REF,
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
    )

    manifest = build_run_manifest(**(delayed_inputs | {"runtime": runtime}))

    assert manifest["runtime"]["reasoning_split"] is True
    assert "must-not-enter-manifest" not in json.dumps(manifest)


@pytest.mark.asyncio
async def test_smoke_runs_32_answers_and_64_position_swapped_judges(
    delayed_inputs, tmp_path, monkeypatch
):
    from memory_core_eval.delayed_seed_cli import run_smoke

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    summary = await run_smoke(
        **delayed_inputs,
        output_dir=tmp_path / "smoke",
    )

    assert summary["instances"] == 8
    assert summary["records"] == {"answers": 32, "judges": 64}
    assert summary["decision"]["status"] == "preliminary_learned_advantage"
    assert summary["comparisons"]["oracle_seed_vs_anti_seed"] == {
        "oracle_seed": 8,
        "anti_seed": 0,
        "tie": 0,
        "inconsistent": 0,
    }


@pytest.mark.asyncio
async def test_execute_resume_reuses_exact_complete_results(
    delayed_inputs, tmp_path
):
    from memory_core_eval.delayed_seed_cli import build_run_manifest, execute_dev

    dataset = delayed_inputs["dataset"]
    manifest = build_run_manifest(**delayed_inputs)
    calls = 0

    async def process(instance, counterfactual, label):
        nonlocal calls
        calls += 1
        assert instance.instance_ref == counterfactual.instance_ref == label.instance_ref
        return _complete_result(instance.instance_ref, instance.pattern_ref)

    output = tmp_path / "resume"
    first = await execute_dev(
        manifest=manifest,
        dataset=dataset,
        data_path=DATA_PATH,
        output_dir=output,
        process_instance=process,
    )
    second = await execute_dev(
        manifest=manifest,
        dataset=dataset,
        data_path=DATA_PATH,
        output_dir=output,
        process_instance=process,
    )

    assert calls == 8
    assert first == second
    assert first["records"] == {"answers": 32, "judges": 64}

    (output / "instances" / "orphan.json.part").write_text(
        "partial", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="partial"):
        await execute_dev(
            manifest=manifest,
            dataset=dataset,
            data_path=DATA_PATH,
            output_dir=output,
            process_instance=process,
        )


def _complete_result(instance_ref: str, pattern_ref: str) -> dict[str, object]:
    modes = ("rag", "learned_seed", "oracle_seed", "anti_seed")
    answers = [
        {
            "mode": mode,
            "output": mode,
            "error": "",
            "memory_refs": [f"{mode}@1"],
            "selected_recollection_refs": ["rec@1"],
            "selected_disposition_refs": ["seed@1"],
            "rendered_recollection_refs": ["rec@1"],
            "rendered_disposition_refs": (
                [] if mode == "rag" else [f"{mode}@1"]
            ),
            "memory_tokens": 8,
            "memory_budget": 2_048,
            "answer_request_sha256": "a" * 64,
        }
        for mode in modes
    ]
    winners = {
        "oracle_seed_vs_rag": "oracle_seed",
        "learned_seed_vs_rag": "learned_seed",
        "oracle_seed_vs_learned_seed": "oracle_seed",
        "oracle_seed_vs_anti_seed": "oracle_seed",
    }
    pairs = []
    for comparison, winner in winners.items():
        left, _, right = comparison.partition("_vs_")
        for direction, a_mode, b_mode in (
            ("forward", left, right),
            ("reverse", right, left),
        ):
            pairs.append(
                {
                    "comparison": comparison,
                    "direction": direction,
                    "a_mode": a_mode,
                    "b_mode": b_mode,
                    "judge_output": "A" if a_mode == winner else "B",
                    "judge_request_sha256": "b" * 64,
                }
            )
    return {
        "protocol": "seed-essential-delayed-v1",
        "instance_ref": instance_ref,
        "pattern_ref": pattern_ref,
        "request_sha256": "c" * 64,
        "persona_text": "persona",
        "current_request": "request",
        "answers": answers,
        "diagnostic": {
            "expected_episodes": 3,
            "settled_active_dispositions": [0, 1, 1],
            "active_dispositions": 1,
            "selected_disposition_refs": ["seed@1"],
            "rendered_disposition_refs": ["learned_seed@1"],
        },
        "pairwise": pairs,
    }
