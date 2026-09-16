from __future__ import annotations

import hashlib
import io
import json
import sys
import types
import urllib.error
from types import SimpleNamespace

import pytest


def test_download_checks_integrity_before_publishing_cache_file(tmp_path, monkeypatch):
    from memory_core_eval.personamem_runner import fetch

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(b"official data"))
    destination = tmp_path / "data.csv"
    fetch("https://example.test/data", destination, hashlib.sha256(b"official data").hexdigest())
    assert destination.read_bytes() == b"official data"
    bad = tmp_path / "bad.csv"
    with pytest.raises(ValueError, match="SHA-256"):
        fetch("https://example.test/data", bad, "0" * 64)
    assert not bad.exists()


def test_download_retries_transient_network_failure(tmp_path, monkeypatch):
    from memory_core_eval.personamem_runner import fetch

    calls = 0

    def open_with_transient_failure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("temporary TLS EOF")
        return io.BytesIO(b"official data")

    monkeypatch.setattr("urllib.request.urlopen", open_with_transient_failure)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    destination = tmp_path / "data.csv"
    fetch("https://example.test/data", destination,
          hashlib.sha256(b"official data").hexdigest())
    assert destination.read_bytes() == b"official data"
    assert calls == 2


def test_summary_pairs_same_questions_and_exposes_failures():
    from memory_core_eval.personamem_runner import summarize

    rows = []
    for persona in ("p1", "p2"):
        for mode, correct in (("none", False), ("episode_rag", False),
                              ("recollection_only", True), ("full_core", True)):
            rows.append(dict(persona_id=persona, question_ref=persona, mode=mode,
                correct=correct, error=None, predicted_option="A", memory_tokens=50, metadata={}))
    result = summarize(rows, bootstrap_samples=100)
    assert result["modes"]["full_core"]["accuracy"] == 1
    assert result["paired"]["full_core-minus-none"]["difference"] == 1
    assert result["paired"]["full_core-minus-recollection_only"]["difference"] == 0
    rows[-1]["error"] = "network failure"
    rows[-1]["correct"] = False
    result = summarize(rows, bootstrap_samples=100)
    assert result["modes"]["full_core"]["errors"] == 1
    assert result["modes"]["full_core"]["accuracy"] is None
    assert result["paired"]["full_core-minus-none"]["difference"] is None


def test_summary_rejects_duplicate_or_unpaired_results():
    from memory_core_eval.personamem_runner import summarize

    row = dict(persona_id="p", question_ref="q", mode="none", correct=False,
               error=None, predicted_option="A", memory_tokens=0, metadata={})
    with pytest.raises(ValueError, match="duplicate"):
        summarize([row, row])
    with pytest.raises(ValueError, match="paired"):
        summarize([row])


def test_manifest_selection_is_fixed_without_loading_gold_into_history(tmp_path, monkeypatch):
    from memory_core_eval.personamem_runner import manifest_for
    from memory_core_eval.personamem_data import History, Question
    from memory_core_eval.reference import HistoricalTurn

    q = Question("q", "p", "data/chat_history_32k/p.json", "Query", "Gold", ("a", "b", "c"), {})
    history_file = tmp_path / q.history_path
    history_file.parent.mkdir(parents=True)
    history_file.write_text("downloaded original")
    manifest = manifest_for(tmp_path, (q,), size="32k", seed=17)
    assert manifest["questions"] == ["q"]
    assert manifest["histories"][q.history_path] == hashlib.sha256(b"downloaded original").hexdigest()
    assert "Gold" not in json.dumps(manifest)


@pytest.mark.parametrize("cached_content", ["official preference", "modified preference"])
def test_prepare_checks_existing_history_against_pinned_upstream_blob(tmp_path, monkeypatch, cached_content):
    from memory_core_eval import personamem_runner as runner
    from memory_core_eval.personamem_data import Question

    link = "data/chat_history_32k/p.json"
    question = Question("q", "p", link, "Query", "Gold", ("a", "b", "c"), {})

    def history_bytes(content):
        return json.dumps({"chat_history": [{"role": "user", "content": content},
                                           {"role": "assistant", "content": "Observed answer"}]}).encode()

    official = history_bytes("official preference")
    oid = hashlib.sha1(f"blob {len(official)}\0".encode() + official).hexdigest()
    metadata = [{"type": "file", "path": link, "oid": oid, "size": len(official)}]
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(json.dumps(metadata).encode()))
    for relative, value, constant in (("benchmark/text/benchmark.csv", b"csv", "BENCHMARK_SHA256"),
                                      ("upstream/inference.py", b"source", "INFERENCE_SHA256")):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        monkeypatch.setattr(runner, constant, hashlib.sha256(value).hexdigest())
    cached = tmp_path / link
    cached.parent.mkdir(parents=True)
    cached.write_bytes(history_bytes(cached_content))
    monkeypatch.setattr(runner, "load_questions", lambda *a, **k: (question,))
    args = SimpleNamespace(data=tmp_path, size="32k", personas=1, per_persona=1, seed=1)

    if cached_content == "modified preference":
        with pytest.raises(ValueError, match="upstream Git blob"):
            runner.prepare(args)
        assert not (tmp_path / "selection-32k-1-1-1.json").exists()
    else:
        questions, manifest = runner.prepare(args)
        assert questions == (question,)
        assert manifest["histories"][link] == hashlib.sha256(official).hexdigest()


