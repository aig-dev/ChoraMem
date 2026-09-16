from __future__ import annotations

import json
from types import SimpleNamespace

import grpc
import pytest


@pytest.mark.asyncio
async def test_chat_adapter_preserves_plain_text_and_records_real_usage(tmp_path):
    from memory_core_eval.personamem_live import ChatTextModel

    requests = []

    class Completions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Final Answer: A"), finish_reason="stop")],
                usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 123, "completion_tokens": 6}))

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    path = tmp_path / "usage.jsonl"
    model = ChatTextModel(model="deepseek-v4-flash", api_key="unused", base_url="https://example.test",
                          client=client, usage_file=path)
    answer = await model.complete(instructions="Common profile", input_text="Query", max_output_tokens=100)
    assert answer == "Final Answer: A"
    assert requests[0]["messages"] == [
        {"role": "system", "content": "Common profile"}, {"role": "user", "content": "Query"}]
    assert requests[0]["max_tokens"] == 100
    assert json.loads(path.read_text())["usage"]["prompt_tokens"] == 123
    assert json.loads(path.read_text())["finish_reason"] == "stop"
    assert "api_key" not in path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
async def test_chat_adapter_rejects_incomplete_text_but_keeps_usage(tmp_path, finish_reason):
    from memory_core_eval.personamem_live import ChatTextModel

    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="NO_CHANGE"), finish_reason=finish_reason)],
                usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 123, "completion_tokens": 8192}),
            )

    path = tmp_path / "usage.jsonl"
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    model = ChatTextModel(model="test-model", api_key="unused", base_url="https://example.test",
                          client=client, usage_file=path)
    with pytest.raises(RuntimeError, match="did not finish"):
        await model.complete(instructions="", input_text="Window", max_output_tokens=8192)

    usage = json.loads(path.read_text())
    assert usage["finish_reason"] == finish_reason
    assert usage["usage"]["completion_tokens"] == 8192
    assert usage["raw_output"] == "NO_CHANGE"


@pytest.mark.asyncio
async def test_chat_adapter_retries_length_with_the_same_frozen_request(tmp_path):
    from memory_core_eval.personamem_live import ChatTextModel

    requests = []
    responses = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=""),
                    finish_reason="length",
                )
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 80, "completion_tokens": 1024}
            ),
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="A"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 80, "completion_tokens": 6}
            ),
        ),
    ]

    class Completions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            return responses.pop(0)

    usage_path = tmp_path / "usage.jsonl"
    model = ChatTextModel(
        model="MiniMax-M2.5",
        api_key="unused",
        base_url="https://example.test",
        usage_file=usage_path,
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        completion_attempts=3,
    )

    assert await model.complete(
        instructions="Choose one option.",
        input_text="Return A.",
        max_output_tokens=1024,
    ) == "A"
    assert requests[0] == requests[1]
    assert [json.loads(line)["finish_reason"] for line in usage_path.read_text().splitlines()] == [
        "length",
        "stop",
    ]


@pytest.mark.asyncio
async def test_explicit_thinking_arm_keeps_same_model_and_records_mode(tmp_path):
    from memory_core_eval.personamem_live import ChatTextModel
    requests = []

    class Completions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="NO_CHANGE"), finish_reason="stop")], usage=None)

    path = tmp_path / "usage.jsonl"
    model = ChatTextModel(model="deepseek-v4-flash", api_key="unused", base_url="https://example.test",
                          usage_file=path, client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
                          thinking_enabled=True)
    assert await model.complete(instructions="", input_text="Window", max_output_tokens=8192) == "NO_CHANGE"
    assert requests[0]["model"] == "deepseek-v4-flash"
    assert requests[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert requests[0]["reasoning_effort"] == "high"
    assert "temperature" not in requests[0]
    assert json.loads(path.read_text())["thinking_enabled"] is True


@pytest.mark.asyncio
async def test_chat_adapter_can_split_provider_reasoning_from_plain_text(tmp_path):
    from memory_core_eval.personamem_live import ChatTextModel

    requests = []

    class Completions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content="NO_CHANGE",
                        reasoning_details=[{"text": "private reasoning"}],
                    ),
                    finish_reason="stop",
                )],
                usage=None,
            )

    path = tmp_path / "usage.jsonl"
    model = ChatTextModel(
        model="MiniMax-M2.5",
        api_key="unused",
        base_url="https://example.test",
        usage_file=path,
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        reasoning_split=True,
    )

    assert await model.complete(
        instructions="",
        input_text="Window",
        max_output_tokens=8192,
    ) == "NO_CHANGE"
    assert requests[0]["extra_body"] == {"reasoning_split": True}
    assert json.loads(path.read_text())["reasoning_split"] is True


