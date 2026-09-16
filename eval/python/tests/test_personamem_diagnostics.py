from __future__ import annotations

import csv
import json

import pytest
from memory_core import memory_pb2 as pb

from memory_core_eval.personamem_data import History
from memory_core_eval.reference import HistoricalTurn


def benchmark_row(snippet):
    return {
        "persona_id": "7",
        "chat_history_32k_link": "data/chat_history_32k/persona7.json",
        "user_query": repr({"role": "user", "content": "What should I drink?"}),
        "correct_answer": "Tea",
        "incorrect_answers": json.dumps(["Coffee", "Water", "Juice"]),
        "pref_type": "neutral_preferences",
        "who": "self",
        "updated": "False",
        "sensitive_info": "False",
        "topic_query": "Food",
        "distance_from_related_snippet_to_query_32k": "12000",
        "related_conversation_snippet": json.dumps(snippet),
        "preference": "HIDDEN GOLD",
    }


def test_oracle_labels_require_the_separate_diagnostics_loader(tmp_path):
    from memory_core_eval.personamem_data import load_questions
    from memory_core_eval.personamem_diagnostics import load_oracle_evidence

    snippet = [
        {"role": "user", "content": "I prefer tea."},
        {"role": "assistant", "content": "Noted."},
    ]
    path = tmp_path / "benchmark.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(benchmark_row(snippet)))
        writer.writeheader()
        writer.writerow(benchmark_row(snippet))

    question, = load_questions(path, size="32k")
    labels = load_oracle_evidence(path)

    assert "HIDDEN" not in repr(question)
    assert "related_conversation_snippet" not in repr(question)
    assert labels["row-00000"].messages == tuple(
        (item["role"], item["content"]) for item in snippet
    )


@pytest.mark.parametrize(
    ("history", "snippet", "expected"),
    [
        (
            History("", (
                HistoricalTurn("hello", "hi"),
                HistoricalTurn("I like\n tea", "noted"),
            )),
            (("user", "I like tea"), ("assistant", "noted")),
            {"status": "matched", "episode_indexes": [1], "matches": 1},
        ),
        (
            History("", (
                HistoricalTurn("same", "reply"),
                HistoricalTurn("same", "reply"),
            )),
            (("user", "same"), ("assistant", "reply")),
            {"status": "ambiguous", "episode_indexes": [], "matches": 2},
        ),
        (
            History("", (HistoricalTurn("hello", "hi"),), trailing_user="tail"),
            (("user", "tail"),),
            {"status": "unmatched", "episode_indexes": [], "matches": 0},
        ),
    ],
)
def test_oracle_alignment_uses_unique_observed_episode_and_never_guesses(history, snippet, expected):
    from memory_core_eval.personamem_diagnostics import align_snippet

    assert align_snippet(snippet, history) == expected


def test_oracle_alignment_handles_merged_and_noncontiguous_official_messages():
    from memory_core_eval.personamem_diagnostics import align_snippet

    history = History("", (
        HistoricalTurn("Earlier request\n\nI prefer tea.", "Noted."),
        HistoricalTurn("irrelevant", "irrelevant reply"),
        HistoricalTurn("Please forget that I prefer tea.", "Different actual reply"),
    ))
    snippet = (
        ("user", "I prefer tea."),
        ("assistant", "Noted."),
        ("user", "Please forget that I prefer tea."),
        ("assistant", "Oracle reply absent from rebuilt history"),
    )
    assert align_snippet(snippet, history) == {
        "status": "matched",
        "episode_indexes": [0, 2],
        "matches": 1,
    }


