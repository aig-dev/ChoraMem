from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .v1 import inference_pb2


_MAX_STABLE_REF_BYTES = 384
_MAX_SOURCE_TEXT_BYTES = 64 * 1024
_MAX_CHANGE_TEXT_BYTES = 4 * 1024
_MAX_TAGGED_TEXT_BYTES = 64 * 1024
_ORIGINS = {"current", "related", "revision_anchor"}
_ROLES = {"situation", "agent_act"}
_ACTOR_KINDS = {"agent", "user", "system", "tool", "external"}
_APPLICATIONS = {"self", "other", "relation", "situation"}
_RESERVED_PROTOCOL_LINES = {
    "TARGET",
    "APPLICATION",
    "CHANGE",
    "TEXT",
    "BASIS",
    "NEW_RECOLLECTION",
    "NEW_DISPOSITION",
    "SELF",
    "OTHER",
    "RELATION",
    "SITUATION",
    "NO_MEMORY",
    "NO_CHANGE",
}
_RESERVED_BASIS_REFS = {
    "TARGET",
    "APPLICATION",
    "CHANGE",
    "BASIS",
    "NEW",
    "NEW_RECOLLECTION",
    "NEW_DISPOSITION",
}


@dataclass(frozen=True, slots=True)
class RecollectionMaterial:
    episode_ref: str
    source_ref: str
    text: str
    context_text: str
    basis_refs: tuple[str, ...]
    existing_recollections: tuple[str, ...] = ()

    def bind_recollection(self, body: str) -> str:
        if not isinstance(body, str):
            raise TypeError("recollection body must be str")
        stripped = body.strip()
        if stripped == "NO_MEMORY":
            return ""
        if not stripped:
            raise ValueError("recollection body is required")
        if "```" in body:
            raise ValueError("recollection body must not contain a code fence")

        raw_lines = body.splitlines()
        meaningful = [line.strip() for line in raw_lines if line.strip()]
        if any(line == "NO_MEMORY" for line in meaningful):
            raise ValueError("NO_MEMORY cannot be mixed with recollection text")
        if any(line in _RESERVED_PROTOCOL_LINES for line in meaningful):
            raise ValueError("recollection body must not contain protocol lines")

        normalized = " ".join(meaningful)
        if not normalized:
            raise ValueError("recollection body is required")
        if _utf8_size(normalized, "recollection body") > _MAX_CHANGE_TEXT_BYTES:
            raise ValueError("recollection body exceeds 4 KiB")
        if not self.basis_refs or len(set(self.basis_refs)) != len(self.basis_refs):
            raise ValueError("recollection material requires unique Episode Basis refs")
        for ref in self.basis_refs:
            _validate_basis_ref(ref, "basis ref")

        tagged_text = (
            "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
            + normalized
            + "\nBASIS\n"
            + "\n".join(self.basis_refs)
        )
        if _utf8_size(tagged_text, "tagged text") > _MAX_TAGGED_TEXT_BYTES:
            raise ValueError("bound tagged text exceeds 64 KiB")
        return tagged_text