@pytest.mark.asyncio
async def test_worker_recorder_keeps_attempted_input_when_provider_fails(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class FailedModel:
        async def complete(self, **kwargs):
            started = json.loads((tmp_path / "attempts.jsonl").read_text())
            assert started["input"] == "frozen window"
            assert started["status"] == "started"
            assert started["attempt_id"]
            raise TimeoutError("provider did not return")

    worker = RecordingWorkerModel(FailedModel(), tmp_path / "windows.jsonl")
    with pytest.raises(TimeoutError):
        await worker.complete("frozen window")
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["input"] == "frozen window"
    assert result["error_type"] == "TimeoutError"
    assert result["attempt_id"] == json.loads((tmp_path / "attempts.jsonl").read_text())["attempt_id"]


@pytest.mark.asyncio
async def test_worker_explicit_output_budget_does_not_change_default(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel
    limits = []

    class Model:
        async def complete(self, **kwargs):
            limits.append(kwargs["max_output_tokens"])
            return "NO_CHANGE"

    await RecordingWorkerModel(Model(), tmp_path / "windows.jsonl").complete("first")
    await RecordingWorkerModel(Model(), tmp_path / "windows.jsonl", max_output_tokens=32768).complete("second")
    assert limits == [8192, 32768]


@pytest.mark.asyncio
async def test_worker_exact_replay_bypasses_provider_and_is_recorded(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("exact replay called the provider")

    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={"frozen input": "frozen output"},
        strict_replay=True,
    )
    assert await worker.complete("frozen input") == "frozen output"
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["output"] == "frozen output"
    assert result["replay_hit"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["seed", "recollection"])
async def test_worker_replay_only_alpha_renames_opaque_memory_refs(
    tmp_path, prefix
):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("opaque-ref replay called the provider")

    recorded_ref = f"{prefix}_{'a' * 32}@1"
    current_ref = f"{prefix}_{'b' * 32}@1"
    recorded_input = f"ALLOWED_TARGET\n{recorded_ref}\nTEXT\nunchanged"
    current_input = f"ALLOWED_TARGET\n{current_ref}\nTEXT\nunchanged"
    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={recorded_input: f"TARGET\n{recorded_ref}\nCHANGE\nREENACT"},
        strict_replay=True,
        semantic_memory_replay=True,
    )

    assert await worker.complete(current_input) == (
        f"TARGET\n{current_ref}\nCHANGE\nREENACT"
    )
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["replay_hit"] is True
    assert result["replay_match"] == "semantic_memory_state"


@pytest.mark.asyncio
async def test_worker_semantic_cache_reuses_a_completed_equivalent_request(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    provider_calls = []
    first_ref = f"seed_{'a' * 32}@1"
    second_ref = f"seed_{'b' * 32}@1"

    class Model:
        async def complete(self, **kwargs):
            provider_calls.append(kwargs["input_text"])
            return f"TARGET\n{first_ref}\nCHANGE\nREENACT"

    worker = RecordingWorkerModel(
        Model(),
        tmp_path / "windows.jsonl",
        semantic_memory_replay=True,
    )
    first = f"ALLOWED_TARGET\n{first_ref}\nTEXT\nunchanged"
    second = f"ALLOWED_TARGET\n{second_ref}\nTEXT\nunchanged"

    assert await worker.complete(first) == (
        f"TARGET\n{first_ref}\nCHANGE\nREENACT"
    )
    assert await worker.complete(second) == (
        f"TARGET\n{second_ref}\nCHANGE\nREENACT"
    )
    assert provider_calls == [first]
    records = [
        json.loads(line)
        for line in (tmp_path / "windows.jsonl").read_text().splitlines()
    ]
    assert [record["replay_match"] for record in records] == [
        "none",
        "semantic_memory_state",
    ]


def _isolated_worker_window(
    namespace,
    episode_refs,
    *,
    related_order=(1, 2),
    changed_text=False,
    changed_role=False,
    split_related_session=False,
):
    current_ref, first_related_ref, second_related_ref = episode_refs
    episodes = {
        1: (
            first_related_ref,
            "session:1",
            "The Agent waits with the unfinished scene.",
            "CONTEXT" if changed_role else "SITUATION",
            "The user brings a draft." + (" Changed." if changed_text else ""),
        ),
        2: (
            second_related_ref,
            "session:2" if split_related_session else "session:1",
            "The Agent asks one gentle question.",
            "SITUATION",
            "The user brings a second draft.",
        ),
    }

    def episode_block(tag, ref, session, agent_text, role, user_text, index):
        return (
            f"{tag} {ref}\n"
            f"SESSION {namespace}:{session}\n"
            "AGENT_ACT\n"
            f"ACTOR agent agent:{namespace}\n"
            f"SOURCE {namespace}:source:{index}:agent\n"
            f"{agent_text}\n"
            f"{role}\n"
            f"ACTOR user user:{namespace}\n"
            f"SOURCE {namespace}:source:{index}:user\n"
            f"{user_text}"
        )

    related = [
        episode_block("RELATED_EPISODE", *episodes[index], index)
        for index in related_order
    ]
    basis_order = [episode_refs[index] for index in related_order] + [current_ref]
    allowed_basis = "\n".join(
        f"ALLOWED_BASIS\n{ref}" for ref in basis_order
    )
    direct_basis = "\n".join(
        f"DIRECT_EPISODE {episode_refs[index]}" for index in related_order
    )
    return (
        "ALLOWED_TARGETS_BEGIN\n"
        "ALLOWED_TARGET\nNEW_DISPOSITION\n"
        "ALLOWED_TARGETS_END\n"
        "ALLOWED_BASIS_BEGIN\n"
        f"{allowed_basis}\n"
        "ALLOWED_BASIS_END\n"
        "WINDOW_BEGIN\n"
        + episode_block(
            "EPISODE",
            current_ref,
            "session:0",
            "The Agent receives the opening scene.",
            "SITUATION",
            "The user opens with a scene.",
            0,
        )
        + "\n\nRELATED_EPISODES\n"
        + "\n\n".join(related)
        + "\n\nELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\n"
        "APPLICATION RELATION\n"
        f"{direct_basis}\n"
        "WINDOW_END"
    )


@pytest.mark.asyncio
async def test_worker_replay_alpha_renames_isolated_evidence_graph(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("isolated evidence replay called the provider")

    recorded_refs = tuple(f"episode_{letter * 64}" for letter in "abc")
    current_refs = tuple(f"episode_{letter * 64}" for letter in "def")
    recorded_input = _isolated_worker_window("owner-a", recorded_refs)
    current_input = _isolated_worker_window(
        "owner-b", current_refs, related_order=(2, 1)
    )
    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={recorded_input: f"TARGET\nNEW_DISPOSITION\nBASIS\n{recorded_refs[1]}"},
        strict_replay=True,
        semantic_memory_replay=True,
    )

    assert await worker.complete(current_input) == (
        f"TARGET\nNEW_DISPOSITION\nBASIS\n{current_refs[1]}"
    )
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["replay_match"] == "semantic_memory_state"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["evidence_text", "source_role", "session_equality"],
)
async def test_worker_replay_keeps_evidence_semantics_in_cache_key(tmp_path, change):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("cache miss must fail before provider")

    recorded_refs = tuple(f"episode_{letter * 64}" for letter in "abc")
    current_refs = tuple(f"episode_{letter * 64}" for letter in "def")
    recorded_input = _isolated_worker_window("owner-a", recorded_refs)
    current_input = _isolated_worker_window(
        "owner-b",
        current_refs,
        changed_text=change == "evidence_text",
        changed_role=change == "source_role",
        split_related_session=change == "session_equality",
    )
    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={recorded_input: "NO_CHANGE"},
        strict_replay=True,
        semantic_memory_replay=True,
    )

    with pytest.raises(KeyError, match="exact Worker replay miss"):
        await worker.complete(current_input)


