"""Optional live adapters: text-only Chat Completions and a read-only SQL probe."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

import grpc


_OPAQUE_MEMORY_REF_TEXT = (
    r"(?:seed|recollection)_[0-9a-f]{32}@[1-9][0-9]*"
)
_OPAQUE_MEMORY_REF = re.compile(
    rf"(?<![A-Za-z0-9_-]){_OPAQUE_MEMORY_REF_TEXT}(?![A-Za-z0-9_-])"
)
_EPISODE_REF_TEXT = r"episode_[0-9a-f]{64}"
_OUTCOME_REF_TEXT = r"outcome_[0-9a-f]{32}"
_OPAQUE_EVIDENCE_REF = re.compile(
    rf"(?<![A-Za-z0-9_-])(?:{_EPISODE_REF_TEXT}|{_OUTCOME_REF_TEXT})"
    rf"(?![A-Za-z0-9_-])"
)
_OPAQUE_REPLAY_OUTPUT_REF = re.compile(
    rf"(?<![A-Za-z0-9_-])(?:{_OPAQUE_MEMORY_REF_TEXT}|"
    rf"{_EPISODE_REF_TEXT}|{_OUTCOME_REF_TEXT})(?![A-Za-z0-9_-])"
)
_MEMORY_DESCRIPTOR = re.compile(
    rf"(?m)^(?:ELIGIBLE_RECOLLECTION|ELIGIBLE_ADAPTATION|"
    rf"ELIGIBLE_DISPOSITION|ACTIVE_DISPOSITION_HINT) "
    rf"(?P<ref>{_OPAQUE_MEMORY_REF_TEXT})\n"
    r"APPLICATION (?P<application>[^\n]*)\nTEXT (?P<text>[^\n]*)$"
)
_OPAQUE_MEMORY_MARKER = "\x00memory-core-replay-ref:"
_EPISODE_HEADER = re.compile(
    rf"(?m)^(?P<tag>EPISODE|RELATED_EPISODE) "
    rf"(?P<ref>{_EPISODE_REF_TEXT})\n"
)
_EPISODE_BLOCK_BOUNDARY = re.compile(
    rf"(?m)^(?:ACTIVE_DISPOSITION_HINTS|RELATED_EPISODES|"
    rf"ELIGIBLE_[A-Z_]+(?: [^\n]*)?|REVISION_ANCHOR_EVIDENCE|"
    rf"OUTCOME {_OUTCOME_REF_TEXT}|WINDOW_END)$"
)
_EVIDENCE_SOURCE = re.compile(
    r"(?m)^(?P<role>[A-Z][A-Z_]*)\n"
    r"ACTOR (?P<kind>[^ \n]+) (?P<actor>[^\n]+)\n"
    r"SOURCE (?P<source>[^\n]+)\n"
)


def _canonicalize_semantic_memory_state(text: str) -> tuple[str, dict[str, str]]:
    if _OPAQUE_MEMORY_MARKER in text:
        raise ValueError("Worker replay input contains a reserved ref marker")
    ref_to_marker: dict[str, str] = {}
    marker_to_ref: dict[str, str] = {}
    for descriptor in _MEMORY_DESCRIPTOR.finditer(text):
        ref = descriptor.group("ref")
        semantic_identity = "\x00".join(
            (
                ref.split("_", 1)[0],
                descriptor.group("application"),
                descriptor.group("text"),
            )
        )
        digest = hashlib.sha256(semantic_identity.encode()).hexdigest()
        marker = f"{_OPAQUE_MEMORY_MARKER}semantic:{digest}\x00"
        previous_marker = ref_to_marker.get(ref)
        previous_ref = marker_to_ref.get(marker)
        if (previous_marker is not None and previous_marker != marker) or (
            previous_ref is not None and previous_ref != ref
        ):
            raise ValueError("Worker replay input has ambiguous memory identities")
        ref_to_marker[ref] = marker
        marker_to_ref[marker] = ref

    def replace(match: re.Match[str]) -> str:
        ref = match.group(0)
        marker = ref_to_marker.get(ref)
        if marker is None:
            marker = f"{_OPAQUE_MEMORY_MARKER}ordinal:{len(ref_to_marker) + 1}\x00"
            ref_to_marker[ref] = marker
            marker_to_ref[marker] = ref
        return marker

    canonical = _OPAQUE_MEMORY_REF.sub(replace, text)
    canonical, evidence_marker_to_ref = _canonicalize_evidence_state(canonical)
    for marker, ref in evidence_marker_to_ref.items():
        if marker in marker_to_ref and marker_to_ref[marker] != ref:
            raise ValueError("Worker replay input has ambiguous evidence identities")
        marker_to_ref[marker] = ref
    canonical = _canonicalize_memory_candidate_order(canonical)
    return canonical, marker_to_ref


def _semantic_markers(groups: dict, kind: str) -> dict:
    signatures = {
        identity: hashlib.sha256("\x00".join(sorted(contexts)).encode()).hexdigest()
        for identity, contexts in groups.items()
    }
    signature_counts: dict[str, int] = {}
    for signature in signatures.values():
        signature_counts[signature] = signature_counts.get(signature, 0) + 1
    markers = {}
    for identity, signature in signatures.items():
        if signature_counts[signature] == 1:
            suffix = f"semantic:{signature}"
        else:
            opaque = hashlib.sha256(str(identity).encode()).hexdigest()
            suffix = f"opaque:{opaque}"
        markers[identity] = f"{_OPAQUE_MEMORY_MARKER}{kind}:{suffix}\x00"
    return markers


def _canonicalize_evidence_state(text: str) -> tuple[str, dict[str, str]]:
    """Alpha-rename Core evidence identities without erasing their graph."""

    episode_matches = list(_EPISODE_HEADER.finditer(text))
    if not episode_matches:
        return text, {}

    blocks = []
    for index, match in enumerate(episode_matches):
        end = (
            episode_matches[index + 1].start()
            if index + 1 < len(episode_matches)
            else len(text)
        )
        boundary = _EPISODE_BLOCK_BOUNDARY.search(text, match.end())
        if boundary is not None and boundary.start() < end:
            end = boundary.start()
        block = text[match.start() : end].rstrip("\n")
        session_match = re.search(r"(?m)^SESSION ([^\n]+)$", block)
        normalized = _OPAQUE_EVIDENCE_REF.sub("<evidence-ref>", block)
        normalized = re.sub(r"(?m)^SESSION [^\n]+$", "SESSION <ref>", normalized)
        normalized = re.sub(r"(?m)^SOURCE [^\n]+$", "SOURCE <ref>", normalized)
        normalized = re.sub(
            r"(?m)^ACTOR ([^ \n]+) [^\n]+$",
            lambda actor: f"ACTOR {actor.group(1)} <ref>",
            normalized,
        )
        block_signature = hashlib.sha256(normalized.encode()).hexdigest()
        sources = []
        for source_index, source in enumerate(_EVIDENCE_SOURCE.finditer(block)):
            sources.append(
                (
                    source_index,
                    source.group("role"),
                    source.group("kind"),
                    source.group("actor"),
                    source.group("source"),
                )
            )
        blocks.append(
            {
                "tag": match.group("tag"),
                "ref": match.group("ref"),
                "session": session_match.group(1) if session_match else "",
                "signature": block_signature,
                "sources": sources,
            }
        )

    session_groups: dict[str, list[str]] = {}
    source_groups: dict[str, list[str]] = {}
    actor_groups: dict[tuple[str, str], list[str]] = {}
    for block in blocks:
        if block["session"]:
            session_groups.setdefault(block["session"], []).append(block["signature"])
        for source_index, role, kind, actor_ref, source_ref in block["sources"]:
            context = f"{block['signature']}\x00{source_index}\x00{role}\x00{kind}"
            source_groups.setdefault(source_ref, []).append(context)
            actor_groups.setdefault((kind, actor_ref), []).append(context)

    session_markers = _semantic_markers(session_groups, "session")
    source_markers = _semantic_markers(source_groups, "source")
    actor_markers = _semantic_markers(actor_groups, "actor")
    episode_groups: dict[str, list[str]] = {}
    for block in blocks:
        episode_groups.setdefault(block["ref"], []).append(
            "\x00".join(
                (
                    block["tag"],
                    block["signature"],
                    session_markers.get(block["session"], block["session"]),
                )
            )
        )
    episode_markers = _semantic_markers(episode_groups, "episode")

    outcome_groups: dict[str, list[str]] = {}
    outcome_declaration = re.compile(
        rf"(?ms)^OUTCOME (?P<ref>{_OUTCOME_REF_TEXT})\n"
        rf"(?:OUTCOME_EPISODE (?P<episode>{_EPISODE_REF_TEXT})\n)?"
        rf"ACTOR (?P<kind>[^ \n]+) (?P<actor>[^\n]+)\n"
        rf"(?P<body>.*?)(?=\n\n(?:ELIGIBLE_|OUTCOME |REVISION_|WINDOW_END)|\Z)"
    )
    for outcome in outcome_declaration.finditer(text):
        actor_marker = actor_markers.get(
            (outcome.group("kind"), outcome.group("actor")),
            outcome.group("actor"),
        )
        outcome_groups.setdefault(outcome.group("ref"), []).append(
            "\x00".join(
                (outcome.group("kind"), actor_marker, outcome.group("body"))
            )
        )
        if outcome.group("episode"):
            outcome_groups[outcome.group("ref")].append(
                "episode\x00"
                + episode_markers.get(
                    outcome.group("episode"), outcome.group("episode")
                )
            )
    outcome_link = re.compile(
        rf"(?m)^FEEDBACK_OUTCOME (?P<outcome>{_OUTCOME_REF_TEXT})\n"
        rf"OUTCOME_EPISODE (?P<episode>{_EPISODE_REF_TEXT})$"
    )
    for link in outcome_link.finditer(text):
        outcome_groups.setdefault(link.group("outcome"), []).append(
            "episode\x00"
            + episode_markers.get(link.group("episode"), link.group("episode"))
        )
    outcome_markers = _semantic_markers(outcome_groups, "outcome")

    marker_to_ref = {
        marker: ref for ref, marker in episode_markers.items()
    } | {
        marker: ref for ref, marker in outcome_markers.items()
    }
    text = re.sub(
        _EPISODE_REF_TEXT,
        lambda found: episode_markers.get(found.group(0), found.group(0)),
        text,
    )
    text = re.sub(
        _OUTCOME_REF_TEXT,
        lambda found: outcome_markers.get(found.group(0), found.group(0)),
        text,
    )
    text = re.sub(
        r"(?m)^SESSION ([^\n]+)$",
        lambda found: "SESSION "
        + session_markers.get(found.group(1), found.group(1)),
        text,
    )
    text = re.sub(
        r"(?m)^SOURCE ([^\n]+)$",
        lambda found: "SOURCE "
        + source_markers.get(found.group(1), found.group(1)),
        text,
    )
    text = re.sub(
        r"(?m)^ACTOR ([^ \n]+) ([^\n]+)$",
        lambda found: "ACTOR "
        + found.group(1)
        + " "
        + actor_markers.get((found.group(1), found.group(2)), found.group(2)),
        text,
    )
    return _canonicalize_evidence_order(text), marker_to_ref


def _canonicalize_evidence_order(text: str) -> str:
    for label in ("DIRECT_EPISODE", "REVISION_ANCHOR"):
        run = re.compile(rf"(?m)(?:^{label} [^\n]+\n?)+")
        text = run.sub(
            lambda found: "\n".join(sorted(found.group(0).strip().splitlines()))
            + ("\n" if found.group(0).endswith("\n") else ""),
            text,
        )

    related_section = re.compile(
        r"(?ms)(^RELATED_EPISODES\n)(.*?)(?=^ELIGIBLE_[A-Z_]+|^WINDOW_END$)"
    )

    def sort_related(found: re.Match[str]) -> str:
        body = found.group(2)
        starts = list(re.finditer(r"(?m)^RELATED_EPISODE ", body))
        if not starts:
            return found.group(0)
        blocks = []
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
            blocks.append(body[start.start() : end].strip("\n"))
        return found.group(1) + "\n\n".join(sorted(blocks)) + "\n\n"

    return related_section.sub(sort_related, text)


def _canonicalize_memory_candidate_order(text: str) -> str:
    def sort_tagged_pairs(match: re.Match[str], tag: str) -> str:
        lines = match.group(2).splitlines()
        if len(lines) % 2 or any(
            lines[index] != tag for index in range(0, len(lines), 2)
        ):
            return match.group(0)
        pairs = ["\n".join(lines[index : index + 2]) for index in range(0, len(lines), 2)]
        return match.group(1) + "\n".join(sorted(pairs)) + match.group(3)

    for begin, end, tag in (
        ("ALLOWED_TARGETS_BEGIN", "ALLOWED_TARGETS_END", "ALLOWED_TARGET"),
        ("ALLOWED_BASIS_BEGIN", "ALLOWED_BASIS_END", "ALLOWED_BASIS"),
    ):
        section = re.compile(rf"(?s)({begin}\n)(.*?)(\n{end})")
        text = section.sub(
            lambda match, tag=tag: sort_tagged_pairs(match, tag), text, count=1
        )
    fallback = re.compile(
        r"(?s)(EXISTING_RECOLLECTIONS_BEGIN\n)(.*?)(\nEXISTING_RECOLLECTIONS_END)"
    )

    def sort_recollections(match: re.Match[str]) -> str:
        lines = match.group(2).splitlines()
        if not lines or any(line != "NONE" and not line.startswith("> ") for line in lines):
            return match.group(0)
        return match.group(1) + "\n".join(sorted(lines)) + match.group(3)

    text = fallback.sub(sort_recollections, text, count=1)
    paragraphs = text.split("\n\n")
    prefix = (
        "ELIGIBLE_RECOLLECTION ",
        "ELIGIBLE_ADAPTATION ",
        "ELIGIBLE_DISPOSITION ",
    )
    offset = 0
    while offset < len(paragraphs):
        if not paragraphs[offset].startswith(prefix):
            offset += 1
            continue
        end = offset + 1
        while end < len(paragraphs) and paragraphs[end].startswith(prefix):
            end += 1
        paragraphs[offset:end] = sorted(paragraphs[offset:end])
        offset = end
    return "\n\n".join(paragraphs)


def _canonical_replay_output(output: str, marker_to_ref: dict[str, str]) -> str:
    ref_to_marker = {ref: marker for marker, ref in marker_to_ref.items()}

    def replace(match: re.Match[str]) -> str:
        ref = match.group(0)
        marker = ref_to_marker.get(ref)
        if marker is None:
            raise ValueError(
                "Worker replay output contains an opaque memory ref absent from its input"
            )
        return marker

    return _OPAQUE_REPLAY_OUTPUT_REF.sub(replace, output)


def _semantic_memory_state_replay(replay: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for input_text, output_text in replay.items():
        canonical_input, marker_to_ref = _canonicalize_semantic_memory_state(input_text)
        canonical_output = _canonical_replay_output(output_text, marker_to_ref)
        previous = normalized.get(canonical_input)
        if previous is not None and previous != canonical_output:
            raise ValueError(
                "conflicting Worker replay after opaque memory ref normalization"
            )
        normalized[canonical_input] = canonical_output
    return normalized


def _restore_opaque_memory_refs(
    output: str, marker_to_ref: dict[str, str]
) -> str:
    for marker, ref in marker_to_ref.items():
        output = output.replace(marker, ref)
    if _OPAQUE_MEMORY_MARKER in output:
        raise ValueError("Worker replay output refers to an unavailable memory ref")
    return output


class RetryingMemoryClient:
    """Retry transaction aborts with the exact same idempotent RPC request."""

    def __init__(self, delegate, *, attempts: int = 5):
        if type(attempts) is not int or attempts <= 0:
            raise ValueError("RPC attempts must be a positive integer")
        self.delegate = delegate
        self.attempts = attempts

    async def _call(self, method: str, request, **options):
        for attempt in range(self.attempts):
            try:
                return await getattr(self.delegate, method)(request, **options)
            except grpc.RpcError as error:
                if error.code() != grpc.StatusCode.ABORTED or attempt + 1 == self.attempts:
                    raise
                await asyncio.sleep(0.05 * (2 ** attempt))
        raise AssertionError("positive RPC attempt count exhausted without return")

    async def observe_source_event(self, request, **options):
        return await self._call("observe_source_event", request, **options)

    async def select_memory(self, request, **options):
        return await self._call("select_memory", request, **options)

    async def record_memory_delivery(self, request, **options):
        return await self._call("record_memory_delivery", request, **options)

    async def report_outcome(self, request, **options):
        return await self._call("report_outcome", request, **options)

class ChatTextModel:
    def __init__(self, *, model: str, api_key: str, base_url: str,
                 usage_file: Path, client=None, thinking_enabled: bool = False,
                 reasoning_split: bool = False,
                 request_timeout: float = 110, max_retries: int = 2,
                 completion_attempts: int = 1):
        if thinking_enabled and not model.startswith("deepseek-v4"):
            raise ValueError("the explicit thinking arm requires a deepseek-v4 model")
        if type(completion_attempts) is not int or completion_attempts <= 0:
            raise ValueError("completion_attempts must be a positive integer")
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=request_timeout, max_retries=max_retries)
        self.client, self.model, self.usage_file = client, model, usage_file
        self.thinking_enabled = thinking_enabled
        self.reasoning_split = reasoning_split
        self.completion_attempts = completion_attempts
        usage_file.parent.mkdir(parents=True, exist_ok=True)

    async def complete(self, *, instructions: str, input_text: str, max_output_tokens: int) -> str:
        messages = []
        if instructions:
            messages.append({"role": "system", "content": instructions})
        messages.append({"role": "user", "content": input_text})
        request = dict(model=self.model, messages=messages, max_tokens=max_output_tokens, temperature=0)
        extra_body = {}
        if self.reasoning_split:
            extra_body["reasoning_split"] = True
        if self.model.startswith("deepseek-v4"):
            extra_body["thinking"] = {"type": "enabled" if self.thinking_enabled else "disabled"}
            if self.thinking_enabled:
                request.pop("temperature")
                request["reasoning_effort"] = "high"
        if extra_body:
            request["extra_body"] = extra_body
        for attempt in range(self.completion_attempts):
            start = time.perf_counter()
            response = await self.client.chat.completions.create(**request)
            output = response.choices[0].message.content
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            with self.usage_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"model": self.model, "usage": response.usage.model_dump() if response.usage else None,
                                         "finish_reason": finish_reason,
                                         "raw_output": output,
                                         "thinking_enabled": self.thinking_enabled,
                                         "reasoning_split": self.reasoning_split,
                                         "max_output_tokens": max_output_tokens,
                                         "duration_ms": round((time.perf_counter() - start) * 1000, 3)}) + "\n")
            if finish_reason == "length" and attempt + 1 < self.completion_attempts:
                continue
            if finish_reason != "stop":
                raise RuntimeError(f"Chat Completions response did not finish: {finish_reason}")
            if not isinstance(output, str) or not output.strip():
                raise RuntimeError("Chat Completions response contains no text")
            return output
        raise AssertionError("positive completion attempt count exhausted without return")

    async def close(self):
        await self.client.close()


class DatabaseProbe:
    """Eval diagnostics only. Does not write or become part of the public Core API."""
    def __init__(self, url: str, *, read=None):
        self.url = urlsplit(url)
        if self.url.scheme not in {"mysql", "postgres", "postgresql"} or not self.url.hostname or not self.url.path.strip("/"):
            raise ValueError("MEMORY_EVAL_DATABASE_URL must name a MySQL or PostgreSQL eval database")
        self.read = read or self._read

    def _read(self, sql, params):
        if not sql.lstrip().startswith("SELECT"):
            raise ValueError("eval probe is read-only")
        if self.url.scheme == "mysql":
            import pymysql
            connection = pymysql.connect(
                host=self.url.hostname, port=self.url.port or 3306,
                user=unquote(self.url.username or ""), password=unquote(self.url.password or ""),
                database=self.url.path.lstrip("/"), charset="utf8mb4", connect_timeout=10,
                autocommit=True, cursorclass=pymysql.cursors.DictCursor)
        else:
            import psycopg
            from psycopg.rows import dict_row
            connection = psycopg.connect(self.url.geturl(), autocommit=True, row_factory=dict_row)
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
        finally:
            connection.close()

    @staticmethod
    def _params(scope):
        return scope.tenant_ref, scope.agent_ref, scope.relationship_ref

    def snapshot(self, scope) -> dict:
        where = "tenant_ref=%s AND agent_ref=%s AND relationship_ref=%s"
        params = self._params(scope)
        episodes = self.read(f"SELECT COUNT(*) AS episodes FROM episodes WHERE {where}", params)[0]
        jobs = self.read(f"SELECT COUNT(*) AS jobs, COALESCE(SUM(CASE WHEN completed_at IS NULL THEN 1 ELSE 0 END),0) AS pending_jobs FROM consolidation_jobs WHERE {where}", params)[0]
        completed = self.read(
            f"SELECT episode_refs FROM consolidation_jobs WHERE {where} AND completed_at IS NOT NULL",
            params,
        )
        consolidated_refs: set[str] = set()
        for row in completed:
            refs = row.get("episode_refs")
            if isinstance(refs, bytes):
                refs = refs.decode("utf-8")
            if isinstance(refs, str):
                refs = json.loads(refs)
            if refs is None:
                continue
            if not isinstance(refs, (list, tuple)):
                raise TypeError("consolidation job episode_refs must be a JSON array")
            consolidated_refs.update(
                ref.strip() for ref in refs if isinstance(ref, str) and ref.strip()
            )
        return {
            **{k: int(v) for k, v in (episodes | jobs).items()},
            "consolidated_episodes": len(consolidated_refs),
        }

    def pending_index_operations(self, scope) -> int:
        where = "tenant_ref=%s AND agent_ref=%s AND relationship_ref=%s"
        row = self.read(
            f"""SELECT COUNT(*) AS pending_index_operations
                FROM memory_index_operations
                WHERE {where} AND acknowledged_at IS NULL""",
            self._params(scope),
        )[0]
        return int(row["pending_index_operations"])

    def memory_state(self, scope) -> dict:
        params = self._params(scope)
        owner = "r.tenant_ref=%s AND r.agent_ref=%s AND r.relationship_ref=%s"
        recollections = self.read(f"""SELECT v.recollection_version_ref AS ref, v.text,
            v.status, v.version_number, v.origin_job_ref FROM recollection_versions v
            JOIN recollections r ON r.tenant_ref=v.tenant_ref AND r.recollection_ref=v.recollection_ref
            WHERE {owner} ORDER BY v.recollection_version_ref""", params)
        dispositions = self.read(f"""SELECT v.seed_version_ref AS ref, v.tendency_text AS text,
            v.status, v.version_number, v.origin_job_ref FROM seed_versions v
            JOIN disposition_seeds r ON r.tenant_ref=v.tenant_ref AND r.seed_ref=v.seed_ref
            WHERE {owner} ORDER BY v.seed_version_ref""", params)
        basis = {}
        for table, column in (("recollection_basis_links", "recollection_version_ref"),
                              ("seed_basis_links", "seed_version_ref")):
            basis[table] = self.read(f"""SELECT b.{column} AS ref, b.episode_ref, b.role, r.session_ref
                FROM {table} b JOIN episodes r ON r.tenant_ref=b.tenant_ref AND r.episode_ref=b.episode_ref
                WHERE {owner} ORDER BY b.{column}, b.episode_ref, b.role""", params)
        jobs = self.read("""SELECT job_ref, episode_refs, window_hash, attempts FROM consolidation_jobs
            WHERE tenant_ref=%s AND agent_ref=%s AND relationship_ref=%s ORDER BY job_order""", params)

        def clean(value):
            if isinstance(value, bytes):
                try:
                    return value.decode("utf-8")
                except UnicodeDecodeError:
                    return value.hex()
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        return clean({"recollections": recollections, "dispositions": dispositions, "basis": basis, "jobs": jobs})


class RecordingWorkerModel:
    """Eval-only attempt log, including cancellation before a provider response."""
    def __init__(
        self,
        model,
        windows_file: Path,
        *,
        max_output_tokens: int = 8192,
        replay: dict[str, str] | None = None,
        strict_replay: bool = False,
        semantic_memory_replay: bool = False,
    ):
        self.model, self.windows_file = model, windows_file
        self.max_output_tokens = max_output_tokens
        self.replay = dict(replay or {})
        self.strict_replay = strict_replay
        self.semantic_memory_replay_enabled = semantic_memory_replay
        self.semantic_memory_replay = (
            _semantic_memory_state_replay(self.replay)
            if semantic_memory_replay
            else {}
        )

    async def complete(self, text: str) -> str:
        replay_match = "exact" if text in self.replay else "none"
        replay_output = self.replay.get(text)
        canonical_input = ""
        marker_to_ref: dict[str, str] = {}
        if self.semantic_memory_replay_enabled:
            canonical_input, marker_to_ref = _canonicalize_semantic_memory_state(text)
        if replay_match == "none" and canonical_input:
            if canonical_input in self.semantic_memory_replay:
                replay_match = "semantic_memory_state"
                replay_output = _restore_opaque_memory_refs(
                    self.semantic_memory_replay[canonical_input], marker_to_ref
                )
        replay_hit = replay_match != "none"
        record = {
            "input": text,
            "attempt_id": str(uuid.uuid4()),
            "replay_hit": replay_hit,
            "replay_match": replay_match,
        }
        with self.windows_file.with_name("attempts.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record | {"status": "started"}, ensure_ascii=False) + "\n")
        try:
            if replay_hit:
                record["output"] = replay_output
            elif self.strict_replay:
                raise KeyError("exact Worker replay miss")
            else:
                record["output"] = await self.model.complete(
                    instructions="",
                    input_text=text,
                    max_output_tokens=self.max_output_tokens,
                )
                if canonical_input:
                    canonical_output = _canonical_replay_output(
                        record["output"], marker_to_ref
                    )
                    previous = self.semantic_memory_replay.get(canonical_input)
                    if previous is not None and previous != canonical_output:
                        raise ValueError(
                            "conflicting Worker output for one semantic memory state"
                        )
                    self.semantic_memory_replay[canonical_input] = canonical_output
            return record["output"]
        except BaseException as error:
            record["error_type"] = type(error).__name__
            raise
        finally:
            with self.windows_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_worker_replay(paths) -> dict[str, str]:
    """Load exact completed Worker calls; never infer or normalize a key."""

    replay: dict[str, str] = {}
    for path in paths:
        path = Path(path)
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                input_text, output_text = record.get("input"), record.get("output")
                if not isinstance(input_text, str) or not isinstance(output_text, str):
                    continue
                previous = replay.get(input_text)
                if previous is not None and previous != output_text:
                    raise ValueError(
                        f"conflicting Worker replay for exact input at {path}:{line_number}"
                    )
                replay[input_text] = output_text
    return replay


def worker_main(argv=None):
    """Run the unchanged reference Worker with an optional chat-only model endpoint."""
    parser = argparse.ArgumentParser(description=worker_main.__doc__)
    parser.add_argument("--listen", default="127.0.0.1:18082")
    parser.add_argument("--output", type=Path, default=Path(".cache/personamem-worker"))
    parser.add_argument("--thinking", action="store_true", help="explicit DeepSeek v4 thinking evaluation arm; default unchanged")
    parser.add_argument("--reasoning-split", action="store_true",
                        help="ask a compatible provider to keep reasoning outside plain content")
    parser.add_argument(
        "--paired-disposition-formation",
        action="store_true",
        help=(
            "eval-only: form outcome-backed Dispositions from an ID-free causal-pair "
            "text job while retaining Core-owned TARGET/APPLICATION/BASIS"
        ),
    )
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--request-timeout", type=float, default=110)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--replay-windows",
        action="append",
        type=Path,
        default=[],
        help="reuse only exact input/output records from a prior windows.jsonl",
    )
    parser.add_argument(
        "--strict-replay",
        action="store_true",
        help="fail on a replay miss instead of calling the provider",
    )
    parser.add_argument(
        "--replay-semantic-memory-state",
        action="store_true",
        help=(
            "cache and optionally replay when Core-generated memory refs or candidate "
            "order differ but the complete semantic memory set and every evidence byte match"
        ),
    )
    args = parser.parse_args(argv)
    if args.strict_replay and not args.replay_windows:
        parser.error("--strict-replay requires --replay-windows")
    from memory_core_worker.server import serve

    replay = load_worker_replay(args.replay_windows)

    provider_model = ChatTextModel(model=os.environ["MEMORY_WORKER_OPENAI_MODEL"],
                                   api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ["OPENAI_BASE_URL"],
                                   usage_file=args.output / "usage.jsonl", thinking_enabled=args.thinking,
                                   reasoning_split=args.reasoning_split,
                                   request_timeout=args.request_timeout, max_retries=args.max_retries)
    worker_model = provider_model
    if args.paired_disposition_formation:
        from memory_core_eval.disposition_formation_ablation import (
            PairedDispositionFormationModel,
        )

        worker_model = PairedDispositionFormationModel(provider_model)

    async def start():
        try:
            await serve(
                RecordingWorkerModel(
                    worker_model,
                    args.output / "windows.jsonl",
                    max_output_tokens=args.max_output_tokens,
                    replay=replay,
                    strict_replay=args.strict_replay,
                    semantic_memory_replay=args.replay_semantic_memory_state,
                ),
                args.listen,
                max_model_input_bytes=256 * 1024,
            )
        finally:
            await provider_model.close()

    asyncio.run(start())


if __name__ == "__main__":
    worker_main()
