from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from memory_core_eval import personamem_runner as runner


def answer_rows():
    return [dict(persona_id="p", question_ref="q", mode=mode, correct=True,
                 error=None, predicted_option="A", memory_tokens=0, metadata={})
            for mode in ("none", "episode_rag", "recollection_only", "full_core")]


class Probe:
    def __init__(self):
        self.state = {"recollections": [], "dispositions": [], "basis": {}, "jobs": []}

    def memory_state(self, scope):
        return copy.deepcopy(self.state)


class Harness:
    def __init__(self, probe, *, mutate=False):
        self.probe, self.mutate = probe, mutate

    def scope(self, persona):
        return persona

    async def ingest(self, persona, history):
        return {"episodes": 2, "jobs": 1, "pending_jobs": 0}

    async def evaluate(self, question, history):
        if self.mutate:
            self.probe.state["recollections"].append({"ref": "test-leak"})
        return answer_rows()


@pytest.mark.asyncio
async def test_resume_cannot_accept_complete_answers_after_memory_check_failed(tmp_path):
    probe = Probe()
    rows = []
    cases = [SimpleNamespace(question_ref="q")]
    history = SimpleNamespace(turns=(1, 2), trailing_user="")
    with pytest.raises(RuntimeError, match="changed learned memory"):
        await runner.run_persona(Harness(probe, mutate=True), probe, "p", cases,
                                 history, tmp_path, rows)
    assert len(rows) == 4
    assert not (tmp_path / "memory-p.json").exists()
    frozen = json.loads((tmp_path / "memory-before-p.json").read_text())
    assert frozen["memory"]["recollections"] == []
    with pytest.raises(RuntimeError, match="changed learned memory"):
        await runner.run_persona(Harness(probe), probe, "p", cases, history, tmp_path, rows)
    assert len((tmp_path / "results.jsonl").read_text().splitlines()) == 4


@pytest.mark.asyncio
async def test_resume_requires_original_snapshot_even_when_all_answers_exist(tmp_path):
    probe = Probe()
    with pytest.raises(RuntimeError, match="original memory snapshot"):
        await runner.run_persona(Harness(probe), probe, "p", [SimpleNamespace(question_ref="q")],
                                 SimpleNamespace(turns=(1, 2), trailing_user=""), tmp_path, answer_rows())
    assert not (tmp_path / "memory-p.json").exists()


@pytest.mark.asyncio
async def test_resume_revalidates_flushed_answers_without_generating_them_again(tmp_path):
    probe = Probe()
    rows = answer_rows()
    frozen = {"persona": "p", "history_episodes": 2,
              "unanswered_history_recorded_without_episode": False,
              "consolidation": {"episodes": 2, "jobs": 1, "pending_jobs": 0},
              "memory": probe.memory_state("p")}
    (tmp_path / "memory-before-p.json").write_text(json.dumps(frozen))
    original_results = "".join(json.dumps(row) + "\n" for row in rows)
    (tmp_path / "results.jsonl").write_text(original_results)

    class NoGeneration(Harness):
        async def ingest(self, *args):
            raise AssertionError("a frozen persona must not be ingested again")

        async def evaluate(self, *args):
            raise AssertionError("completed questions must not be regenerated")

    await runner.run_persona(NoGeneration(probe), probe, "p", [SimpleNamespace(question_ref="q")],
                             SimpleNamespace(turns=(1, 2), trailing_user=""), tmp_path, rows)
    assert (tmp_path / "results.jsonl").read_text() == original_results
    evidence = json.loads((tmp_path / "memory-p.json").read_text())
    assert evidence["test_phase_memory_unchanged"] is True
    assert evidence["memory"] == frozen["memory"]


def test_published_summary_requires_every_selected_question_and_memory_proof(tmp_path):
    rows = answer_rows()
    manifest = {"questions": ["q"], "personas": ["p"]}
    with pytest.raises(ValueError, match="memory proof"):
        runner.verified_summary(tmp_path, rows, manifest)
    proof = {"persona": "p", "test_phase_memory_unchanged": True,
             "consolidation": {"pending_jobs": 0}, "memory": Probe().state}
    (tmp_path / "memory-p.json").write_text(json.dumps(proof))
    assert runner.verified_summary(tmp_path, rows, manifest)["questions"] == 1
    with pytest.raises(ValueError, match="selected questions"):
        runner.verified_summary(tmp_path, rows, manifest | {"questions": ["q", "missing"]})
    proof["test_phase_memory_unchanged"] = False
    (tmp_path / "memory-p.json").write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="memory proof"):
        runner.verified_summary(tmp_path, rows, manifest)
