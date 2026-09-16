import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MODES = (
    "none",
    "full_history",
    "oracle_episode",
    "semantic_top40",
    "current_core",
    "learned_core",
)


def _load_module():
    path = Path(__file__).parents[1] / "scripts" / "summarize_accepted_validation.py"
    spec = importlib.util.spec_from_file_location("summarize_accepted_validation", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _summarize_accepted_validation():
    return _load_module().summarize_accepted_validation


def _result(question_ref="row-00000", persona_id="7"):
    answers = [
        {
            "question_ref": question_ref,
            "persona_id": persona_id,
            "mode": mode,
            "oracle_label_used": mode == "oracle_episode",
            "correct": mode in {"oracle_episode", "semantic_top40"},
            "predicted_option": "A",
            "memory_tokens": 0,
            "error": None,
            "metadata": {"pref_type": "neutral_preferences"},
        }
        for mode in MODES
    ]
    return {
        "question_ref": question_ref,
        "persona_id": persona_id,
        "answers": answers,
        "diagnostic": {
            "question_ref": question_ref,
            "persona_id": persona_id,
            "mode": "current_core",
            "source_available": True,
            "indexed": True,
            "selected": False,
            "delivered": False,
            "answer_correct": False,
            "learned_basis_selected": False,
            "learned_basis_delivered": False,
        },
    }


def _write_fixture(tmp_path, *, missing=("row-00002",)):
    root = tmp_path / "run"
    questions_dir = root / "questions"
    questions_dir.mkdir(parents=True)
    questions = ["row-00000", "row-00001", *missing]
    manifest = {
        "questions": questions,
        "question_personas": {question: "7" for question in questions},
        "modes": list(MODES),
        "bootstrap_samples": 2000,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (root / "manifest.json").write_bytes(manifest_bytes)
    for question in questions[:2]:
        (questions_dir / f"{question}.json").write_text(
            json.dumps(_result(question)), encoding="utf-8"
        )
    return root, hashlib.sha256(manifest_bytes).hexdigest()


def test_accepted_summary_validates_frozen_cohort_then_uses_existing_summary(tmp_path):
    summarize_accepted_validation = _summarize_accepted_validation()

    root, digest = _write_fixture(tmp_path)
    report = summarize_accepted_validation(
        root,
        expected_manifest_sha256=digest,
        accepted_missing=frozenset({"row-00002"}),
        bootstrap_samples=10,
    )

    assert report["cohort"]["manifest_sha256"] == digest
    assert report["cohort"]["input_results_sha256"] == (
        "41823458357fb6fc00e28c6f49f73e4b9e83927e5fa99ece4d0abaf64a7d9cdf"
    )
    assert report["cohort"]["expected_questions"] == 3
    assert report["cohort"]["accepted_questions"] == 2
    assert report["cohort"]["coverage"] == pytest.approx(2 / 3)
    assert report["cohort"]["missing_questions"] == ["row-00002"]
    assert report["summary"]["questions"] == 2
    assert report["summary"]["personas"] == 1
    assert report["summary"]["modes"]["oracle_episode"]["correct"] == 2


@pytest.mark.parametrize("mutation", ["extra_missing", "wrong_owner", "missing_group"])
def test_accepted_summary_rejects_cohort_drift(tmp_path, mutation):
    summarize_accepted_validation = _summarize_accepted_validation()

    root, digest = _write_fixture(tmp_path)
    if mutation == "extra_missing":
        (root / "questions" / "row-00001.json").unlink()
    elif mutation == "wrong_owner":
        path = root / "questions" / "row-00001.json"
        row = json.loads(path.read_text())
        row["persona_id"] = "8"
        for answer in row["answers"]:
            answer["persona_id"] = "8"
        row["diagnostic"]["persona_id"] = "8"
        path.write_text(json.dumps(row))
    else:
        path = root / "questions" / "row-00001.json"
        row = json.loads(path.read_text())
        row["answers"].pop()
        path.write_text(json.dumps(row))

    with pytest.raises(ValueError):
        summarize_accepted_validation(
            root,
            expected_manifest_sha256=digest,
            accepted_missing=frozenset({"row-00002"}),
            bootstrap_samples=10,
        )


def test_accepted_summary_rejects_duplicate_question_identity(tmp_path):
    summarize_accepted_validation = _summarize_accepted_validation()

    root, digest = _write_fixture(tmp_path)
    duplicate = copy.deepcopy(_result("row-00000"))
    (root / "questions" / "row-00001.json").write_text(json.dumps(duplicate))

    with pytest.raises(ValueError, match="duplicate question"):
        summarize_accepted_validation(
            root,
            expected_manifest_sha256=digest,
            accepted_missing=frozenset({"row-00002"}),
            bootstrap_samples=10,
        )


@pytest.mark.parametrize("relative_output", ["manifest.json", "questions/report.json"])
def test_writer_rejects_output_inside_source_directory(tmp_path, relative_output):
    module = _load_module()
    root, digest = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="outside the input directory"):
        module.write_accepted_summary(
            root,
            root / relative_output,
            expected_manifest_sha256=digest,
            accepted_missing=frozenset({"row-00002"}),
            bootstrap_samples=10,
        )


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_writer_ignores_old_static_scratch_links_without_changing_source(
    tmp_path, link_kind
):
    module = _load_module()
    root, digest = _write_fixture(tmp_path)
    output = tmp_path / "derived" / "summary.json"
    output.parent.mkdir()
    source = root / "manifest.json"
    source_before = source.read_bytes()
    old_scratch = output.with_suffix(".json.part")
    if link_kind == "symlink":
        old_scratch.symlink_to(source)
    else:
        old_scratch.hardlink_to(source)

    module.write_accepted_summary(
        root,
        output,
        expected_manifest_sha256=digest,
        accepted_missing=frozenset({"row-00002"}),
        bootstrap_samples=10,
    )

    assert source.read_bytes() == source_before
    assert json.loads(output.read_text())["summary"]["questions"] == 2
