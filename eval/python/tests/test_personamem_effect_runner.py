from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from memory_core import memory_pb2 as pb

from memory_core_eval.personamem_data import History, Question
from memory_core_eval.reference import HistoricalTurn


class Memory:
    def __init__(self):
        self.events = []
        self.selects = []
        self.deliveries = []

    async def observe_source_event(self, request):
        self.events.append(request)
        return pb.SourceEventReceipt(source_event_ref=request.source_event.source_ref)

    async def select_memory(self, request):
        self.selects.append(request)
        return pb.MemoryContext(
            context_ref="context-1",
            run_ref=request.run_ref,
            scope=request.scope,
            recollections=[pb.Recollection(
                memory_ref="recollection-1",
                text="The user likes tea.",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER,
            )],
            dispositions=[pb.Disposition(
                memory_ref="disposition-1",
                text="Offer tea for a calming break.",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )],
            episode_evidence=[pb.EpisodeEvidence(
                memory_ref="episode-1",
                text="SITUATION [user]\nI like tea.\nAGENT_ACT [agent]\nNoted.",
            )],
        )

    async def record_memory_delivery(self, request):
        self.deliveries.append(request)
        return pb.ReceiptAck(receipt_ref=f"delivery-{len(self.deliveries)}")


class Index:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def search_episodes(self, *, scope, query, limit):
        self.calls.append((scope, query, limit))
        return self.documents


class Model:
    def __init__(self):
        self.inputs = []

    async def complete(self, *, instructions, input_text, max_output_tokens):
        self.inputs.append((instructions, input_text, max_output_tokens))
        return f"Final Answer: A\ncall:{len(self.inputs)}"


def scorer():
    return SimpleNamespace(
        create_mcq_options=lambda _, correct, incorrect, seed: (
            "Choose one. A. Tea B. Coffee C. Water D. Juice",
            {"A": correct, "B": incorrect[0], "C": incorrect[1], "D": incorrect[2]},
        ),
        extract_final_answer=lambda _, output: "A" if "Final Answer: A" in output else "",
        check_mcq_correctness=lambda _, predicted, correct, options: (
            bool(predicted) and options[predicted] == correct
        ),
    )


def question(ref="row-00001", persona="7"):
    return Question(
        ref,
        persona,
        "data/chat_history_32k/persona7.json",
        "What should I drink?",
        "Tea",
        ("Coffee", "Water", "Juice"),
        {"pref_type": "neutral_preferences"},
    )


@pytest.mark.asyncio
async def test_question_evaluation_uses_six_isolated_contexts_and_never_learns_answers(monkeypatch):
    from memory_core_eval.personamem_diagnostics import OracleEvidence
    from memory_core_eval.personamem_effect import EpisodeDocument, EffectMode
    from memory_core_eval import personamem_effect_runner as runner
    from memory_core_eval.personamem_effect_runner import evaluate_question

    monkeypatch.setattr(
        runner,
        "effect_mode_order",
        lambda *_args: tuple(reversed(tuple(EffectMode))),
    )

    memory = Memory()
    model = Model()
    history = History("shared official profile", (
        HistoricalTurn("I like tea.", "Noted."),
        HistoricalTurn("I walk at dawn.", "Sounds peaceful."),
    ))
    episodes = (
        EpisodeDocument("episode-1", "SITUATION [user]\nI like tea.\nAGENT_ACT [agent]\nNoted."),
        EpisodeDocument("episode-2", "SITUATION [user]\nI walk at dawn.\nAGENT_ACT [agent]\nSounds peaceful."),
    )
    index = Index((episodes[0],))
    label = OracleEvidence(
        question_ref="row-00001",
        persona_id="7",
        messages=(("user", "ORACLE_ONLY_SENTINEL"), ("assistant", "oracle reply")),
    )
    scope = pb.MemoryScope(
        tenant_ref="effect-run",
        agent_ref="agent",
        relationship_ref="persona-7",
        session_ref="query-row-00001",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )

    result = await evaluate_question(
        memory=memory,
        episode_search=index,
        model=model,
        scorer=scorer(),
        evaluation_ref="effect-run",
        scope=scope,
        question=question(),
        history=history,
        oracle=label,
        episode_documents=episodes,
        learned_basis={"recollection-1": ("episode-1",)},
        token_count=len,
        core_memory_tokens=10_000,
        max_output_tokens=200,
        episode_evidence_max_bytes=16_384,
    )

    assert [row["mode"] for row in result["answers"]] == [
        mode.value for mode in EffectMode
    ]
    assert result["answers"][0]["output"].endswith("call:6")
    assert result["answers"][-1]["output"].endswith("call:1")
    assert len(model.inputs) == 6
    assert all("shared official profile" not in instructions for instructions, _, _ in model.inputs)
    assert all(input_text.endswith("personalized responses.") for _, input_text, _ in model.inputs)
    oracle_prompts = [
        instructions
        for instructions, _, _ in model.inputs
        if "ORACLE_ONLY_SENTINEL" in instructions
    ]
    non_oracle_prompts = [
        instructions
        for instructions, _, _ in model.inputs
        if "ORACLE_ONLY_SENTINEL" not in instructions
    ]
    assert len(oracle_prompts) == 1 and "ORACLE_ONLY_SENTINEL" in oracle_prompts[0]
    assert all("ORACLE_ONLY_SENTINEL" not in prompt for prompt in non_oracle_prompts)

    assert len(memory.events) == 1
    assert memory.events[0].source_event.text == "What should I drink?"
    assert not memory.events[0].HasField("episode_binding")
    assert len(memory.selects) == 1
    assert memory.selects[0].episode_evidence_max_bytes == 16_384
    deliveries = {
        item.idempotency_key.rsplit(":", 1)[-1]: list(item.delivered_memory_refs)
        for item in memory.deliveries
    }
    assert deliveries == {
        "current_core": ["recollection-1", "disposition-1", "episode-1"],
        "learned_core": ["recollection-1", "disposition-1"],
    }
    assert all(event.source_event.text != "Final Answer: A" for event in memory.events)
    assert index.calls[0][2] == 40
    assert result["diagnostic"]["source_available"] is False
    assert result["diagnostic"]["answer_correct"] is True


