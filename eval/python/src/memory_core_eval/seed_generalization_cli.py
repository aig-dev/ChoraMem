"""Prepare, smoke-test, run, and summarize Seed generalization v1."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from memory_core import memory_pb2 as pb

from .anchor_cli import (
    RPC_ATTEMPTS,
    assert_isolated_database,
    protected_seed_snapshot,
    settle_memory_state,
)
from .delayed_seed_cli import (
    ANSWER_OUTPUT_TOKENS,
    JUDGE_OUTPUT_TOKENS,
    _normalize_runtime,
    _required_environment_values,
    _smoke_runtime,
    runtime_from_environment,
)
from .delayed_seed_effect import MEMORY_TOKENS
from .delayed_seed_runner import delayed_instance_request_sha256, freeze_result
from .personamem_effect_runner import CachedTextModel, freeze_manifest
from .personamem_runner import cl100k_token_count
from .seed_generalization_data import (
    PROTOCOL,
    GeneralizationDataset,
    load_seed_generalization,
)
from .seed_generalization_runner import (
    run_generalization_case,
    summarize_generalization,
)


def build_run_manifest(
    *,
    dataset: GeneralizationDataset,
    data_path: Path,
    repository_root: Path,
    evaluation_ref: str,
    runtime: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(dataset, GeneralizationDataset) or len(dataset.instances) != 8:
        raise ValueError("generalization run requires the frozen eight-instance cohort")
    source = Path(data_path).resolve()
    if not source.is_file() or _file_sha256(source) != dataset.source_sha256:
        raise ValueError("generalization source data drift")
    repository = Path(repository_root).resolve()
    instances: dict[str, dict[str, object]] = {}
    for instance in dataset.instances:
        expectation = dataset.expectations[instance.instance_ref]
        instances[instance.instance_ref] = {
            "pattern_ref": instance.pattern_ref,
            "expected_formation": expectation.expected_formation,
            "request_sha256": delayed_instance_request_sha256(instance),
        }
    return {
        "benchmark": dataset.benchmark,
        "protocol": PROTOCOL,
        "evaluation_ref": _nonblank(evaluation_ref, "evaluation_ref"),
        "counts": {
            "positive": 4,
            "negative": 4,
            "instances": 8,
            "history_sessions_per_instance": 8,
        },
        "data": {"path": str(source), "sha256": dataset.source_sha256},
        "instances": instances,
        "repository_root": str(repository),
        "seed_snapshot": protected_seed_snapshot(repository),
        "harness_sources": _harness_source_hashes(),
        "runtime": _normalize_runtime(runtime),
        "episode_evidence_max_bytes": 0,
        "label_policy": "four positive answers complete before scorer labels; negative cases generate no answers",
    }


async def execute_generalization(
    *,
    manifest: Mapping[str, object],
    dataset: GeneralizationDataset,
    data_path: Path,
    repository_root: Path,
    output_dir: Path,
    process_instance: Callable[..., Awaitable[Mapping[str, object]]],
) -> dict[str, object]:
    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    freeze_manifest(output / "manifest.json", manifest)
    for instance in dataset.instances:
        path = _result_path(output, instance.instance_ref, manifest)
        if path.exists():
            _load_result(
                path,
                instance_ref=instance.instance_ref,
                expected_formation=dataset.expectations[
                    instance.instance_ref
                ].expected_formation,
            )
            continue
        result = await process_instance(
            instance,
            dataset.expectations[instance.instance_ref],
            dataset.counterfactuals.get(instance.instance_ref),
            dataset.labels.get(instance.instance_ref),
        )
        freeze_result(path, result)
    summary = verified_summary(
        output,
        manifest=manifest,
        dataset=dataset,
        data_path=data_path,
        repository_root=repository_root,
    )
    freeze_manifest(output / "summary.json", summary)
    return summary


def verified_summary(
    output_dir: Path,
    *,
    manifest: Mapping[str, object],
    dataset: GeneralizationDataset,
    data_path: Path,
    repository_root: Path,
) -> dict[str, object]:
    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    frozen = _read_mapping(output / "manifest.json", "run manifest")
    normalized = json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    if frozen != normalized:
        raise ValueError("generalization run manifest drift")
    _verify_manifest_inputs(
        manifest,
        dataset=dataset,
        data_path=data_path,
        repository_root=repository_root,
    )
    expected = {
        _result_path(output, instance.instance_ref, manifest).name: instance
        for instance in dataset.instances
    }
    directory = output / "instances"
    discovered = (
        {path.name: path for path in directory.glob("*.json") if path.is_file()}
        if directory.is_dir()
        else {}
    )
    if set(discovered) != set(expected):
        raise ValueError("generalization result file set is incomplete or unknown")

    positives: list[dict[str, object]] = []
    negatives: list[dict[str, object]] = []
    for filename, instance in expected.items():
        expected_formation = dataset.expectations[
            instance.instance_ref
        ].expected_formation
        result = _load_result(
            discovered[filename],
            instance_ref=instance.instance_ref,
            expected_formation=expected_formation,
        )
        (positives if expected_formation else negatives).append(result)
    summary = summarize_generalization(
        positive_results=positives,
        negative_results=negatives,
        dataset=dataset,
    )
    if dataset.benchmark == "Memory Core ANCHOR companion-disposition v1":
        from .anchor_companion_effect import summarize_anchor_companion

        summary = summary | {
            "anchor_companion": summarize_anchor_companion(summary)
        }
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return summary | {
        "records": {
            "answers": sum(len(result.get("answers", ())) for result in positives),
            "judges": sum(len(result.get("pairwise", ())) for result in positives),
            "negative_states": len(negatives),
        },
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


async def run_smoke(
    *,
    dataset: GeneralizationDataset,
    data_path: Path,
    repository_root: Path,
    evaluation_ref: str,
    runtime: Mapping[str, object],
    output_dir: Path,
) -> dict[str, object]:
    manifest = build_run_manifest(
        dataset=dataset,
        data_path=data_path,
        repository_root=repository_root,
        evaluation_ref=evaluation_ref,
        runtime=runtime,
    )
    answer_model = _SmokeAnswerModel(dataset)
    judge_model = _SmokeJudgeModel()

    async def process(instance, expectation, counterfactual, label):
        memory = _SmokeMemory(include_seed=expectation.expected_formation)

        async def settle(
            _scope: pb.MemoryScope, _expected: int
        ) -> Mapping[str, object]:
            return {
                "dispositions": (
                    [{"ref": "seed_smoke@1", "status": "active"}]
                    if expectation.expected_formation
                    else []
                ),
                "recollections": [
                    {"ref": "recollection_smoke@1", "status": "active"}
                ],
            }

        return await run_generalization_case(
            instance=instance,
            expectation=expectation,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            token_count=_word_count,
            memory_tokens=int(runtime["memory_tokens"]),
            counterfactual=counterfactual,
            label=label,
            answer_model=answer_model,
            judge_model=judge_model,
            max_output_tokens=int(runtime["answer_output_tokens"]),
        )

    summary = await execute_generalization(
        manifest=manifest,
        dataset=dataset,
        data_path=data_path,
        repository_root=repository_root,
        output_dir=output_dir,
        process_instance=process,
    )
    if answer_model.calls != 16 or judge_model.calls != 32:
        raise RuntimeError("generalization smoke did not execute all 48 completions")
    return summary


async def run_live(
    *,
    dataset: GeneralizationDataset,
    data_path: Path,
    repository_root: Path,
    output_dir: Path,
    evaluation_ref: str,
    environment: Mapping[str, str],
    settle_timeout: float,
    rpc_timeout: float,
    model_timeout: float,
) -> dict[str, object]:
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
    )
    manifest = build_run_manifest(
        dataset=dataset,
        data_path=data_path,
        repository_root=repository_root,
        evaluation_ref=evaluation_ref,
        runtime=runtime,
    )
    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    probe = DatabaseProbe(required["MEMORY_EVAL_DATABASE_URL"])
    if (output / "manifest.json").exists():
        freeze_manifest(output / "manifest.json", manifest)
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

    async def process(instance, expectation, counterfactual, label):
        return await run_generalization_case(
            instance=instance,
            expectation=expectation,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            counterfactual=counterfactual,
            label=label,
            answer_model=model,
            judge_model=model,
            max_output_tokens=ANSWER_OUTPUT_TOKENS,
        )

    try:
        return await execute_generalization(
            manifest=manifest,
            dataset=dataset,
            data_path=data_path,
            repository_root=repository_root,
            output_dir=output,
            process_instance=process,
        )
    finally:
        await raw_memory.close()
        await raw_model.close()


class _SmokeMemory:
    def __init__(self, *, include_seed: bool) -> None:
        self._include_seed = include_seed

    async def observe_source_event(self, request: Any) -> pb.SourceEventReceipt:
        return pb.SourceEventReceipt(
            source_event_ref=request.source_event.source_ref,
            episode_ref=(
                "episode:" + request.episode_binding.run_ref
                if request.HasField("episode_binding")
                else ""
            ),
        )

    async def report_outcome(self, request: Any) -> pb.OutcomeReceipt:
        return pb.OutcomeReceipt(
            outcome_event_ref=request.source_ref,
            episode_ref="episode:" + request.run_ref,
        )

    async def select_memory(self, request: Any) -> pb.MemoryContext:
        dispositions = []
        if self._include_seed:
            dispositions.append(
                pb.Disposition(
                    memory_ref="seed_smoke@1",
                    text="SMOKE_LEARNED_DISPOSITION",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            )
        return pb.MemoryContext(
            context_ref="context:" + request.run_ref,
            run_ref=request.run_ref,
            scope=request.scope,
            recollections=[
                pb.Recollection(
                    memory_ref="recollection_smoke@1",
                    text="SMOKE_RECOLLECTION",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
            dispositions=dispositions,
        )


class _SmokeAnswerModel:
    def __init__(self, dataset: GeneralizationDataset) -> None:
        self.calls = 0
        self._oracle = tuple(
            value.oracle_disposition for value in dataset.counterfactuals.values()
        )
        self._anti = tuple(
            value.anti_disposition for value in dataset.counterfactuals.values()
        )

    async def complete(
        self, *, instructions: str, input_text: str, max_output_tokens: int
    ) -> str:
        del input_text, max_output_tokens
        self.calls += 1
        if "SMOKE_LEARNED_DISPOSITION" in instructions:
            return "SMOKE_LEARNED"
        if any(value in instructions for value in self._oracle):
            return "SMOKE_ORACLE"
        if any(value in instructions for value in self._anti):
            return "SMOKE_ANTI"
        return "SMOKE_RAG"


class _SmokeJudgeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, *, instructions: str, input_text: str, max_output_tokens: int
    ) -> str:
        del instructions, max_output_tokens
        self.calls += 1
        response_a = input_text.split("RESPONSE_A\n", 1)[1].split(
            "\n\nRESPONSE_B\n", 1
        )[0]
        response_b = input_text.split("\n\nRESPONSE_B\n", 1)[1].split(
            "\n\nTARGET_BEHAVIOR\n", 1
        )[0]
        rank = {
            "SMOKE_ORACLE": 3,
            "SMOKE_LEARNED": 2,
            "SMOKE_RAG": 1,
            "SMOKE_ANTI": 0,
        }
        if rank[response_a] == rank[response_b]:
            return "TIE"
        return "A" if rank[response_a] > rank[response_b] else "B"


def _verify_manifest_inputs(
    manifest: Mapping[str, object],
    *,
    dataset: GeneralizationDataset,
    data_path: Path,
    repository_root: Path,
) -> None:
    source = Path(data_path).resolve()
    repository = Path(repository_root).resolve()
    data = manifest.get("data")
    if (
        manifest.get("protocol") != PROTOCOL
        or not isinstance(data, Mapping)
        or data.get("path") != str(source)
        or data.get("sha256") != dataset.source_sha256
        or _file_sha256(source) != dataset.source_sha256
    ):
        raise ValueError("generalization source data drift")
    if (
        manifest.get("repository_root") != str(repository)
        or protected_seed_snapshot(repository) != manifest.get("seed_snapshot")
        or _harness_source_hashes() != manifest.get("harness_sources")
    ):
        raise ValueError("generalization protected source drift")
    expected = {
        instance.instance_ref: {
            "pattern_ref": instance.pattern_ref,
            "expected_formation": dataset.expectations[
                instance.instance_ref
            ].expected_formation,
            "request_sha256": delayed_instance_request_sha256(instance),
        }
        for instance in dataset.instances
    }
    if manifest.get("instances") != expected:
        raise ValueError("generalization instance registry drift")


def _harness_source_hashes() -> dict[str, str]:
    directory = Path(__file__).resolve().parent
    names = (
        "anchor_companion_data.py",
        "anchor_companion_effect.py",
        "delayed_seed_effect.py",
        "delayed_seed_runner.py",
        "seed_generalization_data.py",
        "seed_generalization_runner.py",
        "seed_generalization_cli.py",
    )
    return {name: _file_sha256(directory / name) for name in names}


def _result_path(
    output: Path, instance_ref: str, manifest: Mapping[str, object]
) -> Path:
    registry = manifest.get("instances")
    if not isinstance(registry, Mapping):
        raise ValueError("generalization manifest instance registry is missing")
    record = registry.get(instance_ref)
    if not isinstance(record, Mapping):
        raise ValueError("generalization instance is not registered")
    digest = record.get("request_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("generalization instance request hash is invalid")
    return output / "instances" / f"{digest}.json"


def _load_result(
    path: Path, *, instance_ref: str, expected_formation: bool
) -> dict[str, object]:
    value = _read_mapping(path, "instance result")
    protocol = "seed-essential-delayed-v1" if expected_formation else PROTOCOL
    if value.get("protocol") != protocol or value.get("instance_ref") != instance_ref:
        raise ValueError("generalization result identity drift on resume")
    return value


def _read_mapping(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"missing generalization {label}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"generalization {label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"generalization {label} must be an object")
    return value


def _reject_partial_files(output: Path) -> None:
    if output.exists() and any(path.is_file() for path in output.rglob("*.part")):
        raise ValueError("generalization output contains a partial artifact")


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"frozen file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _word_count(text: str) -> int:
    return len(text.split())


def load_generalization_dataset(path: Path) -> GeneralizationDataset:
    """Route the frozen base cohort or its ANCHOR behavior-only overlay."""

    source = Path(path).resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("generalization source data is invalid JSON") from error
    if not isinstance(raw, Mapping):
        raise ValueError("generalization source data must be an object")
    if raw.get("protocol") == "anchor-companion-v1":
        from .anchor_companion_data import load_anchor_companion

        return load_anchor_companion(source)
    return load_seed_generalization(source)


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def main(argv: Sequence[str] | None = None) -> int:
    repository_default = Path(__file__).resolve().parents[4]
    data_default = repository_default / "eval" / "seed-generalization-v1.json"
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "smoke", "run", "summarize"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--data", type=Path, default=data_default)
        subparser.add_argument(
            "--repository-root", type=Path, default=repository_default
        )
        if command in {"smoke", "run", "summarize"}:
            subparser.add_argument("--output", type=Path, required=True)
        if command in {"smoke", "run"}:
            subparser.add_argument(
                "--run-ref", default=f"seed-generalization-v1-{command}"
            )
        if command == "run":
            subparser.add_argument("--settle-timeout", type=float, default=900.0)
            subparser.add_argument("--rpc-timeout", type=float, default=30.0)
            subparser.add_argument("--model-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    dataset = load_generalization_dataset(args.data)
    if args.command == "prepare":
        value = {
            "protocol": PROTOCOL,
            "source_sha256": dataset.source_sha256,
            "positive": len(dataset.positive_refs),
            "negative": len(dataset.negative_refs),
            "history_sessions_per_instance": 8,
        }
    elif args.command == "smoke":
        value = asyncio.run(
            run_smoke(
                dataset=dataset,
                data_path=args.data,
                repository_root=args.repository_root,
                evaluation_ref=args.run_ref,
                runtime=_smoke_runtime(),
                output_dir=args.output,
            )
        )
    elif args.command == "run":
        value = asyncio.run(
            run_live(
                dataset=dataset,
                data_path=args.data,
                repository_root=args.repository_root,
                output_dir=args.output,
                evaluation_ref=args.run_ref,
                environment=os.environ,
                settle_timeout=args.settle_timeout,
                rpc_timeout=args.rpc_timeout,
                model_timeout=args.model_timeout,
            )
        )
    else:
        manifest = _read_mapping(args.output / "manifest.json", "run manifest")
        value = verified_summary(
            args.output,
            manifest=manifest,
            dataset=dataset,
            data_path=args.data,
            repository_root=args.repository_root,
        )
        freeze_manifest(args.output / "summary.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
