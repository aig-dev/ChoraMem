"""Prepare, smoke-test, run, and summarize Seed-essential delayed eval v1."""
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
    CORE_TIMING_PROFILE,
    MODEL,
    MODEL_ENDPOINT,
    MODEL_REVISION,
    RPC_ATTEMPTS,
    WORKER_OUTPUT_TOKENS,
    assert_isolated_database,
    protected_seed_snapshot,
    settle_memory_state,
)
from .delayed_seed_data import (
    DelayedSeedDataset,
    DelayedSeedInstance,
    PROTOCOL,
    load_delayed_seed,
)
from .delayed_seed_effect import (
    MEMORY_TOKENS,
    DelayedSeedMode,
    build_answer_prompt,
    build_pairwise_prompt,
    summarize_delayed_seed,
)
from .delayed_seed_runner import (
    delayed_instance_request_sha256,
    freeze_result,
    generate_delayed_answers,
    judge_delayed_answers,
    load_frozen_result,
)
from .local_memory_index import PROVIDER_REVISION_REF
from .personamem_effect_runner import CachedTextModel, freeze_manifest
from .personamem_runner import cl100k_token_count


ANSWER_OUTPUT_TOKENS = 1_024
JUDGE_OUTPUT_TOKENS = 1_024

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
    "answer_output_tokens",
    "judge_output_tokens",
    "settle_timeout_seconds",
    "rpc_timeout_seconds",
    "model_timeout_seconds",
    "rpc_attempts",
    "worker_semantic_cache",
}


def build_run_manifest(
    *,
    dataset: DelayedSeedDataset,
    data_path: Path,
    repository_root: Path,
    evaluation_ref: str,
    runtime: Mapping[str, object],
) -> dict[str, object]:
    """Freeze all empirical inputs while excluding credentials and labels from prompts."""

    if not isinstance(dataset, DelayedSeedDataset) or len(dataset.instances) != 8:
        raise ValueError("delayed Seed run requires the frozen eight-instance cohort")
    source = Path(data_path).resolve()
    if not source.is_file() or _file_sha256(source) != dataset.source_sha256:
        raise ValueError("delayed Seed source data drift")
    repository = Path(repository_root).resolve()
    normalized_runtime = _normalize_runtime(runtime)
    instances: dict[str, dict[str, str]] = {}
    for instance in dataset.instances:
        if instance.instance_ref in instances:
            raise ValueError("delayed Seed instance identity is duplicated")
        instances[instance.instance_ref] = {
            "pattern_ref": instance.pattern_ref,
            "request_sha256": delayed_instance_request_sha256(instance),
        }
    return {
        "benchmark": "Memory Core Seed-essential delayed synthetic dev v1",
        "protocol": PROTOCOL,
        "evaluation_ref": _nonblank(evaluation_ref, "evaluation_ref"),
        "counts": {"patterns": 4, "instances": 8},
        "modes": [mode.value for mode in DelayedSeedMode],
        "comparisons_per_instance": 8,
        "data": {"path": str(source), "sha256": dataset.source_sha256},
        "instances": instances,
        "repository_root": str(repository),
        "seed_snapshot": protected_seed_snapshot(repository),
        "prompt_sha256": _prompt_hashes(),
        "harness_sources": _harness_source_hashes(),
        "runtime": normalized_runtime,
        "episode_evidence_max_bytes": 0,
        "test_write_policy": (
            "unbound query SourceEvent plus one Select; no Delivery, AgentAct, or Outcome"
        ),
        "label_policy": "all four answers complete before scorer target is loaded",
    }


async def execute_dev(
    *,
    manifest: Mapping[str, object],
    dataset: DelayedSeedDataset,
    data_path: Path,
    output_dir: Path,
    process_instance: Callable[[Any, Any, Any], Awaitable[Mapping[str, object]]],
) -> dict[str, object]:
    """Run or exactly resume the fixed cohort, then compute one diagnosis."""

    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    freeze_manifest(output / "manifest.json", manifest)
    for instance in dataset.instances:
        path = _result_path(output, instance, manifest)
        existing = load_frozen_result(path, instance_ref=instance.instance_ref)
        if existing is not None:
            continue
        result = await process_instance(
            instance,
            dataset.counterfactuals[instance.instance_ref],
            dataset.labels[instance.instance_ref],
        )
        freeze_result(path, result)
    summary = verified_summary(
        output,
        manifest=manifest,
        dataset=dataset,
        data_path=data_path,
    )
    freeze_manifest(output / "summary.json", summary)
    return summary