def test_effect_mode_order_is_stable_complete_and_interleaved():
    from memory_core_eval.personamem_effect import EffectMode
    from memory_core_eval.personamem_effect_runner import effect_mode_order

    first = effect_mode_order("run", "persona", "question-1")
    assert first == effect_mode_order("run", "persona", "question-1")
    assert set(first) == set(EffectMode)
    orders = {
        effect_mode_order("run", "persona", f"question-{index}")
        for index in range(12)
    }
    assert len(orders) > 1


def valid_question_result(ref="q1", persona="p1", scores=None):
    from memory_core_eval.personamem_effect import EffectMode

    scores = scores or {}
    answers = []
    for mode in EffectMode:
        answers.append({
            "question_ref": ref,
            "persona_id": persona,
            "mode": mode.value,
            "correct": scores.get(mode.value, False),
            "predicted_option": "A",
            "memory_tokens": 0,
            "oracle_label_used": mode is EffectMode.ORACLE_EPISODE,
            "metadata": {"pref_type": "neutral_preferences"},
        })
    return {
        "question_ref": ref,
        "persona_id": persona,
        "answers": answers,
        "diagnostic": {
            "question_ref": ref,
            "persona_id": persona,
            "mode": "current_core",
            "source_available": True,
            "indexed": True,
            "selected": True,
            "delivered": True,
            "answer_correct": scores.get("current_core", False),
            "learned_basis_selected": True,
            "learned_basis_delivered": True,
        },
    }


def test_summary_rejects_incomplete_groups_duplicate_questions_and_oracle_leakage():
    from memory_core_eval.personamem_effect_runner import summarize_effect_results

    valid = valid_question_result()
    assert summarize_effect_results([valid])["questions"] == 1

    missing = copy.deepcopy(valid)
    missing["answers"].pop()
    with pytest.raises(ValueError, match="six modes"):
        summarize_effect_results([missing])

    with pytest.raises(ValueError, match="duplicate"):
        summarize_effect_results([valid, copy.deepcopy(valid)])

    leaked = copy.deepcopy(valid)
    leaked["answers"][0]["oracle_label_used"] = True
    with pytest.raises(ValueError, match="Oracle"):
        summarize_effect_results([leaked])

    missing_funnel = copy.deepcopy(valid)
    del missing_funnel["diagnostic"]["delivered"]
    with pytest.raises((TypeError, ValueError), match="delivered"):
        summarize_effect_results([missing_funnel])

    wrong_identity = copy.deepcopy(valid)
    wrong_identity["answers"][0]["question_ref"] = "other"
    with pytest.raises(ValueError, match="identity"):
        summarize_effect_results([wrong_identity])


