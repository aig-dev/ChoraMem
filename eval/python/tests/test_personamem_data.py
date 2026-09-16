from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest


def row(persona="7", query="What should we do this weekend?"):
    return {
        "persona_id": persona,
        "chat_history_32k_link": f"data/chat_history_32k/persona{persona}.json",
        "user_query": repr({"role": "user", "content": query}),
        "correct_answer": "Go for a quiet walk.",
        "incorrect_answers": json.dumps(["Go clubbing.", "Watch sports.", "Go shopping."]),
        "pref_type": "anti_stereotypical_pref",
        "who": "self",
        "updated": "False",
        "sensitive_info": "False",
        "preference": "HIDDEN_PREFERENCE",
        "expanded_persona": "HIDDEN_PROFILE",
        "related_conversation_snippet": "HIDDEN_EVIDENCE_LABEL",
        "distance_from_related_snippet_to_query_32k": "25000",
    }


def test_loader_separates_query_from_hidden_persona_and_gold(tmp_path):
    from memory_core_eval.personamem_data import load_questions

    path = tmp_path / "benchmark.csv"
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row()))
        writer.writeheader()
        writer.writerow(row())
    question, = load_questions(path, size="32k")
    assert question.query == "What should we do this weekend?"
    assert question.history_path == "data/chat_history_32k/persona7.json"
    assert question.correct_answer == "Go for a quiet walk."
    assert "HIDDEN" not in repr(question)
    assert question.metadata["distance_tokens"] == 25000


def test_history_preserves_order_and_keeps_official_profile_separate(tmp_path):
    from memory_core_eval.personamem_data import load_history

    path = tmp_path / "history.json"
    path.write_text(json.dumps({"metadata": {"secret": "NOT_INPUT"}, "chat_history": [
        {"role": "system", "content": "Official initial background"},
        {"role": "user", "content": "First experience"},
        {"role": "assistant", "content": "First actual reply"},
        {"role": "user", "content": "Later experience"},
        {"role": "assistant", "content": "Later actual reply"},
    ]}))
    history = load_history(path)
    assert history.background == "Official initial background"
    assert [(x.situation, x.agent_act) for x in history.turns] == [
        ("First experience", "First actual reply"),
        ("Later experience", "Later actual reply"),
    ]
    assert "NOT_INPUT" not in repr(history)


@pytest.mark.parametrize("messages", [
    [{"role": "user", "content": "Unanswered"}],
    [{"role": "assistant", "content": "No situation"}],
    [{"role": "user", "content": "A"}, {"role": "system", "content": "B"}],
])
def test_history_rejects_unpaired_or_unexpected_roles(tmp_path, messages):
    from memory_core_eval.personamem_data import load_history

    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"chat_history": messages}))
    with pytest.raises(ValueError):
        load_history(path)


def test_history_keeps_consecutive_messages_and_unanswered_tail_without_fabricating_reply(tmp_path):
    from memory_core_eval.personamem_data import load_history

    path = tmp_path / "history.json"
    path.write_text(json.dumps({"chat_history": [
        {"role": "user", "content": "First message"},
        {"role": "user", "content": "Additional detail"},
        {"role": "assistant", "content": "Actual reply"},
        {"role": "user", "content": "Unanswered follow-up"},
    ]}))
    history = load_history(path)
    assert len(history.turns) == 1
    assert history.turns[0].situation == "First message\n\nAdditional detail"
    assert history.turns[0].agent_act == "Actual reply"
    assert history.trailing_user == "Unanswered follow-up"


def test_history_skips_official_blank_message_without_inventing_a_turn(tmp_path):
    from memory_core_eval.personamem_data import load_history

    path = tmp_path / "history.json"
    path.write_text(json.dumps({"chat_history": [
        {"role": "user", "content": "Observed situation"},
        {"role": "assistant", "content": "First actual reply"},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "Additional observed assistant text"},
    ]}))
    history = load_history(path)
    assert len(history.turns) == 1
    assert history.turns[0].situation == "Observed situation"
    assert history.turns[0].agent_act == (
        "First actual reply\n\nAdditional observed assistant text"
    )


def test_history_drops_user_whose_official_assistant_record_is_null(tmp_path):
    from memory_core_eval.personamem_data import load_history

    path = tmp_path / "history.json"
    path.write_text(json.dumps({"chat_history": [
        {"role": "user", "content": "Complete one"},
        {"role": "assistant", "content": "Reply one"},
        {"role": "user", "content": "Incomplete request"},
        {"role": "assistant", "content": None},
        {"role": "user", "content": "Complete two"},
        {"role": "assistant", "content": "Reply two"},
    ]}))
    history = load_history(path)
    assert [(turn.situation, turn.agent_act) for turn in history.turns] == [
        ("Complete one", "Reply one"),
        ("Complete two", "Reply two"),
    ]


def test_selection_is_persona_balanced_deterministic_and_not_correctness_based(tmp_path):
    from memory_core_eval.personamem_data import load_questions, select_questions

    path = tmp_path / "benchmark.csv"
    rows = [row(str(p), f"question {q}") for p in range(5) for q in range(6)]
    with path.open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    questions = load_questions(path, size="32k")
    chosen = select_questions(questions, personas=3, per_persona=4, seed=17)
    assert len(chosen) == 12
    assert len({q.persona_id for q in chosen}) == 3
    assert all(sum(q.persona_id == p for q in chosen) == 4 for p in {q.persona_id for q in chosen})
    assert chosen == select_questions(tuple(reversed(questions)), personas=3, per_persona=4, seed=17)


def test_history_links_cannot_escape_dataset_directory(tmp_path):
    from memory_core_eval.personamem_data import resolve_history_path

    with pytest.raises(ValueError):
        resolve_history_path(tmp_path, "../../private.json")
    with pytest.raises(ValueError):
        resolve_history_path(tmp_path, "data/raw_data/gold.json")
    assert resolve_history_path(tmp_path, "data/chat_history_32k/a.json") == tmp_path / "data/chat_history_32k/a.json"


def test_checksum_mismatch_is_not_silently_used(tmp_path):
    from memory_core_eval.personamem_data import verify_sha256

    path = tmp_path / "download.csv"
    path.write_text("partial download")
    with pytest.raises(ValueError, match="SHA-256"):
        verify_sha256(path, "0" * 64)


def test_official_scorer_requires_the_pinned_reviewed_source(tmp_path):
    from memory_core_eval.personamem_data import load_official_mcq

    path = tmp_path / "inference.py"
    path.write_text("raise RuntimeError('must never execute')")
    with pytest.raises(ValueError, match="SHA-256"):
        load_official_mcq(path)
