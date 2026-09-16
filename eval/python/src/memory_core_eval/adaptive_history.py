"""Frozen public-history extraction diagnostic, not QA or personality scoring."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from memory_core import AsyncMemoryClient
from .personamem import PersonaMemHarness, wait_for_consolidation
from .personamem_data import History, load_history, verify_sha256
from .personamem_live import DatabaseProbe


# Fixed before new outputs. No labels, background, questions, or options are used.
COHORT = (
    ("977", "631c9c50b1073cae1429f440dd2878f63bd371ef04c6092dabd4617cd705e2c5"),
    ("486", "5ab6a5a81170354b51491ab8ace8da4ecd256166eaa9f8d4521ba4bdb06a8b44"),
)


async def run(args):
    if not args.disposable_database:
        raise ValueError("explicit --disposable-database acknowledgement is required")
    args.output.mkdir(parents=True, exist_ok=False)
    selected = []
    for persona, digest in COHORT:
        path = args.data / f"data/chat_history_32k/chat_history_250913_163134_persona{persona}.json"
        verify_sha256(path, digest)
        original = load_history(path)
        selected.append((persona, digest, History(background="", turns=original.turns[:56])))
    manifest = {"evaluation_ref": args.evaluation_ref, "baseline": "UNKNOWN", "batch_turns": 28,
                "cohort": [{"persona": p, "sha256": d, "completed_turns": len(h.turns)} for p, d, h in selected],
                "scope": "public benchmark conversations; extraction only; no static QA or personality growth claim"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    probe = DatabaseProbe(args.database_url)
    memory = AsyncMemoryClient.connect(args.endpoint, token_provider=lambda _: "disposable-adaptive-diagnostic")

    async def settle(scope, expected):
        state = await wait_for_consolidation(lambda: probe.snapshot(scope), expected_episodes=expected, timeout=900)
        with (args.output / "batches.jsonl").open("a") as stream:
            stream.write(json.dumps({"relationship": scope.relationship_ref, "expected": expected,
                                     "state": state, "memory": probe.memory_state(scope)}, ensure_ascii=False) + "\n")
        print(json.dumps({"relationship": scope.relationship_ref, "expected": expected, "state": state}), flush=True)
        return state

    harness = PersonaMemHarness(memory=memory, model=None, scorer=None, settle=settle,
                               evaluation_ref=args.evaluation_ref, token_count=len, batch_turns=28)
    try:
        for persona, _, history in selected:
            scope = harness.scope(persona)
            if probe.snapshot(scope)["episodes"] != 0:
                raise ValueError("refusing to reuse a previously populated history namespace")
            await harness.ingest(persona, history)
            (args.output / f"memory-{persona}.json").write_text(json.dumps(probe.memory_state(scope), ensure_ascii=False, indent=2))
        tables = ("source_events", "episodes", "disposition_seeds", "seed_versions", "seed_basis_links",
                  "seed_outcome_basis_links", "recollections", "recollection_versions", "recollection_basis_links",
                  "memory_contexts", "memory_delivery_receipts", "outcome_events", "consolidation_receipts")
        counts = {table: int(probe.read(f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_ref=%s",
                                       (f"personamem-{args.evaluation_ref}",))[0]["n"]) for table in tables}
        (args.output / "counts.json").write_text(json.dumps(counts, indent=2))
    finally:
        await memory.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--evaluation-ref", required=True)
    parser.add_argument("--disposable-database", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
