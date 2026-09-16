"""Pinned, label-separated PERMA inputs for the Seed effect evaluation."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote


DATASET = "ustclsc/PERMA"
DATA_REVISION = "440e64e4fb8baec6f7ad10c1de135505f93e7cb1"
CODE_REVISION = "d678640987170e8cfbe9260b311e0493b9cd2c31"
PROTOCOL = "perma-seed-mcq-v2"
TASK_SAMPLE_SALT = "memory-core-perma-task-v2-20260913"
TASKS_PER_PERSONA = 10
PERSONA_SPLITS: dict[str, tuple[str, ...]] = {
    "H1": ("419", "112"),
    "H2": ("507", "108", "334"),
}
DEFAULT_DATA_ENDPOINT = "https://huggingface.co"

_TASK_PATH = re.compile(
    r"^evaluation/user(?P<persona>[^/]+)/meta/overall/"
    r"(?P<task>SD-.+)_(?P<stage>[23])\.json$"
)


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class TimelineSession:
    task_id: str
    occurred_on: date
    messages: tuple[Message, ...]
    trailing_user: str = ""


@dataclass(frozen=True, slots=True)
class Probe:
    question_ref: str
    persona_id: str
    task_id: str
    stage: int
    question_date: date
    question: str
    options: str


@dataclass(frozen=True, slots=True)
class PersonaData:
    persona_id: str
    sessions: tuple[TimelineSession, ...]
    probes: tuple[Probe, ...]
    labels: Mapping[str, str]


def select_task_ids(
    paths: Iterable[str], *, persona_id: str, limit: int = 5
) -> tuple[str, ...]:
    """Select complete Type 2/3 SD tasks using the frozen content-blind hash."""

    persona_id = _nonblank(persona_id, "persona_id")
    if type(limit) is not int or limit <= 0:
        raise ValueError("task limit must be positive")
    stages: dict[str, set[int]] = {}
    for path in paths:
        match = _TASK_PATH.fullmatch(str(path))
        if match is None or match.group("persona") != persona_id:
            continue
        stages.setdefault(match.group("task"), set()).add(int(match.group("stage")))
    complete = [task_id for task_id, values in stages.items() if values == {2, 3}]
    ranked = sorted(
        complete,
        key=lambda task_id: (
            hashlib.sha256(
                f"{TASK_SAMPLE_SALT}:{persona_id}:{task_id}".encode()
            ).digest(),
            task_id,
        ),
    )
    if len(ranked) < limit:
        raise ValueError(
            f"persona {persona_id} has {len(ranked)} complete Type 2/3 tasks; "
            f"need {limit}"
        )
    return tuple(ranked[:limit])


def load_persona(
    root: Path, *, persona_id: str, task_ids: Sequence[str]
) -> PersonaData:
    """Load observable dialogue text and public probes, keeping labels separate."""

    root = root.resolve()
    persona_id = _nonblank(persona_id, "persona_id")
    selected = tuple(_nonblank(task_id, "task_id") for task_id in task_ids)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("task_ids must be nonempty and unique")

    task_root = root / "tasks" / f"user{persona_id}"
    raw = _read_json(task_root / "raw_dialogues_c.json")
    timeline = _read_json(task_root / "interleaved_timeline.json")
    if not isinstance(raw, list) or not isinstance(timeline, list):
        raise ValueError("PERMA raw dialogue and timeline files must be arrays")

    dialogues: dict[str, Mapping[str, Any]] = {}
    for topic in raw:
        if not isinstance(topic, Mapping) or not isinstance(topic.get("dialogs"), list):
            raise ValueError("invalid PERMA raw dialogue topic")
        for dialogue in topic["dialogs"]:
            if not isinstance(dialogue, Mapping):
                raise ValueError("invalid PERMA dialogue")
            task_id = _nonblank(dialogue.get("task_id"), "dialogue task_id")
            if task_id in dialogues:
                raise ValueError(f"duplicate raw dialogue task_id: {task_id}")
            dialogues[task_id] = dialogue

    sessions: list[TimelineSession] = []
    seen_timeline: set[str] = set()
    for item in timeline:
        if not isinstance(item, Mapping):
            raise ValueError("invalid PERMA timeline item")
        task_id = _nonblank(item.get("task_id"), "timeline task_id")
        if task_id in seen_timeline:
            raise ValueError(f"duplicate timeline task_id: {task_id}")
        seen_timeline.add(task_id)
        dialogue = dialogues.get(task_id)
        if dialogue is None:
            raise ValueError(f"timeline dialogue missing from raw data: {task_id}")
        messages, trailing_user = _load_messages(dialogue.get("conversation"))
        sessions.append(TimelineSession(
            task_id=task_id,
            occurred_on=_parse_date(item.get("date"), "timeline date"),
            messages=messages,
            trailing_user=trailing_user,
        ))

    probes: list[Probe] = []
    labels: dict[str, str] = {}
    meta_root = root / "evaluation" / f"user{persona_id}" / "meta" / "overall"
    for task_id in selected:
        for stage in (2, 3):
            value = _read_json(meta_root / f"{task_id}_{stage}.json")
            if not isinstance(value, Mapping):
                raise ValueError("PERMA meta file must be an object")
            if value.get("task_id") != task_id or value.get("task_type") != stage:
                raise ValueError(f"PERMA meta identity mismatch: {task_id}_{stage}")
            question_ref = f"{persona_id}:{task_id}:{stage}"
            probe = Probe(
                question_ref=question_ref,
                persona_id=persona_id,
                task_id=task_id,
                stage=stage,
                question_date=_parse_date(value.get("question_date"), "question_date"),
                question=_nonblank(value.get("question"), "question"),
                options=_nonblank(value.get("options"), "options"),
            )
            label = _nonblank(value.get("gold_label"), "gold_label")
            probes.append(probe)
            labels[question_ref] = label
    probes.sort(key=lambda item: (item.question_date, item.task_id, item.stage))
    return PersonaData(persona_id, tuple(sessions), tuple(probes), labels)


def prepare_split(
    root: Path,
    *,
    split: str,
    endpoint: str = DEFAULT_DATA_ENDPOINT,
    fetch_bytes: Callable[[str], bytes] | None = None,
) -> tuple[tuple[PersonaData, ...], dict[str, object]]:
    """Download only the frozen observable files and freeze their content hashes."""

    split = split.strip().upper()
    if split not in PERSONA_SPLITS:
        raise ValueError("split must be H1 or H2")
    root = root.resolve()
    endpoint = _nonblank(endpoint, "endpoint").rstrip("/")
    fetch_bytes = fetch_bytes or _http_get
    manifest_path = root / f"selection-{split}.json"

    if manifest_path.is_file():
        frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            frozen.get("dataset") != DATASET
            or frozen.get("data_revision") != DATA_REVISION
            or frozen.get("split") != split
        ):
            raise ValueError("PERMA data manifest drift")
        raw_tasks = frozen.get("tasks")
        raw_files = frozen.get("files")
        if not isinstance(raw_tasks, Mapping) or not isinstance(raw_files, Mapping):
            raise ValueError("invalid frozen PERMA data manifest")
        selected_tasks = {
            str(persona): tuple(str(task) for task in tasks)
            for persona, tasks in raw_tasks.items()
            if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes))
        }
        source_files = tuple(root / str(name) for name in raw_files)
        current = build_data_manifest(
            split=split,
            selected_tasks=selected_tasks,
            source_files=source_files,
            relative_to=root,
        )
        freeze_data_manifest(manifest_path, current)
        return (
            tuple(
                load_persona(root, persona_id=persona, task_ids=selected_tasks[persona])
                for persona in PERSONA_SPLITS[split]
            ),
            current,
        )

    selected_tasks: dict[str, tuple[str, ...]] = {}
    source_files: list[Path] = []
    for persona_id in PERSONA_SPLITS[split]:
        task_tree_path = f"tasks/user{persona_id}"
        task_entries = _fetch_tree(
            endpoint, task_tree_path, fetch_bytes=fetch_bytes
        )
        by_path = _file_entries(task_entries)
        for name in ("raw_dialogues_c.json", "interleaved_timeline.json"):
            source_path = f"{task_tree_path}/{name}"
            source_files.append(_download_entry(
                root,
                endpoint=endpoint,
                entry=_required_entry(by_path, source_path),
                fetch_bytes=fetch_bytes,
            ))

        meta_tree_path = f"evaluation/user{persona_id}/meta/overall"
        meta_entries = _fetch_tree(
            endpoint, meta_tree_path, fetch_bytes=fetch_bytes
        )
        meta_by_path = _file_entries(meta_entries)
        tasks = select_task_ids(
            meta_by_path, persona_id=persona_id, limit=TASKS_PER_PERSONA
        )
        selected_tasks[persona_id] = tasks
        for task_id in tasks:
            for stage in (2, 3):
                source_path = f"{meta_tree_path}/{task_id}_{stage}.json"
                source_files.append(_download_entry(
                    root,
                    endpoint=endpoint,
                    entry=_required_entry(meta_by_path, source_path),
                    fetch_bytes=fetch_bytes,
                ))

    manifest = build_data_manifest(
        split=split,
        selected_tasks=selected_tasks,
        source_files=tuple(source_files),
        relative_to=root,
    )
    personas = tuple(
        load_persona(root, persona_id=persona, task_ids=selected_tasks[persona])
        for persona in PERSONA_SPLITS[split]
    )
    freeze_data_manifest(manifest_path, manifest)
    return personas, manifest


def build_data_manifest(
    *,
    split: str,
    selected_tasks: Mapping[str, Sequence[str]],
    source_files: Sequence[Path],
    relative_to: Path,
) -> dict[str, object]:
    split = split.strip().upper()
    if split not in PERSONA_SPLITS:
        raise ValueError("split must be H1 or H2")
    relative_to = relative_to.resolve()
    files: dict[str, str] = {}
    for source in source_files:
        resolved = source.resolve()
        try:
            name = resolved.relative_to(relative_to).as_posix()
        except ValueError as exc:
            raise ValueError("source file escapes dataset root") from exc
        if not resolved.is_file():
            raise ValueError(f"source file missing: {name}")
        files[name] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    expected_personas = PERSONA_SPLITS[split]
    if set(selected_tasks) != set(expected_personas):
        raise ValueError("selected tasks do not exactly cover the frozen split")
    if any(
        len(tuple(selected_tasks[persona])) != TASKS_PER_PERSONA
        or len(set(selected_tasks[persona])) != TASKS_PER_PERSONA
        for persona in expected_personas
    ):
        raise ValueError(
            f"each persona must have exactly {TASKS_PER_PERSONA} unique tasks"
        )
    tasks = {
        persona_id: list(selected_tasks[persona_id]) for persona_id in expected_personas
    }
    return {
        "benchmark": "PERMA",
        "protocol": PROTOCOL,
        "dataset": DATASET,
        "data_revision": DATA_REVISION,
        "code_revision": CODE_REVISION,
        "split": split,
        "personas": list(expected_personas),
        "tasks": tasks,
        "files": dict(sorted(files.items())),
    }


def freeze_data_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    normalized = json.loads(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    if path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != normalized:
            raise ValueError("PERMA data manifest drift")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_messages(value: object) -> tuple[tuple[Message, ...], str]:
    if not isinstance(value, list) or not value:
        raise ValueError("dialogue conversation must be a nonempty array")
    result: list[Message] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("dialogue message must be an object")
        role = _nonblank(raw.get("role"), "message role")
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported PERMA message role: {role}")
        if result and result[-1].role == role:
            raise ValueError("PERMA conversation must alternate user and assistant")
        result.append(Message(role, _nonblank(raw.get("content"), "message content")))
    trailing_user = ""
    if result and result[-1].role == "user":
        trailing_user = result.pop().text
    if not result or result[0].role != "user" or result[-1].role != "assistant" or len(result) % 2:
        raise ValueError("PERMA session must contain complete user/assistant pairs")
    return tuple(result), trailing_user


def _fetch_tree(
    endpoint: str,
    path: str,
    *,
    fetch_bytes: Callable[[str], bytes],
) -> list[Mapping[str, Any]]:
    url = (
        f"{endpoint}/api/datasets/{DATASET}/tree/{DATA_REVISION}/{quote(path, safe='/')}"
        "?recursive=false&expand=false&limit=1000"
    )
    value = json.loads(fetch_bytes(url))
    if not isinstance(value, list):
        raise ValueError(f"invalid PERMA tree response: {path}")
    if len(value) >= 1000:
        raise ValueError(f"PERMA tree response may be truncated: {path}")
    return value


def _file_entries(entries: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("type") != "file":
            continue
        path = _nonblank(entry.get("path"), "tree entry path")
        if path in result:
            raise ValueError(f"duplicate PERMA tree entry: {path}")
        result[path] = entry
    return result


def _required_entry(
    entries: Mapping[str, Mapping[str, Any]], path: str
) -> Mapping[str, Any]:
    try:
        return entries[path]
    except KeyError as exc:
        raise ValueError(f"required PERMA source missing from pinned tree: {path}") from exc


def _download_entry(
    root: Path,
    *,
    endpoint: str,
    entry: Mapping[str, Any],
    fetch_bytes: Callable[[str], bytes],
) -> Path:
    source_path = _nonblank(entry.get("path"), "source path")
    destination = (root / source_path).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("PERMA source path escapes dataset root") from exc
    url = (
        f"{endpoint}/datasets/{DATASET}/resolve/{DATA_REVISION}/"
        f"{quote(source_path, safe='/')}"
    )
    payload = fetch_bytes(url)
    if not isinstance(payload, bytes):
        raise TypeError("PERMA fetcher must return bytes")
    expected_size = entry.get("size")
    if type(expected_size) is not int or len(payload) != expected_size:
        raise ValueError(f"PERMA source size mismatch: {source_path}")
    lfs = entry.get("lfs")
    if isinstance(lfs, Mapping):
        expected = _nonblank(lfs.get("oid"), "LFS oid")
        actual = hashlib.sha256(payload).hexdigest()
    else:
        expected = _nonblank(entry.get("oid"), "git oid")
        actual = hashlib.sha1(
            f"blob {len(payload)}\0".encode() + payload
        ).hexdigest()
    if actual != expected:
        raise ValueError(f"PERMA source object hash mismatch: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ValueError(f"existing PERMA source differs: {source_path}")
        return destination
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    return destination


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "memory-core-eval/0.1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(0.25 * (2 ** attempt))
    raise AssertionError("positive retry count exhausted without return")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"required PERMA file missing: {path}") from exc


def _parse_date(value: object, field: str) -> date:
    text = _nonblank(value, field)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"invalid {field}: {text}") from exc


def _nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonblank text")
    return value.strip()
