from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable

from memory_core import memory_pb2, render_memory_context

from .reference import HarnessProfile, MemoryClient, TextModel


MEMORY_AGENT_BENCH_COMMIT = "fe1735de8cf8b9908e1e3d3b5612afc815698062"
MEMORY_AGENT_BENCH_URL = (
    "https://github.com/HUST-AI-HYZ/MemoryAgentBench/tree/"
    + MEMORY_AGENT_BENCH_COMMIT
)


class MemoryAgentBenchAdapter:
    """Synchronous MemoryAgentBench boundary backed by the public Core RPCs."""

    def __init__(
        self,
        *,
        memory_factory: Callable[[], MemoryClient],
        model_factory: Callable[[], TextModel],
        profile: HarnessProfile,
        benchmark_context_ref: str,
        count_tokens: Callable[[str], int],
        settle_seconds: float = 5.0,
    ) -> None:
        if not benchmark_context_ref.strip():
            raise ValueError("benchmark_context_ref must be nonblank")
        if settle_seconds < 0:
            raise ValueError("settle_seconds must not be negative")
        self._profile = profile
        self._benchmark_context_ref = benchmark_context_ref
        self._count_tokens = count_tokens
        self._settle_seconds = settle_seconds
        self._memorized_count = 0
        self._query_count = 0
        self._memory_construction_time = 0.0
        self._memory_dirty = False
        self._closed = False
        self._runner = asyncio.Runner()
        try:
            self._memory, self._model = self._runner.run(
                _create_resources(memory_factory, model_factory)
            )
        except Exception:
            self._runner.close()
            raise

    def send_message(
        self,
        message: str,
        memorizing: bool = False,
        query_id: object | None = None,
        context_id: object | None = None,
    ) -> str | dict[str, str | int | float]:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be nonblank text")
        if self._closed:
            raise RuntimeError("MemoryAgentBenchAdapter is closed")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "MemoryAgentBenchAdapter.send_message is synchronous and cannot run "
                "inside an active event loop"
            )

        if memorizing:
            return self._runner.run(self._memorize(message))
        return self._runner.run(self._query(message, query_id, context_id))

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._runner.run(_close_resources(self._model, self._memory))
        finally:
            self._runner.close()
            self._closed = True

    def __enter__(self) -> MemoryAgentBenchAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def _memorize(self, message: str) -> str:
        started = time.perf_counter()
        chunk_number = self._memorized_count + 1
        turn_ref = f"memory-{chunk_number}"
        acknowledgement = "Memorized"
        await self._memory.observe_source_event(
            self._event_request(
                turn_ref=turn_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_SITUATION,
                text=message,
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_USER,
                actor_ref=self._profile.user_ref,
            )
        )
        await self._memory.observe_source_event(
            self._event_request(
                turn_ref=turn_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT,
                text=acknowledgement,
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_AGENT,
                actor_ref=self._profile.agent_ref,
            )
        )
        self._memorized_count = chunk_number
        self._memory_construction_time += time.perf_counter() - started
        self._memory_dirty = True
        return acknowledgement

    async def _query(
        self,
        message: str,
        query_id: object | None,
        context_id: object | None,
    ) -> dict[str, str | int | float]:
        if self._memory_dirty:
            settle_started = time.perf_counter()
            await asyncio.sleep(self._settle_seconds)
            self._memory_construction_time += time.perf_counter() - settle_started
            self._memory_dirty = False

        self._query_count += 1
        query_ref = self._query_ref(query_id, context_id)
        query_started = time.perf_counter()
        situation = await self._memory.observe_source_event(
            self._event_request(
                turn_ref=query_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_SITUATION,
                text=message,
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_USER,
                actor_ref=self._profile.user_ref,
            )
        )
        run_ref = self._identity(query_ref, "run")
        context = await self._memory.select_memory(
            memory_pb2.SelectMemoryRequest(
                scope=self._scope(),
                run_ref=run_ref,
                situation_source_event_refs=[situation.source_event_ref],
                constitution=self._profile.constitution,
            )
        )
        rendered = render_memory_context(context)
        instructions = self._profile.instructions
        if rendered.text:
            instructions = f"{instructions}\n\nLONG_TERM_MEMORY\n{rendered.text}"
            await self._memory.record_memory_delivery(
                memory_pb2.MemoryDeliveryReceipt(
                    idempotency_key=self._identity(query_ref, "delivery"),
                    scope=self._scope(),
                    run_ref=run_ref,
                    memory_context_ref=context.context_ref,
                    delivered_memory_refs=rendered.memory_refs,
                )
            )

        output = await self._model.complete(
            instructions=instructions,
            input_text=message,
            max_output_tokens=self._profile.max_output_tokens,
        )
        if not isinstance(output, str) or not output.strip():
            raise TypeError("MemoryAgentBench model requires non-empty text output")
        await self._memory.observe_source_event(
            self._event_request(
                turn_ref=query_ref,
                role=memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT,
                text=output,
                actor_kind=memory_pb2.SOURCE_ACTOR_KIND_AGENT,
                actor_ref=self._profile.agent_ref,
            )
        )
        query_time = time.perf_counter() - query_started
        input_text = instructions + "\n" + message
        memory_construction_time = self._memory_construction_time
        self._memory_construction_time = 0.0
        return {
            "output": output,
            "input_len": self._count_tokens(input_text),
            "output_len": self._count_tokens(output),
            "memory_construction_time": memory_construction_time,
            "query_time_len": query_time,
        }

    def _event_request(
        self,
        *,
        turn_ref: str,
        role: int,
        text: str,
        actor_kind: int,
        actor_ref: str,
    ) -> memory_pb2.ObserveSourceEventRequest:
        role_ref = {
            memory_pb2.EPISODE_SOURCE_ROLE_SITUATION: "situation",
            memory_pb2.EPISODE_SOURCE_ROLE_AGENT_ACT: "agent-act",
        }[role]
        return memory_pb2.ObserveSourceEventRequest(
            idempotency_key=self._identity(turn_ref, f"observe-{role_ref}"),
            source_event=memory_pb2.SourceEvent(
                scope=self._scope(),
                text=text,
                source_ref=self._identity(turn_ref, role_ref),
                actor_kind=actor_kind,
                actor_ref=actor_ref,
            ),
            episode_binding=memory_pb2.EpisodeBinding(
                run_ref=self._identity(turn_ref, "run"),
                source_group_ref=self._identity(turn_ref, "source-group"),
                role=role,
            ),
        )

    def _scope(self) -> memory_pb2.MemoryScope:
        owner_ref = ":".join(
            (
                self._profile.relationship_ref,
                self._profile.evaluation_ref,
                self._benchmark_context_ref,
            )
        )
        return memory_pb2.MemoryScope(
            tenant_ref=self._profile.tenant_ref,
            agent_ref=self._profile.agent_ref,
            relationship_ref=owner_ref,
            session_ref=self._identity("session", "scope"),
            kind=memory_pb2.MEMORY_SCOPE_KIND_RELATIONSHIP,
        )

    def _query_ref(
        self,
        query_id: object | None,
        context_id: object | None,
    ) -> str:
        query = str(query_id) if query_id is not None else f"auto-{self._query_count}"
        context = str(context_id) if context_id is not None else self._benchmark_context_ref
        return f"query-{context}-{query}"

    def _identity(self, turn_ref: str, kind: str) -> str:
        return ":".join(
            (
                "memory-agent-bench",
                self._profile.evaluation_ref,
                self._benchmark_context_ref,
                turn_ref,
                kind,
            )
        )


async def _create_resources(
    memory_factory: Callable[[], MemoryClient],
    model_factory: Callable[[], TextModel],
) -> tuple[MemoryClient, TextModel]:
    return memory_factory(), model_factory()


async def _close_resources(*resources: object) -> None:
    for resource in resources:
        close = getattr(resource, "close", None)
        if close is None:
            continue
        result = close()
        if inspect.isawaitable(result):
            await result