def test_summary_decides_core_bottleneck_only_on_source_available_questions():
    from memory_core_eval.personamem_effect_runner import summarize_effect_results

    eligible = valid_question_result(
        ref="eligible",
        scores={
            "oracle_episode": True,
            "semantic_top40": True,
        },
    )
    unavailable = valid_question_result(
        ref="not-memory-evaluable",
        scores={"oracle_episode": True},
    )
    unavailable["diagnostic"].update(
        source_available=False,
        indexed=False,
        selected=False,
        delivered=False,
        learned_basis_selected=False,
        learned_basis_delivered=False,
    )

    result = summarize_effect_results([eligible, unavailable], bootstrap_samples=10)
    assert result["questions"] == 2
    assert result["causal_subset"]["questions"] == 1
    assert result["excluded_from_causal_decision"] == 1
    assert result["decision"]["bottleneck"] == "selection_assembly"
    assert result["decision"]["comparison"] == (
        "semantic_top40-minus-current_core|indexed"
    )


def test_paired_point_difference_uses_question_weighting_with_persona_cluster_ci():
    from memory_core_eval.personamem_effect_runner import summarize_effect_results

    rows = [
        valid_question_result(
            ref="a-1",
            persona="a",
            scores={"oracle_episode": True},
        ),
        valid_question_result(
            ref="a-2",
            persona="a",
            scores={"oracle_episode": True},
        ),
        valid_question_result(
            ref="b-1",
            persona="b",
            scores={"none": True},
        ),
    ]

    result = summarize_effect_results(rows, bootstrap_samples=20)

    assert result["modes"]["oracle_episode"]["accuracy"] == pytest.approx(2 / 3)
    assert result["modes"]["none"]["accuracy"] == pytest.approx(1 / 3)
    assert result["paired"]["oracle_episode-minus-none"]["difference"] == pytest.approx(1 / 3)


def test_conditioned_decision_does_not_tune_an_uncertain_largest_gap():
    from memory_core_eval.personamem_effect_runner import _decide_conditioned_bottleneck

    effects = {
        "oracle_episode-minus-none|source_available": {
            "difference": 0.3,
            "ci95": [0.1, 0.5],
        },
        "oracle_episode-minus-semantic_top40|source_available": {
            "difference": 0.4,
            "ci95": [-0.1, 0.7],
        },
        "semantic_top40-minus-current_core|indexed": {
            "difference": 0.2,
            "ci95": [0.05, 0.35],
        },
        "current_core-minus-learned_core|delivered": {
            "difference": 0.1,
            "ci95": [-0.05, 0.25],
        },
    }

    decision = _decide_conditioned_bottleneck(effects)

    assert decision["bottleneck"] == "selection_assembly"


def test_conditioned_decision_reports_no_core_bottleneck_when_all_gaps_are_uncertain():
    from memory_core_eval.personamem_effect_runner import _decide_conditioned_bottleneck

    effects = {
        "oracle_episode-minus-none|source_available": {
            "difference": 0.3,
            "ci95": [0.1, 0.5],
        },
        "oracle_episode-minus-semantic_top40|source_available": {
            "difference": 0.04,
            "ci95": [-0.03, 0.11],
        },
        "semantic_top40-minus-current_core|indexed": {
            "difference": 0.02,
            "ci95": [-0.05, 0.09],
        },
        "current_core-minus-learned_core|delivered": {
            "difference": -0.01,
            "ci95": [-0.08, 0.06],
        },
    }

    decision = _decide_conditioned_bottleneck(effects)

    assert decision["bottleneck"] == "no_measured_core_bottleneck"


def test_manifest_is_immutable_across_resume(tmp_path):
    from memory_core_eval.personamem_effect_runner import freeze_manifest

    path = tmp_path / "manifest.json"
    config = {"split": "val", "questions": ["q1"], "modes": ["six"]}
    freeze_manifest(path, config)
    freeze_manifest(path, copy.deepcopy(config))

    with pytest.raises(ValueError, match="manifest changed"):
        freeze_manifest(path, config | {"questions": ["q2"]})