@pytest.mark.asyncio
async def test_worker_semantic_cache_reuses_completed_isolated_evidence(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    provider_calls = []
    first_refs = tuple(f"episode_{letter * 64}" for letter in "abc")
    second_refs = tuple(f"episode_{letter * 64}" for letter in "def")

    class Model:
        async def complete(self, **kwargs):
            provider_calls.append(kwargs["input_text"])
            return f"TARGET\nNEW_DISPOSITION\nBASIS\n{first_refs[2]}"

    worker = RecordingWorkerModel(
        Model(),
        tmp_path / "windows.jsonl",
        semantic_memory_replay=True,
    )
    first = _isolated_worker_window("owner-a", first_refs)
    second = _isolated_worker_window(
        "owner-b", second_refs, related_order=(2, 1)
    )

    assert await worker.complete(first) == (
        f"TARGET\nNEW_DISPOSITION\nBASIS\n{first_refs[2]}"
    )
    assert await worker.complete(second) == (
        f"TARGET\nNEW_DISPOSITION\nBASIS\n{second_refs[2]}"
    )
    assert provider_calls == [first]


@pytest.mark.asyncio
async def test_worker_semantic_cache_alpha_renames_formation_outcomes(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    provider_calls = []
    first_episodes = tuple(f"episode_{letter * 64}" for letter in "ab")
    second_episodes = tuple(f"episode_{letter * 64}" for letter in "cd")
    first_outcomes = tuple(f"outcome_{letter * 32}" for letter in "ef")
    second_outcomes = tuple(f"outcome_{letter * 32}" for letter in "12")

    def request(namespace, episodes, outcomes):
        def episode(ref, session, source, situation, agent_act):
            return (
                f"EPISODE {ref}\n"
                f"SESSION {namespace}:session:{session}\n"
                "AGENT_ACT\n"
                f"ACTOR agent agent:{namespace}\n"
                f"SOURCE {namespace}:agent:{source}\n"
                f"{agent_act}\n"
                "SITUATION\n"
                f"ACTOR user user:{namespace}\n"
                f"SOURCE {namespace}:user:{source}\n"
                f"{situation}"
            )

        allowed = "\n".join(
            f"ALLOWED_BASIS\n{ref}" for ref in episodes + outcomes
        )
        return (
            "ALLOWED_TARGETS_BEGIN\n"
            "ALLOWED_TARGET\nNEW_DISPOSITION\n"
            "ALLOWED_TARGETS_END\n"
            "ALLOWED_BASIS_BEGIN\n"
            f"{allowed}\n"
            "ALLOWED_BASIS_END\n"
            "WINDOW_BEGIN\n"
            + episode(
                episodes[0],
                0,
                0,
                "The user has too many equally urgent tasks and freezes.",
                "The Agent asks for the single most consequential blocker.",
            )
            + "\n\n"
            + episode(
                episodes[1],
                1,
                1,
                "The user has several competing deadlines and cannot choose.",
                "The Agent asks which missed deadline has the largest consequence.",
            )
            + "\n\nELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\n"
            "APPLICATION RELATION\n\n"
            f"OUTCOME {outcomes[0]}\n"
            f"OUTCOME_EPISODE {episodes[0]}\n"
            f"ACTOR user user:{namespace}\n"
            "That question helped me choose.\n\n"
            f"OUTCOME {outcomes[1]}\n"
            f"OUTCOME_EPISODE {episodes[1]}\n"
            f"ACTOR user user:{namespace}\n"
            "That made the next step clear.\n\n"
            "WINDOW_END"
        )

    class Model:
        async def complete(self, **kwargs):
            provider_calls.append(kwargs["input_text"])
            return (
                "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
                "When the user freezes among equally urgent tasks, the Agent asks for "
                "the single most consequential blocker.\nBASIS\n"
                + "\n".join(first_episodes + first_outcomes)
            )

    worker = RecordingWorkerModel(
        Model(),
        tmp_path / "windows.jsonl",
        semantic_memory_replay=True,
    )
    first = request("owner-a", first_episodes, first_outcomes)
    second = request("owner-b", second_episodes, second_outcomes)

    assert await worker.complete(first) == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user freezes among equally urgent tasks, the Agent asks for the "
        "single most consequential blocker.\nBASIS\n"
        + "\n".join(first_episodes + first_outcomes)
    )
    assert await worker.complete(second) == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user freezes among equally urgent tasks, the Agent asks for the "
        "single most consequential blocker.\nBASIS\n"
        + "\n".join(second_episodes + second_outcomes)
    )
    assert provider_calls == [first]


