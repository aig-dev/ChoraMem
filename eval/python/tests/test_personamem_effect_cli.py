from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from memory_core_eval.personamem_data import Question
from memory_core_eval.personamem_effect import EffectMode


def question(ref="row-00000", persona="7"):
    return Question(
        ref,
        persona,
        "data/chat_history_32k/persona7.json",
        "query",
        "gold",
        ("a", "b", "c"),
        {"pref_type": "neutral_preferences"},
    )


def test_persona_holdout_selects_complete_users_by_stable_hash():
    from memory_core_eval.personamem_effect_cli import select_persona_holdout

    questions = (
        question(ref="q-p1-a", persona="p1"),
        question(ref="q-p2-a", persona="p2"),
        question(ref="q-p3-a", persona="p3"),
        question(ref="q-p1-b", persona="p1"),
        question(ref="q-p2-b", persona="p2"),
        question(ref="q-p3-b", persona="p3"),
    )

    selected = select_persona_holdout(questions, limit=2, salt="heldout-v1")

    assert [item.question_ref for item in selected] == [
        "q-p2-a", "q-p3-a", "q-p2-b", "q-p3-b"
    ]
    assert {item.persona_id for item in selected} == {"p2", "p3"}


@pytest.mark.parametrize(
    ("limit", "salt", "message"),
    [(0, "heldout-v1", "positive"), (4, "heldout-v1", "exceeds"), (1, "", "salt")],
)
def test_persona_holdout_rejects_invalid_selection(limit, salt, message):
    from memory_core_eval.personamem_effect_cli import select_persona_holdout

    with pytest.raises(ValueError, match=message):
        select_persona_holdout(
            (question(ref="q1", persona="p1"), question(ref="q2", persona="p2")),
            limit=limit,
            salt=salt,
        )


def test_validation_manifest_freezes_sources_without_gold(tmp_path):
    from memory_core_eval.personamem_effect_cli import manifest_for_validation

    history = tmp_path / "data/chat_history_32k/persona7.json"
    history.parent.mkdir(parents=True)
    history.write_text('{"chat_history": []}')
    manifest = manifest_for_validation(tmp_path, (question(),), question_limit=0)
    encoded = json.dumps(manifest)
    assert manifest["split"] == "validation"
    assert manifest["questions"] == ["row-00000"]
    assert manifest["question_personas"] == {"row-00000": "7"}
    assert manifest["modes"] == [mode.value for mode in EffectMode]
    assert "gold" not in encoded
    assert manifest["histories"][question().history_path]


def test_validation_manifest_freezes_persona_holdout_identity(tmp_path):
    from memory_core_eval.personamem_effect_cli import manifest_for_validation

    item = question()
    history = tmp_path / item.history_path
    history.parent.mkdir(parents=True)
    history.write_text('{"chat_history": []}')

    manifest = manifest_for_validation(
        tmp_path,
        (item,),
        question_limit=0,
        persona_limit=1,
        persona_sample_salt="learned-core-heldout-a",
    )

    assert manifest["persona_limit"] == 1
    assert manifest["persona_sample_salt"] == "learned-core-heldout-a"


def test_validation_manifest_rejects_one_persona_with_multiple_histories(tmp_path):
    from memory_core_eval.personamem_effect_cli import manifest_for_validation

    first = question(ref="row-00000", persona="7")
    second = Question(
        "row-00001",
        "7",
        "data/chat_history_32k/persona7-other.json",
        "query",
        "gold",
        ("a", "b", "c"),
        {"pref_type": "neutral_preferences"},
    )
    for item in (first, second):
        history = tmp_path / item.history_path
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text('{"chat_history": []}')

    with pytest.raises(ValueError, match="multiple histories"):
        manifest_for_validation(tmp_path, (first, second), question_limit=0)


def test_runtime_manifest_freezes_all_execution_controls():
    from memory_core_eval.personamem_effect_cli import runtime_controls

    args = SimpleNamespace(
        memory_tokens=2_048,
        episode_evidence_max_bytes=16_384,
        output_tokens=1_024,
        batch_turns=28,
        settle_timeout=900.0,
        rpc_timeout=30.0,
        index_timeout=45.0,
        persona_workers=3,
        rpc_attempts=5,
    )
    assert runtime_controls(args, bootstrap_samples=2_000) == {
        "core_memory_tokens": 2_048,
        "episode_evidence_max_bytes": 16_384,
        "max_output_tokens": 1_024,
        "batch_turns": 28,
        "settle_timeout_seconds": 900.0,
        "rpc_timeout_seconds": 30.0,
        "index_timeout_seconds": 45.0,
        "persona_workers": 3,
        "rpc_attempts": 5,
        "bootstrap_samples": 2_000,
        "answer_arm_order": "sha256-interleaved-v1",
    }


@pytest.mark.asyncio
async def test_persona_runner_bounds_live_work_and_processes_every_persona():
    from memory_core_eval.personamem_effect_cli import run_personas_bounded

    release = asyncio.Event()
    active = 0
    peak = 0
    started = []
    completed = []

    async def process(persona, cases):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append((persona, cases))
        await release.wait()
        completed.append(persona)
        active -= 1

    task = asyncio.create_task(run_personas_bounded(
        {"p1": ["q1"], "p2": ["q2"], "p3": ["q3"], "p4": ["q4"]},
        limit=2,
        process=process,
    ))
    for _ in range(100):
        if len(started) == 2:
            break
        await asyncio.sleep(0)

    assert started == [("p1", ["q1"]), ("p2", ["q2"])]
    assert peak == 2
    assert not task.done()

    release.set()
    await task
    assert sorted(completed) == ["p1", "p2", "p3", "p4"]
    assert peak == 2


