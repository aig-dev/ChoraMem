from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

import grpc
import pytest

from memoryindex.v1 import memory_index_pb2 as index_pb
from memoryindex.v1 import memory_index_pb2_grpc as index_rpc


class _Encoding:
    def __init__(self, offsets: list[tuple[int, int]]) -> None:
        self.offsets = offsets


class _Tokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> _Encoding:
        assert add_special_tokens is False
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for token in text.split(" "):
            offsets.append((cursor, cursor + len(token)))
            cursor += len(token) + 1
        return _Encoding(offsets)


def test_chunk_text_preserves_tail_and_overlap() -> None:
    from memory_core_eval.local_memory_index import chunk_text

    text = "zero one two three four five six"

    assert chunk_text(_Tokenizer(), text, chunk_tokens=4, overlap_tokens=1) == [
        "zero one two three",
        "three four five six",
    ]


def test_episode_semantic_text_alpha_renames_projection_identities() -> None:
    from memory_core_eval.local_memory_index import semantic_index_text

    first = """EPISODE episode_owner_a
SESSION owner-a:session:7
AGENT_ACT
ACTOR agent agent-owner-a
SOURCE owner-a:assistant:7
The assistant listens before offering options.
SITUATION
ACTOR user user-owner-a
SOURCE owner-a:user:7
I need a moment before advice.
"""
    second = """EPISODE episode_owner_b
SESSION owner-b:session:7
AGENT_ACT
ACTOR agent agent-owner-b
SOURCE owner-b:assistant:7
The assistant listens before offering options.
SITUATION
ACTOR user user-owner-b
SOURCE owner-b:user:7
I need a moment before advice.
"""

    normalized = semantic_index_text(index_pb.MEMORY_KIND_EPISODE, first)

    assert normalized == semantic_index_text(index_pb.MEMORY_KIND_EPISODE, second)
    assert "owner-a" not in normalized
    assert "ACTOR agent <actor>" in normalized
    assert "I need a moment before advice." in normalized
    assert (
        semantic_index_text(index_pb.MEMORY_KIND_RECOLLECTION, first) == first
    )


def test_rank_unique_candidates_uses_best_chunk_distance() -> None:
    from memory_core_eval.local_memory_index import rank_unique_candidates

    result = rank_unique_candidates(
        {
            "metadatas": [
                [
                    {"kind": 1, "ref": "episode-b", "semantic_sha256": "b"},
                    {"kind": 1, "ref": "episode-a", "semantic_sha256": "a"},
                    {"kind": 1, "ref": "episode-b", "semantic_sha256": "b"},
                ],
                [
                    {
                        "kind": 2,
                        "ref": "recollection",
                        "semantic_sha256": "r",
                    },
                    {"kind": 1, "ref": "episode-a", "semantic_sha256": "a"},
                ],
            ],
            "distances": [[0.40, 0.30, 0.10], [0.20, 0.50]],
        },
        limit=2,
    )

    assert result == [
        (0.10, 1, "episode-b"),
        (0.20, 2, "recollection"),
    ]


def test_rank_unique_candidates_breaks_distance_ties_by_semantic_text() -> None:
    from memory_core_eval.local_memory_index import rank_unique_candidates

    result = rank_unique_candidates(
        {
            "metadatas": [
                [
                    {"kind": 1, "ref": "owner-a-z", "semantic_sha256": "alpha"},
                    {"kind": 1, "ref": "owner-a-a", "semantic_sha256": "beta"},
                ]
            ],
            "distances": [[0.25, 0.25]],
        },
        limit=2,
    )

    assert [ref for _, _, ref in result] == ["owner-a-z", "owner-a-a"]