@pytest.mark.asyncio
async def test_worker_replay_treats_memory_candidate_order_as_nonsemantic(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("candidate-set replay called the provider")

    old_first = f"recollection_{'a' * 32}@1"
    old_second = f"recollection_{'b' * 32}@1"
    new_first = f"recollection_{'c' * 32}@1"
    new_second = f"recollection_{'d' * 32}@1"

    def request(first_ref, first_text, second_ref, second_text):
        return (
            "ALLOWED_TARGETS_BEGIN\n"
            f"ALLOWED_TARGET\n{first_ref}\n"
            f"ALLOWED_TARGET\n{second_ref}\n"
            "ALLOWED_TARGETS_END\n"
            "WINDOW_BEGIN\n\n"
            f"ELIGIBLE_RECOLLECTION {first_ref}\n"
            f"APPLICATION OTHER\nTEXT {first_text}\n\n"
            f"ELIGIBLE_RECOLLECTION {second_ref}\n"
            f"APPLICATION OTHER\nTEXT {second_text}\n\n"
            "WINDOW_END"
        )

    recorded_input = request(old_first, "Alpha memory", old_second, "Beta memory")
    current_input = request(new_second, "Beta memory", new_first, "Alpha memory")
    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={
            recorded_input: f"TARGET\n{old_first}\nCHANGE\nKEEP\nBASIS\nepisode_1"
        },
        strict_replay=True,
        semantic_memory_replay=True,
    )

    assert await worker.complete(current_input) == (
        f"TARGET\n{new_first}\nCHANGE\nKEEP\nBASIS\nepisode_1"
    )
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["replay_match"] == "semantic_memory_state"


