"""Independent grader calibration and unchanged-budget capacity diagnosis."""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
from pathlib import Path

import pytest


DATA = Path(__file__).parents[3] / "eval/cases/memory-effect-diagnostic-v1.json"


def _api():
    assert importlib.util.find_spec("memory_core_eval.memory_effect_diagnostic") is not None, "missing independent calibrated diagnostic"
    return importlib.import_module("memory_core_eval.memory_effect_diagnostic")


def test_diagnostic_has_separate_heldout_replay_and_frozen_calibration():
    data = _api().load_diagnostic(DATA)
    assert {i.instance_ref for i in data.instances} == {f"d{i}" for i in range(1, 9)}
    assert {i.instance_ref for i in data.negative_controls} == {f"dn{i}" for i in range(1, 5)}
    assert {c.case_ref for c in data.calibration} == {f"k{i}" for i in range(1, 9)}
    assert all(len(i.history_sessions) == 6 for i in data.instances)


def test_long_capacity_cohort_is_independent_and_keeps_the_frozen_protocol():
    api = _api()
    original = api.load_diagnostic(DATA)
    path = DATA.with_name("memory-effect-capacity-v2.json")
    capacity = api.load_diagnostic(path)
    assert capacity.source_sha256 != original.source_sha256
    assert len(capacity.instances) == 8 and len(capacity.negative_controls) == 4
    assert capacity.calibration == original.calibration
    assert not {i.current_request for i in capacity.instances} & {i.current_request for i in original.instances}
    for instance in capacity.instances:
        assert len(instance.history_sessions) == 6
        for session in instance.history_sessions:
            text = "\n".join((session.situation, session.agent_act, session.outcome))
            assert len(session.situation) > 600
            assert len(text.encode("utf-8")) < 8192


@pytest.mark.parametrize("mutation", [
    lambda d: d["trajectories"].pop(),
    lambda d: d["negative_controls"].pop(),
    lambda d: d["calibration"].pop(),
    lambda d: d["trajectories"][0]["history_sessions"][1].update(session_ref="s1"),
    lambda d: d["trajectories"][0]["history_sessions"][3].update(session_ref="s1"),
    lambda d: d["calibration"][0].update(expected_left=[1]),
    lambda d: d["oracle_gate"].update(wins_minimum=5),
    lambda d: d["grader"].update(output_tokens=1024),
])
def test_diagnostic_rejects_partial_denominators_and_qualification_drift(tmp_path, mutation):
    raw = json.loads(DATA.read_text())
    mutation(raw)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        _api().load_diagnostic(path)


def test_diagnostic_replay_hash_does_not_depend_on_hidden_scoring_text(tmp_path):
    api = _api()
    original = api.load_diagnostic(DATA)
    raw = json.loads(DATA.read_text())
    raw["trajectories"][0]["expectation"]["checks"][0] += "评分专用补充"
    path = tmp_path / "scorer.json"
    path.write_text(json.dumps(raw))
    changed = api.load_diagnostic(path)
    assert api.diagnostic_input_sha256(original.instances[0]) == api.diagnostic_input_sha256(changed.instances[0])
    assert original.source_sha256 != changed.source_sha256


def test_calibration_expected_labels_are_absent_from_judge_input():
    api = _api()
    case = api.load_diagnostic(DATA).calibration[0]
    instructions, prompt = api.build_diagnostic_pair_prompt(current_request=case.current_request, checks=case.checks, response_a=case.left, response_b=case.right)
    assert "expected_left" not in instructions + prompt
    assert "expected_right" not in instructions + prompt
    assert json.loads(prompt)["checks"] == list(case.checks)


@pytest.mark.parametrize("output", [
    '{"a":[true],"b":[true],"winner":"A"}',
    '{"a":[1],"b":[false]}',
    '{"a":["true"],"b":[false]}',
    '{"a":[true,true],"b":[false]}',
    '{"a":[true]}',
    '```json\n{"a":[true],"b":[false]}\n```\nadditional text',
])
def test_pair_parser_rejects_label_guessing_nonboolean_and_partial_vectors(output):
    with pytest.raises(ValueError):
        _api().parse_diagnostic_pair(output, check_count=1)