def verified_summary(
    output_dir: Path,
    *,
    manifest: Mapping[str, object],
    dataset: DelayedSeedDataset,
    data_path: Path,
) -> dict[str, object]:
    """Revalidate frozen inputs and artifacts before applying the decision gate."""

    output = Path(output_dir).resolve()
    _reject_partial_files(output)
    frozen = _read_mapping(output / "manifest.json", "run manifest")
    normalized = json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    if frozen != normalized:
        raise ValueError("delayed Seed run manifest drift")
    _verify_manifest_inputs(manifest, dataset=dataset, data_path=data_path)

    expected = {
        _result_path(output, instance, manifest).name: instance
        for instance in dataset.instances
    }
    directory = output / "instances"
    discovered = (
        {path.name: path for path in directory.glob("*.json") if path.is_file()}
        if directory.is_dir()
        else {}
    )
    if set(discovered) != set(expected):
        raise ValueError("delayed Seed result file set is incomplete or unknown")
    results = []
    for filename, instance in expected.items():
        result = load_frozen_result(
            discovered[filename], instance_ref=instance.instance_ref
        )
        if result is None:
            raise ValueError("delayed Seed result is missing")
        results.append(result)
    summary = summarize_delayed_seed(results, labels=dataset.labels)
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return summary | {
        "records": {
            "answers": sum(len(result.get("answers", ())) for result in results),
            "judges": sum(len(result.get("pairwise", ())) for result in results),
        },
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


async def run_smoke(
    *,
    dataset: DelayedSeedDataset,
    data_path: Path,
    repository_root: Path,
    evaluation_ref: str,
    runtime: Mapping[str, object],
    output_dir: Path,
) -> dict[str, object]:
    """Exercise the complete 32-answer/64-judge protocol without services or keys."""

    manifest = build_run_manifest(
        dataset=dataset,
        data_path=data_path,
        repository_root=repository_root,
        evaluation_ref=evaluation_ref,
        runtime=runtime,
    )
    memory = _SmokeMemory()
    answer_model = _SmokeAnswerModel(dataset)
    judge_model = _SmokeJudgeModel()

    async def settle(_scope: pb.MemoryScope, _expected: int) -> Mapping[str, object]:
        return {
            "dispositions": [
                {"ref": "seed_smoke@1", "status": "active"}
            ]
        }

    async def process(instance, counterfactual, label):
        generated = await generate_delayed_answers(
            instance=instance,
            counterfactual=counterfactual,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=answer_model,
            token_count=_word_count,
            memory_tokens=int(runtime["memory_tokens"]),
            max_output_tokens=int(runtime["answer_output_tokens"]),
        )
        return await judge_delayed_answers(
            generated,
            label,
            judge_model=judge_model,
            max_judge_tokens=int(runtime["judge_output_tokens"]),
        )

    summary = await execute_dev(
        manifest=manifest,
        dataset=dataset,
        data_path=data_path,
        output_dir=output_dir,
        process_instance=process,
    )
    if answer_model.calls != 32 or judge_model.calls != 64:
        raise RuntimeError("delayed Seed smoke did not execute all 96 completions")
    return summary


async def run_live(
    *,
    dataset: DelayedSeedDataset,
    data_path: Path,
    repository_root: Path,
    output_dir: Path,
    evaluation_ref: str,
    environment: Mapping[str, str],
    settle_timeout: float,
    rpc_timeout: float,
    model_timeout: float,
) -> dict[str, object]:
    """Run the frozen protocol against one empty, isolated Memory Core database."""

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

    async def process(instance, counterfactual, label):
        generated = await generate_delayed_answers(
            instance=instance,
            counterfactual=counterfactual,
            evaluation_ref=evaluation_ref,
            memory=memory,
            settle=settle,
            answer_model=model,
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_output_tokens=ANSWER_OUTPUT_TOKENS,
        )
        return await judge_delayed_answers(
            generated,
            label,
            judge_model=model,
            max_judge_tokens=JUDGE_OUTPUT_TOKENS,
        )

    try:
        return await execute_dev(
            manifest=manifest,
            dataset=dataset,
            data_path=data_path,
            output_dir=output,
            process_instance=process,
        )
    finally:
        await raw_memory.close()
        await raw_model.close()


def runtime_from_environment(
    environment: Mapping[str, str],
    *,
    settle_timeout: float,
    rpc_timeout: float,
    model_timeout: float,
) -> dict[str, object]:
    """Build public controls without copying any API or RPC credentials."""

    worker_tokens = environment.get("MEMORY_EVAL_WORKER_OUTPUT_TOKENS", "")
    value: dict[str, object] = {
        "answer_model": environment.get("MEMORY_EVAL_MODEL", ""),
        "answer_model_revision": environment.get(
            "MEMORY_EVAL_MODEL_REVISION", ""
        ),
        "answer_endpoint": environment.get("OPENAI_BASE_URL", ""),
        "worker_model": environment.get("MEMORY_WORKER_OPENAI_MODEL", ""),
        "worker_output_tokens": int(worker_tokens) if worker_tokens.isdigit() else "",
        "core_revision": environment.get("MEMORY_EVAL_CORE_REVISION", ""),
        "worker_revision": environment.get("MEMORY_EVAL_WORKER_REVISION", ""),
        "index_revision": environment.get("MEMORY_EVAL_INDEX_REVISION", ""),
        "core_timing_profile": environment.get(
            "MEMORY_EVAL_CORE_TIMING_PROFILE", ""
        ),
        "memory_tokens": MEMORY_TOKENS,
        "answer_output_tokens": ANSWER_OUTPUT_TOKENS,
        "judge_output_tokens": JUDGE_OUTPUT_TOKENS,
        "settle_timeout_seconds": settle_timeout,
        "rpc_timeout_seconds": rpc_timeout,
        "model_timeout_seconds": model_timeout,
        "rpc_attempts": RPC_ATTEMPTS,
        "worker_semantic_cache": True,
    }
    normalized = _normalize_runtime(value)
    if normalized["index_revision"] != PROVIDER_REVISION_REF:
        raise ValueError(
            f"delayed Seed live run requires index_revision={PROVIDER_REVISION_REF}"
        )
    return {name: normalized[name] for name in _RUNTIME_KEYS}


class _SmokeMemory:
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
            dispositions=[
                pb.Disposition(
                    memory_ref="seed_smoke@1",
                    text="SMOKE_LEARNED_DISPOSITION",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )
            ],
        )