@pytest.mark.asyncio
async def test_worker_replay_ignores_fallback_recollection_candidate_order(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("fallback candidate replay called the provider")

    prefix = "EXISTING_RECOLLECTIONS_BEGIN\n"
    suffix = "\nEXISTING_RECOLLECTIONS_END\nCURRENT_USER_SOURCES_BEGIN\n> same"
    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={prefix + "> Alpha\n> Beta" + suffix: "NO_MEMORY"},
        strict_replay=True,
        semantic_memory_replay=True,
    )

    assert await worker.complete(prefix + "> Beta\n> Alpha" + suffix) == "NO_MEMORY"
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["replay_match"] == "semantic_memory_state"


def test_worker_cli_enables_semantic_memory_state_replay(tmp_path, monkeypatch):
    import memory_core_worker.server as worker_server
    from memory_core_eval import personamem_live

    recorded_ref = f"seed_{'a' * 32}@1"
    current_ref = f"seed_{'b' * 32}@1"
    recorded_input = f"ALLOWED_TARGET\n{recorded_ref}"
    current_input = f"ALLOWED_TARGET\n{current_ref}"
    replay_path = tmp_path / "replay.jsonl"
    replay_path.write_text(
        json.dumps(
            {
                "input": recorded_input,
                "output": f"TARGET\n{recorded_ref}\nCHANGE\nREENACT",
            }
        )
        + "\n"
    )
    observed = {}

    class ForbiddenProvider:
        def __init__(self, *, usage_file, **_kwargs):
            usage_file.parent.mkdir(parents=True, exist_ok=True)

        async def complete(self, **_kwargs):
            raise AssertionError("CLI opaque-ref replay called the provider")

        async def close(self):
            pass

    async def serve_once(model, listen, *, max_model_input_bytes):
        observed["listen"] = listen
        observed["max_model_input_bytes"] = max_model_input_bytes
        observed["output"] = await model.complete(current_input)

    monkeypatch.setattr(personamem_live, "ChatTextModel", ForbiddenProvider)
    monkeypatch.setattr(worker_server, "serve", serve_once)
    monkeypatch.setenv("MEMORY_WORKER_OPENAI_MODEL", "unused-model")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    personamem_live.worker_main(
        [
            "--listen",
            "127.0.0.1:19082",
            "--output",
            str(tmp_path / "output"),
            "--replay-windows",
            str(replay_path),
            "--strict-replay",
            "--replay-semantic-memory-state",
        ]
    )

    assert observed == {
        "listen": "127.0.0.1:19082",
        "max_model_input_bytes": 256 * 1024,
        "output": f"TARGET\n{current_ref}\nCHANGE\nREENACT",
    }