@pytest.mark.asyncio
async def test_history_ingestion_captures_actual_episode_refs_and_never_invents_tail():
    from memory_core_eval.personamem_effect_runner import ingest_history

    class IngestMemory:
        def __init__(self):
            self.requests = []

        async def observe_source_event(self, request):
            self.requests.append(request)
            episode_ref = ""
            if request.HasField("episode_binding"):
                episode_ref = "episode-" + request.episode_binding.run_ref.rsplit(":", 1)[1]
            return pb.SourceEventReceipt(
                source_event_ref=request.source_event.source_ref,
                episode_ref=episode_ref,
            )

    memory = IngestMemory()
    settled = []

    async def settle(scope, count):
        settled.append((scope.session_ref, count))
        return {"episodes": count, "pending_jobs": 0, "pending_index_operations": 0}

    history = History("profile", (
        HistoricalTurn("u1", "a1"),
        HistoricalTurn("u2", "a2"),
        HistoricalTurn("u3", "a3"),
    ), trailing_user="unanswered")
    state, documents = await ingest_history(
        memory=memory,
        settle=settle,
        evaluation_ref="effect",
        persona_id="7",
        history=history,
        batch_turns=2,
    )

    assert [item.memory_ref for item in documents] == ["episode-0", "episode-1", "episode-2"]
    assert documents[0].text == "SITUATION [user]\nu1\nAGENT_ACT [agent]\na1"
    assert settled == [("window-0", 2), ("window-1", 3)]
    assert state["pending_index_operations"] == 0
    assert len(memory.requests) == 7
    tail = memory.requests[-1]
    assert tail.source_event.text == "unanswered"
    assert not tail.HasField("episode_binding")


def test_learned_basis_uses_only_active_memory_versions():
    from memory_core_eval.personamem_effect_runner import learned_basis_map

    state = {
        "recollections": [
            {"ref": "r-active", "status": "active"},
            {"ref": "r-old", "status": "superseded"},
        ],
        "dispositions": [{"ref": "d-active", "status": "active"}],
        "basis": {
            "recollection_basis_links": [
                {"ref": "r-active", "episode_ref": "e1"},
                {"ref": "r-old", "episode_ref": "e2"},
            ],
            "seed_basis_links": [
                {"ref": "d-active", "episode_ref": "e3"},
            ],
        },
    }
    assert learned_basis_map(state) == {
        "r-active": ("e1",),
        "d-active": ("e3",),
    }


@pytest.mark.asyncio
async def test_request_cache_reuses_identical_model_inputs_across_modes_and_resume(tmp_path):
    from memory_core_eval.personamem_effect_runner import CachedTextModel

    class Delegate:
        def __init__(self):
            self.calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            return "stable answer"

    delegate = Delegate()
    first = CachedTextModel(delegate, tmp_path, model_revision="answer-model-v1")
    request = dict(instructions="same", input_text="same", max_output_tokens=50)
    assert await first.complete(**request) == "stable answer"
    assert await first.complete(**request) == "stable answer"
    assert delegate.calls == 1

    resumed_delegate = Delegate()
    resumed = CachedTextModel(resumed_delegate, tmp_path, model_revision="answer-model-v1")
    assert await resumed.complete(**request) == "stable answer"
    assert resumed_delegate.calls == 0
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["model_revision"] == "answer-model-v1"


def test_question_checkpoint_is_atomic_and_immutable(tmp_path):
    from memory_core_eval.personamem_effect_runner import freeze_question_result

    path = tmp_path / "questions" / "q1.json"
    result = valid_question_result()
    freeze_question_result(path, result)
    freeze_question_result(path, copy.deepcopy(result))
    assert not path.with_suffix(".json.part").exists()
    with pytest.raises(ValueError, match="question result changed"):
        freeze_question_result(path, result | {"persona_id": "other"})


def test_grpc_episode_search_filters_kind_but_preserves_provider_order():
    from memoryindex.v1 import memory_index_pb2 as index_pb
    from memory_core_eval.personamem_effect_runner import GrpcEpisodeSearch
    from memory_core_eval.personamem_effect import EpisodeDocument

    class Stub:
        def __init__(self):
            self.calls = []

        def Search(self, request, **kwargs):
            self.calls.append((request, kwargs))
            return index_pb.SearchResponse(candidates=[
                index_pb.Candidate(kind=index_pb.MEMORY_KIND_RECOLLECTION, ref="r1"),
                index_pb.Candidate(kind=index_pb.MEMORY_KIND_EPISODE, ref="e2"),
                index_pb.Candidate(kind=index_pb.MEMORY_KIND_EPISODE, ref="e1"),
            ])

    stub = Stub()
    provider = GrpcEpisodeSearch(
        documents=(EpisodeDocument("e1", "one"), EpisodeDocument("e2", "two")),
        owner_document_count=3,
        stub=stub,
        token="secret",
    )
    scope = pb.MemoryScope(tenant_ref="t", agent_ref="a", relationship_ref="r")
    result = provider.search_episodes(scope=scope, query="query", limit=2)
    assert [item.memory_ref for item in result] == ["e2", "e1"]
    request, options = stub.calls[0]
    assert request.query.limit == 3
    assert request.query.scope.relationship_ref == "r"
    assert options["metadata"] == (("x-agent-rpc-token", "secret"),)