@pytest.mark.parametrize("output,expected", [
    ('```json\n{\n  "a": [false, false],\n  "b": [false, false]\n}\n```', ((False, False), (False, False))),
    ('```json\n{\n  "a": [false, false],\n  "b": [true, true]\n}\n```', ((False, False), (True, True))),
    ('```json\n{\n  "a": [true, true],\n  "b": [false, false]\n}\n```', ((True, True), (False, False))),
])
def test_pair_accepts_a_single_complete_json_transport_fence(output, expected):
    """Exact returned k3/k4/k5 envelopes; no change to semantic flags."""
    assert _api().parse_diagnostic_pair(output, check_count=2) == expected


@pytest.mark.parametrize("output", [
    'explanation\n```json\n{"a":[true],"b":[false]}\n```',
    '```json\n{"a":[true],"b":[false]}\n```\n```json\n{}\n```',
    '```json\n{"a":[true],"b":[false],"winner":"A"}\n```',
    '```json\n{"a":[1],"b":[false]}\n```',
])
def test_json_envelope_cannot_salvage_extra_text_blocks_or_invalid_payload(output):
    with pytest.raises(ValueError):
        _api().parse_diagnostic_pair(output, check_count=1)


def test_pair_scores_exact_criteria_and_gives_honest_equivalence_ties():
    api = _api()
    assert api.diagnostic_verdict((True, True), (True, True)) == "TIE"
    assert api.diagnostic_verdict((False, True), (True, False)) == "TIE"
    assert api.diagnostic_verdict((True, True), (True, False)) == "A"
    assert api.diagnostic_verdict((False, True), (True, True)) == "B"


def test_pair_consensus_rejects_position_disagreement_even_if_winner_matches():
    api = _api()
    forward = ((True, True), (False, True))
    reverse = ((False, True), (True, True))
    assert api.diagnostic_consensus(forward, reverse, left="oracle", right="base") == "oracle"
    assert api.diagnostic_consensus(forward, ((True, False), (True, True)), left="oracle", right="base") == "inconsistent"


def test_all_calibration_cases_and_both_positions_are_required():
    api = _api()
    data = api.load_diagnostic(DATA)
    rows = [dict(case_ref=c.case_ref, direction=direction, a=list(c.expected_left if direction == "forward" else c.expected_right), b=list(c.expected_right if direction == "forward" else c.expected_left)) for c in data.calibration for direction in ("forward", "reverse")]
    assert api.validate_calibration(data, rows)["identified"] is True
    with pytest.raises(ValueError, match="calibration"):
        api.validate_calibration(data, rows[:-1])
    wrong = copy.deepcopy(rows)
    wrong[0]["a"][0] = not wrong[0]["a"][0]
    assert api.validate_calibration(data, wrong)["identified"] is False


def test_diagnostic_answer_positions_remain_balanced():
    from memory_core_eval.memory_effect_runner import ARMS, effect_arm_order
    orders = [effect_arm_order(f"d{i}") for i in range(1, 9)]
    for index in range(4):
        assert {arm: sum(order[index] == arm for order in orders) for arm in ARMS} == {arm: 2 for arm in ARMS}


@pytest.mark.asyncio
async def test_failed_known_calibration_stops_before_capacity_memory_writes(tmp_path):
    api = _api()
    from memory_core_eval.memory_effect_cli import CapturedEffectModel, read_jsonl, validate_model_inventory

    class WrongJudge:
        async def complete(self, *, instructions, input_text, **_kwargs):
            if input_text.startswith("{"):
                count = len(json.loads(input_text)["checks"])
                return json.dumps({"a": [False] * count, "b": [False] * count})
            return "A"

    class ForbiddenMemory:
        def __getattr__(self, name):
            raise AssertionError("failed calibration must not reach public memory writes: " + name)

    result = await api.run_public_diagnostic(data=api.load_diagnostic(DATA), evaluation_ref="diagnostic-test", memory=ForbiddenMemory(), probe=None, model=CapturedEffectModel(WrongJudge(), tmp_path), token_count=len, output=tmp_path)
    assert result["status"] == "calibration_failed"
    assert result["calibration"]["denominator"] == 16
    assert result["calibration"]["identified"] is False
    inventory = validate_model_inventory(read_jsonl(tmp_path / "model-started.jsonl"), read_jsonl(tmp_path / "model-completed.jsonl"), read_jsonl(tmp_path / "model-failed.jsonl"))
    assert inventory["started"] == inventory["completed"] == 32


