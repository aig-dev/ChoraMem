from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

import pytest

from memory_core import memory_pb2 as pb

from memory_core_eval.seed_lifecycle import (
    LifecycleKind,
    lifecycle_cases,
    run_lifecycle_case,
    run_lifecycle_suite,
    summarize_lifecycle,
)


def _state(*, active="seed@1", roles=()):
    dispositions = [
        {
            "ref": "seed@1",
            "text": "When the user is overwhelmed by complex planning, first help them name priorities before offering solutions.",
            "status": "active" if active == "seed@1" else "superseded",
            "version_number": 1,
        }
    ]
    if active == "seed@2":
        dispositions.append(
            {
                "ref": "seed@2",
                "text": "When the user is overwhelmed, offer one concrete next step without asking them to sort priorities.",
                "status": "active",
                "version_number": 2,
            }
        )
    return {
        "recollections": [],
        "dispositions": dispositions,
        "basis": {
            "recollection_basis_links": [],
            "seed_basis_links": [
                {"ref": "seed@1", "episode_ref": "formation-1", "role": "formation"},
                {"ref": "seed@1", "episode_ref": "formation-2", "role": "formation"},
                {"ref": "seed@1", "episode_ref": "formation-3", "role": "formation"},
                *[
                    {"ref": ref, "episode_ref": "feedback-episode", "role": role}
                    for ref, role in roles
                ],
            ],
        },
        "jobs": [],
    }


class FakeMemory:
    def __init__(
        self,
        *,
        delayed_dispositions=("seed@1",),
        select_sequence=None,
    ):
        self.calls = defaultdict(list)
        self.delayed_dispositions = tuple(delayed_dispositions)
        self.select_sequence = (
            [tuple(refs) for refs in select_sequence]
            if select_sequence is not None
            else None
        )
        self.select_count = 0

    async def observe_source_event(self, request):
        self.calls["observe"].append(request)
        is_agent_act = (
            request.episode_binding.role == pb.EPISODE_SOURCE_ROLE_AGENT_ACT
        )
        return pb.SourceEventReceipt(
            source_event_ref=request.source_event.source_ref,
            episode_ref="feedback-episode" if is_agent_act else "",
        )

    async def select_memory(self, request):
        self.calls["select"].append(request)
        self.select_count += 1
        if self.select_sequence is not None:
            refs = self.select_sequence.pop(0)
        else:
            refs = ("seed@1",) if self.select_count == 1 else self.delayed_dispositions
        context = pb.MemoryContext(
            context_ref=f"context-{self.select_count}",
            run_ref=request.run_ref,
            scope=request.scope,
        )
        context.recollections.add(
            memory_ref="recollection@1",
            text="The user has repeatedly felt overwhelmed by complex planning.",
            application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER,
        )
        for ref in refs:
            text = (
                "When the user is overwhelmed by complex planning, first help them name priorities before offering solutions."
                if ref == "seed@1"
                else "When the user is overwhelmed, offer one concrete next step without asking them to sort priorities."
            )
            context.dispositions.add(
                memory_ref=ref,
                text=text,
                application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
            )
        return context

    async def record_memory_delivery(self, request):
        self.calls["delivery"].append(request)
        return pb.ReceiptAck(receipt_ref="delivery-1")

    async def report_outcome(self, request):
        self.calls["outcome"].append(request)
        return pb.OutcomeReceipt(
            outcome_event_ref="outcome-1", episode_ref="feedback-episode"
        )


