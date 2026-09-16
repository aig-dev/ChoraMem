"""Install, validate, smoke-test, run, and summarize ANCHOR v0."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from memory_core import memory_pb2 as pb

from .anchor_data import (
    AnchorDataset,
    BehaviorInstance,
    TrajectoryInstance,
    load_anchor,
)
from .anchor_effect import (
    PROTOCOL,
    build_behavior_judge_prompt,
    build_behavior_prompt,
    build_trajectory_prompt,
    summarize_anchor_v0,
)
from .anchor_runner import (
    freeze_result,
    generate_behavior_answers,
    generate_trajectory_answers,
    judge_behavior_answers,
    load_frozen_result,
    probe_request_sha256,
    score_trajectory_answers,
)
from .anchor_source import install_anchor_source, validate_anchor_source
from .local_memory_index import PROVIDER_REVISION_REF
from .perma_seed_effect import SeedEffectMode
from .personamem_effect_runner import (
    CachedTextModel,
    freeze_manifest,
    wait_for_effect_ready,
)
from .personamem_runner import cl100k_token_count
from .seed_lifecycle import run_lifecycle_suite


MODEL = "MiniMax-M2.5"
MODEL_REVISION = "MiniMax-M2.5@api.minimaxi.com-2026-09-08"
MODEL_ENDPOINT = "https://api.minimaxi.com/v1"
MEMORY_TOKENS = 2_048
WORKER_OUTPUT_TOKENS = 8_192
TRAJECTORY_OUTPUT_TOKENS = 1_024
BEHAVIOR_OUTPUT_TOKENS = 1_024
JUDGE_OUTPUT_TOKENS = 1_024
RPC_ATTEMPTS = 5
CORE_TIMING_PROFILE = (
    "quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2"
)
PRECONSOLIDATION_INDEX_POLL_SECONDS = 0.05

_PROTECTED_SEED_PATHS = ("internal", "worker", "api", "gen")
_IGNORED_TREE_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
_IGNORED_TREE_SUFFIXES = {".pyc", ".pyo", ".swp"}
_STAGE_ERRORS = {"data_isolation_or_harness", "judge_contract"}
_RUNTIME_KEYS = {
    "answer_model",
    "answer_model_revision",
    "answer_endpoint",
    "worker_model",
    "worker_output_tokens",
    "core_revision",
    "worker_revision",
    "index_revision",
    "core_timing_profile",
    "memory_tokens",
    "trajectory_output_tokens",
    "behavior_output_tokens",
    "judge_output_tokens",
    "settle_timeout_seconds",
    "rpc_timeout_seconds",
    "model_timeout_seconds",
    "rpc_attempts",
    "probe_workers",
    "worker_semantic_cache",
}


class AnchorStageError(RuntimeError):
    """A bounded runtime failure with one frozen ANCHOR bottleneck stage."""

    def __init__(self, stage: str, message: str) -> None:
        if stage not in _STAGE_ERRORS:
            raise ValueError("ANCHOR runtime error stage is not allowed")
        super().__init__(_nonblank(message, "ANCHOR stage error"))
        self.stage = stage


def protected_seed_snapshot(repository_root: Path) -> dict[str, str]:
    """Hash the four implementation paths that ANCHOR is forbidden to tune."""

    root = Path(repository_root).resolve()
    result: dict[str, str] = {}
    for name in _PROTECTED_SEED_PATHS:
        directory = root / name
        if not directory.is_dir():
            raise ValueError(f"protected Seed path is missing: {name}")
        records: list[tuple[str, str]] = []
        for path in sorted(directory.rglob("*")):
            if any(part in _IGNORED_TREE_PARTS for part in path.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"protected Seed path contains a symlink: {name}")
            if not path.is_file() or path.suffix in _IGNORED_TREE_SUFFIXES:
                continue
            records.append(
                (
                    path.relative_to(directory).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        encoded = json.dumps(
            records, ensure_ascii=False, separators=(",", ":")
        ).encode()
        result[name] = hashlib.sha256(encoded).hexdigest()
    return result


def assert_isolated_database(probe: Any) -> None:
    """Reject a live dev run unless all three canonical write tables are empty."""

    sql = """SELECT
        (SELECT COUNT(*) FROM source_events) AS source_events,
        (SELECT COUNT(*) FROM episodes) AS episodes,
        (SELECT COUNT(*) FROM consolidation_jobs) AS consolidation_jobs"""
    rows = probe.read(sql, ())
    if not isinstance(rows, Sequence) or len(rows) != 1:
        raise ValueError("ANCHOR database probe returned an invalid empty-state row")
    row = rows[0]
    if not isinstance(row, Mapping):
        raise ValueError("ANCHOR database probe returned an invalid empty-state row")
    counts: dict[str, int] = {}
    for name in ("source_events", "episodes", "consolidation_jobs"):
        value = row.get(name)
        if type(value) is not int or value < 0:
            raise ValueError("ANCHOR database probe returned an invalid count")
        counts[name] = value
    if any(counts.values()):
        raise ValueError(f"ANCHOR evaluation database is not empty: {counts}")


def build_run_manifest(
    *,
    dataset: AnchorDataset,
    source_root: Path,
    behavior_manifest: Path,
    repository_root: Path,
    evaluation_ref: str,
    runtime: Mapping[str, object],
) -> dict[str, object]:
    """Bind one run to every public input, prompt, runtime, and Seed byte."""

    if not isinstance(dataset, AnchorDataset):
        raise TypeError("dataset must be AnchorDataset")
    if len(dataset.trajectory_instances) != 15 or len(dataset.behavior_instances) != 6:
        raise ValueError("ANCHOR run requires the frozen 15+6 cohort")
    source = dict(dataset.source_summary)
    source_path = Path(source_root).resolve()
    _verify_source_files(source_path, source)
    checkpoint_path = Path(behavior_manifest).resolve()
    if not checkpoint_path.is_file():
        raise ValueError("ANCHOR behavior manifest is missing")
    repository = Path(repository_root).resolve()
    normalized_runtime = _normalize_runtime(runtime)
    instances = {
        "trajectory": _instance_registry(dataset.trajectory_instances),
        "behavior": _instance_registry(dataset.behavior_instances),
    }
    personas: dict[str, str] = {}
    for instance in (*dataset.trajectory_instances, *dataset.behavior_instances):
        digest = hashlib.sha256(instance.persona_text.encode()).hexdigest()
        previous = personas.setdefault(instance.bank_id, digest)
        if previous != digest:
            raise ValueError("ANCHOR bank has conflicting persona renderings")
    return {
        "benchmark": "ANCHOR public development set + Memory Core Behavior v0",
        "protocol": PROTOCOL,
        "evaluation_ref": _nonblank(evaluation_ref, "evaluation_ref"),
        "counts": {"trajectory": 15, "behavior": 6},
        "modes": [mode.value for mode in SeedEffectMode],
        "instances": instances,
        "source": source | {"root": str(source_path)},
        "checkpoints": {
            "path": str(checkpoint_path),
            "sha256": _file_sha256(checkpoint_path),
            "count": 6,
        },
        "repository_root": str(repository),
        "seed_snapshot": protected_seed_snapshot(repository),
        "persona_sha256": personas,
        "prompt_sha256": _prompt_hashes(),
        "harness_sources": _harness_source_hashes(),
        "runtime": normalized_runtime,
        "reference_harness": PROTOCOL,
        "episode_evidence_max_bytes": 0,
        "test_write_policy": (
            "unbound query SourceEvent only; no Delivery, AgentAct, or Outcome"
        ),
    }


async def execute_dev(
    *,
    manifest: Mapping[str, object],
    dataset: AnchorDataset,
    output_dir: Path,
    process_trajectory: Callable[
        [TrajectoryInstance], Awaitable[Mapping[str, object]]
    ],
    process_behavior: Callable[
        [BehaviorInstance], Awaitable[Mapping[str, object]]
    ],
    lifecycle: Callable[[], Awaitable[Mapping[str, object]]],
) -> dict[str, object]:
    """Run or exactly resume the bounded dev cohort and freeze every result."""

    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    freeze_manifest(output / "manifest.json", manifest)
    lifecycle_path = output / "lifecycle" / "summary.json"
    if lifecycle_path.is_file():
        lifecycle_result = _read_mapping(lifecycle_path, "lifecycle summary")
    else:
        try:
            lifecycle_result = dict(await lifecycle())
        except Exception as error:
            lifecycle_result = {
                "protocol": "seed-companion-lifecycle-v1",
                "decision": {
                    "passed": False,
                    "reasons": [f"{type(error).__name__}: {error}"],
                },
            }
        freeze_manifest(lifecycle_path, lifecycle_result)

    lifecycle_passed = _lifecycle_passed(lifecycle_result)
    for kind, instances, callback in (
        ("trajectory", dataset.trajectory_instances, process_trajectory),
        ("behavior", dataset.behavior_instances, process_behavior),
    ):
        for instance in instances:
            path = _result_path(output, kind, instance, manifest)
            existing = load_frozen_result(path, instance_ref=instance.instance_ref)
            if existing is not None:
                continue
            if not lifecycle_passed:
                result = _failure_result(
                    instance=instance,
                    kind=kind,
                    stage="data_isolation_or_harness",
                    error_type="LifecycleGateError",
                    message="Seed companion lifecycle gate did not pass",
                )
            else:
                try:
                    result = dict(await callback(instance))
                except Exception as error:
                    stage = (
                        error.stage
                        if isinstance(error, AnchorStageError)
                        else "data_isolation_or_harness"
                    )
                    result = _failure_result(
                        instance=instance,
                        kind=kind,
                        stage=stage,
                        error_type=type(error).__name__,
                        message=str(error) or type(error).__name__,
                    )
            freeze_result(path, result)

    summary = verified_summary(output, manifest=manifest, dataset=dataset)
    freeze_manifest(output / "summary.json", summary)
    return summary


def verified_summary(
    output_dir: Path,
    *,
    manifest: Mapping[str, object],
    dataset: AnchorDataset,
) -> dict[str, object]:
    """Revalidate frozen inputs and artifacts, then compute the pure decision."""

    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    frozen_manifest = _read_mapping(output / "manifest.json", "run manifest")
    if frozen_manifest != json.loads(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    ):
        raise ValueError("ANCHOR run manifest drift")
    _verify_manifest_inputs(manifest, dataset)

    results: dict[str, list[Mapping[str, object]]] = {
        "trajectory": [],
        "behavior": [],
    }
    for kind, instances in (
        ("trajectory", dataset.trajectory_instances),
        ("behavior", dataset.behavior_instances),
    ):
        directory = output / kind
        expected = {
            _result_path(output, kind, instance, manifest).name: instance
            for instance in instances
        }
        discovered = {
            path.name: path
            for path in directory.glob("*")
            if path.is_file()
        } if directory.is_dir() else {}
        if set(discovered) != set(expected):
            raise ValueError(f"ANCHOR {kind} result file set is incomplete or unknown")
        for filename, instance in expected.items():
            result = load_frozen_result(
                discovered[filename], instance_ref=instance.instance_ref
            )
            if result is None:
                raise ValueError(f"ANCHOR {kind} result is missing")
            results[kind].append(result)

    lifecycle_result = _read_mapping(
        output / "lifecycle" / "summary.json", "lifecycle summary"
    )
    summary = summarize_anchor_v0(
        trajectory_results=results["trajectory"],
        behavior_results=results["behavior"],
        trajectory_labels=dataset.trajectory_labels,
        behavior_labels=dataset.behavior_labels,
        lifecycle=lifecycle_result,
        manifest=manifest,
    )
    answer_records = sum(
        len(result.get("answers", ()))
        for result in (*results["trajectory"], *results["behavior"])
        if isinstance(result.get("answers"), list)
    )
    judge_records = sum(
        1
        for result in results["behavior"]
        for answer in result.get("answers", ())
        if isinstance(answer, Mapping) and "judge_request_sha256" in answer
    )
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return summary | {
        "records": {"answers": answer_records, "judges": judge_records},
        "lifecycle": lifecycle_result,
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


async def run_smoke(
    *,
    dataset: AnchorDataset,
    source_root: Path,
    behavior_manifest: Path,
    repository_root: Path,
    evaluation_ref: str,
    runtime: Mapping[str, object],
    output_dir: Path,
) -> dict[str, object]:
    """Exercise all 21 probes and 81 completions with deterministic fakes."""

    manifest = build_run_manifest(
        dataset=dataset,
        source_root=source_root,
        behavior_manifest=behavior_manifest,
        repository_root=repository_root,
        evaluation_ref=evaluation_ref,
        runtime=runtime,
    )
    memory = _SmokeMemory()
    answer_model = _SmokeAnswerModel()
    judge_model = _SmokeJudgeModel()

    async def settle(_scope: pb.MemoryScope, _expected: int) -> Mapping[str, object]:
        return {
            "dispositions": [
                {"ref": "seed_smoke@1", "text": "Remain steady.", "status": "active"}
            ]
        }

    async def lifecycle() -> Mapping[str, object]:
        return {
            "protocol": "seed-companion-lifecycle-v1",
            "decision": {"passed": True, "reasons": []},
        }

    async def process_trajectory(instance: TrajectoryInstance) -> Mapping[str, object]:
        generated = await generate_trajectory_answers(
            instance=instance,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=answer_model,
            token_count=_word_count,
            memory_tokens=int(runtime["memory_tokens"]),
            max_output_tokens=int(runtime["trajectory_output_tokens"]),
        )
        return score_trajectory_answers(
            generated, dataset.trajectory_labels[instance.instance_ref]
        )

    async def process_behavior(instance: BehaviorInstance) -> Mapping[str, object]:
        generated = await generate_behavior_answers(
            instance=instance,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=answer_model,
            token_count=_word_count,
            memory_tokens=int(runtime["memory_tokens"]),
            max_output_tokens=int(runtime["behavior_output_tokens"]),
        )
        return await judge_behavior_answers(
            generated,
            dataset.behavior_labels[instance.instance_ref],
            judge_model=judge_model,
            max_judge_tokens=int(runtime["judge_output_tokens"]),
        )

    summary = await execute_dev(
        manifest=manifest,
        dataset=dataset,
        output_dir=output_dir,
        process_trajectory=process_trajectory,
        process_behavior=process_behavior,
        lifecycle=lifecycle,
    )
    if answer_model.calls != 63 or judge_model.calls != 18:
        raise RuntimeError("ANCHOR smoke did not execute the frozen 81 completions")
    return summary


class _SmokeMemory:
    async def observe_source_event(self, request: Any) -> pb.SourceEventReceipt:
        episode_ref = ""
        if request.HasField("episode_binding"):
            episode_ref = "episode:" + request.episode_binding.run_ref
        return pb.SourceEventReceipt(
            source_event_ref=request.source_event.source_ref,
            episode_ref=episode_ref,
        )

    async def report_outcome(self, request: Any) -> pb.OutcomeReceipt:
        return pb.OutcomeReceipt(
            outcome_event_ref=request.source_ref,
            episode_ref="episode:" + request.run_ref,
        )

    async def select_memory(self, request: Any) -> pb.MemoryContext:
        return pb.MemoryContext(
            context_ref="context:" + request.run_ref,
            run_ref=request.run_ref,
            scope=request.scope,
            recollections=[
                pb.Recollection(
                    memory_ref="recollection_smoke@1",
                    text="The user values a steady response.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
            dispositions=[
                pb.Disposition(
                    memory_ref="seed_smoke@1",
                    text="Remain steady when responding to this user.",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
        )


class _SmokeAnswerModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, *, instructions: str, input_text: str, max_output_tokens: int
    ) -> str:
        self.calls += 1
        has_seed = "\n\nDISPOSITIONS\n" in instructions
        has_memory = "\n\nLONG_TERM_MEMORY\n" in instructions
        if "Return exactly one token: A, B, C, or D." in input_text:
            return "A" if has_seed else "B"
        if has_seed:
            return "The companion remains steady and responsive."
        if has_memory:
            return "The companion is partly steady."
        return "The companion ignores the requested continuity."


class _SmokeJudgeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, *, instructions: str, input_text: str, max_output_tokens: int
    ) -> str:
        self.calls += 1
        if "remains steady and responsive" in input_text:
            return "2"
        if "partly steady" in input_text:
            return "1"
        return "0"


def _instance_registry(
    instances: Sequence[TrajectoryInstance] | Sequence[BehaviorInstance],
) -> dict[str, dict[str, str]]:
    registry: dict[str, dict[str, str]] = {}
    for instance in instances:
        if instance.instance_ref in registry:
            raise ValueError("ANCHOR instance identity is duplicated")
        registry[instance.instance_ref] = {
            "bank_id": instance.bank_id,
            "request_sha256": probe_request_sha256(instance),
        }
    return registry


def _normalize_runtime(runtime: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(runtime, Mapping) or set(runtime) != _RUNTIME_KEYS:
        raise ValueError("ANCHOR runtime controls are incomplete or unknown")
    result = dict(runtime)
    if result["answer_model"] != MODEL or result["worker_model"] != MODEL:
        raise ValueError(f"ANCHOR v0 requires {MODEL} for Worker, Answer, and Judge")
    if result["answer_model_revision"] != MODEL_REVISION:
        raise ValueError(f"ANCHOR v0 requires model revision {MODEL_REVISION}")
    if str(result["answer_endpoint"]).rstrip("/") != MODEL_ENDPOINT:
        raise ValueError(f"ANCHOR v0 requires endpoint {MODEL_ENDPOINT}")
    result["answer_endpoint"] = MODEL_ENDPOINT
    if result["core_timing_profile"] != CORE_TIMING_PROFILE:
        raise ValueError(
            f"ANCHOR v0 requires core_timing_profile={CORE_TIMING_PROFILE}"
        )
    for name in (
        "memory_tokens",
        "worker_output_tokens",
        "trajectory_output_tokens",
        "behavior_output_tokens",
        "judge_output_tokens",
        "rpc_attempts",
        "probe_workers",
    ):
        _positive_int(result[name], name)
    if result["worker_semantic_cache"] is not True:
        raise ValueError("ANCHOR v0 requires the label-free Worker semantic cache")
    expected_budgets = {
        "memory_tokens": MEMORY_TOKENS,
        "worker_output_tokens": WORKER_OUTPUT_TOKENS,
        "trajectory_output_tokens": TRAJECTORY_OUTPUT_TOKENS,
        "behavior_output_tokens": BEHAVIOR_OUTPUT_TOKENS,
        "judge_output_tokens": JUDGE_OUTPUT_TOKENS,
        "rpc_attempts": RPC_ATTEMPTS,
    }
    for name, expected in expected_budgets.items():
        if result[name] != expected:
            raise ValueError(f"ANCHOR v0 requires {name}={expected}")
    for name in (
        "settle_timeout_seconds",
        "rpc_timeout_seconds",
        "model_timeout_seconds",
    ):
        result[name] = _positive_float(result[name], name)
    for name in ("core_revision", "worker_revision", "index_revision"):
        result[name] = _nonblank(result[name], name)
    if result["index_revision"] != PROVIDER_REVISION_REF:
        raise ValueError(
            f"ANCHOR v0 requires index_revision={PROVIDER_REVISION_REF}"
        )
    return result | {
        "worker_model_revision": MODEL_REVISION,
        "judge_model_revision": MODEL_REVISION,
        "temperature": 0,
        "reasoning_split": True,
        "model_max_retries": 0,
    }


def _prompt_hashes() -> dict[str, str]:
    prompts = {
        "trajectory_answer": build_trajectory_prompt(
            persona_text="PERSONA_ID prompt-hash",
            current_request="frozen request",
            options=("one", "two", "three", "four"),
            memory_text="DISPOSITIONS\nRELATION: frozen tendency",
        ),
        "behavior_answer": build_behavior_prompt(
            persona_text="PERSONA_ID prompt-hash",
            current_request="frozen request",
            memory_text="DISPOSITIONS\nRELATION: frozen tendency",
        ),
        "behavior_judge": build_behavior_judge_prompt(
            persona_text="PERSONA_ID prompt-hash",
            evidence_text="frozen evidence",
            current_request="frozen request",
            response="frozen response",
            rubric={
                "score_2": "held",
                "score_1": "partial",
                "score_0": "failed",
            },
        ),
    }
    return {
        name: hashlib.sha256(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        for name, value in prompts.items()
    }


def _harness_source_hashes() -> dict[str, str]:
    directory = Path(__file__).resolve().parent
    names = (
        "anchor_source.py",
        "anchor_data.py",
        "anchor_effect.py",
        "anchor_runner.py",
        "anchor_cli.py",
        "seed_lifecycle.py",
        "personamem_live.py",
        "local_memory_index.py",
    )
    return {name: _file_sha256(directory / name) for name in names}


def _verify_source_files(root: Path, summary: Mapping[str, object]) -> None:
    files = summary.get("files")
    if not isinstance(files, Mapping) or not files:
        raise ValueError("ANCHOR source summary has no frozen files")
    expected: dict[str, str] = {}
    for raw_path, raw_digest in files.items():
        if not isinstance(raw_path, str) or not _is_safe_relative(raw_path):
            raise ValueError("ANCHOR source summary has an unsafe path")
        expected[raw_path] = _sha256_digest(raw_digest, "ANCHOR source SHA-256")
    if not root.is_dir():
        raise ValueError("ANCHOR source root is missing")
    discovered = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if discovered != set(expected):
        raise ValueError("ANCHOR source file inventory drift")
    for relative, digest in expected.items():
        if _file_sha256(root / relative) != digest:
            raise ValueError(f"ANCHOR source content drift: {relative}")


def _verify_manifest_inputs(
    manifest: Mapping[str, object], dataset: AnchorDataset
) -> None:
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("ANCHOR manifest protocol drift")
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("ANCHOR manifest source is missing")
    source_root = source.get("root")
    if not isinstance(source_root, str):
        raise ValueError("ANCHOR manifest source root is invalid")
    _verify_source_files(Path(source_root), source)
    if {key: value for key, value in source.items() if key != "root"} != dict(
        dataset.source_summary
    ):
        raise ValueError("ANCHOR source summary drift")
    checkpoints = manifest.get("checkpoints")
    if not isinstance(checkpoints, Mapping):
        raise ValueError("ANCHOR checkpoint manifest is missing")
    checkpoint_path = checkpoints.get("path")
    if not isinstance(checkpoint_path, str) or _file_sha256(Path(checkpoint_path)) != (
        checkpoints.get("sha256")
    ):
        raise ValueError("ANCHOR behavior checkpoint drift")
    repository_root = manifest.get("repository_root")
    if not isinstance(repository_root, str):
        raise ValueError("ANCHOR repository root is invalid")
    if protected_seed_snapshot(Path(repository_root)) != manifest.get("seed_snapshot"):
        raise ValueError("ANCHOR protected Seed snapshot drift")
    if _prompt_hashes() != manifest.get("prompt_sha256"):
        raise ValueError("ANCHOR prompt contract drift")
    if _harness_source_hashes() != manifest.get("harness_sources"):
        raise ValueError("ANCHOR Harness source drift")
    expected_instances = {
        "trajectory": _instance_registry(dataset.trajectory_instances),
        "behavior": _instance_registry(dataset.behavior_instances),
    }
    if expected_instances != manifest.get("instances"):
        raise ValueError("ANCHOR instance registry drift")


def _result_path(
    output: Path,
    kind: str,
    instance: TrajectoryInstance | BehaviorInstance,
    manifest: Mapping[str, object],
) -> Path:
    registries = manifest.get("instances")
    if not isinstance(registries, Mapping):
        raise ValueError("ANCHOR manifest instance registry is missing")
    registry = registries.get(kind)
    if not isinstance(registry, Mapping):
        raise ValueError(f"ANCHOR manifest {kind} registry is missing")
    record = registry.get(instance.instance_ref)
    if not isinstance(record, Mapping):
        raise ValueError("ANCHOR instance is not registered")
    digest = _sha256_digest(record.get("request_sha256"), "request_sha256")
    return output / kind / f"{digest}.json"


def _failure_result(
    *,
    instance: TrajectoryInstance | BehaviorInstance,
    kind: str,
    stage: str,
    error_type: str,
    message: str,
) -> dict[str, object]:
    if stage not in _STAGE_ERRORS:
        raise ValueError("ANCHOR failure stage is invalid")
    return {
        "protocol": PROTOCOL,
        "kind": kind,
        "instance_ref": instance.instance_ref,
        "request_sha256": probe_request_sha256(instance),
        "error_stage": stage,
        "error_type": _nonblank(error_type, "error_type"),
        "error": _nonblank(message, "error"),
    }


def _lifecycle_passed(value: Mapping[str, object]) -> bool:
    decision = value.get("decision")
    return (
        value.get("protocol") == "seed-companion-lifecycle-v1"
        and isinstance(decision, Mapping)
        and decision.get("passed") is True
    )


def _reject_partial_files(output: Path) -> None:
    if output.exists() and any(path.is_file() for path in output.rglob("*.part")):
        raise ValueError("ANCHOR output contains a partial artifact")


def _read_mapping(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"missing ANCHOR {label}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"ANCHOR {label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"ANCHOR {label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"frozen file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def _is_safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and "\\" not in value
        and not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _word_count(text: str) -> int:
    return len(text.split())


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _positive_float(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


async def wait_for_index_projection(
    probe: Any,
    scope: pb.MemoryScope,
    *,
    timeout: float,
    interval: float = PRECONSOLIDATION_INDEX_POLL_SECONDS,
) -> None:
    """Require the owner projection to settle before the 5-second quiet gate."""

    timeout = _positive_float(timeout, "preconsolidation index timeout")
    interval = _positive_float(interval, "preconsolidation index poll interval")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while probe.pending_index_operations(scope):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AnchorStageError(
                "data_isolation_or_harness",
                "MemoryIndex projection did not settle before consolidation",
            )
        await asyncio.sleep(min(interval, remaining))


async def settle_memory_state(
    probe: Any,
    scope: pb.MemoryScope,
    *,
    expected_episodes: int,
    timeout: float,
) -> Mapping[str, object]:
    """Wait for one owner with the same bound for projection and completion."""

    timeout = _positive_float(timeout, "settle timeout")
    await wait_for_index_projection(probe, scope, timeout=timeout)
    await wait_for_effect_ready(
        lambda: probe.snapshot(scope)
        | {
            "pending_index_operations": probe.pending_index_operations(scope)
        },
        expected_episodes=expected_episodes,
        timeout=timeout,
    )
    return probe.memory_state(scope)


def runtime_from_environment(
    environment: Mapping[str, str],
    *,
    settle_timeout: float,
    rpc_timeout: float,
    model_timeout: float,
    probe_workers: int,
) -> dict[str, object]:
    """Build the public runtime controls without copying credentials."""

    base: dict[str, object] = {
        "answer_model": environment.get("MEMORY_EVAL_MODEL", ""),
        "answer_model_revision": environment.get(
            "MEMORY_EVAL_MODEL_REVISION", ""
        ),
        "answer_endpoint": environment.get("OPENAI_BASE_URL", ""),
        "worker_model": environment.get("MEMORY_WORKER_OPENAI_MODEL", ""),
        "worker_output_tokens": (
            int(environment["MEMORY_EVAL_WORKER_OUTPUT_TOKENS"])
            if environment.get("MEMORY_EVAL_WORKER_OUTPUT_TOKENS", "").isdigit()
            else environment.get("MEMORY_EVAL_WORKER_OUTPUT_TOKENS", "")
        ),
        "core_revision": environment.get("MEMORY_EVAL_CORE_REVISION", ""),
        "worker_revision": environment.get("MEMORY_EVAL_WORKER_REVISION", ""),
        "index_revision": environment.get("MEMORY_EVAL_INDEX_REVISION", ""),
        "core_timing_profile": environment.get(
            "MEMORY_EVAL_CORE_TIMING_PROFILE", ""
        ),
        "memory_tokens": MEMORY_TOKENS,
        "trajectory_output_tokens": TRAJECTORY_OUTPUT_TOKENS,
        "behavior_output_tokens": BEHAVIOR_OUTPUT_TOKENS,
        "judge_output_tokens": JUDGE_OUTPUT_TOKENS,
        "settle_timeout_seconds": settle_timeout,
        "rpc_timeout_seconds": rpc_timeout,
        "model_timeout_seconds": model_timeout,
        "rpc_attempts": RPC_ATTEMPTS,
        "probe_workers": probe_workers,
        "worker_semantic_cache": True,
    }
    normalized = _normalize_runtime(base)
    return {name: normalized[name] for name in _RUNTIME_KEYS}


async def run_live(
    *,
    dataset: AnchorDataset,
    source_root: Path,
    behavior_manifest: Path,
    repository_root: Path,
    output_dir: Path,
    evaluation_ref: str,
    environment: Mapping[str, str],
    settle_timeout: float,
    rpc_timeout: float,
    model_timeout: float,
    probe_workers: int,
) -> dict[str, object]:
    """Run the frozen live Harness against one isolated Memory Core database."""

    from memory_core import AsyncMemoryClient
    import tiktoken

    from .personamem_live import ChatTextModel, DatabaseProbe, RetryingMemoryClient

    required = _required_environment_values(
        environment,
        "MEMORY_EVAL_DATABASE_URL",
        "MEMORY_EVAL_MODEL",
        "MEMORY_EVAL_MODEL_REVISION",
        "MEMORY_WORKER_OPENAI_MODEL",
        "MEMORY_EVAL_WORKER_OUTPUT_TOKENS",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "MEMORY_CORE_ENDPOINT",
        "MEMORY_CORE_TOKEN",
        "MEMORY_EVAL_CORE_REVISION",
        "MEMORY_EVAL_WORKER_REVISION",
        "MEMORY_EVAL_INDEX_REVISION",
        "MEMORY_EVAL_CORE_TIMING_PROFILE",
    )
    runtime = runtime_from_environment(
        required,
        settle_timeout=settle_timeout,
        rpc_timeout=rpc_timeout,
        model_timeout=model_timeout,
        probe_workers=probe_workers,
    )
    manifest = build_run_manifest(
        dataset=dataset,
        source_root=source_root,
        behavior_manifest=behavior_manifest,
        repository_root=repository_root,
        evaluation_ref=evaluation_ref,
        runtime=runtime,
    )
    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    manifest_path = output / "manifest.json"
    probe = DatabaseProbe(required["MEMORY_EVAL_DATABASE_URL"])
    if manifest_path.exists():
        freeze_manifest(manifest_path, manifest)
    else:
        assert_isolated_database(probe)

    raw_model = ChatTextModel(
        model=required["MEMORY_EVAL_MODEL"],
        api_key=required["OPENAI_API_KEY"],
        base_url=required["OPENAI_BASE_URL"],
        usage_file=output / "model-usage.jsonl",
        reasoning_split=True,
        request_timeout=model_timeout,
        max_retries=0,
        completion_attempts=RPC_ATTEMPTS,
    )
    model = CachedTextModel(
        raw_model,
        output / "model-cache",
        model_revision=required["MEMORY_EVAL_MODEL_REVISION"],
    )
    raw_memory = AsyncMemoryClient.connect(
        required["MEMORY_CORE_ENDPOINT"],
        token_provider=lambda _: required["MEMORY_CORE_TOKEN"],
        default_timeout=rpc_timeout,
    )
    memory = RetryingMemoryClient(raw_memory, attempts=RPC_ATTEMPTS)
    encoding = tiktoken.get_encoding("cl100k_base")
    token_count = lambda text: cl100k_token_count(encoding, text)

    async def settle(
        scope: pb.MemoryScope, expected_episodes: int
    ) -> Mapping[str, object]:
        return await settle_memory_state(
            probe,
            scope,
            expected_episodes=expected_episodes,
            timeout=settle_timeout,
        )

    async def lifecycle() -> Mapping[str, object]:
        return await run_lifecycle_suite(
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            model=model,
            output_dir=output / "lifecycle",
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_output_tokens=TRAJECTORY_OUTPUT_TOKENS,
        )

    async def process_trajectory(instance: TrajectoryInstance) -> Mapping[str, object]:
        generated = await generate_trajectory_answers(
            instance=instance,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=model,
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_output_tokens=TRAJECTORY_OUTPUT_TOKENS,
        )
        return score_trajectory_answers(
            generated, dataset.trajectory_labels[instance.instance_ref]
        )

    async def process_behavior(instance: BehaviorInstance) -> Mapping[str, object]:
        generated = await generate_behavior_answers(
            instance=instance,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=model,
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_output_tokens=BEHAVIOR_OUTPUT_TOKENS,
        )
        try:
            return await judge_behavior_answers(
                generated,
                dataset.behavior_labels[instance.instance_ref],
                judge_model=model,
                max_judge_tokens=JUDGE_OUTPUT_TOKENS,
            )
        except ValueError as error:
            raise AnchorStageError("judge_contract", str(error)) from error

    try:
        return await execute_dev(
            manifest=manifest,
            dataset=dataset,
            output_dir=output,
            process_trajectory=process_trajectory,
            process_behavior=process_behavior,
            lifecycle=lifecycle,
        )
    finally:
        await raw_memory.close()
        await raw_model.close()


def main(argv: Sequence[str] | None = None) -> int:
    repository_default = Path(__file__).resolve().parents[4]
    behavior_default = repository_default / "eval" / "anchor-behavior-v0.json"
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install-source")
    install.add_argument("--output", type=Path, required=True)

    for command in ("prepare", "smoke", "run", "summarize"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--source", type=Path, default=repository_default / ".cache/anchor-source"
        )
        subparser.add_argument(
            "--behavior-manifest", type=Path, default=behavior_default
        )
        subparser.add_argument(
            "--repository-root", type=Path, default=repository_default
        )
        if command in {"smoke", "run", "summarize"}:
            subparser.add_argument("--output", type=Path, required=True)
        if command in {"smoke", "run"}:
            subparser.add_argument("--run-ref", default=f"anchor-v0-{command}")
        if command == "run":
            subparser.add_argument("--settle-timeout", type=float, default=900.0)
            subparser.add_argument("--rpc-timeout", type=float, default=30.0)
            subparser.add_argument("--model-timeout", type=float, default=300.0)
            subparser.add_argument("--probe-workers", type=int, default=1)

    args = parser.parse_args(argv)
    if args.command == "install-source":
        install_anchor_source(args.output)
        _print_json(validate_anchor_source(args.output))
        return 0

    dataset = load_anchor(args.source, behavior_manifest=args.behavior_manifest)
    if args.command == "prepare":
        _print_json(
            {
                "source": dict(dataset.source_summary),
                "counts": {
                    "trajectory": len(dataset.trajectory_instances),
                    "behavior": len(dataset.behavior_instances),
                },
                "behavior_dimensions": {
                    dimension: sum(
                        label.dimension == dimension
                        for label in dataset.behavior_labels.values()
                    )
                    for dimension in (
                        "persona_continuity",
                        "relationship_adaptation",
                        "relationship_repair",
                    )
                },
            }
        )
        return 0
    if args.command == "smoke":
        runtime = {
            "answer_model": MODEL,
            "answer_model_revision": MODEL_REVISION,
            "answer_endpoint": MODEL_ENDPOINT,
            "worker_model": MODEL,
            "worker_output_tokens": WORKER_OUTPUT_TOKENS,
            "core_revision": "smoke-core",
            "worker_revision": "smoke-worker",
            "index_revision": "smoke-index",
            "core_timing_profile": CORE_TIMING_PROFILE,
            "memory_tokens": MEMORY_TOKENS,
            "trajectory_output_tokens": TRAJECTORY_OUTPUT_TOKENS,
            "behavior_output_tokens": BEHAVIOR_OUTPUT_TOKENS,
            "judge_output_tokens": JUDGE_OUTPUT_TOKENS,
            "settle_timeout_seconds": 900.0,
            "rpc_timeout_seconds": 30.0,
            "model_timeout_seconds": 300.0,
            "rpc_attempts": RPC_ATTEMPTS,
            "probe_workers": 1,
            "worker_semantic_cache": True,
        }
        summary = asyncio.run(
            run_smoke(
                dataset=dataset,
                source_root=args.source,
                behavior_manifest=args.behavior_manifest,
                repository_root=args.repository_root,
                evaluation_ref=args.run_ref,
                runtime=runtime,
                output_dir=args.output,
            )
        )
    elif args.command == "summarize":
        manifest = _read_mapping(args.output / "manifest.json", "run manifest")
        summary = verified_summary(args.output, manifest=manifest, dataset=dataset)
        freeze_manifest(args.output / "summary.json", summary)
    else:
        summary = asyncio.run(
            run_live(
                dataset=dataset,
                source_root=args.source,
                behavior_manifest=args.behavior_manifest,
                repository_root=args.repository_root,
                output_dir=args.output,
                evaluation_ref=args.run_ref,
                environment=os.environ,
                settle_timeout=args.settle_timeout,
                rpc_timeout=args.rpc_timeout,
                model_timeout=args.model_timeout,
                probe_workers=args.probe_workers,
            )
        )
    _print_json(summary)
    decision = summary.get("decision")
    return (
        0
        if isinstance(decision, Mapping)
        and decision.get("status") == "holdout_ready"
        else 1
    )


def _required_environment_values(
    environment: Mapping[str, str], *names: str
) -> dict[str, str]:
    result = {
        name: str(environment.get(name, "")).strip()
        for name in names
    }
    missing = [name for name, value in result.items() if not value]
    if missing:
        raise ValueError("missing required environment: " + ", ".join(missing))
    return result


def _print_json(value: Mapping[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