class _SmokeAnswerModel:
    def __init__(self, dataset: DelayedSeedDataset) -> None:
        self.calls = 0
        self.oracle = tuple(
            item.oracle_disposition for item in dataset.counterfactuals.values()
        )
        self.anti = tuple(
            item.anti_disposition for item in dataset.counterfactuals.values()
        )

    async def complete(
        self, *, instructions: str, input_text: str, max_output_tokens: int
    ) -> str:
        del input_text, max_output_tokens
        self.calls += 1
        if "SMOKE_LEARNED_DISPOSITION" in instructions:
            return "SMOKE_LEARNED"
        if any(text in instructions for text in self.oracle):
            return "SMOKE_ORACLE"
        if any(text in instructions for text in self.anti):
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


def _normalize_runtime(runtime: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(runtime, Mapping) or set(runtime) != _RUNTIME_KEYS:
        raise ValueError("delayed Seed runtime controls are incomplete or unknown")
    result = dict(runtime)
    if result["answer_model"] != MODEL or result["worker_model"] != MODEL:
        raise ValueError(f"delayed Seed v1 requires {MODEL}")
    if result["answer_model_revision"] != MODEL_REVISION:
        raise ValueError(f"delayed Seed v1 requires model revision {MODEL_REVISION}")
    if str(result["answer_endpoint"]).rstrip("/") != MODEL_ENDPOINT:
        raise ValueError(f"delayed Seed v1 requires endpoint {MODEL_ENDPOINT}")
    result["answer_endpoint"] = MODEL_ENDPOINT
    if result["core_timing_profile"] != CORE_TIMING_PROFILE:
        raise ValueError(
            f"delayed Seed v1 requires core_timing_profile={CORE_TIMING_PROFILE}"
        )
    expected = {
        "memory_tokens": MEMORY_TOKENS,
        "worker_output_tokens": WORKER_OUTPUT_TOKENS,
        "answer_output_tokens": ANSWER_OUTPUT_TOKENS,
        "judge_output_tokens": JUDGE_OUTPUT_TOKENS,
        "rpc_attempts": RPC_ATTEMPTS,
    }
    for name, required in expected.items():
        if result[name] != required:
            raise ValueError(f"delayed Seed v1 requires {name}={required}")
    for name in ("settle_timeout_seconds", "rpc_timeout_seconds", "model_timeout_seconds"):
        result[name] = _positive_float(result[name], name)
    for name in ("core_revision", "worker_revision", "index_revision"):
        result[name] = _nonblank(result[name], name)
    if result["worker_semantic_cache"] is not True:
        raise ValueError("delayed Seed v1 requires Worker semantic cache")
    return result | {
        "worker_model_revision": MODEL_REVISION,
        "judge_model_revision": MODEL_REVISION,
        "temperature": 0,
        "reasoning_split": True,
        "model_max_retries": 0,
    }


def _prompt_hashes() -> dict[str, str]:
    prompts = {
        "answer": build_answer_prompt(
            persona_text="frozen persona",
            current_request="frozen request",
            memory_text="DISPOSITIONS\nRELATION: frozen tendency",
        ),
        "pairwise_judge": build_pairwise_prompt(
            persona_text="frozen persona",
            current_request="frozen request",
            target_behavior="frozen target",
            response_a="frozen A",
            response_b="frozen B",
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
        "delayed_seed_data.py",
        "delayed_seed_effect.py",
        "delayed_seed_runner.py",
        "delayed_seed_cli.py",
        "personamem_live.py",
        "local_memory_index.py",
    )
    return {name: _file_sha256(directory / name) for name in names}


def _verify_manifest_inputs(
    manifest: Mapping[str, object],
    *,
    dataset: DelayedSeedDataset,
    data_path: Path,
) -> None:
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("delayed Seed manifest protocol drift")
    data = manifest.get("data")
    source = Path(data_path).resolve()
    if (
        not isinstance(data, Mapping)
        or data.get("path") != str(source)
        or data.get("sha256") != dataset.source_sha256
        or _file_sha256(source) != dataset.source_sha256
    ):
        raise ValueError("delayed Seed source data drift")
    repository = manifest.get("repository_root")
    if not isinstance(repository, str) or protected_seed_snapshot(
        Path(repository)
    ) != manifest.get("seed_snapshot"):
        raise ValueError("delayed Seed protected Seed snapshot drift")
    if _prompt_hashes() != manifest.get("prompt_sha256"):
        raise ValueError("delayed Seed prompt contract drift")
    if _harness_source_hashes() != manifest.get("harness_sources"):
        raise ValueError("delayed Seed Harness source drift")
    expected = {
        instance.instance_ref: {
            "pattern_ref": instance.pattern_ref,
            "request_sha256": delayed_instance_request_sha256(instance),
        }
        for instance in dataset.instances
    }
    if expected != manifest.get("instances"):
        raise ValueError("delayed Seed instance registry drift")


def _result_path(
    output: Path,
    instance: DelayedSeedInstance,
    manifest: Mapping[str, object],
) -> Path:
    registry = manifest.get("instances")
    if not isinstance(registry, Mapping):
        raise ValueError("delayed Seed manifest instance registry is missing")
    record = registry.get(instance.instance_ref)
    if not isinstance(record, Mapping):
        raise ValueError("delayed Seed instance is not registered")
    digest = record.get("request_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("delayed Seed instance request hash is invalid")
    return output / "instances" / f"{digest}.json"


def _reject_partial_files(output: Path) -> None:
    if output.exists() and any(path.is_file() for path in output.rglob("*.part")):
        raise ValueError("delayed Seed output contains a partial artifact")


def _read_mapping(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"missing delayed Seed {label}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"delayed Seed {label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"delayed Seed {label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"frozen file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _word_count(text: str) -> int:
    return len(text.split())


def _required_environment_values(
    environment: Mapping[str, str], *names: str
) -> dict[str, str]:
    values = {name: str(environment.get(name, "")).strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("missing required environment: " + ", ".join(missing))
    return values


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def _positive_float(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


def _smoke_runtime() -> dict[str, object]:
    return {
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
        "answer_output_tokens": ANSWER_OUTPUT_TOKENS,
        "judge_output_tokens": JUDGE_OUTPUT_TOKENS,
        "settle_timeout_seconds": 900.0,
        "rpc_timeout_seconds": 30.0,
        "model_timeout_seconds": 300.0,
        "rpc_attempts": RPC_ATTEMPTS,
        "worker_semantic_cache": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    repository_default = Path(__file__).resolve().parents[4]
    data_default = repository_default / "eval" / "seed-essential-delayed-v1.json"
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
                "--run-ref", default=f"seed-essential-delayed-v1-{command}"
            )
        if command == "run":
            subparser.add_argument("--settle-timeout", type=float, default=900.0)
            subparser.add_argument("--rpc-timeout", type=float, default=30.0)
            subparser.add_argument("--model-timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    dataset = load_delayed_seed(args.data)
    if args.command == "prepare":
        value = {
            "protocol": PROTOCOL,
            "source_sha256": dataset.source_sha256,
            "patterns": len({item.pattern_ref for item in dataset.instances}),
            "instances": len(dataset.instances),
            "history_sessions_per_instance": 3,
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
        )
        freeze_manifest(args.output / "summary.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
