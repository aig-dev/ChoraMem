"""Observable Harness boundaries with real typed SDK values and no LLM replay."""
from __future__ import annotations

import copy
import importlib
import importlib.util
from pathlib import Path

import pytest

from memory_core import memory_pb2 as pb
from memory_core_eval.memory_effect_data import load_memory_effect


DATA = load_memory_effect(Path(__file__).parents[3] / "eval/cases/memory-effect-causal-v1.json")


def _api():
    assert importlib.util.find_spec("memory_core_eval.memory_effect_runner") is not None, "missing actual four-arm runner"
    return importlib.import_module("memory_core_eval.memory_effect_runner")


class MemoryBoundary:
    """External RPC double; all rendering, hashes and validation remain real."""
    def __init__(self, drift=False):
        self.events = []
        self.observed = []
        self.deliveries = []
        self.outcomes = []
        self.selected = []
        self.drift = drift

    async def observe_source_event(self, request):
        self.observed.append(request)
        self.events.append("observe")
        return pb.SourceEventReceipt(source_event_ref=request.source_event.source_ref, episode_ref="ep:" + request.episode_binding.run_ref if request.HasField("episode_binding") else "")

    async def report_outcome(self, request):
        self.events.append("outcome")
        self.outcomes.append(request)
        return pb.OutcomeReceipt(outcome_event_ref=request.source_ref, episode_ref="ep:" + request.run_ref)

    async def select_memory(self, request):
        self.selected.append(request)
        self.events.append("select")
        return pb.MemoryContext(
            context_ref="ctx:" + request.run_ref, run_ref=request.run_ref, scope=request.scope,
            constitution=request.constitution,
            recollections=[pb.Recollection(memory_ref="rec@1", text="A stable remembered fact." if not (self.drift and len(self.selected) == 4) else "Changed between arms.", application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION)],
            dispositions=[pb.Disposition(memory_ref="seed@1", text="When overloaded, offer one small starting action.", application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION)],
            episode_evidence=[pb.EpisodeEvidence(memory_ref="ep:history", text="USER: Many tasks blocked me. AGENT: One small action. OUTCOME: That helped me start.")],
        )

    async def record_memory_delivery(self, request):
        self.deliveries.append(request)
        self.events.append("delivery")
        return pb.ReceiptAck(receipt_ref="receipt:" + request.run_ref)


async def _settle(_scope, count):
    return {
        "dispositions": [{"ref": "seed@1", "text": "When overloaded, offer one small starting action.", "status": "active", "origin_job_ref": "job:history"}] if count >= 2 else [],
        "recollections": [{"ref": "rec@1", "text": "A stable remembered fact.", "status": "active"}],
        "basis": {}, "jobs": [],
    }


def _count(text):
    return len(text)  # Deterministic test budget; live uses cl100k.


async def _run(memory=None, model=None):
    api = _api()
    memory = memory or MemoryBoundary()
    prepared = await api.prepare_effect_trajectory(instance=DATA.instances[0], evaluation_ref="effect-test", memory=memory, settle=_settle)
    persisted = []

    class Model:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            assert input_text == "CURRENT_REQUEST\n" + DATA.instances[0].current_request
            assert max_output_tokens == 512
            assert DATA.instances[0].persona_text in instructions
            memory.events.append("answer")
            return "A real completed alternative " + str(memory.events.count("answer"))

    trace = await api.run_effect_arms(instance=DATA.instances[0], prepared=prepared, counterfactual=DATA.counterfactuals["r1"], memory=memory, answer_model=model or Model(), token_count=_count, persist=lambda value: persisted.append(copy.deepcopy(value)))
    return trace, memory, persisted


@pytest.mark.asyncio
async def test_four_arms_share_one_physical_source_and_frozen_foundation():
    """Separate sources or mutated base content would confound the contrast."""
    trace, memory, persisted = await _run()
    assert {a["mode"] for a in trace["arms"]} == {"none", "base", "learned", "oracle"}
    assert len({a["run_ref"] for a in trace["arms"]}) == 4
    assert len(memory.selected) == 4
    assert len({r.scope.session_ref for r in memory.selected}) == 1
    assert len({tuple(r.situation_source_event_refs) for r in memory.selected}) == 1
    assert all(r.episode_evidence_max_bytes == 8192 for r in memory.selected)
    assert memory.events.index("answer") > max(i for i, event in enumerate(memory.events) if event == "select")
    assert len({a["foundation_text"] for a in trace["arms"] if a["mode"] != "none"}) == 1
    arms = {a["mode"]: a for a in trace["arms"]}
    assert arms["none"]["memory_text"] == "" and arms["none"]["delivery"] is None
    assert arms["none"]["rendered_memory_refs"] == []
    assert arms["base"]["foundation_refs"] == ["rec@1", "ep:history"]
    assert arms["learned"]["rendered_memory_refs"] == ["rec@1", "ep:history", "seed@1"]
    assert arms["oracle"]["rendered_memory_refs"] == ["rec@1", "ep:history"]
    assert len(memory.deliveries) == 3
    assert len(persisted[-1]["arms"]) == 4
    assert len(memory.outcomes) == 3  # Only training; no answer has been judged yet.
    acts = [r for r in memory.observed if r.HasField("episode_binding") and r.episode_binding.role == pb.EPISODE_SOURCE_ROLE_AGENT_ACT][-4:]
    assert {r.source_event.text for r in acts} == {a["output"] for a in trace["arms"]}
    probe_sources = [r for r in memory.observed if r.source_event.source_ref == trace["situation_source_event_ref"]]
    assert len(probe_sources) == 5  # Once unbound, attached to four alternative runs.
    assert len({r.source_event.SerializeToString() for r in probe_sources}) == 1
    _api().validate_effect_trace(trace, token_count=_count)


