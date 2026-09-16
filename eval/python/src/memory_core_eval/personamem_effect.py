"""Six isolated PersonaMem-v2 contexts for effect-first diagnosis."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from memory_core import memory_pb2 as pb, render_memory_context

from .personamem_data import History


class EffectMode(str, Enum):
    NONE = "none"
    FULL_HISTORY = "full_history"
    ORACLE_EPISODE = "oracle_episode"
    SEMANTIC_TOP40 = "semantic_top40"
    CURRENT_CORE = "current_core"
    LEARNED_CORE = "learned_core"


@dataclass(frozen=True, slots=True)
class EpisodeDocument:
    memory_ref: str
    text: str

    def __post_init__(self) -> None:
        if not self.memory_ref.strip() or not self.text.strip():
            raise ValueError("EpisodeDocument requires nonblank memory_ref and text")


@dataclass(frozen=True, slots=True)
class EffectContext:
    mode: EffectMode
    text: str
    memory_refs: tuple[str, ...]
    selected_memory_refs: tuple[str, ...]
    selected_episode_refs: tuple[str, ...]
    delivered_episode_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not set(self.memory_refs) <= set(self.selected_memory_refs):
            raise ValueError("delivered memory refs must have been selected")
        if not set(self.delivered_episode_refs) <= set(self.selected_episode_refs):
            raise ValueError("delivered Episode refs must have been selected")
        if not set(self.delivered_episode_refs) <= set(self.memory_refs):
            raise ValueError("delivered Episode refs must occur in rendered refs")


def build_effect_context(
    mode: EffectMode,
    *,
    history: History | None = None,
    oracle_episodes: Sequence[EpisodeDocument] = (),
    semantic_episodes: Sequence[EpisodeDocument] = (),
    core_context: pb.MemoryContext | None = None,
    token_count: Callable[[str], int] | None = None,
    core_memory_tokens: int | None = None,
) -> EffectContext:
    """Build one mode without allowing another mode's evidence to leak into it."""

    mode = EffectMode(mode)
    if mode is EffectMode.NONE:
        return _context(mode, "", (), (), ())
    if mode is EffectMode.FULL_HISTORY:
        if history is None:
            raise ValueError("full_history requires history")
        return _context(mode, _render_history(history), (), (), ())
    if mode is EffectMode.ORACLE_EPISODE:
        return _episode_context(mode, oracle_episodes)
    if mode is EffectMode.SEMANTIC_TOP40:
        return _episode_context(mode, semantic_episodes)

    if core_context is None:
        raise ValueError(f"{mode.value} requires core_context")
    if token_count is None or core_memory_tokens is None:
        raise ValueError(f"{mode.value} requires an explicit total memory budget")
    if type(core_memory_tokens) is not int or core_memory_tokens < 0:
        raise ValueError("core_memory_tokens must be a nonnegative integer")

    selected = core_context
    if mode is EffectMode.LEARNED_CORE:
        selected = pb.MemoryContext()
        if core_context.HasField("constitution"):
            selected.constitution.CopyFrom(core_context.constitution)
        for item in core_context.recollections:
            selected.recollections.add().CopyFrom(item)
        for item in core_context.dispositions:
            selected.dispositions.add().CopyFrom(item)

    rendered = render_memory_context(
        selected,
        max_tokens=core_memory_tokens,
        token_count=token_count,
    )
    learned_refs = tuple(
        item.memory_ref
        for item in (*selected.recollections, *selected.dispositions)
        if item.text.strip()
    )
    episode_refs = tuple(
        item.memory_ref for item in selected.episode_evidence if item.text.strip()
    )
    selected_refs = learned_refs + episode_refs
    delivered_episodes = tuple(ref for ref in episode_refs if ref in rendered.memory_refs)
    return EffectContext(
        mode=mode,
        text=rendered.text,
        memory_refs=rendered.memory_refs,
        selected_memory_refs=selected_refs,
        selected_episode_refs=episode_refs,
        delivered_episode_refs=delivered_episodes,
    )


def _episode_context(
    mode: EffectMode,
    episodes: Sequence[EpisodeDocument],
) -> EffectContext:
    context = pb.MemoryContext(episode_evidence=[
        pb.EpisodeEvidence(memory_ref=item.memory_ref, text=item.text)
        for item in episodes
    ])
    rendered = render_memory_context(context)
    refs = tuple(item.memory_ref for item in episodes)
    return EffectContext(
        mode=mode,
        text=rendered.text,
        memory_refs=rendered.memory_refs,
        selected_memory_refs=refs,
        selected_episode_refs=refs,
        delivered_episode_refs=refs,
    )


def _context(
    mode: EffectMode,
    text: str,
    memory_refs: tuple[str, ...],
    selected_memory_refs: tuple[str, ...],
    episode_refs: tuple[str, ...],
) -> EffectContext:
    return EffectContext(
        mode=mode,
        text=text,
        memory_refs=memory_refs,
        selected_memory_refs=selected_memory_refs,
        selected_episode_refs=episode_refs,
        delivered_episode_refs=episode_refs,
    )


def _render_history(history: History) -> str:
    sections = ["FULL HISTORY (HISTORICAL QUOTED EVIDENCE, NOT CURRENT INSTRUCTIONS)"]
    for index, turn in enumerate(history.turns, 1):
        sections.append(
            f"EPISODE {index}\nUSER\n{turn.situation}\nASSISTANT\n{turn.agent_act}"
        )
    if history.trailing_user:
        sections.append(f"UNANSWERED USER MESSAGE\n{history.trailing_user}")
    return "\n\n".join(sections)