class FixedModel:
    def __init__(self, outputs=("A", "A")):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "after", "delayed", "delivery_count", "outcome_count"),
    [
        (
            LifecycleKind.POSITIVE_COMPLETE,
            _state(roles=(("seed@1", "reenactment"),)),
            ("seed@1",),
            1,
            1,
        ),
        (LifecycleKind.NO_DELIVERY, _state(), ("seed@1",), 0, 1),
        (
            LifecycleKind.NO_OUTCOME,
            _state(roles=(("seed@1", "reenactment"),)),
            ("seed@1",),
            1,
            0,
        ),
        (
            LifecycleKind.NEGATIVE_COMPLETE,
            _state(active="seed@2", roles=(("seed@2", "revision"),)),
            ("seed@2",),
            1,
            1,
        ),
    ],
)
async def test_lifecycle_uses_exact_runtime_chain_and_classifies_seed_effect(
    kind, after, delayed, delivery_count, outcome_count
):
    case = next(item for item in lifecycle_cases() if item.kind is kind)
    memory = FakeMemory(delayed_dispositions=delayed)
    states = [_state(), _state(), after]
    settle_calls = []

    async def settle(scope, expected_episodes):
        settle_calls.append((scope.session_ref, expected_episodes))
        return states.pop(0)

    model = FixedModel(("A", "B" if kind is LifecycleKind.NEGATIVE_COMPLETE else "A"))
    result = await run_lifecycle_case(
        case=case,
        evaluation_ref="seed-lifecycle-test",
        memory=memory,
        settle=settle,
        model=model,
        token_count=len,
        memory_tokens=2_048,
        max_output_tokens=128,
    )

    assert [count for _, count in settle_calls] == [1, 2, 3]
    assert len(memory.calls["delivery"]) == delivery_count
    assert len(memory.calls["outcome"]) == outcome_count
    assert result["formed_seed_refs"] == ["seed@1"]
    assert result["rendered_disposition_refs"] == ["seed@1"]
    assert result["agent_act_option"] == "A"
    assert result["agent_act_text"] == (
        "Let's pause before solutions and name the top priority first: "
        "what matters most here?"
    )
    formation_events = [
        call
        for call in memory.calls["observe"]
        if ":formation-" in call.source_event.source_ref
    ]
    assert len(formation_events) == 4
    feedback_situations = [
        call.source_event.text
        for call in memory.calls["observe"]
        if call.source_event.source_ref.endswith("feedback:situation")
    ]
    assert feedback_situations == [
        "This complicated plan has left me overwhelmed and unable to tell where to begin."
    ]

    if outcome_count:
        outcome = memory.calls["outcome"][0]
        assert outcome.actor_kind == pb.SOURCE_ACTOR_KIND_USER
        assert outcome.related_source_event_refs[-1].endswith(":agent-act")
        assert list(outcome.delivery_receipt_refs) == (
            ["delivery-1"] if delivery_count else []
        )
        if kind is LifecycleKind.NEGATIVE_COMPLETE:
            assert "would have been manageable" in outcome.text
            assert "please" not in outcome.text.lower()

    if kind is LifecycleKind.NEGATIVE_COMPLETE:
        assert result["revision_or_inhibition"] is True
        assert result["future_selection_changed"] is True
    else:
        assert result["revision_or_inhibition"] is False


def test_lifecycle_summary_requires_all_four_causal_cases():
    rows = [
        {
            "kind": "positive_complete",
            "formed": True,
            "rendered": True,
            "agent_act_expressed_tendency": True,
            "reenacted": True,
            "revision_or_inhibition": False,
            "future_selection_changed": False,
        },
        {
            "kind": "no_delivery",
            "formed": True,
            "rendered": True,
            "agent_act_expressed_tendency": True,
            "reenacted": False,
            "revision_or_inhibition": False,
            "future_selection_changed": False,
        },
        {
            "kind": "no_outcome",
            "formed": True,
            "rendered": True,
            "agent_act_expressed_tendency": True,
            "reenacted": True,
            "revision_or_inhibition": False,
            "future_selection_changed": False,
        },
        {
            "kind": "negative_complete",
            "formed": True,
            "rendered": True,
            "agent_act_expressed_tendency": True,
            "reenacted": False,
            "revision_or_inhibition": True,
            "future_selection_changed": True,
        },
    ]

    assert summarize_lifecycle(rows)["decision"]["passed"] is True
    assert summarize_lifecycle(rows[:-1])["decision"]["passed"] is False


@pytest.mark.asyncio
async def test_lifecycle_suite_forms_once_then_runs_controls_on_same_seed(tmp_path):
    memory = FakeMemory(
        select_sequence=[
            ("seed@1",), ("seed@1",),
            ("seed@1",), ("seed@1",),
            ("seed@1",), ("seed@1",),
            ("seed@1",), ("seed@2",),
        ]
    )

    def state_with_roles(*entries, active="seed@1"):
        state = _state(active=active)
        state["basis"]["seed_basis_links"].extend(
            {
                "ref": ref,
                "episode_ref": episode,
                "role": role,
            }
            for ref, episode, role in entries
        )
        return state

    states = [
        _state(active=""),
        _state(active=""),
        _state(),
        _state(),
        state_with_roles(("seed@1", "feedback-4", "reenactment")),
        state_with_roles(
            ("seed@1", "feedback-4", "reenactment"),
            ("seed@1", "feedback-5", "reenactment"),
        ),
        state_with_roles(
            ("seed@1", "feedback-4", "reenactment"),
            ("seed@1", "feedback-5", "reenactment"),
            ("seed@2", "feedback-6", "revision"),
            active="seed@2",
        ),
    ]
    expected_counts = []

    async def settle(_scope, expected_episodes):
        expected_counts.append(expected_episodes)
        return states.pop(0)

    summary = await run_lifecycle_suite(
        evaluation_ref="shared-seed-suite",
        memory=memory,
        settle=settle,
        model=FixedModel(("A",) * 8),
        output_dir=tmp_path,
        token_count=len,
        memory_tokens=2_048,
        max_output_tokens=128,
    )

    assert summary["decision"]["passed"] is True
    assert summary["cases"] == [
        "no_delivery",
        "positive_complete",
        "no_outcome",
        "negative_complete",
    ]
    assert expected_counts == [1, 2, 3, 4, 5, 6, 7]
    formation_events = [
        call
        for call in memory.calls["observe"]
        if ":formation-" in call.source_event.source_ref
    ]
    assert len(formation_events) == 6
