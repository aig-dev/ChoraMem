"""Reject incomplete experiments before deriving a behavioral conclusion."""
from __future__ import annotations

import copy
import importlib
import importlib.util
from pathlib import Path

import pytest

from memory_core_eval.memory_effect_data import load_memory_effect


DATA = load_memory_effect(Path(__file__).parents[3] / "eval/cases/memory-effect-causal-v1.json")
RUNTIME = {"model": "configured-model", "model_revision": "alias, build unavailable", "base_url": "https://example.test", "temperature": 0, "thinking": "disabled", "provider_max_retries": 0, "provider_completion_attempts": 1, "provider_timeout_seconds": 35, "worker_rpc_timeout_seconds": 120, "tokenizer": "cl100k_base", "worker_output_tokens": 8192, "answer_output_tokens": 1024, "judge_output_tokens": 512, "memory_tokens": 768, "foundation_tokens": 544, "tendency_tokens": 192, "episode_evidence_bytes": 8192}
SOURCES = {"eval/harness.py": "a" * 64}


def _api():
    assert importlib.util.find_spec("memory_core_eval.memory_effect_cli") is not None, "missing live causal gate"
    return importlib.import_module("memory_core_eval.memory_effect_cli")


def _manifest():
    return _api().make_effect_manifest(DATA, evaluation_ref="causal-test", runtime=RUNTIME, source_sha256=SOURCES, core_binary_sha256="b" * 64, provider={"revision_ref": "frozen-index"})


@pytest.mark.parametrize("mutate", [
    lambda m: m.pop("runtime"),
    lambda m: m.update(data_sha256="c" * 64),
    lambda m: m["runtime"].update(memory_tokens=2048),
    lambda m: m["runtime"].update(model="another-model"),
    lambda m: m["runtime"].update(provider_max_retries=2),
    lambda m: m["source_sha256"].update({"eval/harness.py": "d" * 64}),
])
def test_manifest_rejects_partial_or_changed_frozen_experiment(mutate):
    manifest = _manifest()
    mutate(manifest)
    with pytest.raises(ValueError, match="manifest|drift|budget"):
        _api().validate_effect_manifest(manifest, DATA, runtime=RUNTIME, source_sha256=SOURCES)


def test_manifest_keeps_every_case_and_source_without_credentials():
    manifest = _manifest()
    _api().validate_effect_manifest(manifest, DATA, runtime=RUNTIME, source_sha256=SOURCES)
    assert set(manifest["case_refs"]) == {f"r{i}" for i in range(1, 9)} | {f"n{i}" for i in range(1, 5)} | {f"f{i}" for i in range(1, 10)} | {f"g{i}" for i in range(1, 9)}
    assert "api_key" not in str(manifest).lower()


@pytest.mark.asyncio
async def test_capture_preserves_provider_failure_in_complete_attempt_inventory(tmp_path):
    api = _api()

    class Model:
        async def complete(self, **_kwargs):
            raise RuntimeError("private provider diagnostic")

    model = api.CapturedEffectModel(Model(), tmp_path)
    with pytest.raises(RuntimeError):
        await model.complete(instructions="role", input_text="source", max_output_tokens=512)
    starts = api.read_jsonl(tmp_path / "model-started.jsonl")
    failed = api.read_jsonl(tmp_path / "model-failed.jsonl")
    assert len(starts) == len(failed) == 1
    assert failed[0]["error_class"] == "RuntimeError"
    assert "private provider diagnostic" not in (tmp_path / "model-failed.jsonl").read_text()
    assert api.validate_model_inventory(starts, [], failed) == {"started": 1, "completed": 0, "failed": 1}
    with pytest.raises(ValueError, match="inventory"):
        api.validate_model_inventory(starts, [], [])


@pytest.mark.asyncio
async def test_capture_binds_actual_output_to_started_request(tmp_path):
    api = _api()

    class Model:
        async def complete(self, **_kwargs):
            return "completed provider response"

    model = api.CapturedEffectModel(Model(), tmp_path)
    await model.complete(instructions="role", input_text="source", max_output_tokens=512)
    starts = api.read_jsonl(tmp_path / "model-started.jsonl")
    calls = api.read_jsonl(tmp_path / "model-completed.jsonl")
    assert api.validate_model_inventory(starts, calls, []) == {"started": 1, "completed": 1, "failed": 0}
    calls[0]["output"] = "changed output"
    with pytest.raises(ValueError, match="output"):
        api.validate_model_inventory(starts, calls, [])


