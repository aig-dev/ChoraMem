from __future__ import annotations

import grpc
import pytest

from memory_core import memory_pb2 as pb


class RecordingCore:
    def __init__(self) -> None:
        self.observed: list[pb.ObserveSourceEventRequest] = []
        self.selected: list[pb.SelectMemoryRequest] = []
        self.deliveries: list[pb.MemoryDeliveryReceipt] = []

    def ObserveSourceEvent(self, request, **_options):
        copied = pb.ObserveSourceEventRequest()
        copied.CopyFrom(request)
        self.observed.append(copied)
        return pb.SourceEventReceipt(
            source_event_ref=f"source-{len(self.observed)}",
            episode_ref=f"episode-{(len(self.observed) + 1) // 2}",
        )

    def SelectMemory(self, request, **_options):
        copied = pb.SelectMemoryRequest()
        copied.CopyFrom(request)
        self.selected.append(copied)
        return pb.MemoryContext(
            context_ref="context-1",
            run_ref=request.run_ref,
            scope=request.scope,
            recollections=[pb.Recollection(
                memory_ref="recollection-1",
                text="The user prefers quiet mornings.",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER,
            )],
            episode_evidence=[pb.EpisodeEvidence(
                memory_ref="episode-1",
                text="USER: I begin each day with yoga.\nAGENT: That sounds grounding.",
            )],
        )

    def RecordMemoryDelivery(self, request, **_options):
        copied = pb.MemoryDeliveryReceipt()
        copied.CopyFrom(request)
        self.deliveries.append(copied)
        return pb.ReceiptAck(receipt_ref=f"delivery-{len(self.deliveries)}")


def client(core: RecordingCore):
    from memory_core_eval.omnimemeval_adapter import MemoryCoreClient

    return MemoryCoreClient(
        tenant_ref="omni-test",
        agent_ref="agent-test",
        token="secret",
        episode_evidence_max_bytes=16_384,
        _stub=core,
    )


