"""Reproducible eval-only Chroma/MiniLM MemoryIndex provider.

The relation store remains authoritative. This process only stores a disposable
semantic projection and returns ordered ``(kind, ref)`` candidates over the
public MemoryIndex contract.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Mapping, Sequence

import grpc

from memoryindex.v1 import memory_index_pb2 as index_pb
from memoryindex.v1 import memory_index_pb2_grpc as index_rpc


CHROMA_VERSION = "1.5.5"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_ARCHIVE_SHA256 = (
    "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
)
EMBEDDING_DIMENSIONS = 384
DISTANCE = "cosine"
CHUNK_TOKENS = 220
OVERLAP_TOKENS = 32
COLLECTION_NAME = "memory-core-eval-minilm-v1"
TOKEN_METADATA_KEY = "x-agent-rpc-token"
PROVIDER_REVISION_REF = (
    "chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:"
    "chunk220-overlap32:episode-alpha-v1:"
    "exact-all-scoped-semantic-tiebreak-v1"
)


def provider_revision() -> dict[str, str | int]:
    """Return the semantic configuration that must be frozen with an eval."""

    return {
        "revision_ref": PROVIDER_REVISION_REF,
        "provider": "chroma",
        "provider_version": CHROMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_archive_sha256": EMBEDDING_ARCHIVE_SHA256,
        "dimensions": EMBEDDING_DIMENSIONS,
        "distance": DISTANCE,
        "chunk_tokens": CHUNK_TOKENS,
        "overlap_tokens": OVERLAP_TOKENS,
        "semantic_projection": "episode_identity_alpha_v1",
        "search_algorithm": "exact_cosine_v1",
        "candidate_order": "distance,semantic_sha256,kind,ref",
        "query_scope": "all_scoped_chunks",
    }


def chunk_text(
    tokenizer: Any,
    text: str,
    *,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[str]:
    """Split text by model-token offsets while retaining the final chunk."""

    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens must be in [0, chunk_tokens)")
    offsets = tokenizer.encode(text, add_special_tokens=False).offsets
    if not offsets:
        return [text]
    chunks: list[str] = []
    step = chunk_tokens - overlap_tokens
    for start in range(0, len(offsets), step):
        end = min(start + chunk_tokens, len(offsets))
        chunks.append(text[offsets[start][0] : offsets[end - 1][1]])
        if end == len(offsets):
            break
    return chunks


def semantic_index_text(kind: int, text: str) -> str:
    """Remove Core-owned Episode identities from the embedded projection.

    Episode refs, session refs, source refs, and actor refs identify evidence in
    the relation store; they are not semantic content.  Replacing only the
    structural header fields keeps equivalent isolated owners on the same
    vector projection without weakening owner-scoped retrieval or provenance.
    """

    if kind != index_pb.MEMORY_KIND_EPISODE:
        return text
    lines = text.splitlines(keepends=True)
    if (
        len(lines) < 2
        or not lines[0].removesuffix("\n").removesuffix("\r").startswith(
            "EPISODE "
        )
        or not lines[1].removesuffix("\n").removesuffix("\r").startswith(
            "SESSION "
        )
    ):
        return text

    def replace_line(line: str, value: str) -> str:
        if line.endswith("\r\n"):
            return value + "\r\n"
        if line.endswith("\n"):
            return value + "\n"
        return value

    lines[0] = replace_line(lines[0], "EPISODE <episode>")
    lines[1] = replace_line(lines[1], "SESSION <session>")
    roles = {"AGENT_ACT", "OUTCOME", "SITUATION"}
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") not in roles:
            continue
        structural = index + 1
        if structural < len(lines) and lines[structural].startswith("ACTOR "):
            parts = lines[structural].rstrip("\r\n").split(" ", 2)
            if len(parts) == 3:
                lines[structural] = replace_line(
                    lines[structural], f"ACTOR {parts[1]} <actor>"
                )
                structural += 1
        if structural < len(lines) and lines[structural].startswith("SOURCE "):
            lines[structural] = replace_line(lines[structural], "SOURCE <source>")
    return "".join(lines)


def rank_unique_candidates(
    result: Mapping[str, Sequence[Sequence[Any]]],
    *,
    limit: int,
) -> list[tuple[float, int, str]]:
    """Collapse duplicate chunks and query chunks by minimum cosine distance."""

    if limit <= 0:
        return []
    best: dict[tuple[int, str], tuple[float, str]] = {}
    metadatas = result.get("metadatas", ())
    distances = result.get("distances", ())
    for metadata_row, distance_row in zip(metadatas, distances, strict=True):
        for metadata, distance in zip(metadata_row, distance_row, strict=True):
            identity = int(metadata["kind"]), str(metadata["ref"])
            semantic = str(metadata["semantic_sha256"])
            previous = best.get(identity)
            if previous is not None and previous[1] != semantic:
                raise ValueError("one indexed identity has conflicting semantic hashes")
            if previous is None or float(distance) < previous[0]:
                best[identity] = float(distance), semantic
    ordered = sorted(
        (distance, semantic, kind, ref)
        for (kind, ref), (distance, semantic) in best.items()
    )[:limit]
    return [(distance, kind, ref) for distance, _, kind, ref in ordered]


def rank_exact_cosine_candidates(
    result: Mapping[str, Any],
    query_embeddings: Sequence[Sequence[float]],
    *,
    limit: int,
) -> list[tuple[float, int, str]]:
    """Rank every scoped stored vector by exact cosine distance."""

    if limit <= 0:
        return []
    stored_embeddings = result.get("embeddings")
    metadatas = result.get("metadatas")
    if stored_embeddings is None or metadatas is None or len(metadatas) == 0:
        return []

    import numpy as np

    documents = np.asarray(stored_embeddings, dtype=np.float64)
    queries = np.asarray(query_embeddings, dtype=np.float64)
    if (
        documents.ndim != 2
        or queries.ndim != 2
        or documents.shape[0] != len(metadatas)
        or documents.shape[1] != queries.shape[1]
        or not np.isfinite(documents).all()
        or not np.isfinite(queries).all()
    ):
        raise ValueError("MemoryIndex embeddings have an invalid shape or value")
    document_norms = np.linalg.norm(documents, axis=1)
    query_norms = np.linalg.norm(queries, axis=1)
    if np.any(document_norms == 0) or np.any(query_norms == 0):
        raise ValueError("MemoryIndex cosine embeddings must be nonzero")
    similarities = (queries @ documents.T) / (
        query_norms[:, None] * document_norms[None, :]
    )
    best_distances = 1.0 - np.clip(similarities, -1.0, 1.0).max(axis=0)
    return rank_unique_candidates(
        {
            "metadatas": [metadatas],
            "distances": [best_distances.tolist()],
        },
        limit=limit,
    )


class ChromaMiniLMIndex(index_rpc.MemoryIndexServicer):
    """A local, eval-only implementation of ``memoryindex.v1.MemoryIndex``."""

    def __init__(
        self,
        database_path: Path,
        log_path: Path,
        *,
        model_cache: Path | None = None,
        token: str = "",
    ) -> None:
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
                ONNXMiniLM_L6_V2,
            )
            from tokenizers import Tokenizer
        except ImportError as error:  # pragma: no cover - exercised by CLI users
            raise RuntimeError(
                "local MemoryIndex dependencies are missing; install "
                "'./eval/python[local-index]'"
            ) from error

        self._lock = threading.Lock()
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._token = token

        self._encoder = ONNXMiniLM_L6_V2(
            preferred_providers=["CPUExecutionProvider"]
        )
        if model_cache is not None:
            self._encoder.DOWNLOAD_PATH = str(model_cache)
        self._encoder(["Initialize the local semantic encoder."])
        self._tokenizer = Tokenizer.from_str(self._encoder.tokenizer.to_str())
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

        self._client = chromadb.PersistentClient(
            path=str(database_path),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self._get_or_create_collection()

    def _get_or_create_collection(self) -> Any:
        return self._client.get_or_create_collection(
            COLLECTION_NAME,
            embedding_function=None,
            configuration={"hnsw": {"space": DISTANCE}},
        )

    def _authorize(self, context: grpc.ServicerContext) -> None:
        if not self._token:
            return
        supplied = dict(context.invocation_metadata()).get(TOKEN_METADATA_KEY, "")
        if not secrets.compare_digest(supplied, self._token):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid MemoryIndex token")

    def _log(self, record: Mapping[str, Any]) -> None:
        with self._log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _scope(scope: Any) -> dict[str, str]:
        return {
            "tenant": scope.tenant_ref,
            "agent": scope.agent_ref,
            "relationship": scope.relationship_ref,
        }

    @staticmethod
    def _where(values: Mapping[str, str | int]) -> dict[str, Any]:
        return {"$and": [{key: {"$eq": value}} for key, value in values.items()]}

    def _chunks(self, text: str) -> list[str]:
        return chunk_text(self._tokenizer, text)

    def Upsert(self, request: Any, context: grpc.ServicerContext) -> Any:
        self._authorize(context)
        started = time.perf_counter()
        allowed_kinds = {
            index_pb.MEMORY_KIND_EPISODE,
            index_pb.MEMORY_KIND_RECOLLECTION,
            index_pb.MEMORY_KIND_DISPOSITION,
        }
        with self._lock:
            prepared: list[tuple[dict[str, str | int], list[str], Any, str]] = []
            for document in request.documents:
                if (
                    document.kind not in allowed_kinds
                    or not document.ref
                    or not document.text.strip()
                ):
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT, "invalid index document"
                    )
                metadata: dict[str, str | int] = self._scope(document.scope) | {
                    "kind": int(document.kind),
                    "ref": document.ref,
                }
                semantic_text = semantic_index_text(document.kind, document.text)
                chunks = self._chunks(semantic_text)
                vectors = self._encoder(chunks)
                text_hash = hashlib.sha256(semantic_text.encode()).hexdigest()
                prepared.append((metadata, chunks, vectors, text_hash))

            for metadata, chunks, vectors, text_hash in prepared:
                root_id = hashlib.sha256(
                    json.dumps(metadata, sort_keys=True).encode()
                ).hexdigest()
                self.collection.delete(where=self._where(metadata))
                self.collection.upsert(
                    ids=[f"{root_id}:{index}" for index in range(len(chunks))],
                    documents=chunks,
                    embeddings=vectors,
                    metadatas=[
                        metadata
                        | {"chunk": index, "semantic_sha256": text_hash}
                        for index in range(len(chunks))
                    ],
                )
                self._log(
                    {
                        "operation": "upsert",
                        **metadata,
                        "chunks": len(chunks),
                        "text_sha256": text_hash,
                        "duration_ms": (time.perf_counter() - started) * 1000,
                    }
                )
        return index_pb.UpsertResponse()

    def Search(self, request: Any, context: grpc.ServicerContext) -> Any:
        self._authorize(context)
        started = time.perf_counter()
        query = request.query
        with self._lock:
            scope = self._scope(query.scope)
            where = self._where(scope)
            found = self.collection.get(
                where=where,
                include=["metadatas", "embeddings"],
            )
            size = len(found["ids"])
            ranked: list[tuple[float, int, str]] = []
            if size and query.limit > 0 and query.text.strip():
                embeddings = self._encoder(self._chunks(query.text))
                ranked = rank_exact_cosine_candidates(
                    found,
                    embeddings,
                    limit=query.limit,
                )
            self._log(
                {
                    "operation": "search",
                    **scope,
                    "query": query.text,
                    "limit": query.limit,
                    "scoped_chunks": size,
                    "candidates": [
                        {"kind": kind, "ref": ref, "distance": distance}
                        for distance, kind, ref in ranked
                    ],
                    "duration_ms": (time.perf_counter() - started) * 1000,
                }
            )
            return index_pb.SearchResponse(
                candidates=[
                    index_pb.Candidate(kind=kind, ref=ref)
                    for _, kind, ref in ranked
                ]
            )

    def Delete(self, request: Any, context: grpc.ServicerContext) -> Any:
        self._authorize(context)
        with self._lock:
            for candidate in request.candidates:
                metadata: dict[str, str | int] = self._scope(request.scope) | {
                    "kind": int(candidate.kind),
                    "ref": candidate.ref,
                }
                self.collection.delete(where=self._where(metadata))
                self._log({"operation": "delete", **metadata})
        return index_pb.DeleteResponse()

    def Reset(self, request: Any, context: grpc.ServicerContext) -> Any:
        del request
        self._authorize(context)
        with self._lock:
            self._client.delete_collection(self.collection.name)
            self.collection = self._get_or_create_collection()
            self._log({"operation": "reset"})
        return index_pb.ResetResponse()


def _loopback_or_authenticated(listen: str, token: str) -> None:
    host = listen.rsplit(":", 1)[0].strip("[]")
    if not token and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("a non-loopback listener requires MEMORY_EVAL_INDEX_TOKEN")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the reproducible eval-only Chroma/MiniLM MemoryIndex"
    )
    parser.add_argument("--listen", default="127.0.0.1:18083")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if args.workers <= 0:
        parser.error("--workers must be positive")
    token = os.environ.get("MEMORY_EVAL_INDEX_TOKEN", "")
    _loopback_or_authenticated(args.listen, token)

    provider = ChromaMiniLMIndex(
        args.database,
        args.log,
        model_cache=args.model_cache,
        token=token,
    )
    server = grpc.server(ThreadPoolExecutor(max_workers=args.workers))
    index_rpc.add_MemoryIndexServicer_to_server(provider, server)
    if not server.add_insecure_port(args.listen):
        raise RuntimeError(f"cannot bind local MemoryIndex at {args.listen}")
    server.start()
    print(
        json.dumps(
            {
                "index_ready": args.listen,
                "stored_chunks": provider.collection.count(),
                "revision": provider_revision(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        server.stop(5).wait()


if __name__ == "__main__":
    main()
