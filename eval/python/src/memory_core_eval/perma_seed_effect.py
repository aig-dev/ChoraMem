"""Deterministic three-arm rendering and paired PERMA Seed statistics."""
from __future__ import annotations

import hashlib
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from memory_core import memory_pb2 as pb, render_memory_context

from .perma_seed_data import PERSONA_SPLITS, TASKS_PER_PERSONA


class SeedEffectMode(str, Enum):
    NONE = "none"
    RECOLLECTION_ONLY = "recollection_only"
    SEED_ENABLED = "seed_enabled"


@dataclass(frozen=True, slots=True)
class SeedEffectContext:
    mode: SeedEffectMode
    text: str
    memory_refs: tuple[str, ...]
    recollection_refs: tuple[str, ...]
    disposition_refs: tuple[str, ...]
    selected_recollection_refs: tuple[str, ...]
    selected_disposition_refs: tuple[str, ...]
    memory_tokens: int


def build_seed_contexts(
    selected: pb.MemoryContext,
    *,
    max_tokens: int,
    token_count: Callable[[str], int],
) -> dict[SeedEffectMode, SeedEffectContext]:
    """Derive all arms from one Select while excluding raw Episode evidence."""

    if type(max_tokens) is not int or max_tokens < 0:
        raise ValueError("max_tokens must be a nonnegative integer")
    selected_recollections = tuple(
        item.memory_ref for item in selected.recollections if item.text.strip()
    )
    selected_dispositions = tuple(
        item.memory_ref for item in selected.dispositions if item.text.strip()
    )
    contexts: dict[SeedEffectMode, SeedEffectContext] = {
        SeedEffectMode.NONE: SeedEffectContext(
            mode=SeedEffectMode.NONE,
            text="",
            memory_refs=(),
            recollection_refs=(),
            disposition_refs=(),
            selected_recollection_refs=selected_recollections,
            selected_disposition_refs=selected_dispositions,
            memory_tokens=0,
        )
    }
    for mode in (SeedEffectMode.RECOLLECTION_ONLY, SeedEffectMode.SEED_ENABLED):
        filtered = pb.MemoryContext()
        for item in selected.recollections:
            filtered.recollections.add().CopyFrom(item)
        if mode is SeedEffectMode.SEED_ENABLED:
            for item in selected.dispositions:
                filtered.dispositions.add().CopyFrom(item)
        rendered = render_memory_context(
            filtered,
            max_tokens=max_tokens,
            token_count=token_count,
        )
        rendered_refs = set(rendered.memory_refs)
        contexts[mode] = SeedEffectContext(
            mode=mode,
            text=rendered.text,
            memory_refs=rendered.memory_refs,
            recollection_refs=tuple(
                ref for ref in selected_recollections if ref in rendered_refs
            ),
            disposition_refs=tuple(
                ref for ref in selected_dispositions if ref in rendered_refs
            ),
            selected_recollection_refs=selected_recollections,
            selected_disposition_refs=selected_dispositions,
            memory_tokens=token_count(rendered.text),
        )
    return contexts


def effect_mode_order(question_ref: str) -> tuple[SeedEffectMode, ...]:
    question_ref = _nonblank(question_ref, "question_ref")
    return tuple(sorted(
        SeedEffectMode,
        key=lambda mode: hashlib.sha256(
            f"perma-seed-arm-v1:{question_ref}:{mode.value}".encode()
        ).digest(),
    ))


def extract_option_token(output: object) -> str:
    if not isinstance(output, str):
        return ""
    token = output.strip().upper()
    return token if len(token) == 1 and "A" <= token <= "Z" else ""