@pytest.mark.parametrize("mutation", [
    lambda m: m["runtime"].update(memory_tokens=2048),
    lambda m: m["oracle_gate"].update(wins_minimum=5),
    lambda m: m["case_refs"].pop(),
    lambda m: m["calibration_refs"].pop(),
    lambda m: m.update(data_sha256="d" * 64),
    lambda m: m.update(grader_output_transport="any JSON substring"),
    lambda m: m.pop("grader_response_format", None),
])
def test_independent_manifest_cannot_silently_change_budget_gate_or_denominator(mutation):
    api = _api()
    from memory_core_eval.memory_effect_cli import _BUDGETS
    data = api.load_diagnostic(DATA)
    runtime = _BUDGETS | {"model": "configured-model", "model_revision": "alias", "base_url": "https://example.test"}
    sources = {"eval/module.py": "a" * 64}
    manifest = api.make_diagnostic_manifest(data, evaluation_ref="diagnostic-test", runtime=runtime, source_sha256=sources, core_binary_sha256="b" * 64, provider={"revision_ref": "fixed-index"})
    mutation(manifest)
    with pytest.raises(ValueError):
        api.validate_diagnostic_manifest(manifest, data, runtime=runtime, source_sha256=sources)


@pytest.mark.parametrize("mutation", [
    lambda rows, calls: rows.pop(),
    lambda rows, calls: rows[0].update(a_mode="invented"),
    lambda rows, calls: rows[0].update(response_a_sha256="c" * 64),
    lambda rows, calls: rows[0].update(a=[False, False, False]),
    lambda rows, calls: calls[rows[0]["attempt_ref"]].update(input_text="substituted"),
])
def test_judgments_bind_both_positions_and_flags_to_actual_completed_calls(mutation):
    api = _api()
    from memory_core_eval.memory_effect_cli import COMPARISONS
    from memory_core_eval.memory_effect_runner import effect_request_sha256, sha256_text
    instance = api.load_diagnostic(DATA).instances[0]
    checks = ("check one", "check two", "check three")
    responses = {mode: mode + " output" for mode in ("none", "base", "learned", "oracle", "anti")}
    rows, calls = [], {}
    for left, right in COMPARISONS + (("oracle", "anti"),):
        for direction in ("forward", "reverse"):
            a, b = (left, right) if direction == "forward" else (right, left)
            instructions, prompt = api.build_diagnostic_pair_prompt(current_request=instance.current_request, checks=checks, response_a=responses[a], response_b=responses[b])
            attempt = f"{left}:{right}:{direction}"
            request = effect_request_sha256(instructions, prompt, 512, json_output=True)
            calls[attempt] = dict(instructions=instructions, input_text=prompt, max_output_tokens=512, json_output=True, request_sha256=request, output='{"a":[true,true,true],"b":[true,true,true]}')
            rows.append(dict(left=left, right=right, direction=direction, a_mode=a, b_mode=b, a=[True]*3, b=[True]*3, attempt_ref=attempt, request_sha256=request, response_a_sha256=sha256_text(responses[a]), response_b_sha256=sha256_text(responses[b])))
    mutation(rows, calls)
    with pytest.raises(ValueError):
        api.validate_diagnostic_judgments(instance, checks, responses, rows, calls)


def _calibration_bundle():
    api = _api()
    from memory_core_eval.delayed_seed_effect import build_pairwise_prompt
    from memory_core_eval.memory_effect_runner import effect_request_sha256, sha256_text
    data = api.load_diagnostic(DATA)
    legacy, rows, calls = [], [], {}
    for case in data.calibration:
        for direction in ("forward", "reverse"):
            a, b = (case.left, case.right) if direction == "forward" else (case.right, case.left)
            flags = (case.expected_left, case.expected_right) if direction == "forward" else (case.expected_right, case.expected_left)
            for grader in ("legacy", api.GRADER):
                if grader == "legacy":
                    instructions, prompt = build_pairwise_prompt(persona_text=data.instances[0].persona_text, current_request=case.current_request, target_behavior="\n".join(case.checks), response_a=a, response_b=b)
                    output = api.diagnostic_verdict(*flags)
                else:
                    instructions, prompt = api.build_diagnostic_pair_prompt(current_request=case.current_request, checks=case.checks, response_a=a, response_b=b)
                    output = json.dumps(dict(a=flags[0], b=flags[1]))
                attempt = f"{case.case_ref}:{grader}:{direction}"
                request = effect_request_sha256(instructions, prompt, 512, json_output=grader == api.GRADER)
                calls[attempt] = dict(instructions=instructions, input_text=prompt, max_output_tokens=512, json_output=grader == api.GRADER, request_sha256=request, output=output)
                row = dict(case_ref=case.case_ref, direction=direction, grader=grader, attempt_ref=attempt, request_sha256=request, response_a_sha256=sha256_text(a), response_b_sha256=sha256_text(b))
                if grader == "legacy":
                    row["token"] = output
                    legacy.append(row)
                else:
                    row.update(a=list(flags[0]), b=list(flags[1]))
                    rows.append(row)
    return data, legacy, rows, calls