def test_grpc_episode_search_rejects_unknown_or_duplicate_episode_refs():
    from memoryindex.v1 import memory_index_pb2 as index_pb
    from memory_core_eval.personamem_effect_runner import GrpcEpisodeSearch
    from memory_core_eval.personamem_effect import EpisodeDocument

    scope = pb.MemoryScope(tenant_ref="t", agent_ref="a", relationship_ref="r")

    class Stub:
        def __init__(self, refs):
            self.refs = refs

        def Search(self, *_args, **_kwargs):
            return index_pb.SearchResponse(candidates=[
                index_pb.Candidate(kind=index_pb.MEMORY_KIND_EPISODE, ref=ref)
                for ref in self.refs
            ])

    document = EpisodeDocument("e1", "one")
    with pytest.raises(ValueError, match="unknown Episode"):
        GrpcEpisodeSearch(documents=(document,), owner_document_count=2,
                          stub=Stub(["missing"])).search_episodes(
            scope=scope, query="query", limit=1)
    with pytest.raises(ValueError, match="duplicate Episode"):
        GrpcEpisodeSearch(documents=(document,), owner_document_count=2,
                          stub=Stub(["e1", "e1"])).search_episodes(
            scope=scope, query="query", limit=2)


def test_grpc_episode_search_rejects_a_truncated_provider_result():
    from memoryindex.v1 import memory_index_pb2 as index_pb
    from memory_core_eval.personamem_effect_runner import GrpcEpisodeSearch
    from memory_core_eval.personamem_effect import EpisodeDocument

    class Stub:
        def Search(self, *_args, **_kwargs):
            return index_pb.SearchResponse(candidates=[
                index_pb.Candidate(kind=index_pb.MEMORY_KIND_EPISODE, ref="e1"),
            ])

    documents = tuple(
        EpisodeDocument(f"e{index}", f"episode {index}")
        for index in range(1, 42)
    )
    provider = GrpcEpisodeSearch(
        documents=documents,
        owner_document_count=len(documents),
        stub=Stub(),
    )
    with pytest.raises(RuntimeError, match="complete Episode top-40"):
        provider.search_episodes(
            scope=pb.MemoryScope(tenant_ref="t", agent_ref="a", relationship_ref="r"),
            query="query",
            limit=40,
        )


@pytest.mark.asyncio
async def test_effect_ready_waits_for_both_consolidation_and_index_projection():
    from memory_core_eval.personamem_effect_runner import wait_for_effect_ready

    states = iter([
        {"episodes": 2, "consolidated_episodes": 2, "jobs": 1, "pending_jobs": 1, "pending_index_operations": 3},
        {"episodes": 2, "consolidated_episodes": 2, "jobs": 1, "pending_jobs": 0, "pending_index_operations": 1},
        {"episodes": 2, "consolidated_episodes": 2, "jobs": 1, "pending_jobs": 0, "pending_index_operations": 0},
    ])
    result = await wait_for_effect_ready(
        lambda: next(states), expected_episodes=2, timeout=1, interval=0
    )
    assert result["pending_index_operations"] == 0


@pytest.mark.asyncio
async def test_effect_ready_requires_every_observed_episode_to_be_consolidated():
    from memory_core_eval.personamem_effect_runner import wait_for_effect_ready

    states = iter([
        {"episodes": 2, "consolidated_episodes": 1, "jobs": 1, "pending_jobs": 0, "pending_index_operations": 0},
        {"episodes": 2, "consolidated_episodes": 1, "jobs": 2, "pending_jobs": 1, "pending_index_operations": 0},
        {"episodes": 2, "consolidated_episodes": 2, "jobs": 2, "pending_jobs": 0, "pending_index_operations": 0},
    ])
    result = await wait_for_effect_ready(
        lambda: next(states),
        expected_episodes=2,
        timeout=1,
        interval=0,
    )

    assert result["consolidated_episodes"] == 2