def summarize_seed_effect(
    results: Sequence[Mapping[str, object]], *, bootstrap_samples: int = 2_000
) -> dict[str, object]:
    """Validate the frozen 100-question paired cohort and apply its pass gate."""

    if bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    expected_modes = {mode.value for mode in SeedEffectMode}
    expected_persona_split = {
        persona: split for split, personas in PERSONA_SPLITS.items() for persona in personas
    }
    questions: dict[tuple[str, str], dict[str, Mapping[str, object]]] = {}
    stages: dict[str, list[int]] = defaultdict(list)
    disposition_rendered = {split: 0 for split in PERSONA_SPLITS}

    for result in results:
        split = _nonblank(result.get("split"), "split").upper()
        persona = _nonblank(result.get("persona_id"), "persona_id")
        question_ref = _nonblank(result.get("question_ref"), "question_ref")
        if expected_persona_split.get(persona) != split:
            raise ValueError("result persona is outside its frozen split")
        stage = result.get("stage")
        if stage not in {2, 3}:
            raise ValueError("PERMA Seed result stage must be Type 2 or Type 3")
        key = (persona, question_ref)
        if key in questions:
            raise ValueError(f"duplicate PERMA Seed question: {key}")
        raw_answers = result.get("answers")
        if not isinstance(raw_answers, Sequence) or isinstance(raw_answers, (str, bytes)):
            raise TypeError("each question requires three answer arms")
        by_mode: dict[str, Mapping[str, object]] = {}
        for answer in raw_answers:
            if not isinstance(answer, Mapping):
                raise TypeError("answer arm must be a mapping")
            mode = answer.get("mode")
            if not isinstance(mode, str) or mode in by_mode:
                raise ValueError("answer modes must be unique strings")
            if type(answer.get("correct")) is not bool:
                raise TypeError("answer correctness must be bool")
            if answer.get("error") not in {None, ""}:
                raise ValueError("failed answer arms cannot enter the effect summary")
            by_mode[mode] = answer
        if set(by_mode) != expected_modes:
            raise ValueError("each question requires exactly the three frozen modes")
        refs = by_mode[SeedEffectMode.SEED_ENABLED.value].get(
            "rendered_disposition_refs", ()
        )
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
            raise TypeError("rendered disposition refs must be a sequence")
        if refs:
            disposition_rendered[split] += 1
        questions[key] = by_mode
        stages[persona].append(stage)

    expected_personas = set(expected_persona_split)
    if {persona for persona, _ in questions} != expected_personas:
        raise ValueError("summary requires both complete frozen persona splits")
    for persona in expected_personas:
        if sorted(stages[persona]) != [2] * TASKS_PER_PERSONA + [
            3
        ] * TASKS_PER_PERSONA:
            raise ValueError(
                f"persona {persona} requires ten Type 2 and ten Type 3 questions"
            )

    split_summary: dict[str, dict[str, object]] = {}
    flips: dict[str, int] = {}
    for split, personas in PERSONA_SPLITS.items():
        keys = [key for key in questions if key[0] in personas]
        recollection = [
            bool(questions[key][SeedEffectMode.RECOLLECTION_ONLY.value]["correct"])
            for key in keys
        ]
        seed = [
            bool(questions[key][SeedEffectMode.SEED_ENABLED.value]["correct"])
            for key in keys
        ]
        net_flips = sum(int(right) - int(left) for left, right in zip(recollection, seed))
        flips[split] = net_flips
        split_summary[split] = {
            "questions": len(keys),
            "recollection_accuracy": statistics.mean(recollection),
            "seed_accuracy": statistics.mean(seed),
            "seed-minus-recollection": statistics.mean(
                int(right) - int(left) for left, right in zip(recollection, seed)
            ),
            "rendered_disposition_questions": disposition_rendered[split],
        }

    paired = _clustered_difference(questions, bootstrap_samples=bootstrap_samples)
    reasons: list[str] = []
    for split in PERSONA_SPLITS:
        if split_summary[split]["seed-minus-recollection"] <= 0:
            reasons.append(f"split {split} did not improve")
        if flips[split] <= 0:
            reasons.append(f"split {split} has no net correct Seed flip")
        if disposition_rendered[split] == 0:
            reasons.append(f"split {split} rendered no Disposition")
    if paired["ci95"][0] <= 0:
        reasons.append("pooled persona-cluster CI lower bound is not positive")
    return {
        "questions": len(questions),
        "personas": len(expected_personas),
        "splits": split_summary,
        "paired": {"seed-minus-recollection": paired},
        "net_correct_flips": flips,
        "uncertainty": (
            "question-weighted difference; 95% paired persona-cluster bootstrap; "
            "seed=0"
        ),
        "decision": {"passed": not reasons, "reasons": reasons},
    }


def _clustered_difference(
    questions: Mapping[tuple[str, str], Mapping[str, Mapping[str, object]]],
    *,
    bootstrap_samples: int,
) -> dict[str, object]:
    by_persona: dict[str, list[int]] = defaultdict(list)
    for (persona, _), answers in questions.items():
        by_persona[persona].append(
            int(answers[SeedEffectMode.SEED_ENABLED.value]["correct"])
            - int(answers[SeedEffectMode.RECOLLECTION_ONLY.value]["correct"])
        )
    personas = sorted(by_persona)
    point = [value for persona in personas for value in by_persona[persona]]
    rng = random.Random(0)
    samples = sorted(
        statistics.mean(
            value
            for persona in rng.choices(personas, k=len(personas))
            for value in by_persona[persona]
        )
        for _ in range(bootstrap_samples)
    )
    lower = min(int(bootstrap_samples * 0.025), bootstrap_samples - 1)
    upper = min(int(bootstrap_samples * 0.975), bootstrap_samples - 1)
    return {
        "difference": statistics.mean(point),
        "ci95": [samples[lower], samples[upper]],
    }


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()
