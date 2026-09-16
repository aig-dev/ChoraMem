"""Frozen CUPID Seed-effect evaluation orchestration."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .cupid_data import CupidDataset, CupidInstance, CupidLabel, load_cupid
from .cupid_effect import (
    PROTOCOL,
    build_answer_prompt,
    build_judge_prompt,
    summarize_cupid_effect,
)
from .cupid_runner import (
    evaluate_instance,
    freeze_result,
    load_frozen_result,
    owner_scope,
)
from .perma_seed_effect import SeedEffectMode
from .personamem_effect_runner import (
    CachedTextModel,
    freeze_manifest,
    wait_for_effect_ready,
)
from .personamem_runner import cl100k_token_count
from .seed_lifecycle import run_lifecycle_suite


FROZEN_MODEL = "MiniMax-M2.5"
FROZEN_MODEL_REVISION = "MiniMax-M2.5@api.minimaxi.com-2026-09-08"
FROZEN_MODEL_ENDPOINT = "https://api.minimaxi.com/v1"
COHORT_SPLITS = {"dev": ("dev",), "formal": ("H1", "H2")}
MEMORY_TOKENS = 2_048
ANSWER_TOKENS = 32_768
JUDGE_TOKENS = 8_192
BOOTSTRAP_SAMPLES = 2_000
RPC_ATTEMPTS = 5


def select_cohort(
    datasets: Mapping[str, CupidDataset],
    *,
    cohort: str,
    persona_limit: int = 0,
) -> dict[str, CupidDataset]:
    """Select only whole personas; formal holdouts can never be sampled."""

    if cohort not in COHORT_SPLITS:
        raise ValueError("CUPID cohort must be dev or formal")
    if type(persona_limit) is not int or persona_limit < 0:
        raise ValueError("persona limit must be a nonnegative integer")
    if cohort == "formal" and persona_limit:
        raise ValueError("formal CUPID cohort does not allow a persona limit")

    required = COHORT_SPLITS[cohort]
    missing = [split for split in required if split not in datasets]
    if missing:
        raise ValueError("CUPID cohort is missing split: " + ", ".join(missing))
    selected = {split: _validated_dataset(datasets[split], split) for split in required}
    if not persona_limit:
        return selected

    dataset = selected["dev"]
    personas = sorted(
        {instance.persona_id for instance in dataset.instances},
        key=lambda value: (
            hashlib.sha256(f"cupid-dev-persona-limit-v1:{value}".encode()).digest(),
            value,
        ),
    )
    if persona_limit > len(personas):
        raise ValueError("persona limit exceeds the CUPID dev cohort")
    allowed = set(personas[:persona_limit])
    instances = tuple(
        instance for instance in dataset.instances if instance.persona_id in allowed
    )
    refs = {instance.instance_ref for instance in instances}
    return {
        "dev": CupidDataset(
            split="dev",
            instances=instances,
            labels={ref: dataset.labels[ref] for ref in refs},
        )
    }


def build_run_manifest(
    *,
    data_path: Path,
    split_manifest_path: Path,
    datasets: Mapping[str, CupidDataset],
    cohort: str,
    persona_limit: int = 0,
    evaluation_ref: str,
    answer_model: str,
    answer_model_revision: str,
    judge_model: str,
    judge_model_revision: str,
    worker_model: str,
    worker_model_revision: str,
    model_endpoint: str,
    core_revision: str,
    worker_revision: str,
    index_revision: str,
    memory_tokens: int,
    answer_tokens: int,
    judge_tokens: int,
    settle_timeout: float,
    rpc_timeout: float,
    model_timeout: float,
    rpc_attempts: int,
    instance_workers: int,
    bootstrap_samples: int,
) -> dict[str, object]:
    """Freeze all inputs capable of changing the paired causal comparison."""

    if type(persona_limit) is not int or persona_limit < 0:
        raise ValueError("persona limit must be a nonnegative integer")
    if cohort == "formal" and persona_limit:
        raise ValueError("formal CUPID cohort does not allow a persona limit")
    selected = select_cohort(datasets, cohort=cohort)
    _require_frozen_models(
        answer_model=answer_model,
        answer_model_revision=answer_model_revision,
        judge_model=judge_model,
        judge_model_revision=judge_model_revision,
        worker_model=worker_model,
        worker_model_revision=worker_model_revision,
        model_endpoint=model_endpoint,
    )
    data_path = _regular_file(data_path, "CUPID data")
    split_manifest_path = _regular_file(
        split_manifest_path, "CUPID split manifest"
    )
    source_root = Path(__file__).parent
    source_names = (
        "cupid_data.py",
        "cupid_effect.py",
        "cupid_runner.py",
        "cupid_cli.py",
        "seed_lifecycle.py",
        "perma_seed_effect.py",
        "personamem_effect_runner.py",
        "personamem_live.py",
    )
    answer_prompt = build_answer_prompt(
        current_request="<CURRENT_REQUEST>", memory_text="<LONG_TERM_MEMORY>"
    )
    judge_prompt = build_judge_prompt(
        user_request="<CURRENT_REQUEST>",
        ai_response="<AI_RESPONSE>",
        preference="<SCORER_PREFERENCE>",
        checklist=("<SCORER_CHECKLIST_ITEM>",),
    )
    return {
        "benchmark": "CUPID",
        "protocol": PROTOCOL,
        "cohort": cohort,
        "persona_limit": persona_limit,
        "dev_sampling": "sha256 whole-persona v1",
        "evaluation_ref": _nonblank(evaluation_ref, "evaluation_ref"),
        "dataset": {
            "file": data_path.name,
            "size_bytes": data_path.stat().st_size,
            "sha256": _file_sha256(data_path),
            "split_manifest_file": split_manifest_path.name,
            "split_manifest_sha256": _file_sha256(split_manifest_path),
        },
        "split_order": list(COHORT_SPLITS[cohort]),
        "splits": {
            split: _split_manifest(dataset) for split, dataset in selected.items()
        },
        "modes": [mode.value for mode in SeedEffectMode],
        "answer_model": answer_model,
        "answer_model_revision": answer_model_revision,
        "judge_model": judge_model,
        "judge_model_revision": judge_model_revision,
        "worker_model": worker_model,
        "worker_model_revision": worker_model_revision,
        "model_endpoint": model_endpoint.rstrip("/"),
        "core_revision": _nonblank(core_revision, "core_revision"),
        "worker_revision": _nonblank(worker_revision, "worker_revision"),
        "index_revision": _nonblank(index_revision, "index_revision"),
        "reference_harness": PROTOCOL,
        "learned_memory_tokens": _nonnegative_int(
            memory_tokens, "memory_tokens"
        ),
        "episode_evidence_max_bytes": 0,
        "answer_max_output_tokens": _positive_int(
            answer_tokens, "answer_tokens"
        ),
        "judge_max_output_tokens": _positive_int(judge_tokens, "judge_tokens"),
        "tokenizer": "cl100k_base",
        "answer_temperature": 0,
        "judge_temperature": 0,
        "answer_reasoning_split": True,
        "judge_reasoning_split": True,
        "answer_max_retries": 0,
        "judge_max_retries": 0,
        "answer_arm_order": "sha256-interleaved-v1",
        "judge_arm_order": "sha256-interleaved-v1",
        "session_window": "one CUPID prior interaction",
        "test_write_policy": (
            "unbound query SourceEvent only; no Delivery, AgentAct, or Outcome"
        ),
        "select_generation_model_calls": 0,
        "worker_contract": "plain text input; shallow tagged text output",
        "owner_decision": False,
        "settle_timeout_seconds": _positive_float(
            settle_timeout, "settle_timeout"
        ),
        "rpc_timeout_seconds": _positive_float(rpc_timeout, "rpc_timeout"),
        "model_timeout_seconds": _positive_float(model_timeout, "model_timeout"),
        "rpc_attempts": _positive_int(rpc_attempts, "rpc_attempts"),
        "instance_workers": _positive_int(instance_workers, "instance_workers"),
        "bootstrap_samples": _positive_int(
            bootstrap_samples, "bootstrap_samples"
        ),
        "prompts_sha256": {
            "answer": _json_sha256(answer_prompt),
            "judge": _json_sha256(judge_prompt),
        },
        "harness_sources": {
            name: _file_sha256(source_root / name) for name in source_names
        },
    }


async def run_instances_bounded(
    *,
    datasets: Mapping[str, CupidDataset],
    output_dir: Path,
    limit: int,
    process: Callable[
        [CupidInstance, CupidLabel], Awaitable[Mapping[str, object]]
    ],
) -> tuple[dict[str, object], ...]:
    """Run only missing complete instances with bounded independent concurrency."""

    limit = _positive_int(limit, "instance concurrency")
    semaphore = asyncio.Semaphore(limit)
    results: list[dict[str, object]] = []

    async def run_one(instance: CupidInstance, label: CupidLabel) -> None:
        path = output_dir / "instances" / f"{instance.instance_ref}.json"
        async with semaphore:
            result = load_frozen_result(path, instance=instance, label=label)
            if result is None:
                candidate = await process(instance, label)
                freeze_result(path, candidate)
                result = load_frozen_result(path, instance=instance, label=label)
            if result is None:  # pragma: no cover - guarded by the freeze above
                raise AssertionError("frozen CUPID result disappeared")
            results.append(result)

    for split in datasets:
        dataset = _validated_dataset(datasets[split], split)
        async with asyncio.TaskGroup() as tasks:
            for instance in dataset.instances:
                tasks.create_task(run_one(instance, dataset.labels[instance.instance_ref]))
    return tuple(sorted(results, key=lambda row: str(row["instance_ref"])))


async def execute_cohort(
    *,
    manifest: Mapping[str, object],
    datasets: Mapping[str, CupidDataset],
    output_dir: Path,
    instance_workers: int,
    process: Callable[
        [CupidInstance, CupidLabel], Awaitable[Mapping[str, object]]
    ],
    lifecycle: Callable[[], Awaitable[Mapping[str, object]]] | None = None,
) -> dict[str, object]:
    """Freeze configuration, prove lifecycle, run instances, and freeze summary."""

    cohort = manifest.get("cohort")
    if cohort not in COHORT_SPLITS:
        raise ValueError("output manifest is not the frozen CUPID protocol")
    selected = select_cohort(datasets, cohort=str(cohort))
    _validate_manifest_cohort(manifest, selected)
    freeze_manifest(output_dir / "manifest.json", manifest)

    if cohort == "formal":
        if lifecycle is None:
            raise ValueError("formal CUPID run requires the lifecycle gate")
        lifecycle_result = await lifecycle()
        freeze_manifest(output_dir / "lifecycle" / "summary.json", lifecycle_result)
        decision = lifecycle_result.get("decision")
        if (
            lifecycle_result.get("protocol") != "seed-companion-lifecycle-v1"
            or not isinstance(decision, Mapping)
            or decision.get("passed") is not True
        ):
            raise RuntimeError("Seed companion lifecycle gate failed")

    await run_instances_bounded(
        datasets=selected,
        output_dir=output_dir,
        limit=instance_workers,
        process=process,
    )
    summary = verified_summary(output_dir, manifest=manifest, datasets=selected)
    freeze_manifest(output_dir / "summary.json", summary)
    return summary


async def run_live(args: argparse.Namespace, datasets: Mapping[str, CupidDataset]) -> int:
    """Connect the frozen Harness to one isolated live Memory Core deployment."""

    from memory_core import AsyncMemoryClient
    import tiktoken

    from .personamem_live import ChatTextModel, DatabaseProbe, RetryingMemoryClient

    environment = {
        name: os.environ.get(name, "").strip()
        for name in (
            "MEMORY_EVAL_DATABASE_URL",
            "MEMORY_EVAL_MODEL",
            "MEMORY_EVAL_MODEL_REVISION",
            "MEMORY_WORKER_OPENAI_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "MEMORY_CORE_ENDPOINT",
            "MEMORY_CORE_TOKEN",
            "MEMORY_EVAL_CORE_REVISION",
            "MEMORY_EVAL_WORKER_REVISION",
            "MEMORY_EVAL_INDEX_REVISION",
        )
    }
    missing = [name for name, value in environment.items() if not value]
    if missing:
        raise ValueError("missing required environment: " + ", ".join(missing))

    manifest = build_run_manifest(
        data_path=args.data,
        split_manifest_path=args.split_manifest,
        datasets=datasets,
        cohort=args.cohort,
        persona_limit=args.persona_limit,
        evaluation_ref=args.run_ref,
        answer_model=environment["MEMORY_EVAL_MODEL"],
        answer_model_revision=environment["MEMORY_EVAL_MODEL_REVISION"],
        judge_model=environment["MEMORY_EVAL_MODEL"],
        judge_model_revision=environment["MEMORY_EVAL_MODEL_REVISION"],
        worker_model=environment["MEMORY_WORKER_OPENAI_MODEL"],
        worker_model_revision=environment["MEMORY_EVAL_MODEL_REVISION"],
        model_endpoint=environment["OPENAI_BASE_URL"],
        core_revision=environment["MEMORY_EVAL_CORE_REVISION"],
        worker_revision=environment["MEMORY_EVAL_WORKER_REVISION"],
        index_revision=environment["MEMORY_EVAL_INDEX_REVISION"],
        memory_tokens=MEMORY_TOKENS,
        answer_tokens=ANSWER_TOKENS,
        judge_tokens=JUDGE_TOKENS,
        settle_timeout=args.settle_timeout,
        rpc_timeout=args.rpc_timeout,
        model_timeout=args.model_timeout,
        rpc_attempts=RPC_ATTEMPTS,
        instance_workers=args.instance_workers,
        bootstrap_samples=BOOTSTRAP_SAMPLES,
    )

    probe = DatabaseProbe(environment["MEMORY_EVAL_DATABASE_URL"])
    raw_answer_model = ChatTextModel(
        model=environment["MEMORY_EVAL_MODEL"],
        api_key=environment["OPENAI_API_KEY"],
        base_url=environment["OPENAI_BASE_URL"],
        usage_file=args.output / "answer-usage.jsonl",
        reasoning_split=True,
        request_timeout=args.model_timeout,
        max_retries=0,
    )
    raw_judge_model = ChatTextModel(
        model=environment["MEMORY_EVAL_MODEL"],
        api_key=environment["OPENAI_API_KEY"],
        base_url=environment["OPENAI_BASE_URL"],
        usage_file=args.output / "judge-usage.jsonl",
        reasoning_split=True,
        request_timeout=args.model_timeout,
        max_retries=0,
    )
    answer_model = CachedTextModel(
        raw_answer_model,
        args.output / "answer-cache",
        model_revision=environment["MEMORY_EVAL_MODEL_REVISION"],
    )
    judge_model = CachedTextModel(
        raw_judge_model,
        args.output / "judge-cache",
        model_revision=environment["MEMORY_EVAL_MODEL_REVISION"],
    )
    raw_memory = AsyncMemoryClient.connect(
        environment["MEMORY_CORE_ENDPOINT"],
        token_provider=lambda _: environment["MEMORY_CORE_TOKEN"],
        default_timeout=args.rpc_timeout,
    )
    memory = RetryingMemoryClient(raw_memory, attempts=RPC_ATTEMPTS)
    encoding = tiktoken.get_encoding("cl100k_base")
    token_count = lambda text: cl100k_token_count(encoding, text)

    async def settle(scope, expected_episodes):
        await wait_for_effect_ready(
            lambda: probe.snapshot(scope)
            | {
                "pending_index_operations": probe.pending_index_operations(scope)
            },
            expected_episodes=expected_episodes,
            timeout=args.settle_timeout,
        )
        return probe.memory_state(scope)

    async def process(instance: CupidInstance, label: CupidLabel):
        result = await evaluate_instance(
            instance=instance,
            label=label,
            evaluation_ref=args.run_ref,
            memory=memory,
            settle=settle,
            answer_model=answer_model,
            judge_model=judge_model,
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_answer_tokens=ANSWER_TOKENS,
            max_judge_tokens=JUDGE_TOKENS,
        )
        freeze_manifest(
            args.output / "memory" / f"{instance.instance_ref}.json",
            {
                "split": label.split,
                "instance_ref": instance.instance_ref,
                "memory": probe.memory_state(
                    owner_scope(args.run_ref, instance.instance_ref)
                ),
            },
        )
        return result

    async def lifecycle():
        return await run_lifecycle_suite(
            evaluation_ref=args.run_ref + ":lifecycle",
            memory=memory,
            settle=settle,
            model=answer_model,
            output_dir=args.output / "lifecycle",
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_output_tokens=ANSWER_TOKENS,
        )

    try:
        summary = await execute_cohort(
            manifest=manifest,
            datasets=datasets,
            output_dir=args.output,
            instance_workers=args.instance_workers,
            process=process,
            lifecycle=lifecycle if args.cohort == "formal" else None,
        )
    finally:
        await raw_memory.close()
        await raw_answer_model.close()
        await raw_judge_model.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["decision"]["passed"] else 1


def verified_summary(
    output_dir: Path,
    *,
    manifest: Mapping[str, object],
    datasets: Mapping[str, CupidDataset],
) -> dict[str, object]:
    """Summarize only the exact, complete cohort frozen in the run manifest."""

    cohort = manifest.get("cohort")
    if manifest.get("protocol") != PROTOCOL or cohort not in COHORT_SPLITS:
        raise ValueError("output manifest is not the frozen CUPID protocol")
    selected = select_cohort(
        datasets,
        cohort=str(cohort),
        persona_limit=_nonnegative_int(
            manifest.get("persona_limit"), "persona_limit"
        ),
    )
    _validate_manifest_cohort(manifest, selected)
    expected = {
        instance.instance_ref: (instance, dataset.labels[instance.instance_ref])
        for dataset in selected.values()
        for instance in dataset.instances
    }
    instance_dir = output_dir / "instances"
    actual_paths = {
        path.stem: path for path in instance_dir.glob("*.json")
    } if instance_dir.is_dir() else {}
    extras = set(actual_paths) - set(expected)
    if extras:
        raise ValueError("unregistered CUPID result file")
    if set(actual_paths) != set(expected):
        raise ValueError("CUPID summary requires the complete frozen cohort")

    results = []
    labels: dict[str, CupidLabel] = {}
    memory_budget = _nonnegative_int(
        manifest.get("learned_memory_tokens"), "learned_memory_tokens"
    )
    for instance_ref in sorted(expected):
        instance, label = expected[instance_ref]
        result = load_frozen_result(
            actual_paths[instance_ref], instance=instance, label=label
        )
        if result is None:  # pragma: no cover - exact files checked above
            raise ValueError("CUPID summary requires the complete frozen cohort")
        answers = result.get("answers")
        if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)):
            raise ValueError("CUPID result requires answer arms")
        if any(
            not isinstance(answer, Mapping)
            or type(answer.get("memory_tokens")) is not int
            or answer["memory_tokens"] > memory_budget
            for answer in answers
        ):
            raise ValueError("CUPID result exceeded the frozen memory budget")
        results.append(result)
        labels[instance_ref] = label

    formal = cohort == "formal"
    lifecycle: Mapping[str, object] | None = None
    if formal:
        lifecycle_path = output_dir / "lifecycle" / "summary.json"
        if not lifecycle_path.is_file():
            raise ValueError("missing frozen lifecycle gate")
        lifecycle_value = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        if not isinstance(lifecycle_value, Mapping):
            raise ValueError("invalid frozen lifecycle gate")
        decision = lifecycle_value.get("decision")
        if (
            lifecycle_value.get("protocol") != "seed-companion-lifecycle-v1"
            or not isinstance(decision, Mapping)
            or decision.get("passed") is not True
        ):
            raise ValueError("lifecycle gate did not pass")
        lifecycle = lifecycle_value

    summary = summarize_cupid_effect(
        results,
        labels=labels,
        bootstrap_samples=_positive_int(
            manifest.get("bootstrap_samples"), "bootstrap_samples"
        ),
        formal=formal,
    )
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    output = summary | {
        "cohort": cohort,
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    if lifecycle is not None:
        output["lifecycle"] = lifecycle
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize"))
    parser.add_argument(
        "--data", type=Path, default=Path(".cache/cupid-source/test.parquet")
    )
    parser.add_argument(
        "--split-manifest", type=Path, default=Path("eval/cupid-split-v1.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/cupid-seed-effect-v1")
    )
    parser.add_argument("--cohort", choices=("dev", "formal"), default="dev")
    parser.add_argument("--persona-limit", type=int, default=0)
    parser.add_argument("--run-ref", default="cupid-seed-effect-v1")
    parser.add_argument("--settle-timeout", type=float, default=900.0)
    parser.add_argument("--rpc-timeout", type=float, default=30.0)
    parser.add_argument("--model-timeout", type=float, default=300.0)
    parser.add_argument("--instance-workers", type=int, default=4)
    args = parser.parse_args(argv)

    if args.command == "prepare":
        loaded = {
            split: load_cupid(
                args.data,
                split_manifest=args.split_manifest,
                split=split,
            )
            for split in ("dev", "H1", "H2")
        }
        structure = {
            split: {
                "instances": len(dataset.instances),
                "personas": len(
                    {instance.persona_id for instance in dataset.instances}
                ),
            }
            for split, dataset in loaded.items()
        }
        print(json.dumps(structure, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summarize":
        manifest_path = args.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cohort = manifest.get("cohort")
        if cohort not in COHORT_SPLITS:
            raise ValueError("output manifest is not the frozen CUPID protocol")
        datasets = {
            split: load_cupid(
                args.data,
                split_manifest=args.split_manifest,
                split=split,
            )
            for split in COHORT_SPLITS[str(cohort)]
        }
        summary = verified_summary(
            args.output, manifest=manifest, datasets=datasets
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["decision"]["passed"] else 1

    datasets = {
        split: load_cupid(
            args.data,
            split_manifest=args.split_manifest,
            split=split,
        )
        for split in COHORT_SPLITS[args.cohort]
    }
    selected = select_cohort(
        datasets,
        cohort=args.cohort,
        persona_limit=args.persona_limit,
    )
    return asyncio.run(run_live(args, selected))


if __name__ == "__main__":
    raise SystemExit(main())


def _validated_dataset(dataset: CupidDataset, split: str) -> CupidDataset:
    if dataset.split != split:
        raise ValueError("CUPID dataset split identity drift")
    refs = [instance.instance_ref for instance in dataset.instances]
    if len(refs) != len(set(refs)) or set(refs) != set(dataset.labels):
        raise ValueError("CUPID dataset instances and labels differ")
    for instance in dataset.instances:
        label = dataset.labels[instance.instance_ref]
        if (
            label.instance_ref != instance.instance_ref
            or label.persona_id != instance.persona_id
            or label.split != split
        ):
            raise ValueError("CUPID dataset label identity drift")
    return dataset


def _split_manifest(dataset: CupidDataset) -> dict[str, object]:
    refs = sorted(instance.instance_ref for instance in dataset.instances)
    return {
        "instances": len(refs),
        "personas": len({instance.persona_id for instance in dataset.instances}),
        "instance_refs": refs,
        "instance_refs_sha256": hashlib.sha256(
            ("\n".join(refs) + "\n").encode()
        ).hexdigest(),
    }


def _validate_manifest_cohort(
    manifest: Mapping[str, object], datasets: Mapping[str, CupidDataset]
) -> None:
    if manifest.get("split_order") != list(datasets):
        raise ValueError("CUPID run manifest split drift")
    frozen = manifest.get("splits")
    if not isinstance(frozen, Mapping) or set(frozen) != set(datasets):
        raise ValueError("CUPID run manifest cohort drift")
    for split, dataset in datasets.items():
        if frozen.get(split) != _split_manifest(dataset):
            raise ValueError("CUPID run manifest instance drift")


def _require_frozen_models(**values: str) -> None:
    expected = {
        "answer_model": FROZEN_MODEL,
        "answer_model_revision": FROZEN_MODEL_REVISION,
        "judge_model": FROZEN_MODEL,
        "judge_model_revision": FROZEN_MODEL_REVISION,
        "worker_model": FROZEN_MODEL,
        "worker_model_revision": FROZEN_MODEL_REVISION,
        "model_endpoint": FROZEN_MODEL_ENDPOINT,
    }
    normalized = {
        key: _nonblank(value, key).rstrip("/") for key, value in values.items()
    }
    if normalized != expected:
        raise ValueError("CUPID run must use the frozen model configuration")


def _regular_file(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} file is missing: {path}")
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _positive_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be positive")
    return float(value)


if __name__ == "__main__":
    raise SystemExit(main())