def test_rank_exact_cosine_candidates_uses_best_query_and_document_chunk() -> None:
    from memory_core_eval.local_memory_index import rank_exact_cosine_candidates

    result = rank_exact_cosine_candidates(
        {
            "metadatas": [
                {"kind": 1, "ref": "episode-a", "semantic_sha256": "a"},
                {"kind": 1, "ref": "episode-a", "semantic_sha256": "a"},
                {"kind": 1, "ref": "episode-b", "semantic_sha256": "b"},
            ],
            "embeddings": [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
        },
        [[0.0, 1.0]],
        limit=2,
    )

    assert result == [(0.0, 1, "episode-a"), (1.0, 1, "episode-b")]


def test_search_ranks_all_scoped_chunks_before_applying_limit() -> None:
    from memory_core_eval.local_memory_index import ChromaMiniLMIndex

    class Collection:
        def __init__(self) -> None:
            self.includes = []

        def get(self, *, include, **_kwargs):
            self.includes.append(include)
            return {
                "ids": [str(index) for index in range(20)],
                "metadatas": [
                    {
                        "kind": 1,
                        "ref": f"opaque-{index}",
                        "semantic_sha256": f"{19 - index:02}",
                    }
                    for index in range(20)
                ],
                "embeddings": [[1.0, 0.0] for _ in range(20)],
            }

        def query(self, **_kwargs):
            raise AssertionError("approximate HNSW query must not rank eval evidence")

    provider = ChromaMiniLMIndex.__new__(ChromaMiniLMIndex)
    provider._token = ""
    provider._lock = threading.Lock()
    provider._encoder = lambda chunks: [[1.0, 0.0] for _ in chunks]
    provider._chunks = lambda text: [text]
    provider._log = lambda _record: None
    provider.collection = Collection()
    request = index_pb.SearchRequest(
        query=index_pb.Query(
            scope=index_pb.Scope(
                tenant_ref="tenant",
                agent_ref="agent",
                relationship_ref="relationship",
            ),
            text="same semantic query",
            limit=2,
        )
    )

    response = provider.Search(request, object())

    assert provider.collection.includes == [["metadatas", "embeddings"]]
    assert [candidate.ref for candidate in response.candidates] == [
        "opaque-19",
        "opaque-18",
    ]


def test_provider_revision_freezes_semantic_configuration() -> None:
    from memory_core_eval.local_memory_index import provider_revision

    assert provider_revision() == {
        "revision_ref": (
            "chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:"
            "chunk220-overlap32:episode-alpha-v1:"
            "exact-all-scoped-semantic-tiebreak-v1"
        ),
        "provider": "chroma",
        "provider_version": "1.5.5",
        "embedding_model": "all-MiniLM-L6-v2",
        "embedding_archive_sha256": (
            "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
        ),
        "dimensions": 384,
        "distance": "cosine",
        "chunk_tokens": 220,
        "overlap_tokens": 32,
        "semantic_projection": "episode_identity_alpha_v1",
        "search_algorithm": "exact_cosine_v1",
        "candidate_order": "distance,semantic_sha256,kind,ref",
        "query_scope": "all_scoped_chunks",
    }


@pytest.fixture
def real_index(tmp_path: Path):
    pytest.importorskip("chromadb")
    model_cache = os.environ.get("MEMORY_EVAL_MINILM_MODEL_CACHE")
    if not model_cache:
        pytest.skip("set MEMORY_EVAL_MINILM_MODEL_CACHE for the real index check")
    from memory_core_eval.local_memory_index import ChromaMiniLMIndex

    provider = ChromaMiniLMIndex(
        tmp_path / "chroma",
        tmp_path / "calls.jsonl",
        model_cache=Path(model_cache),
    )
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    index_rpc.add_MemoryIndexServicer_to_server(provider, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
        yield index_rpc.MemoryIndexStub(channel), provider
    server.stop(0).wait()


def test_real_index_recalls_paraphrase_and_isolates_owner(real_index) -> None:
    client, _ = real_index
    scope = index_pb.Scope(
        tenant_ref="test-tenant", agent_ref="agent", relationship_ref="alice"
    )
    other = index_pb.Scope(
        tenant_ref="test-tenant", agent_ref="agent", relationship_ref="bob"
    )
    query = "How should you comfort me when I am stressed?"
    client.Upsert(
        index_pb.UpsertRequest(
            documents=[
                index_pb.Document(
                    kind=index_pb.MEMORY_KIND_RECOLLECTION,
                    ref="listen",
                    scope=scope,
                    text=(
                        "When overwhelmed, I want someone to listen patiently "
                        "before suggesting solutions."
                    ),
                ),
                index_pb.Document(
                    kind=index_pb.MEMORY_KIND_RECOLLECTION,
                    ref="sport",
                    scope=scope,
                    text="I enjoy high altitude mountaineering and rock climbing.",
                ),
                index_pb.Document(
                    kind=index_pb.MEMORY_KIND_RECOLLECTION,
                    ref="wrong-owner",
                    scope=other,
                    text=query,
                ),
            ]
        )
    )

    response = client.Search(
        index_pb.SearchRequest(
            query=index_pb.Query(scope=scope, text=query, limit=1)
        )
    )

    assert [(item.kind, item.ref) for item in response.candidates] == [
        (index_pb.MEMORY_KIND_RECOLLECTION, "listen")
    ]


def test_real_index_retrieves_long_episode_tail_and_replaces_chunks(real_index) -> None:
    client, provider = real_index
    scope = index_pb.Scope(
        tenant_ref="test-tenant", agent_ref="agent", relationship_ref="alice"
    )
    text = (
        "The boat crossed the sea under clear skies. " * 90
        + "My preferred bedtime beverage is peppermint infusion."
    )
    client.Upsert(
        index_pb.UpsertRequest(
            documents=[
                index_pb.Document(
                    kind=index_pb.MEMORY_KIND_EPISODE,
                    ref="long-episode",
                    scope=scope,
                    text=text,
                )
            ]
        )
    )
    assert provider.collection.count() > 1

    response = client.Search(
        index_pb.SearchRequest(
            query=index_pb.Query(
                scope=scope,
                text="What herbal drink do I like before sleep?",
                limit=1,
            )
        )
    )
    assert [item.ref for item in response.candidates] == ["long-episode"]

    client.Upsert(
        index_pb.UpsertRequest(
            documents=[
                index_pb.Document(
                    kind=index_pb.MEMORY_KIND_EPISODE,
                    ref="long-episode",
                    scope=scope,
                    text="I now prefer hiking.",
                )
            ]
        )
    )
    assert provider.collection.count() == 1
