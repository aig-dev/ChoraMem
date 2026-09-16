from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_core_eval.seed_generalization_data import load_seed_generalization


DATA = Path(__file__).resolve().parents[2] / "seed-generalization-v1.json"
ANCHOR_COMPANION_DATA = (
    Path(__file__).resolve().parents[2] / "anchor-companion-v1.json"
)


@pytest.fixture
def generalization_inputs(tmp_path: Path) -> dict[str, object]:
    repository = tmp_path / "repository"
    for name in ("internal", "worker", "api", "gen"):
        directory = repository / name
        directory.mkdir(parents=True)
        (directory / "frozen.txt").write_text(name, encoding="utf-8")
    return {
        "dataset": load_seed_generalization(DATA),
        "data_path": DATA,
        "repository_root": repository,
        "evaluation_ref": "seed-generalization-test",
        "runtime": {
            "answer_model": "MiniMax-M2.5",
            "answer_model_revision": "MiniMax-M2.5@api.minimaxi.com-2026-09-08",
            "answer_endpoint": "https://api.minimaxi.com/v1",
            "worker_model": "MiniMax-M2.5",
            "worker_output_tokens": 8_192,
            "core_revision": "core-rev",
            "worker_revision": "worker-rev",
            "index_revision": "index-rev",
            "core_timing_profile": "quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2",
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


def test_manifest_freezes_eight_cases_without_credentials(
    generalization_inputs: dict[str, object],
) -> None:
    from memory_core_eval.seed_generalization_cli import build_run_manifest

    manifest = build_run_manifest(**generalization_inputs)

    assert manifest["protocol"] == "seed-generalization-v1"
    assert manifest["counts"] == {
        "positive": 4,
        "negative": 4,
        "instances": 8,
        "history_sessions_per_instance": 8,
    }
    assert len(manifest["instances"]) == 8
    assert {
        item["expected_formation"] for item in manifest["instances"].values()
    } == {True, False}
    assert manifest["data"]["sha256"] == generalization_inputs[
        "dataset"
    ].source_sha256
    assert "OPENAI_API_KEY" not in json.dumps(manifest)
    assert "must-not-enter-manifest" not in json.dumps(manifest)


def test_cli_routes_anchor_companion_overlay_and_names_the_experiment(
    generalization_inputs: dict[str, object],
) -> None:
    from memory_core_eval.seed_generalization_cli import (
        build_run_manifest,
        load_generalization_dataset,
    )

    dataset = load_generalization_dataset(ANCHOR_COMPANION_DATA)
    manifest = build_run_manifest(
        **(
            generalization_inputs
            | {"dataset": dataset, "data_path": ANCHOR_COMPANION_DATA}
        )
    )

    assert manifest["benchmark"] == "Memory Core ANCHOR companion-disposition v1"
    assert manifest["data"]["path"] == str(ANCHOR_COMPANION_DATA.resolve())
    assert manifest["data"]["sha256"] == dataset.source_sha256


@pytest.mark.asyncio
async def test_anchor_companion_smoke_adds_strict_causal_conclusion(
    generalization_inputs: dict[str, object], tmp_path: Path
) -> None:
    from memory_core_eval.seed_generalization_cli import (
        load_generalization_dataset,
        run_smoke,
    )

    dataset = load_generalization_dataset(ANCHOR_COMPANION_DATA)
    summary = await run_smoke(
        **(
            generalization_inputs
            | {"dataset": dataset, "data_path": ANCHOR_COMPANION_DATA}
        ),
        output_dir=tmp_path / "anchor-companion-smoke",
    )

    assert summary["anchor_companion"]["decision"] == {
        "status": "preliminary_learned_companion_advantage",
        "bottleneck": None,
        "architecture_verdict": False,
    }


@pytest.mark.asyncio
async def test_smoke_executes_only_four_positive_answer_and_judge_sets(
    generalization_inputs: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from memory_core_eval.seed_generalization_cli import run_smoke

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-manifest")
    summary = await run_smoke(
        **generalization_inputs,
        output_dir=tmp_path / "smoke",
    )

    assert summary["records"] == {
        "answers": 16,
        "judges": 32,
        "negative_states": 4,
    }
    assert summary["decision"]["status"] == "preliminary_generalization"
    assert summary["negative_false_positives"] == 0
    assert summary["positive_recollection_cases"] == 4
    assert "must-not-enter-manifest" not in (
        tmp_path / "smoke" / "manifest.json"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_resume_requires_exact_complete_file_set(
    generalization_inputs: dict[str, object], tmp_path: Path
) -> None:
    from memory_core_eval.seed_generalization_cli import run_smoke, verified_summary

    output = tmp_path / "resume"
    await run_smoke(**generalization_inputs, output_dir=output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    victim = next((output / "instances").glob("*.json"))
    victim.unlink()

    with pytest.raises(ValueError, match="incomplete or unknown"):
        verified_summary(
            output,
            manifest=manifest,
            dataset=generalization_inputs["dataset"],
            data_path=DATA,
            repository_root=generalization_inputs["repository_root"],
        )