def test_semantic_episode_port_requests_top40_and_preserves_provider_order():
    from memory_core_eval.personamem_effect import EpisodeDocument
    from memory_core_eval.personamem_diagnostics import semantic_top40

    class Index:
        def search_episodes(self, *, scope, query, limit):
            assert scope.relationship_ref == "persona-7"
            assert query == "What should I drink?"
            assert limit == 40
            return (
                EpisodeDocument("episode-3", "third-ranked"),
                EpisodeDocument("episode-1", "first-ranked"),
            )

    scope = pb.MemoryScope(
        tenant_ref="eval",
        agent_ref="agent",
        relationship_ref="persona-7",
        kind=pb.MEMORY_SCOPE_KIND_RELATIONSHIP,
    )
    result = semantic_top40(Index(), scope=scope, query="What should I drink?")

    assert [item.memory_ref for item in result] == ["episode-3", "episode-1"]


def test_direct_episode_funnel_keeps_basis_coverage_separate_from_actual_evidence():
    from memory_core_eval.personamem_effect import EffectContext, EffectMode
    from memory_core_eval.personamem_diagnostics import build_funnel_record

    context = EffectContext(
        mode=EffectMode.CURRENT_CORE,
        text="rendered",
        memory_refs=("recollection-1", "other-episode"),
        selected_memory_refs=("recollection-1", "gold-episode", "other-episode"),
        selected_episode_refs=("gold-episode", "other-episode"),
        delivered_episode_refs=("other-episode",),
    )
    row = build_funnel_record(
        question_ref="q1",
        persona_id="p1",
        source_available=True,
        gold_episode_refs=("gold-episode",),
        indexed_episode_refs=("gold-episode",),
        context=context,
        learned_basis={"recollection-1": ("gold-episode",)},
        answer_correct=True,
    )

    assert row["source_available"] is True
    assert row["indexed"] is True
    assert row["selected"] is True
    assert row["delivered"] is False
    assert row["learned_basis_selected"] is True
    assert row["learned_basis_delivered"] is True


def test_funnel_summary_reports_each_transition_and_conditional_accuracy():
    from memory_core_eval.personamem_diagnostics import summarize_funnel

    rows = [
        dict(question_ref="q1", persona_id="p1", source_available=True,
             indexed=True, selected=True, delivered=True, answer_correct=True,
             learned_basis_selected=False, learned_basis_delivered=False),
        dict(question_ref="q2", persona_id="p1", source_available=True,
             indexed=True, selected=False, delivered=False, answer_correct=False,
             learned_basis_selected=True, learned_basis_delivered=True),
        dict(question_ref="q3", persona_id="p2", source_available=True,
             indexed=False, selected=False, delivered=False, answer_correct=True,
             learned_basis_selected=False, learned_basis_delivered=False),
        dict(question_ref="q4", persona_id="p2", source_available=False,
             indexed=False, selected=False, delivered=False, answer_correct=False,
             learned_basis_selected=False, learned_basis_delivered=False),
    ]

    summary = summarize_funnel(rows)

    assert summary["questions"] == 4
    assert summary["counts"] == {
        "source_available": 3,
        "indexed": 2,
        "selected": 1,
        "delivered": 1,
        "answer_correct": 2,
        "learned_basis_selected": 1,
        "learned_basis_delivered": 1,
    }
    assert summary["transition_rates"] == {
        "source_available": 0.75,
        "indexed_given_source": 2 / 3,
        "selected_given_indexed": 0.5,
        "delivered_given_selected": 1.0,
    }
    assert summary["answer_accuracy"] == 0.5
    assert summary["answer_accuracy_given"]["delivered"] == 1.0
    assert summary["answer_accuracy_without"]["delivered"] == 1 / 3


def test_funnel_rejects_a_selected_episode_that_was_not_in_semantic_results():
    from memory_core_eval.personamem_diagnostics import summarize_funnel

    row = {
        "question_ref": "q",
        "persona_id": "p",
        "source_available": True,
        "indexed": False,
        "selected": True,
        "delivered": False,
        "answer_correct": False,
        "learned_basis_selected": False,
        "learned_basis_delivered": False,
    }
    with pytest.raises(ValueError, match="indexed"):
        summarize_funnel([row])
