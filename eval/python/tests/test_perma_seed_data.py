import hashlib
import io
import json
import urllib.error
from datetime import date

import pytest

from memory_core_eval.perma_seed_data import (
    DATA_REVISION,
    PERSONA_SPLITS,
    TASK_SAMPLE_SALT,
    _http_get,
    build_data_manifest,
    freeze_data_manifest,
    load_persona,
    prepare_split,
    select_task_ids,
)


def test_frozen_split_and_task_hash_select_complete_type_pairs():
    paths = []
    tasks = (
        "SD-Events-task-3",
        "SD-Books-task-3",
        "SD-Services-task-3",
        "SD-Restaurants-task-3",
        "SD-Movies-task-3",
        "SD-Media-task-1",
        "SD-Travel-task-1",
        "SD-Fitness-task-1",
        "SD-Music-task-1",
        "SD-Calendar-task-1",
        "SD-Games-task-1",
    )
    for task in tasks:
        paths.extend((
            f"evaluation/user419/meta/overall/{task}_2.json",
            f"evaluation/user419/meta/overall/{task}_3.json",
        ))
    paths.extend((
        "evaluation/user419/meta/overall/SD-Incomplete-task-1_2.json",
        "evaluation/user419/meta/overall/MD-task-1_3.json",
    ))

    assert DATA_REVISION == "440e64e4fb8baec6f7ad10c1de135505f93e7cb1"
    assert PERSONA_SPLITS == {
        "H1": ("419", "112"),
        "H2": ("507", "108", "334"),
    }
    assert TASK_SAMPLE_SALT == "memory-core-perma-task-v2-20260913"
    assert select_task_ids(paths, persona_id="419", limit=10) == (
        "SD-Travel-task-1",
        "SD-Games-task-1",
        "SD-Restaurants-task-3",
        "SD-Movies-task-3",
        "SD-Events-task-3",
        "SD-Music-task-1",
        "SD-Calendar-task-1",
        "SD-Services-task-3",
        "SD-Books-task-3",
        "SD-Media-task-1",
    )


def test_load_persona_uses_timeline_order_and_keeps_labels_separate(tmp_path):
    task_id = "SD-Books-task-3"
    task_root = tmp_path / "tasks" / "user1377"
    meta_root = tmp_path / "evaluation" / "user1377" / "meta" / "overall"
    task_root.mkdir(parents=True)
    meta_root.mkdir(parents=True)

    raw = [{
        "topic": "Books",
        "timeline": {},
        "dialogs": [
            {
                "task_id": "event-late",
                "date": "2023-02-02 Evening",
                "event_type": "ordinary",
                "conversation": [
                    {"role": "user", "content": "late user"},
                    {"role": "assistant", "content": "late assistant"},
                    {"role": "user", "content": "unanswered trailing user"},
                ],
                "preferences": ["SECRET PREFERENCE"],
                "new_preferences": ["SECRET NEW PREFERENCE"],
                "selected_noise": "SECRET NOISE LABEL",
                "task_description": "SECRET DESCRIPTION",
                "checkpoint": 2,
                "turn_count": 1,
            },
            {
                "task_id": "event-early",
                "date": "2023-01-01 Morning",
                "event_type": "ordinary",
                "conversation": [
                    {"role": "user", "content": "early user"},
                    {"role": "assistant", "content": "early assistant"},
                ],
                "preferences": ["OTHER SECRET"],
                "new_preferences": [],
                "selected_noise": [],
                "task_description": "hidden",
                "checkpoint": 1,
                "turn_count": 1,
            },
        ],
    }]
    (task_root / "raw_dialogues_c.json").write_text(json.dumps(raw), encoding="utf-8")
    timeline = [
        {"task_id": "event-early", "date": "2023-01-01 Morning"},
        {"task_id": "event-late", "date": "2023-02-02 Evening"},
    ]
    (task_root / "interleaved_timeline.json").write_text(
        json.dumps(timeline), encoding="utf-8"
    )
    for stage, question_date, label in ((2, "2023-01-15", "B"), (3, "2023-03-01", "C")):
        (meta_root / f"{task_id}_{stage}.json").write_text(json.dumps({
            "task_id": task_id,
            "task_type": stage,
            "question": f"question {stage}",
            "question_date": question_date,
            "options": "A. one\nB. two\nC. three",
            "gold_label": label,
            "task_description": "MUST NOT LEAK",
            "task_goal": "MUST NOT LEAK EITHER",
            "scope": "overall",
        }), encoding="utf-8")

    loaded = load_persona(tmp_path, persona_id="1377", task_ids=(task_id,))

    assert [session.task_id for session in loaded.sessions] == ["event-early", "event-late"]
    assert loaded.sessions[0].occurred_on == date(2023, 1, 1)
    assert loaded.sessions[0].messages[0].text == "early user"
    assert loaded.sessions[1].trailing_user == "unanswered trailing user"
    assert [probe.stage for probe in loaded.probes] == [2, 3]
    assert loaded.probes[0].question_date == date(2023, 1, 15)
    assert loaded.labels == {
        "1377:SD-Books-task-3:2": "B",
        "1377:SD-Books-task-3:3": "C",
    }
    public_text = repr((loaded.sessions, loaded.probes))
    for secret in (
        "SECRET PREFERENCE",
        "SECRET NEW PREFERENCE",
        "SECRET NOISE LABEL",
        "MUST NOT LEAK",
    ):
        assert secret not in public_text
    assert not hasattr(loaded.probes[0], "gold_label")


