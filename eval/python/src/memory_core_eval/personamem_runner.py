"""Prepare, run and score a reproducible PersonaMem-v2 four-mode experiment."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .personamem_data import (
    BENCHMARK_SHA256, DATA_REVISION, DATASET, INFERENCE_SHA256, UPSTREAM_REVISION,
    load_history, load_official_mcq, load_questions, resolve_history_path,
    select_questions, verify_sha256,
)
from .reference import EvalMode


def cl100k_token_count(encoding, text: str) -> int:
    return len(encoding.encode(text, disallowed_special=()))


def episode_evidence_budget(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 16384") from error
    if not 0 <= parsed <= 16_384:
        raise argparse.ArgumentTypeError("must be an integer from 0 to 16384")
    return parsed


def fetch(url: str, destination: Path, checksum: str | None = None, *, git_oid: str | None = None) -> None:
    def verify(path):
        if checksum:
            verify_sha256(path, checksum)
        if git_oid:
            data = path.read_bytes()
            actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
            if actual != git_oid:
                raise ValueError(f"history differs from pinned upstream Git blob: {path}")

    if destination.is_file():
        verify(destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as source, temporary.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(0.25 * (2 ** attempt))
    verify(temporary)
    temporary.replace(destination)


def upstream_history_metadata(links: list[str]) -> dict:
    request = urllib.request.Request(
        f"https://huggingface.co/api/datasets/{DATASET}/paths-info/{DATA_REVISION}",
        data=json.dumps({"paths": links}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        entries = json.load(response)
    files = {entry["path"]: entry for entry in entries if entry.get("type") == "file"}
    if set(files) != set(links) or any(not entry.get("oid") for entry in files.values()):
        raise ValueError("pinned upstream history metadata is incomplete")
    return files


def manifest_for(root: Path, questions, *, size: str, seed: int) -> dict:
    return {
        "benchmark": "PersonaMem-v2", "dataset": DATASET, "data_revision": DATA_REVISION,
        "upstream_revision": UPSTREAM_REVISION, "benchmark_sha256": BENCHMARK_SHA256,
        "inference_sha256": INFERENCE_SHA256, "size": size, "sampling_seed": seed,
        "questions": [q.question_ref for q in questions],
        "personas": list(dict.fromkeys(q.persona_id for q in questions)),
        "histories": {link: hashlib.sha256(resolve_history_path(root, link).read_bytes()).hexdigest()
                      for link in dict.fromkeys(q.history_path for q in questions)},
    }


def prepare(args) -> tuple:
    root = args.data
    base = f"https://huggingface.co/datasets/{DATASET}/resolve/{DATA_REVISION}"
    csv_path = root / "benchmark/text/benchmark.csv"
    fetch(f"{base}/benchmark/text/benchmark.csv?download=true", csv_path, BENCHMARK_SHA256)
    fetch(f"https://raw.githubusercontent.com/{DATASET}/{UPSTREAM_REVISION}/inference.py",
          root / "upstream/inference.py", INFERENCE_SHA256)
    questions = select_questions(load_questions(csv_path, size=args.size),
                                 personas=args.personas, per_persona=args.per_persona, seed=args.seed)
    links = list(dict.fromkeys(q.history_path for q in questions))
    metadata = upstream_history_metadata(links)

    def download(link):
        path = resolve_history_path(root, link)
        entry = metadata[link]
        if entry.get("lfs"):
            fetch(f"{base}/{link}?download=true", path, entry["lfs"]["oid"])
        else:
            fetch(f"{base}/{link}?download=true", path, git_oid=entry["oid"])
        load_history(path)  # Fail on partial files, unexpected roles, or fabricated pairs.
        print(json.dumps({"prepared": link}), file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, links))
    manifest = manifest_for(root, questions, size=args.size, seed=args.seed)
    manifest_path = root / f"selection-{args.size}-{args.personas}-{args.per_persona}-{args.seed}.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("prepared data changed from the frozen selection manifest")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return questions, manifest


def summarize(rows: list[dict], *, bootstrap_samples: int = 2000) -> dict:
    keyed = {}
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["persona_id"], row["question_ref"], row["mode"])
        if key in keyed:
            raise ValueError(f"duplicate result: {key}")
        keyed[key] = row
        by_mode[row["mode"]].append(row)
    expected = {(r["persona_id"], r["question_ref"]) for r in rows}
    modes = [m.value for m in EvalMode]
    if not expected or set(by_mode) != set(modes) or any(
        {(r["persona_id"], r["question_ref"]) for r in by_mode[mode]} != expected for mode in modes
    ):
        raise ValueError("a complete paired set of all four modes is required")
    summary = {"questions": len(expected), "personas": len({p for p, _ in expected}),
               "modes": {}, "paired": {}, "uncertainty": "95% paired persona-cluster bootstrap; seed=0"}
    for mode, records in by_mode.items():
        errors = sum(r["error"] is not None for r in records)
        groups = defaultdict(list)
        for record in records:
            for field in ("pref_type", "who", "updated", "sensitive_info"):
                if field in record["metadata"]:
                    groups[f"{field}={record['metadata'][field]}"].append(record)
        summary["modes"][mode] = {
            "n": len(records), "errors": errors,
            "correct": sum(bool(r["correct"]) and not r["error"] for r in records),
            "accuracy": None if errors else statistics.mean(r["correct"] for r in records),
            "invalid_answers": sum(not r["predicted_option"] and not r["error"] for r in records),
            "mean_memory_tokens": statistics.mean(r["memory_tokens"] for r in records),
            "nonempty_contexts": sum(r["memory_tokens"] > 0 for r in records),
            "groups": {k: {"n": len(v), "accuracy": None if any(r["error"] for r in v)
                           else statistics.mean(r["correct"] for r in v)} for k, v in groups.items()},
        }
    for baseline in ("none", "episode_rag", "recollection_only"):
        differences = defaultdict(list)
        failed = False
        for persona, query in sorted(expected):
            full = keyed[persona, query, "full_core"]
            base = keyed[persona, query, baseline]
            failed |= bool(full["error"] or base["error"])
            differences[persona].append(int(full["correct"]) - int(base["correct"]))
        values = [statistics.mean(v) for v in differences.values()]
        rng = random.Random(0)
        samples = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(bootstrap_samples))
        summary["paired"][f"full_core-minus-{baseline}"] = {
            "difference": None if failed else statistics.mean(values),
            "ci95": None if failed else [samples[int(bootstrap_samples * .025)], samples[int(bootstrap_samples * .975)]],
        }
    return summary


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def verified_summary(output: Path, rows: list[dict], manifest: dict) -> dict:
    pairs = {(r["persona_id"], r["question_ref"]) for r in rows}
    if ({q for _, q in pairs} != set(manifest["questions"])
            or len(pairs) != len(manifest["questions"])
            or {p for p, _ in pairs} != set(manifest["personas"])):
        raise ValueError("results do not cover exactly the selected questions and personas")
    for persona in manifest["personas"]:
        path = output / f"memory-{persona}.json"
        if not path.is_file():
            raise ValueError(f"missing completed memory proof for persona {persona}")
        proof = json.loads(path.read_text())
        if (proof.get("persona") != persona or proof.get("test_phase_memory_unchanged") is not True
                or proof.get("consolidation", {}).get("pending_jobs") != 0
                or not isinstance(proof.get("memory"), dict)):
            raise ValueError(f"invalid completed memory proof for persona {persona}")
        before_path = output / f"memory-before-{persona}.json"
        if before_path.is_file() and json.loads(before_path.read_text())["memory"] != proof["memory"]:
            raise ValueError(f"memory proof differs from original snapshot for persona {persona}")
    return summarize(rows)


async def run_persona(harness, probe, persona, cases, history, output_dir: Path, rows: list[dict]):
    done = {r["question_ref"] for r in rows if r["persona_id"] == persona}
    before_path = output_dir / f"memory-before-{persona}.json"
    if before_path.exists():
        frozen = json.loads(before_path.read_text())
    else:
        if done:
            raise RuntimeError("cannot resume answers without the original memory snapshot")
        state = await harness.ingest(persona, history)
        frozen = {
            "persona": persona, "history_episodes": len(history.turns),
            "unanswered_history_recorded_without_episode": bool(history.trailing_user),
            "consolidation": state, "memory": probe.memory_state(harness.scope(persona)),
        }
        write_json(before_path, frozen)
        print(json.dumps({"persona": persona, "consolidated": state}), file=sys.stderr, flush=True)

    def verify_memory():
        if frozen["persona"] != persona or frozen["memory"] != probe.memory_state(harness.scope(persona)):
            raise RuntimeError("test queries changed learned memory; results are invalid")

    verify_memory()  # Also required when every answer was flushed before an interruption.
    with (output_dir / "results.jsonl").open("a", encoding="utf-8") as output:
        for question in cases:
            if question.question_ref in done:
                continue
            results = await harness.evaluate(question, history)
            for result in results:
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
            output.flush()
            rows.extend(results)
            print(json.dumps({"persona": persona, "question": question.question_ref,
                              "correct": {r["mode"]: r["correct"] for r in results},
                              "errors": sum(r["error"] is not None for r in results)}),
                  file=sys.stderr, flush=True)
    verify_memory()
    write_json(output_dir / f"memory-{persona}.json", frozen | {"test_phase_memory_unchanged": True})


async def run(args, questions, manifest):
    if sys.flags.hash_randomization != 0:
        raise ValueError("run with PYTHONHASHSEED=0 to fix the upstream option shuffle")
    from memory_core import AsyncMemoryClient
    import tiktoken
    from .personamem import PersonaMemHarness, wait_for_consolidation
    from .personamem_live import ChatTextModel, DatabaseProbe

    args.output.mkdir(parents=True, exist_ok=True)
    probe = DatabaseProbe(os.environ["MEMORY_EVAL_DATABASE_URL"])
    model = ChatTextModel(model=os.environ["MEMORY_EVAL_MODEL"],
                          api_key=os.environ["OPENAI_API_KEY"],
                          base_url=os.environ["OPENAI_BASE_URL"],
                          usage_file=args.output / "answer-usage.jsonl")
    encoding = tiktoken.get_encoding("cl100k_base")
    memory = AsyncMemoryClient.connect(os.environ["MEMORY_CORE_ENDPOINT"],
        token_provider=lambda _: os.environ["MEMORY_CORE_TOKEN"], default_timeout=30)

    async def settle(scope, count):
        return await wait_for_consolidation(lambda: probe.snapshot(scope), expected_episodes=count,
                                           timeout=args.settle_timeout)

    harness = PersonaMemHarness(memory=memory, model=model,
        scorer=load_official_mcq(args.data / "upstream/inference.py"), settle=settle,
        evaluation_ref=args.run_ref, token_count=lambda text: cl100k_token_count(encoding, text),
        memory_tokens=args.memory_tokens, max_output_tokens=args.output_tokens,
        batch_turns=args.batch_turns,
        episode_evidence_max_bytes=args.episode_evidence_max_bytes)
    config = manifest | {
        "evaluation_ref": args.run_ref, "model": os.environ["MEMORY_EVAL_MODEL"],
        "memory_tokens": args.memory_tokens, "output_tokens": args.output_tokens,
        "batch_turns": args.batch_turns,
        "episode_evidence_max_bytes": args.episode_evidence_max_bytes,
        "tokenizer": "cl100k_base", "pythonhashseed": 0,
        "rag": "existing reference character-trigram top-8 with complete-episode budget",
        "profile_policy": "official initial system profile shared by all modes; not learned",
        "learning_policy": "history-only; test SourceEvents unbound; no test Episodes or Outcomes",
        "memory_index": os.environ.get("MEMORY_EVAL_INDEX_DESCRIPTION", "not_configured"),
        "worker_model": os.environ["MEMORY_WORKER_OPENAI_MODEL"],
        "core_revision": os.environ["MEMORY_EVAL_CORE_REVISION"],
        "harness_sources": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                            for name in ("personamem.py", "personamem_data.py", "personamem_live.py", "personamem_runner.py")},
    }
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != config:
        raise ValueError("run manifest changed; use a new output directory and run ref")
    manifest_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    result_path = args.output / "results.jsonl"
    rows = [json.loads(line) for line in result_path.read_text().splitlines()] if result_path.exists() else []
    if rows:
        summarize(rows)  # Only resume complete four-mode question groups, never silently repair evidence.
    grouped = defaultdict(list)
    for question in questions:
        grouped[question.persona_id].append(question)
    try:
        for persona, cases in grouped.items():
            history = load_history(resolve_history_path(args.data, cases[0].history_path))
            await run_persona(harness, probe, persona, cases, history, args.output, rows)
    finally:
        await memory.close()
        await model.close()
    report = verified_summary(args.output, rows, config)
    write_json(args.output / "summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(any(r["error"] for r in rows))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "summarize"))
    parser.add_argument("--data", type=Path, default=Path(".cache/PersonaMem-v2"))
    parser.add_argument("--size", choices=("32k", "128k"), default="32k")
    parser.add_argument("--personas", type=int, default=20)
    parser.add_argument("--per-persona", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260905)
    parser.add_argument("--output", type=Path, default=Path(".cache/personamem-pilot"))
    parser.add_argument("--run-ref", default="personamem-pilot-20260905")
    parser.add_argument("--memory-tokens", type=int, default=2048)
    parser.add_argument("--episode-evidence-max-bytes", type=episode_evidence_budget, default=0)
    parser.add_argument("--output-tokens", type=int, default=1024)
    parser.add_argument("--batch-turns", type=int, default=28)
    parser.add_argument("--settle-timeout", type=float, default=900)
    args = parser.parse_args(argv)
    if args.command == "summarize":
        rows = [json.loads(line) for line in (args.output / "results.jsonl").read_text().splitlines()]
        manifest = json.loads((args.output / "manifest.json").read_text())
        print(json.dumps(verified_summary(args.output, rows, manifest), ensure_ascii=False, indent=2))
        return int(any(row["error"] for row in rows))
    questions, manifest = prepare(args)
    if args.command == "prepare":
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    return asyncio.run(run(args, questions, manifest))


if __name__ == "__main__":
    raise SystemExit(main())
