"""Prompts, arm ordering, strict judging, and paired CUPID statistics."""
from __future__ import annotations

import hashlib
import random
import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence

from .cupid_data import CupidLabel
from .perma_seed_effect import SeedEffectMode


PROTOCOL = "cupid-seed-generation-v1"

_ANSWER_SYSTEM = """You are responding directly to the user as a helpful personalized assistant.
Use LONG_TERM_MEMORY when present and relevant, but do not quote, mention, or discuss the memory itself.
The current request takes precedence over any conflicting memory.
Return only the response you would provide to the user."""

_JUDGE_SYSTEM = """## **Your Objective**

You are a critical and meticulous evaluator. You will be presented with a user's request to an AI assistant and the AI's response to the user. Your task is to evaluate whether the AI assistant's response satisfied the user's **preference**. To help you evaluate the responses on the preference, you will also be provided with an **evaluation checklist** that decomposes the preference into specific questions.

### **Preference and Checklist**

**Preference**: "{preference}"

**Evaluation Checklist**:
{checklist}

## **Instructions**

You should write down your analysis and assessment on how well the AI assistant's response satisfies each item in your checklist. You should follow these considerations:

- Walk through each checklist item and summarize the response's "strengths" and "weaknesses" regarding that checklist item.
- For each checklist item, you should consider whether the checklist item was satisfied or dissatisfied.
- Avoid considering aspects that are not included in the checklist. Focus only on the evaluation checklist. Ensure that your persona profile does not influence your evaluation.
- You should then return a score in the range of 1~10, with the following labels for each score:
  - 10: Exceptional
  - 9: Excellent
  - 8: Very Good
  - 7: Good
  - 6: Above Average
  - 5: Acceptable
  - 4: Below Average
  - 3: Poor
  - 2: Very Poor
  - 1: Unacceptable

Ensure that you follow the format given below. Avoid adding additional content that is not included in the format below.

---

## **Output Format**

### Evaluation of AI Assistant's Response

1. **<Checklist item 1>**: <Detailed analysis and evaluation of the AI assistant's response on the first item in the checklist>

2. **<Checklist item 2>**: <Detailed analysis and evaluation of the AI assistant's response on the second item in the checklist>

...

### Evaluation Label

<Return the evaluation label from the rubric>

### Evaluation Score

<Return your numeric score in the range of 1~10>"""

_SCORE_PATTERN = re.compile(
    r"\*{0,2}(10|[1-9])(?:\.0+)?(?:\s*/\s*10)?\*{0,2}"
    r"(?:\s*[-–—:]\s+\S.*)?"
)


def build_answer_prompt(
    *, current_request: str, memory_text: str
) -> tuple[str, str]:
    request = _nonblank(current_request, "current_request")
    instructions = _ANSWER_SYSTEM
    if not isinstance(memory_text, str):
        raise TypeError("memory_text must be str")
    if memory_text.strip():
        instructions += "\n\nLONG_TERM_MEMORY\n" + memory_text.strip()
    return instructions, "CURRENT_REQUEST\n" + request


def build_judge_prompt(
    *,
    user_request: str,
    ai_response: str,
    preference: str,
    checklist: Sequence[str],
) -> tuple[str, str]:
    request = _nonblank(user_request, "user_request")
    response = _nonblank(ai_response, "ai_response")
    preference = _nonblank(preference, "preference")
    items = tuple(_nonblank(item, "checklist item") for item in checklist)
    if not items:
        raise ValueError("checklist must not be empty")
    system = _JUDGE_SYSTEM.format(
        preference=preference,
        checklist="\n".join(f"- {item}" for item in items),
    )
    user = (
        "### User's Request\n\n"
        + request
        + "\n\n### AI Assistant's Response\n\n"
        + response
    )
    return system, user


def parse_judge_score(output: object) -> int:
    if not isinstance(output, str):
        raise ValueError("invalid CUPID judge score")
    headers = ("### **Evaluation Score**", "### Evaluation Score")
    matches = [(header, output.count(header)) for header in headers]
    if sum(count for _, count in matches) != 1:
        raise ValueError("invalid CUPID judge score")
    header = next(header for header, count in matches if count == 1)
    tail = output.split(header, 1)[1].strip()
    if not tail:
        raise ValueError("invalid CUPID judge score")
    token = tail.splitlines()[0].strip()
    match = _SCORE_PATTERN.fullmatch(token)
    if match is None:
        raise ValueError("invalid CUPID judge score")
    return int(match.group(1))


