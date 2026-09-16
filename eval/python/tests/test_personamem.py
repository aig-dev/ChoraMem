from __future__ import annotations

from types import SimpleNamespace

import pytest
from memory_core import memory_pb2 as pb

from memory_core_eval.personamem_data import History, Question
from memory_core_eval.reference import HistoricalTurn


class Memory:
    def __init__(self):
        self.events, self.deliveries = [], []

    async def observe_source_event(self, request):
        self.events.append(request)
        return pb.SourceEventReceipt(source_event_ref=request.source_event.source_ref)

    async def select_memory(self, request):
        return pb.MemoryContext(context_ref="context", run_ref=request.run_ref, scope=request.scope,
            recollections=[pb.Recollection(memory_ref="r", text="Quiet walks help the user unwind.",
                                          application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER)],
            dispositions=[pb.Disposition(memory_ref="d", text="Offer a walk when the user needs rest.",
                                        application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION)])

    async def record_memory_delivery(self, request):
        self.deliveries.append(request)
        return pb.ReceiptAck(receipt_ref="receipt")


class Model:
    def __init__(self):
        self.inputs = []

    async def complete(self, *, instructions, input_text, max_output_tokens):
        self.inputs.append((instructions, input_text))
        return "Final Answer: B"


def scorer():
    # The upstream parser is external code. Its adapter is tested separately.
    return SimpleNamespace(
        create_mcq_options=lambda _, correct, incorrect, seed: ("A. Other\nB. Walk", {"A": "Other", "B": correct}),
        extract_final_answer=lambda _, response: "B",
        check_mcq_correctness=lambda _, predicted, correct, options: options[predicted] == correct,
    )


def question():
    return Question("q1", "p1", "data/chat_history_32k/p1.json", "I need a restful weekend.",
                    "Walk", ("Other", "Third", "Fourth"), {"pref_type": "neutral_preferences"})


@pytest.mark.asyncio
async def test_ingestion_uses_real_turns_once_and_scoring_cannot_train_on_test_questions():
    from memory_core_eval.personamem import PersonaMemHarness

    memory, model, waits = Memory(), Model(), []

    async def settle(scope, expected_episodes):
        waits.append(expected_episodes)
        return {"episodes": expected_episodes, "pending_jobs": 0}

    harness = PersonaMemHarness(memory=memory, model=model, scorer=scorer(), settle=settle,
                               evaluation_ref="run1", token_count=len, memory_tokens=2000)
    history = History("Same official profile", (
        HistoricalTurn("Past user 1", "Past assistant 1"),
        HistoricalTurn("Past user 2", "Past assistant 2"),
    ), "I need a restful weekend.")
    await harness.ingest("p1", history)
    results = await harness.evaluate(question(), history)
    assert waits == [2]
    assert [x.source_event.text for x in memory.events[:4]] == [
        "Past user 1", "Past assistant 1", "Past user 2", "Past assistant 2"]
    assert len(results) == 4 and all(r["correct"] for r in results)
    assert all("Same official profile" in text for text, _ in model.inputs)
    assert all("pref_type" not in text for text, _ in model.inputs)
    assert memory.events[4].source_event.text == "I need a restful weekend."
    assert not memory.events[4].HasField("episode_binding")
    assert memory.events[4].source_event.source_ref.endswith(":unanswered")
    query_events = memory.events[5:]
    assert query_events and all(not event.HasField("episode_binding") for event in query_events)
    assert all(event.source_event.text == "I need a restful weekend." for event in query_events)
    assert [list(d.delivered_memory_refs) for d in memory.deliveries] == [["r"], ["r", "d"]]
    assert "DISPOSITIONS" not in model.inputs[2][0]
    assert "DISPOSITIONS" in model.inputs[3][0]
    assert "I need a restful weekend." in model.inputs[1][0]
    assert "Walk" not in memory.events[0].source_event.text


def test_context_budget_keeps_delivery_refs_exact_and_never_injects_truncated_items():
    from memory_core_eval.personamem import bounded_context

    context = pb.MemoryContext(recollections=[
        pb.Recollection(memory_ref="r1", text="1234", application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER),
        pb.Recollection(memory_ref="r2", text="X" * 100, application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER),
    ])
    rendered = bounded_context(context, include_dispositions=True, token_count=len, limit=40)
    assert rendered.memory_refs == ("r1",)
    assert "1234" in rendered.text and "XXXXX" not in rendered.text


@pytest.mark.asyncio
async def test_settle_waits_for_completed_jobs_not_elapsed_guess():
    from memory_core_eval.personamem import wait_for_consolidation

    states = iter([
        {"episodes": 4, "jobs": 1, "pending_jobs": 1},
        {"episodes": 4, "jobs": 1, "pending_jobs": 0},
    ])
    result = await wait_for_consolidation(lambda: next(states), expected_episodes=4,
                                          timeout=1, interval=0.001)
    assert result["pending_jobs"] == 0


@pytest.mark.asyncio
async def test_settle_does_not_accept_empty_or_unfinished_ingestion():
    from memory_core_eval.personamem import wait_for_consolidation

    with pytest.raises(TimeoutError):
        await wait_for_consolidation(lambda: {"episodes": 1, "jobs": 0, "pending_jobs": 0},
                                     expected_episodes=2, timeout=0.002, interval=0.001)
