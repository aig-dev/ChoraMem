"""Prepare, run, and summarize the six-arm PersonaMem-v2 effect diagnosis."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from memory_core import memory_pb2 as pb

from .personamem_data import (
    DATA_REVISION,
    DATASET,
    INFERENCE_SHA256,
    UPSTREAM_REVISION,
    History,
    Question,
    load_history,
    load_official_mcq,
    load_questions,
    resolve_history_path,
)
from .personamem_diagnostics import OracleEvidence, load_oracle_evidence
from .personamem_effect import EffectMode, EpisodeDocument
from .personamem_effect_runner import (
    CachedTextModel,
    GrpcEpisodeSearch,
    evaluate_question,
    freeze_manifest,
    freeze_question_result,
    ingest_history,
    learned_basis_map,
    summarize_effect_results,
    wait_for_effect_ready,
)
from .personamem_runner import cl100k_token_count, fetch


VALIDATION_SHA256 = "a47a7dd3879de5e282c6d15266437ed44cf69c0c93c79634645dbd73d655b29a"
VALIDATION_ROWS = 2_061
DEFAULT_BOOTSTRAP_SAMPLES = 2_000


def manifest_for_validation(
    root: Path,
    questions: tuple[Question, ...],
    *,
    question_limit: int,
    persona_limit: int = 0,
    persona_sample_salt: str = "",
) -> dict[str, object]:
    histories_by_persona: dict[str, str] = {}
    for question in questions:
        previous = histories_by_persona.setdefault(
            question.persona_id,
            question.history_path,
        )
        if previous != question.history_path:
            raise ValueError(
                f"persona {question.persona_id} maps to multiple histories"
            )
    manifest = {
        "benchmark": "PersonaMem-v2",
        "split": "validation",
        "dataset": DATASET,
        "data_revision": DATA_REVISION,
        "upstream_revision": UPSTREAM_REVISION,
        "validation_sha256": VALIDATION_SHA256,
        "inference_sha256": INFERENCE_SHA256,
        "size": "32k",
        "question_limit": question_limit,
        "questions": [question.question_ref for question in questions],
        "personas": list(dict.fromkeys(question.persona_id for question in questions)),
        "question_personas": {
            question.question_ref: question.persona_id for question in questions
        },
        "histories": {
            link: hashlib.sha256(resolve_history_path(root, link).read_bytes()).hexdigest()
            for link in dict.fromkeys(question.history_path for question in questions)
        },
        "modes": [mode.value for mode in EffectMode],
    }
    if persona_limit:
        manifest["persona_limit"] = persona_limit
        manifest["persona_sample_salt"] = persona_sample_salt
    return manifest


def select_persona_holdout(
    questions: Sequence[Question],
    *,
    limit: int,
    salt: str,
) -> tuple[Question, ...]:
    """Select complete persona groups without inspecting questions or labels."""

    if type(limit) is not int or limit <= 0:
        raise ValueError("persona holdout limit must be positive")
    salt = salt.strip()
    if not salt:
        raise ValueError("persona holdout salt is required")
    personas = sorted({question.persona_id for question in questions})
    if limit > len(personas):
        raise ValueError("persona holdout limit exceeds available personas")
    ranked = sorted(
        personas,
        key=lambda persona: (
            hashlib.sha256(f"{salt}\0{persona}".encode()).digest(),
            persona,
        ),
    )
    selected = set(ranked[:limit])
    return tuple(question for question in questions if question.persona_id in selected)


def runtime_controls(args, *, bootstrap_samples: int) -> dict[str, object]:
    """Freeze every CLI control that can alter execution or reported effects."""

    return {
        "core_memory_tokens": args.memory_tokens,
        "episode_evidence_max_bytes": args.episode_evidence_max_bytes,
        "max_output_tokens": args.output_tokens,
        "batch_turns": args.batch_turns,
        "settle_timeout_seconds": args.settle_timeout,
        "rpc_timeout_seconds": args.rpc_timeout,
        "index_timeout_seconds": args.index_timeout,
        "persona_workers": args.persona_workers,
        "rpc_attempts": args.rpc_attempts,
        "bootstrap_samples": bootstrap_samples,
        "answer_arm_order": "sha256-interleaved-v1",
    }


async def run_personas_bounded(
    grouped: Mapping[str, Sequence[Question]],
    *,
    limit: int,
    process: Callable[[str, Sequence[Question]], Awaitable[None]],
) -> None:
    """Process independent persona scopes concurrently with a hard upper bound."""

    if type(limit) is not int or limit <= 0:
        raise ValueError("persona concurrency limit must be positive")
    semaphore = asyncio.Semaphore(limit)

    async def run_one(persona_id: str, cases: Sequence[Question]) -> None:
        async with semaphore:
            await process(persona_id, cases)

    async with asyncio.TaskGroup() as tasks:
        for persona_id, cases in grouped.items():
            tasks.create_task(run_one(persona_id, cases))


def prepare(args) -> tuple[tuple[Question, ...], dict[str, OracleEvidence], dict[str, object]]:
    if args.question_limit < 0:
        raise ValueError("question_limit must be zero or positive")
    persona_limit = getattr(args, "persona_limit", 0)
    persona_sample_salt = getattr(args, "persona_sample_salt", "")
    if args.question_limit and persona_limit:
        raise ValueError("question_limit and persona_limit are mutually exclusive")
    root = args.data
    download_endpoint, api_endpoint = hf_endpoints()
    base = f"{download_endpoint}/datasets/{DATASET}/resolve/{DATA_REVISION}"
    validation_path = root / "benchmark/text/val.csv"
    fetch(
        f"{base}/benchmark/text/val.csv?download=true",
        validation_path,
        VALIDATION_SHA256,
    )
    fetch(
        f"https://raw.githubusercontent.com/{DATASET}/{UPSTREAM_REVISION}/inference.py",
        root / "upstream/inference.py",
        INFERENCE_SHA256,
    )

    all_questions = load_questions(validation_path, size="32k")
    if len(all_questions) != VALIDATION_ROWS:
        raise ValueError(
            f"pinned validation split contains {len(all_questions)} rows, expected {VALIDATION_ROWS}"
        )
    if args.question_limit > len(all_questions):
        raise ValueError("question_limit exceeds the validation split")
    if persona_limit:
        questions = select_persona_holdout(
            all_questions,
            limit=persona_limit,
            salt=persona_sample_salt,
        )
    else:
        questions = (
            all_questions[:args.question_limit]
            if args.question_limit
            else all_questions
        )
    all_labels = load_oracle_evidence(validation_path)
    if set(all_labels) != {question.question_ref for question in all_questions}:
        raise ValueError("validation questions and diagnostic evidence are misaligned")
    labels = {question.question_ref: all_labels[question.question_ref] for question in questions}
    if any(labels[q.question_ref].persona_id != q.persona_id for q in questions):
        raise ValueError("validation evidence belongs to a different persona")

    links = list(dict.fromkeys(question.history_path for question in questions))
    manifest = _reuse_prepared_validation(
        root,
        questions,
        links=links,
        question_limit=args.question_limit,
        persona_limit=persona_limit,
        persona_sample_salt=persona_sample_salt,
    )
    if manifest is not None:
        return questions, labels, manifest

    metadata = _history_metadata(links, endpoint=api_endpoint)

    def download(link: str) -> None:
        path = resolve_history_path(root, link)
        entry = metadata[link]
        if entry.get("lfs"):
            fetch(f"{base}/{link}?download=true", path, entry["lfs"]["oid"])
        else:
            fetch(f"{base}/{link}?download=true", path, git_oid=entry["oid"])
        load_history(path)
        print(json.dumps({"prepared": link}), file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, links))
    manifest = manifest_for_validation(
        root,
        questions,
        question_limit=args.question_limit,
        persona_limit=persona_limit,
        persona_sample_salt=persona_sample_salt,
    )
    suffix = _validation_selection_suffix(
        question_limit=args.question_limit,
        persona_limit=persona_limit,
        persona_sample_salt=persona_sample_salt,
    )
    freeze_manifest(root / f"selection-validation-{suffix}.json", manifest)
    return questions, labels, manifest


def _reuse_prepared_validation(
    root: Path,
    questions: tuple[Question, ...],
    *,
    links: list[str],
    question_limit: int,
    persona_limit: int = 0,
    persona_sample_salt: str = "",
) -> dict[str, object] | None:
    """Reuse only a byte-identical, structurally valid frozen preparation."""

    suffix = _validation_selection_suffix(
        question_limit=question_limit,
        persona_limit=persona_limit,
        persona_sample_salt=persona_sample_salt,
    )
    path = root / f"selection-validation-{suffix}.json"
    if not path.is_file():
        return None
    try:
        current = manifest_for_validation(
            root,
            questions,
            question_limit=question_limit,
            persona_limit=persona_limit,
            persona_sample_salt=persona_sample_salt,
        )
    except FileNotFoundError:
        return None
    frozen = json.loads(path.read_text(encoding="utf-8"))
    if frozen != current:
        raise ValueError("prepared data changed from the frozen validation manifest")
    for link in links:
        load_history(resolve_history_path(root, link))
    return current


def _validation_selection_suffix(
    *,
    question_limit: int,
    persona_limit: int,
    persona_sample_salt: str,
) -> str:
    if persona_limit:
        digest = hashlib.sha256(persona_sample_salt.encode()).hexdigest()[:12]
        return f"personas-{persona_limit}-{digest}"
    return str(question_limit) if question_limit else "all"


def verified_effect_summary(
    output: Path,
    manifest: dict[str, object],
    *,
    bootstrap_samples: int = 2_000,
) -> dict[str, object]:
    expected_questions = set(manifest.get("questions", ()))
    expected_personas = set(manifest.get("personas", ()))
    question_personas = manifest.get("question_personas")
    if not isinstance(question_personas, dict) or set(question_personas) != expected_questions:
        raise ValueError("manifest must map every frozen question to its persona")
    question_dir = output / "questions"
    paths = tuple(question_dir.glob("*.json")) if question_dir.is_dir() else ()
    if {path.stem for path in paths} != expected_questions:
        raise ValueError("results must cover exactly the frozen validation questions")
    rows = []
    for path in sorted(paths):
        row = json.loads(path.read_text(encoding="utf-8"))
        if (
            row.get("question_ref") != path.stem
            or row.get("persona_id") != question_personas[path.stem]
        ):
            raise ValueError("question result identity differs from the frozen manifest")
        rows.append(row)
    if {row.get("persona_id") for row in rows} != expected_personas:
        raise ValueError("results must cover exactly the frozen personas")

    for persona in expected_personas:
        proof_path = output / f"memory-{persona}.json"
        if not proof_path.is_file():
            raise ValueError(f"missing frozen-memory proof for persona {persona}")
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        if (
            proof.get("persona") != persona
            or proof.get("test_phase_memory_unchanged") is not True
            or proof.get("projection_pending") != 0
        ):
            raise ValueError(f"invalid frozen-memory proof for persona {persona}")
    return summarize_effect_results(rows, bootstrap_samples=bootstrap_samples)


async def run(
    args,
    questions: tuple[Question, ...],
    labels: dict[str, OracleEvidence],
    data_manifest: dict[str, object],
) -> int:
    if sys.flags.hash_randomization != 0:
        raise ValueError("run with PYTHONHASHSEED=0 to freeze official option ordering")

    from memory_core import AsyncMemoryClient
    import tiktoken

    from .personamem_live import ChatTextModel, DatabaseProbe, RetryingMemoryClient

    environment = _required_environment(
        "MEMORY_EVAL_DATABASE_URL",
        "MEMORY_EVAL_MODEL",
        "MEMORY_WORKER_OPENAI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "MEMORY_CORE_ENDPOINT",
        "MEMORY_CORE_TOKEN",
        "MEMORY_EVAL_MEMORY_INDEX_ENDPOINT",
        "MEMORY_EVAL_MEMORY_INDEX_TOKEN",
        "MEMORY_EVAL_INDEX_REVISION",
        "MEMORY_EVAL_CORE_REVISION",
        "MEMORY_EVAL_WORKER_REVISION",
    )
    bootstrap_samples = (
        args.bootstrap_samples
        if args.bootstrap_samples is not None
        else DEFAULT_BOOTSTRAP_SAMPLES
    )
    args.output.mkdir(parents=True, exist_ok=True)
    probe = DatabaseProbe(environment["MEMORY_EVAL_DATABASE_URL"])
    answer_model_revision = os.environ.get(
        "MEMORY_EVAL_MODEL_REVISION", environment["MEMORY_EVAL_MODEL"]
    )
    raw_model = ChatTextModel(
        model=environment["MEMORY_EVAL_MODEL"],
        api_key=environment["OPENAI_API_KEY"],
        base_url=environment["OPENAI_BASE_URL"],
        usage_file=args.output / "answer-usage.jsonl",
    )
    model = CachedTextModel(
        raw_model,
        args.output / "answer-cache",
        model_revision=answer_model_revision,
    )
    raw_memory = AsyncMemoryClient.connect(
        environment["MEMORY_CORE_ENDPOINT"],
        token_provider=lambda _: environment["MEMORY_CORE_TOKEN"],
        default_timeout=args.rpc_timeout,
    )
    memory = RetryingMemoryClient(raw_memory, attempts=args.rpc_attempts)
    encoding = tiktoken.get_encoding("cl100k_base")
    token_count = lambda text: cl100k_token_count(encoding, text)
    scorer = load_official_mcq(args.data / "upstream/inference.py")

    source_names = (
        "personamem_data.py",
        "personamem_diagnostics.py",
        "personamem_effect.py",
        "personamem_effect_runner.py",
        "personamem_effect_cli.py",
        "personamem_live.py",
    )
    source_root = Path(__file__).parent
    run_manifest = data_manifest | runtime_controls(
        args,
        bootstrap_samples=bootstrap_samples,
    ) | {
        "evaluation_ref": args.run_ref,
        "answer_model": environment["MEMORY_EVAL_MODEL"],
        "answer_model_revision": answer_model_revision,
        "answer_endpoint": environment["OPENAI_BASE_URL"],
        "worker_model": environment["MEMORY_WORKER_OPENAI_MODEL"],
        "worker_revision": environment["MEMORY_EVAL_WORKER_REVISION"],
        "core_revision": environment["MEMORY_EVAL_CORE_REVISION"],
        "index_revision": environment["MEMORY_EVAL_INDEX_REVISION"],
        "tokenizer": "cl100k_base",
        "pythonhashseed": 0,
        "answer_temperature": 0,
        "full_history_policy": "unbounded observed 32k history",
        "profile_policy": "official generator system profile excluded from every arm",
        "test_write_policy": "unbound query SourceEvent and exact Delivery only; no answer or Outcome",
        "diagnostic_evidence_policy": "read only in isolated oracle_episode context",
        "harness_sources": {
            name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
            for name in source_names
        },
    }
    freeze_manifest(args.output / "manifest.json", run_manifest)

    grouped: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        grouped[question.persona_id].append(question)

    async def process_persona(
        persona_id: str,
        cases: Sequence[Question],
    ) -> None:
        history = load_history(resolve_history_path(args.data, cases[0].history_path))
        await _run_persona(
            args=args,
            memory=memory,
            probe=probe,
            model=model,
            scorer=scorer,
            token_count=token_count,
            persona_id=persona_id,
            cases=tuple(cases),
            history=history,
            labels=labels,
            index_endpoint=environment["MEMORY_EVAL_MEMORY_INDEX_ENDPOINT"],
            index_token=environment["MEMORY_EVAL_MEMORY_INDEX_TOKEN"],
        )

    try:
        await run_personas_bounded(
            grouped,
            limit=args.persona_workers,
            process=process_persona,
        )
    finally:
        await raw_memory.close()
        await raw_model.close()

    report = verified_effect_summary(
        args.output,
        run_manifest,
        bootstrap_samples=bootstrap_samples,
    )
    freeze_question_result(args.output / "summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


async def _run_persona(
    *,
    args,
    memory,
    probe,
    model,
    scorer,
    token_count,
    persona_id: str,
    cases: tuple[Question, ...],
    history: History,
    labels: dict[str, OracleEvidence],
    index_endpoint: str,
    index_token: str,
) -> None:
    before_path = args.output / f"memory-before-{persona_id}.json"
    result_paths = {
        question.question_ref: args.output / "questions" / f"{question.question_ref}.json"
        for question in cases
    }
    completed = {ref for ref, path in result_paths.items() if path.is_file()}
    if before_path.is_file():
        frozen = json.loads(before_path.read_text(encoding="utf-8"))
    else:
        if completed:
            raise RuntimeError("cannot resume answers without the original memory snapshot")

        async def settle(scope: pb.MemoryScope, count: int) -> dict[str, object]:
            def snapshot() -> dict[str, object]:
                return probe.snapshot(scope) | {
                    "pending_index_operations": probe.pending_index_operations(scope)
                }

            return await wait_for_effect_ready(
                snapshot,
                expected_episodes=count,
                timeout=args.settle_timeout,
            )

        consolidation, documents = await ingest_history(
            memory=memory,
            settle=settle,
            evaluation_ref=args.run_ref,
            persona_id=persona_id,
            history=history,
            batch_turns=args.batch_turns,
        )
        owner = _owner_scope(args.run_ref, persona_id)
        frozen = {
            "persona": persona_id,
            "history_episodes": len(history.turns),
            "unanswered_history_recorded_without_episode": bool(history.trailing_user),
            "consolidation": consolidation,
            "memory": probe.memory_state(owner),
            "episode_documents": [
                {"memory_ref": item.memory_ref, "text": item.text} for item in documents
            ],
        }
        freeze_manifest(before_path, frozen)

    if frozen.get("persona") != persona_id:
        raise ValueError("memory snapshot belongs to a different persona")
    raw_documents = frozen.get("episode_documents")
    if not isinstance(raw_documents, list):
        raise ValueError("memory snapshot has no captured Episodes")
    documents = tuple(
        EpisodeDocument(item["memory_ref"], item["text"])
        for item in raw_documents
    )
    if len(documents) != len(history.turns):
        raise ValueError("captured Episodes do not cover the official history")
    memory_state = frozen.get("memory")
    if not isinstance(memory_state, dict):
        raise ValueError("memory snapshot has no learned state")

    owner = _owner_scope(args.run_ref, persona_id)

    def verify_frozen() -> None:
        if probe.memory_state(owner) != memory_state:
            raise RuntimeError("test questions changed learned memory")
        pending = probe.pending_index_operations(owner)
        if pending != 0:
            raise RuntimeError(f"MemoryIndex projection has {pending} pending operations")

    verify_frozen()
    learned_basis = learned_basis_map(memory_state)
    active_learned = sum(
        record.get("status") == "active"
        for key in ("recollections", "dispositions")
        for record in memory_state.get(key, ())
    )
    episode_search = GrpcEpisodeSearch(
        documents=documents,
        owner_document_count=len(documents) + active_learned,
        endpoint=index_endpoint,
        token=index_token,
        timeout=args.index_timeout,
    )
    try:
        for question in cases:
            result_path = result_paths[question.question_ref]
            if result_path.is_file():
                cached = json.loads(result_path.read_text(encoding="utf-8"))
                summarize_effect_results([cached], bootstrap_samples=10)
                continue
            result = await evaluate_question(
                memory=memory,
                episode_search=episode_search,
                model=model,
                scorer=scorer,
                evaluation_ref=args.run_ref,
                scope=_query_scope(args.run_ref, persona_id, question.question_ref),
                question=question,
                history=history,
                oracle=labels[question.question_ref],
                episode_documents=documents,
                learned_basis=learned_basis,
                token_count=token_count,
                core_memory_tokens=args.memory_tokens,
                max_output_tokens=args.output_tokens,
                episode_evidence_max_bytes=args.episode_evidence_max_bytes,
            )
            verify_frozen()
            freeze_question_result(result_path, result)
            print(json.dumps({
                "persona": persona_id,
                "question": question.question_ref,
                "correct": {
                    row["mode"]: row["correct"] for row in result["answers"]
                },
                "funnel": result["diagnostic"],
            }, ensure_ascii=False), file=sys.stderr, flush=True)
    finally:
        episode_search.close()

    verify_frozen()
    completion = {
        "persona": persona_id,
        "questions": [question.question_ref for question in cases],
        "memory_before_sha256": hashlib.sha256(before_path.read_bytes()).hexdigest(),
        "test_phase_memory_unchanged": True,
        "projection_pending": 0,
    }
    freeze_manifest(args.output / f"memory-{persona_id}.json", completion)


def _history_metadata(links: list[str], *, endpoint: str) -> dict[str, dict]:
    files: dict[str, dict] = {}
    for start in range(0, len(links), 100):
        batch = links[start:start + 100]
        request = urllib.request.Request(
            f"{endpoint}/api/datasets/{DATASET}/paths-info/{DATA_REVISION}",
            data=json.dumps({"paths": batch}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            entries = json.load(response)
        files.update({
            entry["path"]: entry
            for entry in entries
            if entry.get("type") == "file"
        })
    if set(files) != set(links) or any(not entry.get("oid") for entry in files.values()):
        raise ValueError("pinned upstream history metadata is incomplete")
    return files


def hf_endpoints() -> tuple[str, str]:
    """Keep large-file mirrors separate from the POST metadata endpoint."""

    return (
        os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/"),
        os.environ.get("HF_API_ENDPOINT", "https://huggingface.co").rstrip("/"),
    )


def _required_environment(*names: str) -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"missing required environment: {', '.join(missing)}")
    return values


def _owner_scope(evaluation_ref: str, persona_id: str) -> pb.MemoryScope:
    return pb.MemoryScope(
        tenant_ref=f"personamem-{evaluation_ref}",
        agent_ref="reference-agent",
        relationship_ref=f"persona-{persona_id}",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )


def _query_scope(evaluation_ref: str, persona_id: str, question_ref: str) -> pb.MemoryScope:
    scope = _owner_scope(evaluation_ref, persona_id)
    scope.session_ref = f"query-{question_ref}"
    return scope


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive finite number") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _episode_budget(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 16_384:
        raise argparse.ArgumentTypeError("must be between 1 and 16384")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize"))
    parser.add_argument("--data", type=Path, default=Path(".cache/PersonaMem-v2"))
    parser.add_argument("--output", type=Path, default=Path(".cache/personamem-effect"))
    parser.add_argument("--run-ref", default="personamem-effect-validation-v1")
    parser.add_argument("--question-limit", type=int, default=0,
                        help="deterministic prefix for smoke only; zero runs all 2061 validation rows")
    parser.add_argument(
        "--persona-limit",
        type=int,
        default=0,
        help="select this many complete persona groups by a salted stable hash",
    )
    parser.add_argument(
        "--persona-sample-salt",
        default="",
        help="required frozen salt when --persona-limit is nonzero",
    )
    parser.add_argument("--memory-tokens", type=_positive_int, default=2_048)
    parser.add_argument("--episode-evidence-max-bytes", type=_episode_budget, default=16_384)
    parser.add_argument("--output-tokens", type=_positive_int, default=1_024)
    parser.add_argument("--batch-turns", type=_positive_int, default=28)
    parser.add_argument("--settle-timeout", type=_positive_float, default=900.0)
    parser.add_argument("--rpc-timeout", type=_positive_float, default=30.0)
    parser.add_argument("--index-timeout", type=_positive_float, default=30.0)
    parser.add_argument("--persona-workers", type=_positive_int, default=4,
                        help="maximum independent persona scopes processed concurrently")
    parser.add_argument("--rpc-attempts", type=_positive_int, default=5,
                        help="attempts for retry-safe Core transaction aborts")
    parser.add_argument("--bootstrap-samples", type=_positive_int)
    args = parser.parse_args(argv)

    if args.command == "summarize":
        manifest = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
        frozen_bootstrap = manifest.get("bootstrap_samples")
        if type(frozen_bootstrap) is not int or frozen_bootstrap <= 0:
            raise ValueError("manifest bootstrap_samples must be a positive integer")
        if (
            args.bootstrap_samples is not None
            and args.bootstrap_samples != frozen_bootstrap
        ):
            raise ValueError("bootstrap_samples differs from the frozen run manifest")
        report = verified_effect_summary(
            args.output,
            manifest,
            bootstrap_samples=frozen_bootstrap,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    questions, labels, manifest = prepare(args)
    if args.command == "prepare":
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    return asyncio.run(run(args, questions, labels, manifest))


if __name__ == "__main__":
    raise SystemExit(main())