def answer_arm_order(instance_ref: str) -> tuple[SeedEffectMode, ...]:
    return _arm_order("cupid-seed-answer-arm-v1", instance_ref)


def judge_arm_order(instance_ref: str) -> tuple[SeedEffectMode, ...]:
    return _arm_order("cupid-seed-judge-arm-v1", instance_ref)


def summarize_cupid_effect(
    results: Sequence[Mapping[str, object]],
    *,
    labels: Mapping[str, CupidLabel],
    bootstrap_samples: int = 2_000,
    formal: bool = False,
) -> dict[str, object]:
    """Validate complete paired results and calculate persona-cluster effects."""

    if type(bootstrap_samples) is not int or bootstrap_samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    expected_refs = set(labels)
    if not expected_refs:
        raise ValueError("CUPID labels must not be empty")
    by_ref: dict[str, Mapping[str, object]] = {}
    rows: list[dict[str, object]] = []
    expected_modes = {mode.value for mode in SeedEffectMode}

    for result in results:
        instance_ref = _nonblank(result.get("instance_ref"), "instance_ref")
        if instance_ref not in expected_refs or instance_ref in by_ref:
            raise ValueError("CUPID result identity is duplicate or outside cohort")
        label = labels[instance_ref]
        if (
            result.get("protocol") != PROTOCOL
            or result.get("split") != label.split
            or result.get("persona_id") != label.persona_id
        ):
            raise ValueError("CUPID result identity drift")
        raw_answers = result.get("answers")
        if not isinstance(raw_answers, Sequence) or isinstance(
            raw_answers, (str, bytes)
        ):
            raise ValueError("CUPID result requires three answer arms")
        answers: dict[str, Mapping[str, object]] = {}
        for raw_answer in raw_answers:
            if not isinstance(raw_answer, Mapping):
                raise ValueError("CUPID answer arm must be an object")
            mode = raw_answer.get("mode")
            if not isinstance(mode, str) or mode in answers:
                raise ValueError("CUPID answer arm identity is invalid")
            if raw_answer.get("error") not in {None, ""}:
                raise ValueError("failed answer or judge arm cannot enter summary")
            try:
                _nonblank(raw_answer.get("output"), "answer output")
                judge_output = _nonblank(
                    raw_answer.get("judge_output"), "Judge output"
                )
            except ValueError as error:
                raise ValueError(
                    "CUPID result requires a complete answer and Judge record"
                ) from error
            _request_hash(raw_answer.get("answer_request_sha256"))
            _request_hash(raw_answer.get("judge_request_sha256"))
            memory_tokens = raw_answer.get("memory_tokens")
            if type(memory_tokens) is not int or memory_tokens < 0:
                raise ValueError("CUPID memory_tokens must be a nonnegative integer")
            score = raw_answer.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError("CUPID answer arm requires a numeric judge score")
            if score < 1 or score > 10:
                raise ValueError("CUPID answer arm judge score is outside 1..10")
            if float(parse_judge_score(judge_output)) != float(score):
                raise ValueError("CUPID Judge score drift")
            _refs(raw_answer, "selected_recollection_refs")
            _refs(raw_answer, "selected_disposition_refs")
            _refs(raw_answer, "rendered_recollection_refs")
            _refs(raw_answer, "rendered_disposition_refs")
            answers[mode] = raw_answer
        if set(answers) != expected_modes:
            raise ValueError("CUPID result requires exactly three answer arms")

        diagnostic = result.get("diagnostic")
        if not isinstance(diagnostic, Mapping):
            raise ValueError("CUPID result requires diagnostics")
        active = diagnostic.get("active_dispositions")
        if type(active) is not int or active < 0:
            raise ValueError("active_dispositions must be a nonnegative integer")
        none_score = float(answers[SeedEffectMode.NONE.value]["score"])
        recollection_score = float(
            answers[SeedEffectMode.RECOLLECTION_ONLY.value]["score"]
        )
        seed_answer = answers[SeedEffectMode.SEED_ENABLED.value]
        seed_score = float(seed_answer["score"])
        rows.append({
            "instance_ref": instance_ref,
            "persona_id": label.persona_id,
            "split": label.split,
            "instance_type": label.instance_type,
            "none": none_score,
            "recollection_only": recollection_score,
            "seed_enabled": seed_score,
            "delta": seed_score - recollection_score,
            "formed": active > 0,
            "selected": bool(_refs(seed_answer, "selected_disposition_refs")),
            "rendered": bool(_refs(seed_answer, "rendered_disposition_refs")),
            "score_changed": seed_score != recollection_score,
        })
        by_ref[instance_ref] = result

    if set(by_ref) != expected_refs:
        raise ValueError("CUPID summary requires the complete frozen cohort")
    splits = sorted({label.split for label in labels.values()})
    if formal and splits != ["H1", "H2"]:
        raise ValueError("formal CUPID summary requires exactly H1 and H2")

    arms = {
        mode.value: {
            "mean_score": statistics.mean(float(row[mode.value]) for row in rows)
        }
        for mode in SeedEffectMode
    }
    split_summary = {
        split: _group_summary([row for row in rows if row["split"] == split])
        for split in splits
    }
    instance_types = {
        instance_type: _group_summary([
            row for row in rows if row["instance_type"] == instance_type
        ])
        for instance_type in sorted({str(row["instance_type"]) for row in rows})
    }
    funnel = {
        split: {
            "instances": len(group),
            "formed": sum(bool(row["formed"]) for row in group),
            "selected": sum(bool(row["selected"]) for row in group),
            "rendered": sum(bool(row["rendered"]) for row in group),
            "score_changed": sum(bool(row["score_changed"]) for row in group),
        }
        for split in splits
        for group in ([row for row in rows if row["split"] == split],)
    }
    paired = _clustered_difference(rows, bootstrap_samples=bootstrap_samples)
    reasons: list[str] = []
    if formal:
        for split in ("H1", "H2"):
            if split_summary[split]["seed-minus-recollection"] <= 0:
                reasons.append(f"split {split} did not improve")
            for stage in ("formed", "selected", "rendered"):
                if funnel[split][stage] == 0:
                    reasons.append(f"split {split} has no {stage} Disposition")
        if paired["ci95"][0] <= 0:
            reasons.append("pooled persona-cluster CI lower bound is not positive")
    return {
        "instances": len(rows),
        "personas": len({str(row["persona_id"]) for row in rows}),
        "arms": arms,
        "splits": split_summary,
        "instance_types": instance_types,
        "funnel": funnel,
        "paired": {"seed-minus-recollection": paired},
        "uncertainty": (
            "instance-weighted score difference; 95% paired persona-cluster "
            "bootstrap; seed=0"
        ),
        "decision": {"passed": not reasons, "reasons": reasons},
    }