def recollection_materials(
    request: inference_pb2.ProcessConsolidationWindowRequest,
) -> tuple[RecollectionMaterial, ...]:
    if not isinstance(request, inference_pb2.ProcessConsolidationWindowRequest):
        raise TypeError("request must be ProcessConsolidationWindowRequest")
    if not request.HasField("evidence"):
        raise ValueError("typed evidence is required")

    allowed_targets = tuple(request.allowed_target_refs)
    allowed_basis = tuple(request.allowed_basis_refs)
    if len(set(allowed_targets)) != len(allowed_targets):
        raise ValueError("duplicate allowed target ref")
    if len(set(allowed_basis)) != len(allowed_basis):
        raise ValueError("duplicate allowed basis ref")
    if "NEW_RECOLLECTION" not in allowed_targets:
        raise ValueError("NEW_RECOLLECTION target permission is required")
    for ref in allowed_basis:
        _validate_required_ref(ref, "allowed basis ref")

    recollection_refs: set[str] = set()
    existing_recollections = []
    for recollection in request.evidence.recollections:
        _validate_required_ref(recollection.version_ref, "Recollection version ref")
        if recollection.version_ref in recollection_refs:
            raise ValueError("duplicate existing Recollection")
        recollection_refs.add(recollection.version_ref)
        if recollection.version_ref not in allowed_targets:
            raise ValueError("existing Recollection lacks Target permission")
        if recollection.application not in _APPLICATIONS:
            raise ValueError("invalid existing Recollection application")
        text = recollection.text.strip()
        if not text:
            raise ValueError("existing Recollection text is required")
        if _utf8_size(text, "existing Recollection text") > _MAX_CHANGE_TEXT_BYTES:
            raise ValueError("existing Recollection text exceeds 4 KiB")
        existing_recollections.append(text)

    episodes = tuple(request.evidence.episodes)
    if not episodes:
        raise ValueError("typed evidence requires Episodes")
    episode_refs: set[str] = set()
    source_state: dict[tuple[str, str], tuple[str, str, str]] = {}
    context_episodes = []
    current_sources = []
    for episode in episodes:
        _validate_required_ref(episode.episode_ref, "episode ref")
        _validate_optional_ref(episode.session_ref, "session ref")
        if episode.episode_ref in episode_refs:
            raise ValueError("duplicate episode ref")
        episode_refs.add(episode.episode_ref)
        if episode.episode_ref not in allowed_basis:
            raise ValueError("Episode ref lacks Basis permission")
        if episode.origin not in _ORIGINS:
            raise ValueError("unknown evidence origin")
        if episode.origin in {"current", "related"}:
            _validate_basis_ref(episode.episode_ref, "recollection Episode Basis ref")
        if not episode.sources:
            raise ValueError("Episode sources are required")

        links: set[tuple[str, str]] = set()
        has_situation = False
        has_agent_act = False
        copied_sources = []
        for source in episode.sources:
            _validate_required_ref(source.source_ref, "source ref")
            if source.role not in _ROLES:
                raise ValueError("unknown evidence source role")
            if source.actor_kind not in _ACTOR_KINDS:
                raise ValueError("unknown evidence actor kind")
            _validate_required_ref(source.actor_ref, "actor ref")
            _validate_source_text(source.text)
            link = (source.role, source.source_ref)
            if link in links:
                raise ValueError("duplicate source role link in Episode")
            links.add(link)

            identity = (episode.session_ref, source.source_ref)
            immutable = (source.actor_kind, source.actor_ref, source.text)
            previous = source_state.get(identity)
            if previous is not None and previous != immutable:
                raise ValueError("same-session source identity conflict")
            source_state[identity] = immutable

            if source.role == "situation":
                has_situation = True
            else:
                if source.actor_kind != "agent":
                    raise ValueError("agent_act requires an agent actor")
                has_agent_act = True
            copied_sources.append(
                (source.source_ref, source.role, source.actor_kind, source.text)
            )
        if not has_situation or not has_agent_act:
            raise ValueError("Episode must contain Situation and actual AgentAct")

        if episode.origin in {"current", "related"}:
            context_episodes.append((episode.origin, tuple(copied_sources)))
        if episode.origin == "current":
            for source_ref, role, actor_kind, text in copied_sources:
                if role == "situation" and actor_kind == "user":
                    current_sources.append((episode.episode_ref, source_ref, text))

    if not any(episode.origin == "current" for episode in episodes):
        raise ValueError("typed evidence requires a current Episode")

    outcome_refs: set[str] = set()
    for outcome in request.evidence.outcomes:
        _validate_required_ref(outcome.outcome_ref, "outcome ref")
        _validate_required_ref(outcome.episode_ref, "outcome episode ref")
        if outcome.outcome_ref in outcome_refs:
            raise ValueError("duplicate outcome ref")
        outcome_refs.add(outcome.outcome_ref)
        if outcome.episode_ref not in episode_refs:
            raise ValueError("Outcome references an unknown Episode")
        if outcome.actor_kind not in _ACTOR_KINDS:
            raise ValueError("unknown Outcome actor kind")
        _validate_required_ref(outcome.actor_ref, "Outcome actor ref")
        _validate_source_text(outcome.text)

    basis_refs = tuple(
        episode.episode_ref
        for episode in episodes
        if episode.origin == "current"
    )
    context_text = _render_context(context_episodes)
    return tuple(
        RecollectionMaterial(
            episode_ref=episode_ref,
            source_ref=source_ref,
            text=text,
            context_text=context_text,
            basis_refs=basis_refs,
            existing_recollections=tuple(existing_recollections),
        )
        for episode_ref, source_ref, text in current_sources
    )


def _render_context(episodes: list[tuple[str, tuple[tuple[str, str, str, str], ...]]]) -> str:
    blocks = []
    for origin, sources in episodes:
        lines = ["CURRENT_EPISODE" if origin == "current" else "RELATED_EPISODE"]
        for _, role, actor_kind, text in sources:
            lines.append(f"{role.upper()} {actor_kind}")
            lines.extend(f"> {line}" for line in text.split("\n"))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _validate_source_text(text: str) -> None:
    if not text:
        raise ValueError("source text is required")
    if _utf8_size(text, "source text") > _MAX_SOURCE_TEXT_BYTES:
        raise ValueError("source text exceeds 64 KiB")


def _validate_required_ref(ref: str, label: str) -> None:
    if not ref:
        raise ValueError(f"{label} is required")
    if _utf8_size(ref, label) > _MAX_STABLE_REF_BYTES:
        raise ValueError(f"{label} exceeds 384 bytes")


def _validate_optional_ref(ref: str, label: str) -> None:
    if ref:
        _validate_required_ref(ref, label)


def _validate_basis_ref(ref: str, label: str) -> None:
    _validate_required_ref(ref, label)
    if any(character.isspace() or unicodedata.category(character) == "Cc" for character in ref):
        raise ValueError(f"{label} is not a valid tagged-text ref")
    if ref in _RESERVED_BASIS_REFS:
        raise ValueError(f"{label} is reserved tagged-text syntax")


def _utf8_size(value: str, label: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