def _pairs(api, winners):
    records = []
    for left, right in api.COMPARISONS:
        winner = winners.get((left, right), "tie")
        for direction, a, b in (("forward", left, right), ("reverse", right, left)):
            token = "TIE" if winner == "tie" else "A" if winner == a else "B"
            records.append({"left": left, "right": right, "direction": direction, "a_mode": a, "b_mode": b, "token": token})
    return records


def test_all_six_pairs_need_both_positions_and_inconsistency_is_not_majority():
    api = _api()
    pairs = _pairs(api, {("base", "oracle"): "oracle"})
    assert api.validate_pairwise(pairs)["base:oracle"] == "oracle"
    pairs[-1]["token"] = "A"
    assert api.validate_pairwise(pairs)["learned:oracle"] == "inconsistent"
    with pytest.raises(ValueError, match="position|pair"):
        api.validate_pairwise(pairs[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        api.validate_pairwise(pairs + [pairs[0]])


@pytest.mark.parametrize("wins,losses,anti_wins,anti_losses,identified", [
    (6, 0, 6, 0, True), (5, 0, 8, 0, False), (7, 1, 8, 0, False), (8, 0, 5, 0, False), (8, 0, 7, 1, False),
])
def test_oracle_gate_requires_preregistered_position_consistent_contrast(wins, losses, anti_wins, anti_losses, identified):
    api = _api()
    base = ["oracle"] * wins + ["base"] * losses + ["tie"] * (8 - wins - losses)
    anti = ["oracle"] * anti_wins + ["anti"] * anti_losses + ["inconsistent"] * (8 - anti_wins - anti_losses)
    result = api.oracle_gate(base, anti)
    assert result["identified"] is identified
    assert result["oracle_vs_base"]["denominator"] == 8
    if wins == 6 and losses == 0:
        assert result["oracle_vs_base"]["one_sided_sign_probability"] == 0.015625


def test_case_inventory_cannot_drop_negative_or_semantic_controls():
    api = _api()
    records = [{"case_ref": ref} for ref in _manifest()["case_refs"]]
    api.validate_result_set(DATA, records)
    with pytest.raises(ValueError, match="case|incomplete"):
        api.validate_result_set(DATA, [r for r in records if r["case_ref"] != "n2"])
    with pytest.raises(ValueError, match="duplicate"):
        api.validate_result_set(DATA, records + [records[0]])


def test_semantic_review_must_bind_actual_record_and_each_frozen_meaning():
    api = _api()
    records = [{"case_ref": case.case_ref if hasattr(case, "case_ref") else case.instance_ref, "after": {"recollections": [], "dispositions": []}} for case in DATA.feedback_scenarios + DATA.semantic_scenarios]
    reviews = [api.semantic_review_template(DATA, record) for record in records]
    for review in reviews:
        for check in review["checks"].values():
            check.update(passed=True, note="Inspected exact committed state in this fixture.")
    api.validate_semantic_reviews(DATA, records, reviews)
    incomplete = copy.deepcopy(reviews)
    incomplete[0]["checks"].pop("meaning")
    with pytest.raises(ValueError, match="expectation"):
        api.validate_semantic_reviews(DATA, records, incomplete)
    records[0]["after"]["dispositions"].append({"ref": "seed@2", "text": "different committed text"})
    with pytest.raises(ValueError, match="stale|record"):
        api.validate_semantic_reviews(DATA, records, reviews)


@pytest.mark.asyncio
async def test_judging_consumes_four_durable_outputs_and_hides_arm_names(tmp_path):
    """Missing arms or identifiable memory labels invalidate blind comparison."""
    api = _api()
    assert hasattr(api, "judge_effect_pairs"), "missing actual blind pair orchestration"
    inputs = []

    class Model:
        async def complete(self, *, instructions, input_text, max_output_tokens):
            assert max_output_tokens == 512
            assert "seed@" not in input_text and "oracle" not in input_text and "learned" not in input_text
            assert DATA.expectations["r1"]["rubric"] in input_text
            inputs.append(input_text)
            return "TIE"

    model = api.CapturedEffectModel(Model(), tmp_path)
    answers = {"none": "不带材料的答复", "base": "基础材料答复", "learned": "学得倾向答复", "oracle": "理想倾向答复"}
    pairs, manipulation = await api.judge_effect_pairs(instance=DATA.instances[0], rubric=DATA.expectations["r1"]["rubric"], answers=answers, anti_output="反向倾向答复", model=model)
    assert len(inputs) == 14 and len(pairs) == 12 and len(manipulation) == 2
    assert set(api.validate_pairwise(pairs).values()) == {"tie"}
    with pytest.raises(ValueError, match="four"):
        await api.judge_effect_pairs(instance=DATA.instances[0], rubric="rubric", answers={k: v for k, v in answers.items() if k != "none"}, anti_output="anti", model=model)
    assert len(inputs) == 14  # Never judge a partial generation.


@pytest.mark.asyncio
async def test_followup_delivery_precedes_actual_answer_and_source_binding(tmp_path):
    api = _api()
    assert hasattr(api, "run_effect_probe"), "missing actual correction response probe"
    from test_memory_effect_runner import MemoryBoundary
    memory = MemoryBoundary()

    class Model:
        async def complete(self, **_kwargs):
            assert memory.events[-1] == "delivery"
            return "这是实际模型生成的后续回复"

    model = api.CapturedEffectModel(Model(), tmp_path)
    result = await api.run_effect_probe(instance=DATA.instances[0], request="又被一堆任务堵住了", evaluation_ref="followup-test", suffix="after-correction", memory=memory, model=model, token_count=len)
    assert result["agent_act_origin"] == "model_completion"
    assert result["output"] == result["agent_act"]["text"] == "这是实际模型生成的后续回复"
    assert result["answer_attempt_ref"] == model.last_call["attempt_ref"]
    assert result["delivery"]["delivered_memory_refs"] == result["rendered_memory_refs"]
    assert len({r.scope.session_ref for r in memory.selected}) == 1
    assert memory.observed[-1].source_event.text == result["output"]


def test_worker_rpc_inventory_rejects_hidden_attempts_and_unbound_model_calls():
    api = _api()
    assert hasattr(api, "validate_rpc_inventory"), "missing actual Worker inventory gate"
    start = {"rpc_ref": "rpc:1", "job_ref": "job:1", "request_proto_sha256": "a" * 64}
    done = {**start, "tagged_text": "NO_CHANGE", "status": "completed"}
    assert api.validate_rpc_inventory([start], [done], [], [{"rpc_ref": "rpc:1"}]) == {"started": 1, "completed": 1, "failed": 0}
    with pytest.raises(ValueError, match="RPC"):
        api.validate_rpc_inventory([start], [], [], [])
    with pytest.raises(ValueError, match="RPC"):
        api.validate_rpc_inventory([start], [done], [], [{"rpc_ref": "unknown"}])


@pytest.mark.asyncio
async def test_two_real_messages_in_one_session_keep_distinct_source_and_run(tmp_path):
    """A delayed probe cannot overwrite the frozen source of the earlier run."""
    api = _api()
    from test_memory_effect_runner import MemoryBoundary

    class FrozenSourceBoundary(MemoryBoundary):
        def __init__(self):
            super().__init__()
            self.sources = {}

        async def observe_source_event(self, request):
            key = (request.source_event.scope.SerializeToString(), request.source_event.source_ref)
            content = request.source_event.SerializeToString()
            if key in self.sources and self.sources[key] != content:
                raise ValueError("physical source snapshot conflict")
            self.sources[key] = content
            return await super().observe_source_event(request)

    class Model:
        async def complete(self, **_kwargs): return "实际回复"

    memory = FrozenSourceBoundary()
    model = api.CapturedEffectModel(Model(), tmp_path)
    scope = api.effect_scope("late-test", "f8", "one-conversation")
    first = await api.run_effect_probe(instance=DATA.instances[0], request="第一条真实消息", evaluation_ref="late-test", suffix="first", memory=memory, model=model, token_count=len, scope=scope)
    second = await api.run_effect_probe(instance=DATA.instances[0], request="之后的另一条真实消息", evaluation_ref="late-test", suffix="later", memory=memory, model=model, token_count=len, scope=scope)
    assert first["scope"] == second["scope"]
    assert first["run_ref"] != second["run_ref"]
    assert first["situation_source_event_ref"] != second["situation_source_event_ref"]
