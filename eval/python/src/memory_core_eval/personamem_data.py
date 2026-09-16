"""Pinned PersonaMem-v2 text data; hidden labels never enter model inputs."""
from __future__ import annotations

import __future__
import ast
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from .reference import HistoricalTurn


DATASET = "bowen-upenn/PersonaMem-v2"
DATA_REVISION = "0622e56d1cc6f1bc990a5100a6ec4022a60e66a6"
UPSTREAM_REVISION = "dd52429f83ced4394be46c3849186a423942b2a5"
BENCHMARK_SHA256 = "95f2a8a324aab7baf2af937feae12731369e2abf7cad5ab3e170594cb25a3e52"
INFERENCE_SHA256 = "d78b15cb58ad4e9219b90a0ff7713dc3bff997e69df8664889a6cc7fc323484f"
QUERY_SUFFIX = " Please recall my related preferences from our conversation history to give personalized responses."


@dataclass(frozen=True)
class Question:
    question_ref: str
    persona_id: str
    history_path: str
    query: str
    correct_answer: str
    incorrect_answers: tuple[str, ...]
    metadata: dict[str, str | int]


@dataclass(frozen=True)
class History:
    background: str
    turns: tuple[HistoricalTurn, ...]
    trailing_user: str = ""


def verify_sha256(path: Path, expected: str) -> str:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path.name}: {actual}")
    return actual


def load_questions(path: Path, *, size: str) -> tuple[Question, ...]:
    if size not in {"32k", "128k"}:
        raise ValueError("size must be 32k or 128k")
    questions = []
    with path.open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream)):
            try:
                query = ast.literal_eval(row["user_query"])
            except (ValueError, SyntaxError):
                query = json.loads(row["user_query"])
            if not isinstance(query, dict) or query.get("role") != "user":
                raise ValueError(f"row {index}: expected a user query")
            content = _text(query.get("content"))
            incorrect = json.loads(row["incorrect_answers"])
            if not isinstance(incorrect, list) or len(incorrect) != 3:
                raise ValueError(f"row {index}: expected three distractors")
            answers = [_text(row["correct_answer"]), *map(_text, incorrect)]
            if len(set(answers)) != 4:
                raise ValueError(f"row {index}: answers must be distinct")
            questions.append(Question(
                question_ref=f"row-{index:05d}",
                persona_id=_text(row["persona_id"]),
                history_path=_text(row[f"chat_history_{size}_link"]),
                query=content,
                correct_answer=answers[0],
                incorrect_answers=tuple(answers[1:]),
                # Reporting labels only. No preference text, persona gold, or evidence hints.
                metadata={
                    key: row.get(key, "")
                    for key in ("pref_type", "who", "updated", "sensitive_info", "topic_query")
                } | {"distance_tokens": int(row[f"distance_from_related_snippet_to_query_{size}"])},
            ))
    if not questions:
        raise ValueError("benchmark contains no questions")
    return tuple(questions)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonblank text")
    return value


def resolve_history_path(root: Path, link: str) -> Path:
    path = PurePosixPath(link)
    if (len(path.parts) != 3 or path.parts[0] != "data"
            or path.parts[1] not in {"chat_history_32k", "chat_history_128k"}
            or not path.name.endswith(".json")):
        raise ValueError("expected an official chat history path, not raw persona data")
    destination = (root / link).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError("history path escapes dataset root")
    return destination


def load_history(path: Path) -> History:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        messages = value.get("chat_history", value.get("conversations"))
    else:
        messages = value
    if not isinstance(messages, list) or not messages:
        raise ValueError("history must contain messages")
    background = []
    index = 0
    while index < len(messages) and messages[index].get("role") == "system":
        background.append(_text(messages[index].get("content")))
        index += 1
    dialogue: list[dict[str, str]] = []
    for message in messages[index:]:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise ValueError("unexpected role inside dialogue")
        raw_text = message.get("content")
        if raw_text is None:
            if role == "assistant" and dialogue and dialogue[-1]["role"] == "user":
                dialogue.pop()
            continue
        if not isinstance(raw_text, str):
            raise ValueError("expected text content")
        # The pinned validation split contains one empty user artifact. It has
        # no observable content to persist; skipping it lets the existing
        # same-role merge retain every non-empty message without inventing one.
        if not raw_text.strip():
            continue
        text = raw_text
        if dialogue and dialogue[-1]["role"] == role:
            dialogue[-1]["content"] += "\n\n" + text
        else:
            dialogue.append({"role": role, "content": text})
    tail = dialogue.pop()["content"] if dialogue and dialogue[-1]["role"] == "user" else ""
    if not dialogue or len(dialogue) % 2:
        raise ValueError("history must contain observed user/assistant pairs")
    turns = []
    for offset in range(0, len(dialogue), 2):
        situation, act = dialogue[offset:offset + 2]
        if situation.get("role") != "user" or act.get("role") != "assistant":
            raise ValueError("history must alternate user and assistant; no invented replies")
        turns.append(HistoricalTurn(_text(situation.get("content")), _text(act.get("content"))))
    return History("\n\n".join(background), tuple(turns), tail)


def select_questions(questions: tuple[Question, ...], *, personas: int,
                     per_persona: int, seed: int) -> tuple[Question, ...]:
    if personas <= 0 or per_persona <= 0:
        raise ValueError("personas and per_persona must be positive")
    grouped: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        grouped[question.persona_id].append(question)

    def key(value: str) -> str:
        return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()

    if personas > len(grouped):
        raise ValueError("requested more personas than the benchmark contains")
    selected = []
    for persona in sorted(grouped, key=key)[:personas]:
        pool = grouped[persona]
        if per_persona > len(pool):
            raise ValueError(f"persona {persona} has fewer than {per_persona} questions")
        selected.extend(sorted(pool, key=lambda q: key(q.question_ref))[:per_persona])
    return tuple(selected)


def load_official_mcq(path: Path) -> SimpleNamespace:
    """Load only three reviewed, checksum-pinned upstream scoring methods.

    No upstream imports, constructors, API calls, or data generators are executed.
    This avoids redistributing unlicensed upstream code or installing its training stack.
    """
    verify_sha256(path, INFERENCE_SHA256)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    evaluator = next(n for n in tree.body
                     if isinstance(n, ast.ClassDef) and n.name == "PersonaBenchmarkEvaluator")
    names = {"create_mcq_options", "extract_final_answer", "check_mcq_correctness"}
    methods = [n for n in evaluator.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in methods} != names:
        raise ValueError("official MCQ methods missing")
    module = ast.Module(body=methods, type_ignores=[])
    namespace: dict = {}
    exec(compile(module, str(path), "exec", flags=__future__.annotations.compiler_flag), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})