class AbortedRpc(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.ABORTED


@pytest.mark.parametrize("budget", [1.5, "1.5"])
def test_client_rejects_fractional_episode_evidence_budget(budget):
    from memory_core_eval.omnimemeval_adapter import MemoryCoreClient

    with pytest.raises(ValueError, match="integer"):
        MemoryCoreClient(
            tenant_ref="omni-test",
            agent_ref="agent-test",
            token="secret",
            episode_evidence_max_bytes=budget,
            _stub=RecordingCore(),
        )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_client_rejects_nonpositive_or_nonfinite_timeout(timeout):
    from memory_core_eval.omnimemeval_adapter import MemoryCoreClient

    with pytest.raises(ValueError, match="positive finite"):
        MemoryCoreClient(
            tenant_ref="omni-test",
            agent_ref="agent-test",
            token="secret",
            timeout=timeout,
            _stub=RecordingCore(),
        )


def test_client_retries_aborted_core_write_without_changing_request(monkeypatch):
    from memory_core_eval.omnimemeval_adapter import MemoryCoreClient

    class AbortOnceCore(RecordingCore):
        def __init__(self):
            super().__init__()
            self.attempts = []

        def ObserveSourceEvent(self, request, **options):
            copied = pb.ObserveSourceEventRequest()
            copied.CopyFrom(request)
            self.attempts.append((copied, options))
            if len(self.attempts) == 1:
                raise AbortedRpc()
            return super().ObserveSourceEvent(request, **options)

    delays = []
    monkeypatch.setattr(
        "memory_core_eval.omnimemeval_adapter.time.sleep",
        lambda delay: delays.append(delay),
    )
    core = AbortOnceCore()
    adapter = MemoryCoreClient(
        tenant_ref="omni-test",
        agent_ref="agent-test",
        token="secret",
        episode_evidence_max_bytes=16_384,
        rpc_attempts=3,
        _stub=core,
    )
    adapter.add([
        {"role": "user", "content": "I begin each day with yoga."},
        {"role": "assistant", "content": "That sounds grounding."},
    ], "user-1")

    assert len(core.attempts) == 3
    assert core.attempts[0] == core.attempts[1]
    assert len(core.observed) == 2
    assert delays == [0.05]


@pytest.mark.parametrize("attempts", [True, 0, -1, 1.5, "1.5"])
def test_client_rejects_invalid_rpc_attempt_count(attempts):
    from memory_core_eval.omnimemeval_adapter import MemoryCoreClient

    with pytest.raises(ValueError, match="positive integer"):
        MemoryCoreClient(
            tenant_ref="omni-test",
            agent_ref="agent-test",
            token="secret",
            rpc_attempts=attempts,
            _stub=RecordingCore(),
        )


def test_add_maps_only_real_alternating_text_to_complete_episodes_with_stable_ids():
    core = RecordingCore()
    adapter = client(core)
    messages = [
        {"role": "system", "content": "hidden benchmark profile", "preference": "gold"},
        {"role": "user", "content": "I begin each day with yoga.", "preference": "gold"},
        {"role": "assistant", "content": "That sounds grounding.", "preference": "gold"},
    ]

    adapter.add(messages, "pm_exper_user_7_v1")
    first_ids = [(item.idempotency_key, item.source_event.source_ref) for item in core.observed]
    second_core = RecordingCore()
    client(second_core).add(messages, "pm_exper_user_7_v1")

    assert len(core.observed) == 2
    assert first_ids == [
        (item.idempotency_key, item.source_event.source_ref)
        for item in second_core.observed
    ]
    first, second = core.observed[:2]
    assert first.episode_binding.role == pb.EPISODE_SOURCE_ROLE_SITUATION
    assert second.episode_binding.role == pb.EPISODE_SOURCE_ROLE_AGENT_ACT
    assert first.episode_binding.run_ref == second.episode_binding.run_ref
    assert first.episode_binding.source_group_ref == second.episode_binding.source_group_ref
    assert [item.source_event.text for item in core.observed[:2]] == [
        "I begin each day with yoga.",
        "That sounds grounding.",
    ]
    assert all("gold" not in item.source_event.text for item in core.observed)
    assert all(item.source_event.scope.relationship_ref == "pm_exper_user_7_v1"
               for item in core.observed)


def test_add_carries_an_incomplete_pair_across_omni_batches_and_merges_same_role():
    core = RecordingCore()
    adapter = client(core)

    adapter.add([
        {"role": "system", "content": "profile"},
        {"role": "user", "content": "first paragraph"},
    ], "user-1")
    assert core.observed == []

    adapter.add([
        {"role": "user", "content": "second paragraph"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "trailing unpaired history"},
    ], "user-1")

    assert [item.source_event.text for item in core.observed] == [
        "first paragraph\n\nsecond paragraph",
        "reply",
    ]


def test_add_keeps_incomplete_dialogue_and_pair_indexes_isolated_per_user():
    core = RecordingCore()
    adapter = client(core)

    adapter.add([{"role": "user", "content": "user one pending"}], "user-1")
    adapter.add([
        {"role": "user", "content": "user two"},
        {"role": "assistant", "content": "reply two"},
    ], "user-2")
    adapter.add([{"role": "assistant", "content": "reply one"}], "user-1")

    assert [item.source_event.text for item in core.observed] == [
        "user two",
        "reply two",
        "user one pending",
        "reply one",
    ]
    assert core.observed[0].source_event.scope.relationship_ref == "user-2"
    assert core.observed[2].source_event.scope.relationship_ref == "user-1"
    assert ":history:0:" in core.observed[0].episode_binding.run_ref
    assert ":history:0:" in core.observed[2].episode_binding.run_ref


@pytest.mark.parametrize("messages", [
    [
        {"role": "assistant", "content": "wrong first role"},
        {"role": "user", "content": "wrong second role"},
    ],
    [{"role": "tool", "content": "unsupported"}],
])
def test_add_rejects_history_that_cannot_start_a_real_episode(messages):
    core = RecordingCore()
    with pytest.raises(ValueError, match="alternat"):
        client(core).add(messages, "user-1")
    assert core.observed == []


def test_search_records_an_unbound_query_and_returns_only_rendered_core_context():
    core = RecordingCore()
    text = client(core).search(
        "What morning routine suits me?",
        "pm_exper_user_7_v1",
        top_k=20,
    )

    assert len(core.observed) == 1
    query = core.observed[0]
    assert query.source_event.text == "What morning routine suits me?"
    assert not query.HasField("episode_binding")
    assert len(core.selected) == 1
    assert core.selected[0].situation_source_event_refs == ["source-1"]
    assert core.selected[0].episode_evidence_max_bytes == 16_384
    assert "RECOLLECTIONS" in text
    assert "quiet mornings" in text
    assert "EPISODE EVIDENCE" in text
    assert "I begin each day with yoga" in text
    assert "context-1" not in text
    assert len(core.deliveries) == 1
    delivery = core.deliveries[0]
    assert delivery.scope == core.selected[0].scope
    assert delivery.run_ref == core.selected[0].run_ref
    assert delivery.memory_context_ref == "context-1"
    assert list(delivery.delivered_memory_refs) == ["recollection-1", "episode-1"]