@pytest.mark.asyncio
async def test_preselection_rejects_changed_item_snapshots_before_answering():
    with pytest.raises(ValueError, match="snapshot"):
        await _run(MemoryBoundary(drift=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate,error", [
    (lambda t: t["arms"].pop(), "four"),
    (lambda t: t["arms"][0].update(finish_reason="length"), "complete"),
    (lambda t: t["started_answer_calls"].pop(), "inventory"),
    (lambda t: t["completed_answer_calls"].pop(), "inventory"),
    (lambda t: next(a for a in t["arms"] if a["mode"] == "learned")["rendered_memory_refs"].append("invented@1"), "render"),
    (lambda t: next(a for a in t["arms"] if a["mode"] == "oracle")["delivery"]["delivered_memory_refs"].append("oracle@1"), "Delivery"),
    (lambda t: next(a for a in t["arms"] if a["mode"] == "base").update(foundation_text="different"), "render"),
    (lambda t: t["arms"][0]["agent_act"].update(text="not the completed answer"), "AgentAct"),
    (lambda t: t["canonical_before"]["dispositions"][0].update(text="unrelated active Seed"), "canonical"),
])
async def test_trace_rejects_incomplete_or_rewritten_causal_evidence(mutate, error):
    trace, _, _ = await _run()
    mutate(trace)
    with pytest.raises(ValueError, match=error):
        _api().validate_effect_trace(trace, token_count=_count)


@pytest.mark.asyncio
async def test_provider_failure_is_persisted_and_not_counted_as_a_completed_arm():
    api = _api()
    memory = MemoryBoundary()
    prepared = await api.prepare_effect_trajectory(instance=DATA.instances[0], evaluation_ref="failed-test", memory=memory, settle=_settle)
    persisted = []

    class Model:
        async def complete(self, **_kwargs):
            raise RuntimeError("provider did not finish")

    with pytest.raises(RuntimeError, match="provider did not finish"):
        await api.run_effect_arms(instance=DATA.instances[0], prepared=prepared, counterfactual=DATA.counterfactuals["r1"], memory=memory, answer_model=Model(), token_count=_count, persist=lambda value: persisted.append(copy.deepcopy(value)))
    assert len(persisted[-1]["started_answer_calls"]) == 1
    assert persisted[-1]["completed_answer_calls"] == []
    assert persisted[-1]["failure"]["error_class"] == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["run_ref", "delivery_receipt_ref", "situation_source_event_ref", "agent_act_source_event_ref"])
async def test_late_feedback_requires_the_exact_explicit_old_chain(changed):
    trace, memory, _ = await _run()
    api = _api()
    chain = api.effect_feedback_chain(trace, "learned")
    chain[changed] = "wrong-chain"
    with pytest.raises(ValueError, match="chain"):
        await api.report_effect_feedback(memory=memory, trace=trace, mode="learned", text="这次修改一下就行", association="trusted_late", trusted_chain=chain, token_count=_count)
    assert len(memory.outcomes) == 3


@pytest.mark.asyncio
async def test_feedback_uses_actual_chain_and_does_not_turn_a_task_edit_into_adapt():
    trace, memory, _ = await _run()
    api = _api()
    before = len(memory.observed)
    result = await api.report_effect_feedback(memory=memory, trace=trace, mode="none", text="这次改成两行就行", token_count=_count)
    assert result["eligible_delivered_disposition_refs"] == []
    assert list(memory.outcomes[-1].delivery_receipt_refs) == []
    assert len(memory.observed) == before  # No invented current USER Situation/ADAPT.
    chain = api.effect_feedback_chain(trace, "learned")
    result = await api.report_effect_feedback(memory=memory, trace=trace, mode="learned", text="补充刚才的真实结果", association="trusted_late", trusted_chain=chain, token_count=_count)
    assert list(memory.outcomes[-1].related_source_event_refs) == [chain["situation_source_event_ref"], chain["agent_act_source_event_ref"]]
    assert result["eligible_delivered_disposition_refs"] == ["seed@1"]


def test_read_only_canonical_probe_exposes_actual_outcome_basis_for_owner():
    """Without Outcome Basis a complete chain could be mistaken for a commit."""
    from memory_core_eval.personamem_live import DatabaseProbe

    queries = []

    def read(sql, params):
        assert sql.lstrip().startswith("SELECT")
        assert params == ("t", "a", "r")
        queries.append(sql)
        if "FROM seed_outcome_basis_links" in sql:
            return [{"ref": "seed@2", "outcome_event_ref": "outcome:real", "role": "inhibition"}]
        return []

    state = DatabaseProbe("mysql://probe@example.test/eval", read=read).memory_state(pb.MemoryScope(tenant_ref="t", agent_ref="a", relationship_ref="r", session_ref="s", kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP))
    assert state["basis"].get("seed_outcome_basis_links") == [{"ref": "seed@2", "outcome_event_ref": "outcome:real", "role": "inhibition"}]
    assert any("JOIN disposition_seeds r" in sql and "r.tenant_ref=%s AND r.agent_ref=%s AND r.relationship_ref=%s" in sql for sql in queries if "FROM seed_outcome_basis_links" in sql)