@pytest.mark.asyncio
async def test_persona_runner_rejects_nonpositive_limit():
    from memory_core_eval.personamem_effect_cli import run_personas_bounded

    async def process(_persona, _cases):
        raise AssertionError("invalid concurrency reached work")

    with pytest.raises(ValueError, match="positive"):
        await run_personas_bounded({"p1": ["q1"]}, limit=0, process=process)


def test_verified_summary_requires_every_frozen_question_and_persona_proof(tmp_path):
    from test_personamem_effect_runner import valid_question_result
    from memory_core_eval.personamem_effect_cli import verified_effect_summary

    manifest = {
        "questions": ["q1"], "personas": ["p1"],
        "question_personas": {"q1": "p1"},
    }
    question_dir = tmp_path / "questions"
    question_dir.mkdir()
    (question_dir / "q1.json").write_text(json.dumps(valid_question_result()))
    (tmp_path / "memory-p1.json").write_text(json.dumps({
        "persona": "p1",
        "test_phase_memory_unchanged": True,
        "projection_pending": 0,
    }))
    assert verified_effect_summary(tmp_path, manifest, bootstrap_samples=10)["questions"] == 1

    (question_dir / "q1.json").unlink()
    with pytest.raises(ValueError, match="exactly"):
        verified_effect_summary(tmp_path, manifest, bootstrap_samples=10)


def test_verified_summary_rejects_extra_question_or_unfrozen_memory(tmp_path):
    from test_personamem_effect_runner import valid_question_result
    from memory_core_eval.personamem_effect_cli import verified_effect_summary

    manifest = {
        "questions": ["q1"], "personas": ["p1"],
        "question_personas": {"q1": "p1"},
    }
    question_dir = tmp_path / "questions"
    question_dir.mkdir()
    for ref in ("q1", "extra"):
        (question_dir / f"{ref}.json").write_text(
            json.dumps(valid_question_result(ref=ref))
        )
    (tmp_path / "memory-p1.json").write_text(json.dumps({
        "persona": "p1",
        "test_phase_memory_unchanged": False,
        "projection_pending": 1,
    }))
    with pytest.raises(ValueError, match="exactly"):
        verified_effect_summary(tmp_path, manifest, bootstrap_samples=10)


def test_hf_download_mirror_does_not_redirect_metadata_post(monkeypatch):
    from memory_core_eval.personamem_effect_cli import hf_endpoints

    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com/")
    monkeypatch.delenv("HF_API_ENDPOINT", raising=False)
    assert hf_endpoints() == (
        "https://hf-mirror.com",
        "https://huggingface.co",
    )


def test_prepare_reuses_complete_frozen_validation_without_network(tmp_path, monkeypatch):
    from memory_core_eval import personamem_effect_cli as cli

    item = question()
    history = tmp_path / item.history_path
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({"chat_history": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]}))
    frozen = cli.manifest_for_validation(
        tmp_path,
        (item,),
        question_limit=0,
    )
    (tmp_path / "selection-validation-all.json").write_text(json.dumps(frozen))

    monkeypatch.setattr(cli, "VALIDATION_ROWS", 1)
    monkeypatch.setattr(cli, "fetch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "load_questions", lambda *_args, **_kwargs: (item,))
    monkeypatch.setattr(
        cli,
        "load_oracle_evidence",
        lambda *_args, **_kwargs: {
            item.question_ref: SimpleNamespace(persona_id=item.persona_id)
        },
    )
    monkeypatch.setattr(
        cli,
        "_history_metadata",
        lambda *_args, **_kwargs: pytest.fail("network metadata was requested"),
    )

    questions, _labels, manifest = cli.prepare(
        SimpleNamespace(data=tmp_path, question_limit=0)
    )

    assert questions == (item,)
    assert manifest == frozen


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--settle-timeout", "0"),
        ("--rpc-timeout", "-1"),
        ("--index-timeout", "nan"),
        ("--index-timeout", "inf"),
    ],
)
def test_cli_rejects_nonpositive_or_nonfinite_timeouts(monkeypatch, flag, value):
    from memory_core_eval import personamem_effect_cli as cli

    monkeypatch.setattr(cli, "prepare", lambda _args: pytest.fail("prepare was reached"))
    with pytest.raises(SystemExit):
        cli.main(["prepare", flag, value])


def test_summarize_uses_frozen_bootstrap_count_and_rejects_drift(tmp_path, monkeypatch):
    from memory_core_eval import personamem_effect_cli as cli

    (tmp_path / "manifest.json").write_text(json.dumps({"bootstrap_samples": 37}))
    calls = []
    monkeypatch.setattr(
        cli,
        "verified_effect_summary",
        lambda output, manifest, *, bootstrap_samples: (
            calls.append((output, manifest, bootstrap_samples)) or {"questions": 1}
        ),
    )

    assert cli.main(["summarize", "--output", str(tmp_path)]) == 0
    assert calls[-1][2] == 37
    with pytest.raises(ValueError, match="bootstrap_samples"):
        cli.main([
            "summarize",
            "--output",
            str(tmp_path),
            "--bootstrap-samples",
            "38",
        ])
