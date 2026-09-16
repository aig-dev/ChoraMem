#!/usr/bin/env python3
"""Validate and summarize the one explicitly accepted PersonaMem cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import AbstractSet

from memory_core_eval.personamem_effect_runner import (
    EffectMode,
    summarize_effect_results,
)


MANIFEST_SHA256 = "8612287421f11e000e1220d5732c78c6c81eb713717b7150742615bf4fbefbd4"
ACCEPTED_MISSING = frozenset(
    {
        "row-00361",
        "row-00362",
        "row-00364",
        "row-00519",
        "row-00815",
        "row-01007",
        "row-01232",
        "row-01756",
        "row-01976",
    }
)
DEFAULT_INPUT = Path(".cache/personamem-effect-minilm/validation-all-v1")
DEFAULT_OUTPUT = Path(
    ".cache/personamem-effect-minilm/accepted-validation-baseline-v1/summary.json"
)


def summarize_accepted_validation(
    input_dir: Path,
    *,
    expected_manifest_sha256: str = MANIFEST_SHA256,
    accepted_missing: AbstractSet[str] = ACCEPTED_MISSING,
    bootstrap_samples: int = 2_000,
) -> dict[str, object]:
    """Fail closed on cohort drift, then call the frozen result summarizer."""

    input_dir = Path(input_dir)
    manifest_path = input_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    actual_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_digest != expected_manifest_sha256:
        raise ValueError(
            f"manifest SHA256 mismatch: expected {expected_manifest_sha256}, got {actual_digest}"
        )

    manifest = json.loads(manifest_bytes)
    expected_questions = manifest.get("questions")
    owners = manifest.get("question_personas")
    expected_modes = [mode.value for mode in EffectMode]
    if not isinstance(expected_questions, list) or not all(
        isinstance(ref, str) and ref for ref in expected_questions
    ):
        raise ValueError("manifest questions must be a list of nonblank identities")
    if len(set(expected_questions)) != len(expected_questions):
        raise ValueError("manifest contains duplicate question identities")
    if not isinstance(owners, dict) or set(owners) != set(expected_questions):
        raise ValueError("manifest question owner mapping is incomplete")
    if manifest.get("modes") != expected_modes:
        raise ValueError("manifest does not declare the exact six effect modes")

    results: list[dict[str, object]] = []
    seen: set[str] = set()
    input_results_digest = hashlib.sha256()
    for path in sorted((input_dir / "questions").glob("*.json")):
        result_bytes = path.read_bytes()
        input_results_digest.update(path.name.encode())
        input_results_digest.update(b"\0")
        input_results_digest.update(result_bytes)
        input_results_digest.update(b"\0")
        result = json.loads(result_bytes)
        question_ref = result.get("question_ref")
        if not isinstance(question_ref, str) or not question_ref:
            raise ValueError(f"result has invalid question identity: {path}")
        if question_ref in seen:
            raise ValueError(f"duplicate question result: {question_ref}")
        seen.add(question_ref)
        if path.stem != question_ref:
            raise ValueError(f"result filename does not match question identity: {path}")
        if question_ref not in owners:
            raise ValueError(f"result question is absent from manifest: {question_ref}")
        if str(result.get("persona_id")) != str(owners[question_ref]):
            raise ValueError(f"result owner does not match manifest: {question_ref}")
        results.append(result)

    expected_set = set(expected_questions)
    unexpected = seen - expected_set
    missing = expected_set - seen
    if unexpected:
        raise ValueError(f"unexpected completed questions: {sorted(unexpected)}")
    if missing != set(accepted_missing):
        raise ValueError(
            "missing question set differs from the explicitly accepted cohort: "
            f"expected {sorted(accepted_missing)}, got {sorted(missing)}"
        )

    summary = summarize_effect_results(results, bootstrap_samples=bootstrap_samples)
    return {
        "cohort": {
            "manifest_sha256": actual_digest,
            "input_results_sha256": input_results_digest.hexdigest(),
            "expected_questions": len(expected_questions),
            "accepted_questions": len(results),
            "coverage": len(results) / len(expected_questions),
            "missing_questions": sorted(missing),
        },
        "summary": summary,
    }


def write_accepted_summary(
    input_dir: Path,
    output: Path,
    *,
    expected_manifest_sha256: str = MANIFEST_SHA256,
    accepted_missing: AbstractSet[str] = ACCEPTED_MISSING,
    bootstrap_samples: int = 2_000,
) -> None:
    """Write a derived summary without allowing either write path into the source."""

    resolved_input = Path(input_dir).resolve()
    resolved_output = Path(output).resolve()
    if resolved_output == resolved_input or resolved_output.is_relative_to(resolved_input):
        raise ValueError("output must remain outside the input directory")

    report = summarize_accepted_validation(
        input_dir,
        expected_manifest_sha256=expected_manifest_sha256,
        accepted_missing=accepted_missing,
        bootstrap_samples=bootstrap_samples,
    )
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=Path(output).parent,
            prefix=f".{Path(output).name}.",
            suffix=".part",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(report, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
        temporary_path.replace(output)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_accepted_summary(args.input_dir, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