@pytest.mark.parametrize("value", [0, 16_384])
def test_cli_accepts_public_episode_evidence_budget(monkeypatch, value):
    from memory_core_eval import personamem_runner as runner

    captured = []
    monkeypatch.setattr(
        runner,
        "prepare",
        lambda args: (captured.append(args.episode_evidence_max_bytes) or (), {}),
    )

    assert runner.main(["prepare", "--episode-evidence-max-bytes", str(value)]) == 0
    assert captured == [value]


@pytest.mark.parametrize("value", ["-1", "16385", "1.5"])
def test_cli_rejects_invalid_episode_evidence_budget(value):
    from memory_core_eval import personamem_runner as runner

    with pytest.raises(SystemExit):
        runner.main(["prepare", "--episode-evidence-max-bytes", value])


def test_cl100k_counter_treats_special_token_looking_source_as_plain_text():
    from memory_core_eval.personamem_runner import cl100k_token_count

    calls = []

    class Encoding:
        def encode(self, text, *, disallowed_special):
            calls.append((text, disallowed_special))
            return [1, 2, 3]

    assert cl100k_token_count(Encoding(), "history says <|endoftext|>") == 3
    assert calls == [("history says <|endoftext|>", ())]


@pytest.mark.asyncio
async def test_run_manifest_freezes_episode_evidence_budget(tmp_path, monkeypatch):
    from memory_core import AsyncMemoryClient
    from memory_core_eval import personamem_runner as runner

    class Memory:
        async def close(self):
            pass

    class Model:
        def __init__(self, **_kwargs):
            pass

        async def close(self):
            pass

    fake_live = types.ModuleType("memory_core_eval.personamem_live")
    fake_live.ChatTextModel = Model
    fake_live.DatabaseProbe = lambda _url: object()
    monkeypatch.setitem(sys.modules, "memory_core_eval.personamem_live", fake_live)
    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        SimpleNamespace(get_encoding=lambda _name: SimpleNamespace(encode=lambda *_a, **_k: [])),
    )
    monkeypatch.setattr(AsyncMemoryClient, "connect", lambda *_a, **_k: Memory())
    monkeypatch.setattr(runner, "load_official_mcq", lambda _path: object())
    monkeypatch.setattr(runner, "verified_summary", lambda *_a, **_k: {})
    original_flags = sys.flags

    class Flags:
        hash_randomization = 0

        def __getattr__(self, name):
            return getattr(original_flags, name)

    monkeypatch.setattr(runner.sys, "flags", Flags())
    for name, value in {
        "MEMORY_EVAL_DATABASE_URL": "mysql://eval",
        "MEMORY_EVAL_MODEL": "model",
        "OPENAI_API_KEY": "key",
        "OPENAI_BASE_URL": "https://example.test",
        "MEMORY_CORE_ENDPOINT": "http://127.0.0.1:1",
        "MEMORY_CORE_TOKEN": "token",
        "MEMORY_WORKER_OPENAI_MODEL": "worker",
        "MEMORY_EVAL_CORE_REVISION": "revision",
    }.items():
        monkeypatch.setenv(name, value)

    def args(episode_evidence_max_bytes):
        return SimpleNamespace(
            output=tmp_path,
            run_ref="run",
            data=tmp_path,
            settle_timeout=1,
            memory_tokens=2_048,
            output_tokens=1_024,
            batch_turns=28,
            episode_evidence_max_bytes=episode_evidence_max_bytes,
        )

    assert await runner.run(args(0), (), {"questions": [], "personas": []}) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["episode_evidence_max_bytes"] == 0

    with pytest.raises(ValueError, match="run manifest changed"):
        await runner.run(args(1_024), (), {"questions": [], "personas": []})
