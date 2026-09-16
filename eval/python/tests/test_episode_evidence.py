from types import SimpleNamespace

import pytest
from memory_core import memory_pb2 as pb

from memory_core_eval.personamem import bounded_context
from memory_core_eval.personamem_data import History, Question


def test_full_core_eval_delivers_episode_only_context_within_total_budget():
    context = pb.MemoryContext(episode_evidence=[pb.EpisodeEvidence(
        memory_ref="historical-episode",
        text="SITUATION [user]\nQuiet walks help me rest.\nAGENT_ACT [agent]\nI understand.",
    )])
    rendered = bounded_context(context, include_dispositions=True, token_count=len, limit=256)
    assert "> Quiet walks help me rest." in rendered.text
    assert rendered.memory_refs == ("historical-episode",)
    assert len(rendered.text) <= 256
    assert "historical-episode" not in rendered.text


def test_recollection_only_eval_removes_dispositions_and_raw_evidence():
    context = pb.MemoryContext(
        recollections=[pb.Recollection(memory_ref="r", text="quiet walks",
            application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER)],
        dispositions=[pb.Disposition(memory_ref="d", text="offer a walk",
            application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION)],
        episode_evidence=[pb.EpisodeEvidence(memory_ref="e", text="raw history")],
    )
    rendered = bounded_context(context, include_dispositions=False, token_count=len, limit=256)
    assert rendered.memory_refs == ("r",)
    assert "offer a walk" not in rendered.text
    assert "raw history" not in rendered.text


@pytest.mark.parametrize("value", [-1, True, 16_385])
def test_personamem_harness_rejects_invalid_episode_evidence_budget(value):
    from memory_core_eval.personamem import PersonaMemHarness

    with pytest.raises(ValueError, match="episode_evidence_max_bytes"):
        PersonaMemHarness(
            memory=object(),
            model=object(),
            scorer=object(),
            settle=lambda *_: None,
            evaluation_ref="run",
            token_count=len,
            episode_evidence_max_bytes=value,
        )


@pytest.mark.asyncio
async def test_harness_enables_episode_evidence_only_for_full_core():
    from memory_core_eval.personamem import PersonaMemHarness

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
                context_ref=f"context-{len(self.selects)}",
                run_ref=request.run_ref,
                scope=request.scope,
                recollections=[pb.Recollection(
                    memory_ref="r",
                    text="quiet walks",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER,
                )],
                dispositions=[pb.Disposition(
                    memory_ref="d",
                    text="offer a walk",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
                )],
                episode_evidence=[pb.EpisodeEvidence(
                    memory_ref="e",
                    text="SITUATION [user]\nQuiet walks help me rest.\nAGENT_ACT [agent]\nUnderstood.",
                )],
            )

        async def record_memory_delivery(self, request):
            self.deliveries.append(request)
            return pb.ReceiptAck(receipt_ref="receipt")

    class Model:
        def __init__(self):
            self.instructions = []

        async def complete(self, *, instructions, input_text, max_output_tokens):
            self.instructions.append(instructions)
            return "Final Answer: B"

    memory, model = Memory(), Model()
    scorer = SimpleNamespace(
        create_mcq_options=lambda *_args, **_kwargs: (
            "A. Other\nB. Walk",
            {"A": "Other", "B": "Walk"},
        ),
        extract_final_answer=lambda *_: "B",
        check_mcq_correctness=lambda *_: True,
    )
    harness = PersonaMemHarness(
        memory=memory,
        model=model,
        scorer=scorer,
        settle=lambda *_: None,
        evaluation_ref="run",
        token_count=len,
        memory_tokens=1_000,
        episode_evidence_max_bytes=512,
    )
    question = Question(
        "q",
        "p",
        "history.json",
        "What helps me rest?",
        "Walk",
        ("Other", "Third", "Fourth"),
        {},
    )

    results = await harness.evaluate(question, History("profile", (), ""))

    assert [request.episode_evidence_max_bytes for request in memory.selects] == [0, 512]
    assert all(not event.HasField("episode_binding") for event in memory.events)
    assert [list(receipt.delivered_memory_refs) for receipt in memory.deliveries] == [
        ["r"],
        ["r", "d", "e"],
    ]
    assert results[2]["memory_refs"] == ["r"]
    assert results[3]["memory_refs"] == ["r", "d", "e"]
    assert "EPISODE EVIDENCE" not in model.instructions[2]
    assert "EPISODE EVIDENCE" in model.instructions[3]