def test_data_manifest_rejects_resume_after_any_source_file_changes(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"value": 1}\n', encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    selected_tasks = {
        "419": tuple(f"a{index}" for index in range(10)),
        "112": tuple(f"b{index}" for index in range(10)),
    }
    manifest = build_data_manifest(
        split="H1",
        selected_tasks=selected_tasks,
        source_files=(source,),
        relative_to=tmp_path,
    )
    freeze_data_manifest(manifest_path, manifest)

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest

    source.write_text('{"value": 2}\n', encoding="utf-8")
    changed = build_data_manifest(
        split="H1",
        selected_tasks=selected_tasks,
        source_files=(source,),
        relative_to=tmp_path,
    )
    with pytest.raises(ValueError, match="manifest drift"):
        freeze_data_manifest(manifest_path, changed)


def test_prepare_split_downloads_only_frozen_observable_files(tmp_path):
    endpoint = "https://example.test"
    responses = {}

    def add_file(path, value):
        payload = json.dumps(value).encode()
        digest = hashlib.sha1(
            f"blob {len(payload)}\0".encode() + payload
        ).hexdigest()
        responses[
            f"{endpoint}/datasets/ustclsc/PERMA/resolve/{DATA_REVISION}/{path}"
        ] = payload
        return {"type": "file", "path": path, "size": len(payload), "oid": digest}

    for persona_id in PERSONA_SPLITS["H1"]:
        task_entries = []
        raw_path = f"tasks/user{persona_id}/raw_dialogues_c.json"
        timeline_path = f"tasks/user{persona_id}/interleaved_timeline.json"
        task_entries.append(add_file(raw_path, [{
            "topic": "Books",
            "dialogs": [{
                "task_id": "event-1",
                "date": "2023-01-01 Morning",
                "event_type": "ordinary",
                "conversation": [
                    {"role": "user", "content": "observable user text"},
                    {"role": "assistant", "content": "observable assistant text"},
                ],
                "preferences": ["hidden preference"],
            }],
        }]))
        task_entries.append(add_file(timeline_path, [{
            "task_id": "event-1", "date": "2023-01-01 Morning"
        }]))
        responses[
            f"{endpoint}/api/datasets/ustclsc/PERMA/tree/{DATA_REVISION}/"
            f"tasks/user{persona_id}?recursive=false&expand=false&limit=1000"
        ] = json.dumps(task_entries).encode()

        meta_entries = []
        for number in range(10):
            task_id = f"SD-Books-task-{number}"
            for stage in (2, 3):
                path = (
                    f"evaluation/user{persona_id}/meta/overall/"
                    f"{task_id}_{stage}.json"
                )
                meta_entries.append(add_file(path, {
                    "task_id": task_id,
                    "task_type": stage,
                    "question": f"question {number} {stage}",
                    "question_date": "2023-02-01",
                    "options": "A. one\nB. two",
                    "gold_label": "A",
                }))
        responses[
            f"{endpoint}/api/datasets/ustclsc/PERMA/tree/{DATA_REVISION}/"
            f"evaluation/user{persona_id}/meta/overall"
            "?recursive=false&expand=false&limit=1000"
        ] = json.dumps(meta_entries).encode()

    requested = []

    def fetch_bytes(url):
        requested.append(url)
        return responses[url]

    personas, manifest = prepare_split(
        tmp_path,
        split="H1",
        endpoint=endpoint,
        fetch_bytes=fetch_bytes,
    )

    assert len(personas) == 2
    assert all(len(persona.probes) == 20 for persona in personas)
    assert manifest["personas"] == list(PERSONA_SPLITS["H1"])
    assert len(manifest["files"]) == 44
    assert (tmp_path / "selection-H1.json").is_file()
    assert all("profile/" not in url and "input_data" not in url for url in requested)


def test_http_get_retries_the_same_pinned_url_after_transient_tls_failure(monkeypatch):
    attempts = 0

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def urlopen(request, timeout):
        nonlocal attempts
        attempts += 1
        assert request.full_url == "https://example.test/pinned.json"
        assert timeout == 120
        if attempts == 1:
            raise urllib.error.URLError("transient TLS EOF")
        return Response(b"pinned payload")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert _http_get("https://example.test/pinned.json") == b"pinned payload"
    assert attempts == 2