def _group_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, float | int]:
    if not rows:
        raise ValueError("CUPID result group must not be empty")
    return {
        "instances": len(rows),
        "none_mean": statistics.mean(float(row["none"]) for row in rows),
        "recollection_mean": statistics.mean(
            float(row["recollection_only"]) for row in rows
        ),
        "seed_mean": statistics.mean(float(row["seed_enabled"]) for row in rows),
        "seed-minus-recollection": statistics.mean(
            float(row["delta"]) for row in rows
        ),
    }


def _clustered_difference(
    rows: Sequence[Mapping[str, object]], *, bootstrap_samples: int
) -> dict[str, object]:
    by_persona: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_persona[str(row["persona_id"])].append(float(row["delta"]))
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


def _arm_order(salt: str, instance_ref: str) -> tuple[SeedEffectMode, ...]:
    instance_ref = _nonblank(instance_ref, "instance_ref")
    return tuple(sorted(
        SeedEffectMode,
        key=lambda mode: hashlib.sha256(
            f"{salt}:{instance_ref}:{mode.value}".encode()
        ).digest(),
    ))


def _refs(answer: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = answer.get(field)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    refs = tuple(_nonblank(item, field) for item in value)
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field} must contain unique refs")
    return refs


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonblank text")
    return value.strip()


def _request_hash(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("CUPID request hash must be lowercase SHA-256")
    return value