@pytest.mark.asyncio
async def test_worker_strict_replay_rejects_unknown_input_without_provider(tmp_path):
    from memory_core_eval.personamem_live import RecordingWorkerModel

    class ForbiddenModel:
        async def complete(self, **_kwargs):
            raise AssertionError("strict replay called the provider")

    worker = RecordingWorkerModel(
        ForbiddenModel(),
        tmp_path / "windows.jsonl",
        replay={},
        strict_replay=True,
    )
    with pytest.raises(KeyError, match="exact Worker replay miss"):
        await worker.complete("new input")
    result = json.loads((tmp_path / "windows.jsonl").read_text())
    assert result["error_type"] == "KeyError"
    assert result["replay_hit"] is False


def test_load_worker_replay_rejects_conflicting_exact_inputs(tmp_path):
    from memory_core_eval.personamem_live import load_worker_replay

    path = tmp_path / "source.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"input": "same", "output": "first"}),
                json.dumps({"input": "same", "output": "second"}),
                json.dumps({"input": "failed", "error_type": "TimeoutError"}),
            ]
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="conflicting Worker replay"):
        load_worker_replay([path])


class RpcFailure(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "observe_source_event",
        "select_memory",
        "record_memory_delivery",
        "report_outcome",
    ],
)
async def test_eval_memory_client_retries_aborted_rpc_with_same_request(
    monkeypatch,
    method,
):
    from memory_core_eval.personamem_live import RetryingMemoryClient

    class Delegate:
        def __init__(self):
            self.calls = []

        async def invoke(self, request, **options):
            self.calls.append((request, options))
            if len(self.calls) == 1:
                raise RpcFailure(grpc.StatusCode.ABORTED)
            return "receipt"

        observe_source_event = invoke
        select_memory = invoke
        record_memory_delivery = invoke
        report_outcome = invoke

    delays = []

    async def no_wait(delay):
        delays.append(delay)

    monkeypatch.setattr("memory_core_eval.personamem_live.asyncio.sleep", no_wait)
    delegate = Delegate()
    memory = RetryingMemoryClient(delegate, attempts=3)
    request = object()

    assert await getattr(memory, method)(request, timeout=12) == "receipt"
    assert delegate.calls == [
        (request, {"timeout": 12}),
        (request, {"timeout": 12}),
    ]
    assert delays == [0.05]


@pytest.mark.asyncio
async def test_eval_memory_client_does_not_retry_non_aborted_rpc(monkeypatch):
    from memory_core_eval.personamem_live import RetryingMemoryClient

    class Delegate:
        calls = 0

        async def observe_source_event(self, _request, **_options):
            self.calls += 1
            raise RpcFailure(grpc.StatusCode.INVALID_ARGUMENT)

    async def fail_sleep(_delay):
        raise AssertionError("non-retryable error slept")

    monkeypatch.setattr("memory_core_eval.personamem_live.asyncio.sleep", fail_sleep)
    delegate = Delegate()
    with pytest.raises(RpcFailure):
        await RetryingMemoryClient(delegate, attempts=3).observe_source_event(object())
    assert delegate.calls == 1