@pytest.mark.parametrize("mutation", [
    lambda legacy, rows, calls: legacy.pop(),
    lambda legacy, rows, calls: rows[0].update(response_a_sha256="f" * 64),
    lambda legacy, rows, calls: calls[rows[0]["attempt_ref"]].update(input_text="substituted"),
    lambda legacy, rows, calls: calls[rows[0]["attempt_ref"]].update(output='{"a":[false],"b":[false]}'),
])
def test_calibration_acceptance_is_bound_to_all_actual_known_answer_calls(mutation):
    api = _api()
    data, legacy, rows, calls = _calibration_bundle()
    assert api.validate_calibration_calls(data, legacy, rows, calls)["identified"] is True
    mutation(legacy, rows, calls)
    with pytest.raises(ValueError):
        api.validate_calibration_calls(data, legacy, rows, calls)


@pytest.mark.parametrize("mutation", [
    lambda records: records.pop(),
    lambda records: records[-1].update(input_sha256="f" * 64),
    lambda records: records[-1].update(kind="common"),
])
def test_diagnostic_result_denominator_keeps_all_negative_controls(mutation):
    api = _api()
    data = api.load_diagnostic(DATA)
    records = [dict(case_ref=i.instance_ref, kind="common" if i in data.instances else "negative", input_sha256=api.diagnostic_input_sha256(i)) for i in data.instances + data.negative_controls]
    api.validate_diagnostic_result_set(data, records)
    mutation(records)
    with pytest.raises(ValueError):
        api.validate_diagnostic_result_set(data, records)


def test_diagnostic_memory_review_binds_each_committed_record_and_condition():
    api = _api()
    data = api.load_diagnostic(DATA)
    records = [dict(case_ref=i.instance_ref, kind="common" if i in data.instances else "negative", input_sha256=api.diagnostic_input_sha256(i)) for i in data.instances + data.negative_controls]
    reviews = [api.diagnostic_review_template(data, r) for r in records]
    for review in reviews:
        for check in review["checks"].values():
            check.update(passed=True, note="inspected exact committed fixture")
    api.validate_diagnostic_reviews(data, records, reviews)
    records[0]["committed_body"] = "changed after inspection"
    with pytest.raises(ValueError, match="review"):
        api.validate_diagnostic_reviews(data, records, reviews)


@pytest.mark.parametrize("mutation", [
    lambda trace, anti, calls: next(iter(calls.values())).update(output="substituted"),
    lambda trace, anti, calls: anti.update(canonical_refs=["invented-delivery"]),
    lambda trace, anti, calls: anti.update(foundation_text="different foundation"),
])
def test_capacity_answers_bind_completed_order_and_eval_only_anti(mutation):
    api = _api()
    from memory_core_eval.delayed_seed_effect import build_answer_prompt
    from memory_core_eval.memory_effect_runner import ARMS, effect_request_sha256, sha256_text
    data = api.load_diagnostic(DATA)
    instance = data.instances[0]
    trace, calls = {"arms": []}, {}
    for mode in ARMS:
        memory = "a fact" if mode != "none" else ""
        instructions, prompt = build_answer_prompt(persona_text=instance.persona_text, current_request=instance.current_request, memory_text=memory)
        request = effect_request_sha256(instructions, prompt, 1024)
        output = mode + " output"
        trace["arms"].append(dict(mode=mode, instructions=instructions, input_text=prompt, request_sha256=request, output=output, output_sha256=sha256_text(output), foundation_text=memory, foundation_refs=["fact@1"] if memory else []))
        calls[mode] = dict(attempt_ref=mode, purpose="diagnostic:d1:answer", rpc_ref="", instructions=instructions, input_text=prompt, max_output_tokens=1024, request_sha256=request, output=output, output_sha256=sha256_text(output))
    tendency = "DISPOSITIONS\nRELATION: " + data.counterfactuals["d1"].anti_disposition
    memory = "a fact\n\n" + tendency
    instructions, prompt = build_answer_prompt(persona_text=instance.persona_text, current_request=instance.current_request, memory_text=memory)
    request = effect_request_sha256(instructions, prompt, 1024)
    calls["anti"] = dict(attempt_ref="anti", purpose="diagnostic:d1:anti", rpc_ref="", instructions=instructions, input_text=prompt, max_output_tokens=1024, request_sha256=request, output="anti output", output_sha256=sha256_text("anti output"))
    anti = dict(attempt_ref="anti", request_sha256=request, output="anti output", output_sha256=sha256_text("anti output"), foundation_text="a fact", foundation_refs=["fact@1"], tendency_text=tendency, memory_text=memory, memory_tokens=len(memory), canonical_refs=[], authority="eval-only; no Delivery/AgentAct/Outcome")
    assert len(api.validate_diagnostic_answers(instance, trace, anti, data.counterfactuals["d1"], calls, token_count=len)) == 5
    mutation(trace, anti, calls)
    with pytest.raises(ValueError):
        api.validate_diagnostic_answers(instance, trace, anti, data.counterfactuals["d1"], calls, token_count=len)


