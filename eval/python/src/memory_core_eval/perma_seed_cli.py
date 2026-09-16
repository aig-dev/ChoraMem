"""Prepare, run, and summarize the frozen PERMA Seed effect evaluation."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from memory_core import memory_pb2 as pb

from .perma_seed_data import (
    DEFAULT_DATA_ENDPOINT,
    PERSONA_SPLITS,
    PROTOCOL,
    PersonaData,
    prepare_split,
)
from .perma_seed_effect import SeedEffectMode, summarize_seed_effect
from .perma_seed_runner import run_persona
from .seed_lifecycle import run_lifecycle_suite
from .personamem_effect_runner import (
    CachedTextModel,
    freeze_manifest,
    wait_for_effect_ready,
)
from .personamem_runner import cl100k_token_count


MEMORY_TOKENS = 2_048
OUTPUT_TOKENS = 32_768
BOOTSTRAP_SAMPLES = 2_000
RPC_ATTEMPTS = 5


def build_run_manifest(
    *,
    data_manifests: Mapping[str, Mapping[str, object]],
    evaluation_ref: str,
    answer_model: str,
    answer_model_revision: str,
    worker_model: str,
    core_revision: str,
    worker_revision: str,
    index_revision: str,
    memory_tokens: int,
    output_tokens: int,
    settle_timeout: float,
    rpc_timeout: float,
    answer_timeout: float,
    answer_reasoning_split: bool,
    answer_max_retries: int,
    rpc_attempts: int,
    persona_workers: int,
    bootstrap_samples: int,
) -> dict[str, object]:
    if set(data_manifests) != set(PERSONA_SPLITS):
        raise ValueError("run requires both frozen PERMA data splits")
    source_root = Path(__file__).parent
    source_names = (
        "perma_seed_data.py",
        "perma_seed_effect.py",
        "perma_seed_runner.py",
        "perma_seed_cli.py",
        "seed_lifecycle.py",
    )
    return {
        "benchmark": "PERMA",
        "protocol": PROTOCOL,
        "data": {
            split: dict(data_manifests[split]) for split in PERSONA_SPLITS
        },
        "evaluation_ref": _nonblank(evaluation_ref, "evaluation_ref"),
        "split_order": list(PERSONA_SPLITS),
        "modes": [mode.value for mode in SeedEffectMode],
        "answer_model": _nonblank(answer_model, "answer_model"),
        "answer_model_revision": _nonblank(
            answer_model_revision, "answer_model_revision"
        ),
        "worker_model": _nonblank(worker_model, "worker_model"),
        "core_revision": _nonblank(core_revision, "core_revision"),
        "worker_revision": _nonblank(worker_revision, "worker_revision"),
        "index_revision": _nonblank(index_revision, "index_revision"),
        "reference_harness": PROTOCOL,
        "constitution": "empty",
        "memory_tokens": _nonnegative_int(memory_tokens, "memory_tokens"),
        "output_tokens": _positive_int(output_tokens, "output_tokens"),
        "episode_evidence_max_bytes": 0,
        "tokenizer": "cl100k_base",
        "answer_temperature": 0,
        "answer_arm_order": "sha256-interleaved-v1",
        "session_window": "one PERMA dialogue session",
        "test_write_policy": (
            "unbound query SourceEvent only; no Delivery, AgentAct, or Outcome"
        ),
        "settle_timeout_seconds": _positive_float(
            settle_timeout, "settle_timeout"
        ),
        "rpc_timeout_seconds": _positive_float(rpc_timeout, "rpc_timeout"),
        "answer_request_timeout_seconds": _positive_float(
            answer_timeout, "answer_timeout"
        ),
        "answer_reasoning_split": _bool(
            answer_reasoning_split, "answer_reasoning_split"
        ),
        "answer_max_retries": _nonnegative_int(
            answer_max_retries, "answer_max_retries"
        ),
        "rpc_attempts": _positive_int(rpc_attempts, "rpc_attempts"),
        "persona_workers": _positive_int(persona_workers, "persona_workers"),
        "bootstrap_samples": _positive_int(
            bootstrap_samples, "bootstrap_samples"
        ),
        "harness_sources": {
            name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
            for name in source_names
        },
    }


def verified_summary(
    output_dir: Path, manifest: Mapping[str, object]
) -> dict[str, object]:
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError("output manifest is not the frozen PERMA Seed protocol")
    bootstrap = manifest.get("bootstrap_samples")
    if type(bootstrap) is not int or bootstrap <= 0:
        raise ValueError("manifest bootstrap_samples must be positive")
    question_dir = output_dir / "questions"
    rows = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(question_dir.glob("*.json"))
    ]
    for row in rows:
        if row.get("protocol") != PROTOCOL:
            raise ValueError("question result has a different protocol")
    lifecycle_path = output_dir / "lifecycle" / "summary.json"
    if not lifecycle_path.is_file():
        raise ValueError("missing frozen lifecycle gate")
    lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    decision = lifecycle.get("decision")
    if (
        lifecycle.get("protocol") != "seed-companion-lifecycle-v1"
        or not isinstance(decision, Mapping)
        or decision.get("passed") is not True
    ):
        raise ValueError("lifecycle gate did not pass")
    summary = summarize_seed_effect(rows, bootstrap_samples=bootstrap)
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    return summary | {
        "lifecycle": lifecycle,
        "manifest_sha256": hashlib.sha256(canonical).hexdigest(),
    }


async def run(
    args: argparse.Namespace,
    personas_by_split: Mapping[str, Sequence[PersonaData]],
    data_manifests: Mapping[str, Mapping[str, object]],
) -> int:
    from memory_core import AsyncMemoryClient
    import tiktoken

    from .personamem_live import ChatTextModel, DatabaseProbe, RetryingMemoryClient

    environment = _required_environment(
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
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = build_run_manifest(
        data_manifests=data_manifests,
        evaluation_ref=args.run_ref,
        answer_model=environment["MEMORY_EVAL_MODEL"],
        answer_model_revision=environment["MEMORY_EVAL_MODEL_REVISION"],
        worker_model=environment["MEMORY_WORKER_OPENAI_MODEL"],
        core_revision=environment["MEMORY_EVAL_CORE_REVISION"],
        worker_revision=environment["MEMORY_EVAL_WORKER_REVISION"],
        index_revision=environment["MEMORY_EVAL_INDEX_REVISION"],
        memory_tokens=MEMORY_TOKENS,
        output_tokens=OUTPUT_TOKENS,
        settle_timeout=args.settle_timeout,
        rpc_timeout=args.rpc_timeout,
        answer_timeout=args.answer_timeout,
        answer_reasoning_split=True,
        answer_max_retries=0,
        rpc_attempts=RPC_ATTEMPTS,
        persona_workers=args.persona_workers,
        bootstrap_samples=BOOTSTRAP_SAMPLES,
    ) | {"answer_endpoint": environment["OPENAI_BASE_URL"]}
    freeze_manifest(args.output / "manifest.json", manifest)

    probe = DatabaseProbe(environment["MEMORY_EVAL_DATABASE_URL"])
    raw_model = ChatTextModel(
        model=environment["MEMORY_EVAL_MODEL"],
        api_key=environment["OPENAI_API_KEY"],
        base_url=environment["OPENAI_BASE_URL"],
        usage_file=args.output / "answer-usage.jsonl",
        reasoning_split=True,
        request_timeout=args.answer_timeout,
        max_retries=0,
    )
    model = CachedTextModel(
        raw_model,
        args.output / "answer-cache",
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

    async def settle_lifecycle(
        scope: pb.MemoryScope, expected_episodes: int
    ) -> Mapping[str, object]:
        await wait_for_effect_ready(
            lambda: probe.snapshot(scope)
            | {
                "pending_index_operations": probe.pending_index_operations(scope)
            },
            expected_episodes=expected_episodes,
            timeout=args.settle_timeout,
        )
        return probe.memory_state(scope)

    lifecycle = await run_lifecycle_suite(
        evaluation_ref=args.run_ref,
        memory=memory,
        settle=settle_lifecycle,
        model=model,
        output_dir=args.output / "lifecycle",
        token_count=token_count,
        memory_tokens=MEMORY_TOKENS,
        max_output_tokens=OUTPUT_TOKENS,
    )
    if lifecycle["decision"]["passed"] is not True:
        await raw_memory.close()
        await raw_model.close()
        raise RuntimeError("Seed companion lifecycle gate failed")
    if args.command == "lifecycle":
        await raw_memory.close()
        await raw_model.close()
        print(json.dumps(lifecycle, ensure_ascii=False, indent=2))
        return 0

    async def process_persona(split: str, persona: PersonaData) -> None:
        async def settle(scope: pb.MemoryScope, expected_episodes: int):
            def snapshot():
                return probe.snapshot(scope) | {
                    "pending_index_operations": probe.pending_index_operations(scope)
                }

            await wait_for_effect_ready(
                snapshot,
                expected_episodes=expected_episodes,
                timeout=args.settle_timeout,
            )
            return probe.memory_state(scope)

        await run_persona(
            persona=persona,
            split=split,
            evaluation_ref=args.run_ref,
            memory=memory,
            settle=settle,
            model=model,
            output_dir=args.output,
            token_count=token_count,
            memory_tokens=MEMORY_TOKENS,
            max_output_tokens=OUTPUT_TOKENS,
        )
        owner = pb.MemoryScope(
            tenant_ref="perma-seed-eval",
            agent_ref="perma-reference-agent",
            relationship_ref=f"{args.run_ref}:persona:{persona.persona_id}",
            kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
        )
        freeze_manifest(
            args.output / f"memory-{persona.persona_id}.json",
            {
                "split": split,
                "persona_id": persona.persona_id,
                "memory": probe.memory_state(owner),
            },
        )

    try:
        for split in PERSONA_SPLITS:
            semaphore = asyncio.Semaphore(args.persona_workers)

            async def bounded(persona: PersonaData) -> None:
                async with semaphore:
                    await process_persona(split, persona)

            async with asyncio.TaskGroup() as tasks:
                for persona in personas_by_split[split]:
                    tasks.create_task(bounded(persona))
    finally:
        await raw_memory.close()
        await raw_model.close()

    summary = verified_summary(args.output, manifest)
    freeze_manifest(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["decision"]["passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "lifecycle", "run", "summarize")
    )
    parser.add_argument("--data", type=Path, default=Path(".cache/PERMA-seed-v2"))
    parser.add_argument("--output", type=Path, default=Path(".cache/perma-seed-effect-v2"))
    parser.add_argument("--run-ref", default="perma-seed-effect-v2")
    parser.add_argument(
        "--data-endpoint",
        default=os.environ.get("HF_ENDPOINT", DEFAULT_DATA_ENDPOINT),
    )
    parser.add_argument("--settle-timeout", type=float, default=900.0)
    parser.add_argument("--rpc-timeout", type=float, default=30.0)
    parser.add_argument("--answer-timeout", type=float, default=300.0)
    parser.add_argument("--persona-workers", type=int, default=5)
    args = parser.parse_args(argv)

    if args.command == "summarize":
        manifest = json.loads(
            (args.output / "manifest.json").read_text(encoding="utf-8")
        )
        summary = verified_summary(args.output, manifest)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["decision"]["passed"] else 1

    personas_by_split: dict[str, tuple[PersonaData, ...]] = {}
    data_manifests: dict[str, dict[str, object]] = {}
    for split in PERSONA_SPLITS:
        personas, data_manifest = prepare_split(
            args.data,
            split=split,
            endpoint=args.data_endpoint,
        )
        personas_by_split[split] = personas
        data_manifests[split] = data_manifest
    if args.command == "prepare":
        print(json.dumps(data_manifests, ensure_ascii=False, indent=2))
        return 0
    return asyncio.run(run(args, personas_by_split, data_manifests))


def _required_environment(*names: str) -> dict[str, str]:
    result = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in result.items() if not value]
    if missing:
        raise ValueError("missing required environment: " + ", ".join(missing))
    return result


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be nonnegative")
    return value


def _positive_float(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return float(value)


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field} must be bool")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