@pytest.mark.parametrize("attempts", [True, 0, -1, 1.5])
def test_eval_memory_client_rejects_invalid_attempt_count(attempts):
    from memory_core_eval.personamem_live import RetryingMemoryClient

    with pytest.raises(ValueError, match="positive integer"):
        RetryingMemoryClient(object(), attempts=attempts)


def test_database_probe_is_scoped_and_reports_actual_pending_work():
    from memory_core_eval.personamem_live import DatabaseProbe
    from memory_core import memory_pb2 as pb

    calls = []

    def read(sql, params):
        calls.append((sql, params))
        if "completed_at IS NOT NULL" in sql:
            return [
                {"episode_refs": '["episode-1", "episode-2"]'},
                {"episode_refs": ["episode-2", "episode-3"]},
            ]
        if "consolidation_jobs" in sql:
            return [{"jobs": 4, "pending_jobs": 1}]
        return [{"episodes": 94}]

    probe = DatabaseProbe("mysql://user:password@localhost/eval", read=read)
    result = probe.snapshot(pb.MemoryScope(tenant_ref="t", agent_ref="a", relationship_ref="r"))
    assert result == {
        "episodes": 94,
        "consolidated_episodes": 3,
        "jobs": 4,
        "pending_jobs": 1,
    }
    assert all(params == ("t", "a", "r") for _, params in calls)
    assert all("tenant_ref" in sql and "agent_ref" in sql and "relationship_ref" in sql for sql, _ in calls)
    assert all(sql.lstrip().startswith("SELECT") for sql, _ in calls)


def test_database_probe_reports_scoped_pending_index_operations():
    from memory_core_eval.personamem_live import DatabaseProbe
    from memory_core import memory_pb2 as pb

    calls = []

    def read(sql, params):
        calls.append((sql, params))
        return [{"pending_index_operations": 3}]

    probe = DatabaseProbe("mysql://user:password@localhost/eval", read=read)
    scope = pb.MemoryScope(tenant_ref="t", agent_ref="a", relationship_ref="r")
    assert probe.pending_index_operations(scope) == 3
    assert len(calls) == 1
    sql, params = calls[0]
    assert "memory_index_operations" in sql and "acknowledged_at IS NULL" in sql
    assert params == ("t", "a", "r")


def test_database_probe_rejects_non_database_urls():
    from memory_core_eval.personamem_live import DatabaseProbe

    with pytest.raises(ValueError):
        DatabaseProbe("https://example.test/not-a-database")


def test_semantic_runner_explicit_resource_envelope_preserves_compatibility_defaults():
    from memory_core_eval.adaptive_semantics import parse_args

    required = ["--fixtures", "cases.py", "--output", "new-run"]
    default = parse_args(required)
    assert (default.max_output_tokens, default.request_timeout, default.rpc_timeout) == (8192, 110, 180)
    explicit = parse_args(required + ["--thinking", "--max-output-tokens", "32768", "--request-timeout", "300", "--rpc-timeout", "330"])
    assert (explicit.max_output_tokens, explicit.request_timeout, explicit.rpc_timeout) == (32768, 300, 330)
    assert explicit.thinking


@pytest.mark.parametrize(("option", "value"), [
    ("--max-output-tokens", "0"), ("--max-output-tokens", "-1"), ("--max-output-tokens", "1.5"),
    ("--request-timeout", "0"), ("--request-timeout", "-1"), ("--request-timeout", "nan"),
    ("--rpc-timeout", "0"), ("--rpc-timeout", "-1"), ("--rpc-timeout", "inf"),
])
def test_semantic_runner_rejects_invalid_resource_envelope_before_call(option, value):
    from memory_core_eval.adaptive_semantics import parse_args

    with pytest.raises(SystemExit) as error:
        parse_args(["--fixtures", "cases.py", "--output", "new-run", option, value])
    assert error.value.code == 2