@pytest.mark.asyncio
async def test_complete_public_diagnostic_summary_preserves_failed_semantics_and_ties(tmp_path, monkeypatch):
    """RPC/model doubles exercise inventory, not natural-language efficacy."""
    api = _api()
    from memory_core_eval import anchor_cli
    from memory_core_eval.memory_effect_cli import CapturedEffectModel, EFFECT_PURPOSE, read_jsonl
    from test_memory_effect_runner import MemoryBoundary, _settle
    data = api.load_diagnostic(DATA)
    known = {c.case_ref: c for c in data.calibration}

    async def settle(_probe, scope, *, expected_episodes, timeout):
        return await _settle(scope, expected_episodes)

    monkeypatch.setattr(anchor_cli, "settle_memory_state", settle)

    class Provider:
        async def complete(self, *, instructions, input_text, **kwargs):
            parts = EFFECT_PURPOSE.get().split(":")
            if parts[1] in known:
                case = known[parts[1]]
                flags = (case.expected_left, case.expected_right) if parts[-1] == "forward" else (case.expected_right, case.expected_left)
                return api.diagnostic_verdict(*flags) if parts[2] == "legacy" else json.dumps(dict(a=flags[0], b=flags[1]))
            if "judge" in parts:
                return '{"a":[true,true,true],"b":[true,true,true]}'
            return "Completed provider fixture."

    result = await api.run_public_diagnostic(data=data, evaluation_ref="diagnostic-test", memory=MemoryBoundary(), probe=None, model=CapturedEffectModel(Provider(), tmp_path), token_count=lambda text: len(text) // 8, output=tmp_path)
    captured = read_jsonl(tmp_path / "model-started.jsonl")
    assert all(c.get("json_output") is True for c in captured if api.GRADER in c["purpose"] or ":judge:" in c["purpose"])
    assert all(not c.get("json_output") for c in captured if api.GRADER not in c["purpose"] and ":judge:" not in c["purpose"])
    records = result["records"]
    reviews = [api.diagnostic_review_template(data, r) for r in records]
    for review in reviews:
        for check in review["checks"].values():
            check.update(passed=False, note="boundary fixture cannot prove the semantic requirement")
    kwargs = dict(calibration_records=read_jsonl(tmp_path / "calibration.jsonl"), starts=read_jsonl(tmp_path / "model-started.jsonl"), completed=read_jsonl(tmp_path / "model-completed.jsonl"), failed=[], rpc_starts=[], rpc_completed=[], rpc_failed=[], token_count=lambda text: len(text) // 8)
    summary = api.summarize_diagnostic(data, records, reviews, **kwargs)
    assert summary["calibration"]["identified"] is True
    assert summary["oracle"]["identified"] is False
    assert summary["oracle"]["oracle_vs_base"]["ties"] == 8
    assert summary["all_semantics_pass"] is False
    assert len(summary["negative_controls"]) == 4
    with pytest.raises(ValueError, match="denominator"):
        api.summarize_diagnostic(data, records[:-1], reviews, **kwargs)
