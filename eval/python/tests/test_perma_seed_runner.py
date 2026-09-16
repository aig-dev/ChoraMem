from datetime import date

import pytest

from memory_core import memory_pb2 as pb

from memory_core_eval.perma_seed_data import (
    Message,
    PersonaData,
    Probe,
    TimelineSession,
)
from memory_core_eval.perma_seed_runner import run_persona


class RecordingMemory:
    def __init__(self):
        self.observed = []
        self.selected = []

    async def observe_source_event(self, request):
        self.observed.append(request)
        return pb.SourceEventReceipt(
            source_event_ref=request.source_event.source_ref,
            episode_ref=(
                "episode:" + request.episode_binding.run_ref
                if request.HasField("episode_binding")
                else ""
            ),
        )

    async def select_memory(self, request):
        self.selected.append(request)
        return pb.MemoryContext(
            context_ref="context:" + request.run_ref,
            run_ref=request.run_ref,
            scope=request.scope,
            recollections=[pb.Recollection(
                memory_ref="rec@1",
                text="the user often chooses tea",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )],
            dispositions=[pb.Disposition(
                memory_ref="seed@1",
                text="offer tea before making another suggestion",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )],
        )

    async def record_memory_delivery(self, _request):
        raise AssertionError("PERMA MCQ must be read-only")

    async def report_outcome(self, _request):
        raise AssertionError("PERMA MCQ must not fabricate outcomes")


class ContextSensitiveModel:
    async def complete(self, *, instructions, input_text, max_output_tokens):
        assert input_text.startswith("QUESTION\n")
        assert max_output_tokens == 32_768
        if "DISPOSITIONS" in instructions:
            return "C"
        if "RECOLLECTIONS" in instructions:
            return "B"
        return "A"


@pytest.mark.asyncio
async def test_runner_ingests_timeline_once_and_uses_one_read_only_select_per_probe(tmp_path):
    persona = PersonaData(
        persona_id="1377",
        sessions=(
            TimelineSession(
                "early",
                date(2023, 1, 1),
                (
                    Message("user", "early user one"),
                    Message("assistant", "early assistant one"),
                    Message("user", "early user two"),
                    Message("assistant", "early assistant two"),
                ),
            ),
            TimelineSession(
                "late",
                date(2023, 2, 1),
                (Message("user", "late user"), Message("assistant", "late assistant")),
                "late unanswered user",
            ),
        ),
        probes=(
            Probe(
                "1377:task:2",
                "1377",
                "task",
                2,
                date(2023, 1, 15),
                "first question",
                "A. x\nB. y\nC. z",
            ),
            Probe(
                "1377:task:3",
                "1377",
                "task",
                3,
                date(2023, 3, 1),
                "second question",
                "A. x\nB. y\nC. z",
            ),
        ),
        labels={"1377:task:2": "C", "1377:task:3": "C"},
    )
    memory = RecordingMemory()
    settled = []

    async def settle(scope, expected_episodes):
        settled.append((scope.session_ref, expected_episodes))
        return {"dispositions": [{"ref": "seed@1", "status": "active"}]}

    results = await run_persona(
        persona=persona,
        split="H1",
        evaluation_ref="perma-test",
        memory=memory,
        settle=settle,
        model=ContextSensitiveModel(),
        output_dir=tmp_path,
        token_count=lambda text: len(text.split()),
        memory_tokens=2_048,
        max_output_tokens=32_768,
    )

    bound = [request for request in memory.observed if request.HasField("episode_binding")]
    unbound = [request for request in memory.observed if not request.HasField("episode_binding")]
    assert len(bound) == 6
    assert [request.source_event.text for request in bound] == [
        "early user one",
        "early assistant one",
        "early user two",
        "early assistant two",
        "late user",
        "late assistant",
    ]
    assert [request.source_event.text for request in unbound] == [
        "first question",
        "late unanswered user",
        "second question",
    ]
    assert settled == [
        ("perma-test:1377:session:0", 2),
        ("perma-test:1377:session:1", 3),
    ]
    assert len(memory.selected) == 2
    assert all(request.episode_evidence_max_bytes == 0 for request in memory.selected)

    assert len(results) == 2
    assert [answer["mode"] for answer in results[0]["answers"]] == [
        "none",
        "recollection_only",
        "seed_enabled",
    ]
    assert [answer["predicted_option"] for answer in results[0]["answers"]] == [
        "A",
        "B",
        "C",
    ]
    assert results[0]["answers"][2]["correct"] is True
    assert results[0]["diagnostic"] == {
        "active_dispositions": 1,
        "selected_disposition_refs": ["seed@1"],
        "rendered_disposition_refs": ["seed@1"],
    }
    assert len(list((tmp_path / "questions").glob("*.json"))) == 2
