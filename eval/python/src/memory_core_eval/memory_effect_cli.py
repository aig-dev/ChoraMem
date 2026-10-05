"""Frozen live causal gate; synthetic external scoring is not human benefit."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
import contextvars
from dataclasses import asdict, replace
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
import time
from typing import Any
import uuid

from google.protobuf.json_format import MessageToDict
from memory_core import AsyncMemoryClient, memory_pb2 as pb, render_memory_context

from .anchor_cli import settle_memory_state
from .delayed_seed_data import DelayedSeedInstance
from .delayed_seed_effect import build_answer_prompt, build_pairwise_prompt, parse_pairwise_token
from .delayed_seed_runner import freeze_result
from .memory_effect_data import PROTOCOL, MemoryEffectDataset, load_memory_effect, memory_effect_input_sha256
from .memory_effect_runner import (
    ARMS, ANSWER_TOKENS, EPISODE_BYTES, FOUNDATION_TOKENS, MEMORY_TOKENS, TENDENCY_TOKENS,
    build_effect_contexts, effect_constitution, effect_request_sha256, effect_scope,
    observe_effect_session, prepare_effect_trajectory, report_effect_feedback,
    run_effect_arms, sha256_text, validate_effect_trace,
)
from .personamem_live import ChatTextModel, DatabaseProbe


COMPARISONS = tuple(combinations(ARMS, 2))
EFFECT_PURPOSE = contextvars.ContextVar("effect_purpose", default="harness")
EFFECT_RPC = contextvars.ContextVar("effect_rpc", default="")
_BUDGETS = {"temperature": 0, "thinking": "disabled", "provider_max_retries": 0, "provider_completion_attempts": 1, "provider_timeout_seconds": 35, "worker_rpc_timeout_seconds": 120, "tokenizer": "cl100k_base", "worker_output_tokens": 8192, "answer_output_tokens": 1024, "judge_output_tokens": 512, "memory_tokens": 768, "foundation_tokens": 544, "tendency_tokens": 192, "episode_evidence_bytes": 8192}


def json_sha256(value: object) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def checkpoint(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    temp.replace(path)


class CapturedEffectModel:
    """Capture every actual attempt before invoking the existing provider."""
    def __init__(self, model: Any, output: Path):
        self.model, self.output = model, Path(output)
        self._last = contextvars.ContextVar("last_effect_completion")

    @property
    def last_call(self) -> dict:
        return self._last.get()

    async def complete(self, *, instructions: str, input_text: str, max_output_tokens: int) -> str:
        started = {"attempt_ref": "model:" + uuid.uuid4().hex, "purpose": EFFECT_PURPOSE.get(), "rpc_ref": EFFECT_RPC.get(), "instructions": instructions, "input_text": input_text, "max_output_tokens": max_output_tokens, "request_sha256": effect_request_sha256(instructions, input_text, max_output_tokens)}
        append_jsonl(self.output / "model-started.jsonl", started)
        began = time.monotonic()
        try:
            output = await self.model.complete(instructions=instructions, input_text=input_text, max_output_tokens=max_output_tokens)
            if not isinstance(output, str) or not output.strip():
                raise ValueError("completed provider output is empty")
        except BaseException as error:
            append_jsonl(self.output / "model-failed.jsonl", {**started, "error_class": type(error).__name__, "duration_ms": round((time.monotonic() - began) * 1000, 3)})
            raise
        completed = {**started, "output": output, "output_sha256": sha256_text(output), "finish_reason": "stop", "duration_ms": round((time.monotonic() - began) * 1000, 3)}
        append_jsonl(self.output / "model-completed.jsonl", completed)
        self._last.set(completed)
        return output


def make_effect_manifest(data: MemoryEffectDataset, *, evaluation_ref: str, runtime: Mapping, source_sha256: Mapping[str, str], core_binary_sha256: str, provider: Mapping) -> dict:
    refs = [i.instance_ref for i in data.instances + data.negative_controls] + [i.case_ref for i in data.feedback_scenarios] + [i.instance_ref for i in data.semantic_scenarios]
    inputs = {i.instance_ref: memory_effect_input_sha256(i) for i in data.instances + data.negative_controls + data.semantic_scenarios}
    inputs.update({i.case_ref: json_sha256(asdict(i)) for i in data.feedback_scenarios})
    manifest = {"protocol": PROTOCOL, "evaluation_ref": evaluation_ref, "data_sha256": data.source_sha256, "case_refs": refs, "case_input_sha256": inputs, "runtime": dict(runtime), "source_sha256": dict(source_sha256), "core_binary_sha256": core_binary_sha256, "provider": dict(provider), "oracle_gate": {"denominator": 8, "wins_minimum": 6, "losses_maximum": 0, "positions": ["forward", "reverse"]}, "scope": "frozen synthetic trajectories; no claim of real human or natural-distribution benefit"}
    validate_effect_manifest(manifest, data, runtime=runtime, source_sha256=source_sha256)
    return manifest


def validate_effect_manifest(manifest: Mapping, data: MemoryEffectDataset, *, runtime: Mapping, source_sha256: Mapping[str, str]) -> None:
    required = {"protocol", "evaluation_ref", "data_sha256", "case_refs", "case_input_sha256", "runtime", "source_sha256", "core_binary_sha256", "provider", "oracle_gate", "scope"}
    if not required <= manifest.keys() or manifest.get("protocol") != PROTOCOL or manifest.get("data_sha256") != data.source_sha256:
        raise ValueError("partial manifest or data drift")
    if manifest["runtime"] != runtime or manifest["source_sha256"] != source_sha256:
        raise ValueError("manifest runtime/source drift")
    if any(runtime.get(k) != v for k, v in _BUDGETS.items()) or any(not runtime.get(k) for k in ("model", "model_revision", "base_url")):
        raise ValueError("manifest model/budget drift")
    if any("key" in str(k).lower() or "secret" in str(k).lower() for k in runtime):
        raise ValueError("manifest must not contain credentials")
    if not source_sha256 or any(not _hash(v) for v in source_sha256.values()) or not _hash(manifest["core_binary_sha256"]) or not manifest["provider"].get("revision_ref"):
        raise ValueError("incomplete manifest revisions")
    refs = {i.instance_ref for i in data.instances + data.negative_controls + data.semantic_scenarios} | {i.case_ref for i in data.feedback_scenarios}
    if set(manifest["case_refs"]) != refs or len(manifest["case_refs"]) != len(refs) or set(manifest["case_input_sha256"]) != refs:
        raise ValueError("manifest case denominator drift")
    for instance in data.instances + data.negative_controls + data.semantic_scenarios:
        if manifest["case_input_sha256"][instance.instance_ref] != memory_effect_input_sha256(instance):
            raise ValueError("manifest input drift")
    for scenario in data.feedback_scenarios:
        if manifest["case_input_sha256"][scenario.case_ref] != json_sha256(asdict(scenario)):
            raise ValueError("manifest feedback input drift")
    if manifest["oracle_gate"] != {"denominator": 8, "wins_minimum": 6, "losses_maximum": 0, "positions": ["forward", "reverse"]}:
        raise ValueError("manifest Oracle gate drift")


def validate_model_inventory(starts: Sequence[Mapping], completed: Sequence[Mapping], failed: Sequence[Mapping]) -> dict:
    def index(values):
        result = {}
        for value in values:
            attempt = value.get("attempt_ref")
            if not attempt or attempt in result:
                raise ValueError("duplicate/invalid model inventory")
            result[attempt] = value
        return result
    begun, done, failures = index(starts), index(completed), index(failed)
    if set(done) & set(failures) or set(done) | set(failures) != set(begun):
        raise ValueError("incomplete model attempt inventory")
    rpc_counts = Counter()
    for attempt, start in begun.items():
        if start.get("request_sha256") != effect_request_sha256(start["instructions"], start["input_text"], start["max_output_tokens"]):
            raise ValueError("started model request drift")
        end = (done | failures)[attempt]
        if any(end.get(k) != v for k, v in start.items()):
            raise ValueError("model inventory request/association drift")
        if start.get("rpc_ref"):
            rpc_counts[start["rpc_ref"]] += 1
        if attempt in done and (end.get("finish_reason") != "stop" or not end.get("output") or end.get("output_sha256") != sha256_text(end["output"])):
            raise ValueError("incomplete or changed model output")
    if rpc_counts and max(rpc_counts.values()) > 4:
        raise ValueError("Worker RPC exceeds four actual model calls")
    return {"started": len(begun), "completed": len(done), "failed": len(failures)}


def validate_rpc_inventory(starts, completed, failed, model_starts) -> dict:
    def index(values):
        result = {r["rpc_ref"]: r for r in values}
        if len(result) != len(values):
            raise ValueError("duplicate Worker RPC inventory")
        return result
    begun, done, failures = index(starts), index(completed), index(failed)
    if set(done) & set(failures) or set(done) | set(failures) != set(begun):
        raise ValueError("incomplete Worker RPC inventory")
    for ref, start in begun.items():
        end = (done | failures)[ref]
        if not _hash(start.get("request_proto_sha256")) or any(end.get(k) != v for k, v in start.items()):
            raise ValueError("Worker RPC request drift")
    if any(c.get("rpc_ref") and c["rpc_ref"] not in begun for c in model_starts):
        raise ValueError("model call lacks actual Worker RPC start")
    return {"started": len(begun), "completed": len(done), "failed": len(failures)}


def validate_pairwise(records: Sequence[Mapping], *, comparisons=COMPARISONS) -> dict[str, str]:
    indexed = {}
    for record in records:
        pair = (record.get("left"), record.get("right"))
        direction = record.get("direction")
        if pair not in comparisons or direction not in {"forward", "reverse"}:
            raise ValueError("unknown pair/position")
        key = (pair, direction)
        if key in indexed:
            raise ValueError("duplicate pair/position")
        a, b = pair if direction == "forward" else pair[::-1]
        if (record.get("a_mode"), record.get("b_mode")) != (a, b):
            raise ValueError("pair response position drift")
        token = parse_pairwise_token(record.get("token"))
        indexed[key] = a if token == "A" else b if token == "B" else "tie"
    if set(indexed) != {(pair, direction) for pair in comparisons for direction in ("forward", "reverse")}:
        raise ValueError("missing pair or reverse position")
    return {":".join(pair): indexed[(pair, "forward")] if indexed[(pair, "forward")] == indexed[(pair, "reverse")] else "inconsistent" for pair in comparisons}


def oracle_gate(vs_base: Sequence[str], vs_anti: Sequence[str]) -> dict:
    def summary(values, competitor):
        if len(values) != 8 or not set(values) <= {"oracle", competitor, "tie", "inconsistent"}:
            raise ValueError("Oracle comparison requires all eight cases")
        wins, losses = values.count("oracle"), values.count(competitor)
        informative = wins + losses
        probability = sum(math.comb(informative, k) for k in range(wins, informative + 1)) / 2 ** informative if informative else 1.0
        z, n, p = 1.959963984540054, 8, wins / 8
        center = (p + z*z/(2*n))/(1+z*z/n)
        radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
        return {"denominator": n, "wins": wins, "losses": losses, "ties": values.count("tie"), "inconsistent": values.count("inconsistent"), "one_sided_sign_probability": probability, "win_fraction_95_wilson": [center-radius, center+radius]}
    base, anti = summary(vs_base, "base"), summary(vs_anti, "anti")
    return {"identified": all(s["wins"] >= 6 and s["losses"] == 0 for s in (base, anti)), "oracle_vs_base": base, "oracle_vs_anti": anti, "scope": "paired finite synthetic contrast; informative-sign calculation does not establish natural-distribution uplift"}


def validate_result_set(data: MemoryEffectDataset, records: Sequence[Mapping]) -> None:
    expected = {i.instance_ref for i in data.instances + data.negative_controls + data.semantic_scenarios} | {i.case_ref for i in data.feedback_scenarios}
    refs = [record.get("case_ref") for record in records]
    if len(refs) != len(set(refs)):
        raise ValueError("duplicate case result")
    if set(refs) != expected:
        raise ValueError("incomplete case/negative/semantic denominator")


def semantic_review_template(data: MemoryEffectDataset, record: Mapping) -> dict:
    ref = record["case_ref"]
    checks = {}
    for key, value in data.expectations[ref].items():
        if isinstance(value, (list, tuple)) and key in {"required_claims", "forbidden_claims"}:
            for i, claim in enumerate(value):
                checks[f"{key}[{i}]"] = {"expectation": claim, "passed": None, "note": ""}
        else:
            checks[key] = {"expectation": value, "passed": None, "note": ""}
    return {"case_ref": ref, "record_sha256": json_sha256(record), "checks": checks}


def validate_semantic_reviews(data: MemoryEffectDataset, records: Sequence[Mapping], reviews: Sequence[Mapping]) -> None:
    expected = {i.case_ref for i in data.feedback_scenarios} | {i.instance_ref for i in data.semantic_scenarios}
    by_ref = {r["case_ref"]: r for r in records if r["case_ref"] in expected}
    review_map = {r["case_ref"]: r for r in reviews}
    if set(by_ref) != expected or set(review_map) != expected or len(review_map) != len(reviews):
        raise ValueError("incomplete or duplicate semantic expectation review")
    for ref, record in by_ref.items():
        review = review_map[ref]
        template = semantic_review_template(data, record)
        if review.get("record_sha256") != template["record_sha256"]:
            raise ValueError("stale semantic record/body review")
        if set(review.get("checks", {})) != set(template["checks"]):
            raise ValueError("incomplete semantic expectation checks")
        for key, check in review["checks"].items():
            if check.get("expectation") != template["checks"][key]["expectation"] or type(check.get("passed")) is not bool or not str(check.get("note", "")).strip():
                raise ValueError("uninspected semantic expectation")


def _hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


async def judge_effect_pairs(*, instance: DelayedSeedInstance, rubric: str, answers: Mapping[str, str], anti_output: str, model: CapturedEffectModel) -> tuple[list[dict], list[dict]]:
    if set(answers) != set(ARMS) or any(not isinstance(v, str) or not v.strip() for v in answers.values()):
        raise ValueError("must preserve four actual completed answers before judging")
    pairwise, manipulation = [], []
    responses = {**answers, "anti": anti_output}
    comparisons = COMPARISONS + (("oracle", "anti"),)
    for left, right in comparisons:
        directions = ("forward", "reverse") if int(sha256_text(instance.instance_ref + left + right)[:8], 16) % 2 == 0 else ("reverse", "forward")
        for direction in directions:
            a, b = (left, right) if direction == "forward" else (right, left)
            instructions, input_text = build_pairwise_prompt(persona_text=instance.persona_text, current_request=instance.current_request, target_behavior=rubric, response_a=responses[a], response_b=responses[b])
            token = EFFECT_PURPOSE.set(f"{instance.instance_ref}:judge:{left}:{right}:{direction}")
            try:
                output = await model.complete(instructions=instructions, input_text=input_text, max_output_tokens=512)
            finally:
                EFFECT_PURPOSE.reset(token)
            record = {"left": left, "right": right, "direction": direction, "a_mode": a, "b_mode": b, "token": parse_pairwise_token(output), "attempt_ref": model.last_call["attempt_ref"], "request_sha256": model.last_call["request_sha256"], "response_a_sha256": sha256_text(responses[a]), "response_b_sha256": sha256_text(responses[b])}
            (manipulation if (left, right) == ("oracle", "anti") else pairwise).append(record)
    validate_pairwise(pairwise)
    validate_pairwise(manipulation, comparisons=(("oracle", "anti"),))
    return pairwise, manipulation


def _learned_content(context: pb.MemoryContext, token_count) -> dict:
    # Same frozen partitions as the main comparison. No Oracle ref is created.
    foundation = render_memory_context(pb.MemoryContext(recollections=context.recollections, episode_evidence=context.episode_evidence), max_tokens=FOUNDATION_TOKENS, token_count=token_count)
    tendency = render_memory_context(pb.MemoryContext(dispositions=context.dispositions), max_tokens=TENDENCY_TOKENS, token_count=token_count)
    text = "\n\n".join(v for v in (foundation.text, tendency.text) if v)
    count = token_count(text)
    if count > MEMORY_TOKENS:
        raise ValueError("followup exceeds frozen memory budget")
    return {"memory_text": text, "memory_tokens": count, "foundation_refs": list(foundation.memory_refs), "tendency_refs": list(tendency.memory_refs), "rendered_memory_refs": list(foundation.memory_refs + tendency.memory_refs)}


async def run_effect_probe(*, instance: DelayedSeedInstance, request: str, evaluation_ref: str, suffix: str, memory: Any, model: CapturedEffectModel, token_count, scope: pb.MemoryScope | None = None, selected: pb.MemoryContext | None = None, source_ref: str | None = None, scripted_act: str | None = None) -> dict:
    """One subsequent real response, or an explicitly labelled semantic fixture."""
    scope = scope or effect_scope(evaluation_ref, instance.instance_ref, suffix)
    source_ref = source_ref or scope.session_ref + ":" + suffix + ":situation"
    run = selected.run_ref if selected is not None else scope.session_ref + ":" + suffix + ":run"
    constitution = effect_constitution(instance.persona_text)
    user = "memory-effect-user:" + sha256_text(scope.relationship_ref)[:24]
    source = pb.SourceEvent(scope=scope, text=request, source_ref=source_ref, actor_kind=pb.SOURCE_ACTOR_KIND_USER, actor_ref=user, constitution=constitution)
    situation = await memory.observe_source_event(pb.ObserveSourceEventRequest(idempotency_key=source_ref + ":observe", source_event=source))
    if selected is None:
        selected = await memory.select_memory(pb.SelectMemoryRequest(scope=scope, run_ref=run, situation_source_event_refs=[situation.source_event_ref], constitution=constitution, episode_evidence_max_bytes=EPISODE_BYTES))
    content = _learned_content(selected, token_count)
    delivery = None
    if content["rendered_memory_refs"]:
        receipt = await memory.record_memory_delivery(pb.MemoryDeliveryReceipt(idempotency_key=run + ":delivery", scope=scope, run_ref=run, memory_context_ref=selected.context_ref, delivered_memory_refs=content["rendered_memory_refs"]))
        delivery = {"receipt_ref": receipt.receipt_ref, "run_ref": run, "memory_context_ref": selected.context_ref, "delivered_memory_refs": content["rendered_memory_refs"]}
    instructions, input_text = build_answer_prompt(persona_text=instance.persona_text, current_request=request, memory_text=content["memory_text"])
    if scripted_act is None:
        token = EFFECT_PURPOSE.set(f"{instance.instance_ref}:probe:{suffix}")
        try:
            output = await model.complete(instructions=instructions, input_text=input_text, max_output_tokens=ANSWER_TOKENS)
        finally:
            EFFECT_PURPOSE.reset(token)
        origin, attempt = "model_completion", model.last_call["attempt_ref"]
    else:
        output, origin, attempt = scripted_act, "scripted-semantic-fixture", None
    await memory.observe_source_event(pb.ObserveSourceEventRequest(idempotency_key=source_ref + ":bind:" + suffix, source_event=source, episode_binding=pb.EpisodeBinding(run_ref=run, source_group_ref=run, role=pb.EPISODE_SOURCE_ROLE_SITUATION)))
    agent_ref = run + ":agent-act"
    act = await memory.observe_source_event(pb.ObserveSourceEventRequest(idempotency_key=agent_ref + ":observe", source_event=pb.SourceEvent(scope=scope, text=output, source_ref=agent_ref, actor_kind=pb.SOURCE_ACTOR_KIND_AGENT, actor_ref=scope.agent_ref, constitution=constitution), episode_binding=pb.EpisodeBinding(run_ref=run, source_group_ref=run, role=pb.EPISODE_SOURCE_ROLE_AGENT_ACT)))
    return {"scope": MessageToDict(scope, preserving_proto_field_name=True), "run_ref": run, "situation_source_event_ref": situation.source_event_ref, "request": request, "selected": MessageToDict(selected, preserving_proto_field_name=True), **content, "delivery": delivery, "instructions": instructions, "input_text": input_text, "request_sha256": effect_request_sha256(instructions, input_text, ANSWER_TOKENS), "answer_attempt_ref": attempt, "output": output, "output_sha256": sha256_text(output), "agent_act_origin": origin, "agent_act": {"source_event_ref": act.source_event_ref, "text": output, "episode_ref": act.episode_ref}}


async def _report_user_outcome(*, memory, scope, probe_run, text, persona_text, suffix, attributed=True):
    source_ref = probe_run["run_ref"] + ":" + suffix + ":outcome"
    receipt = await memory.report_outcome(pb.ReportOutcomeRequest(idempotency_key=source_ref + ":report", scope=scope, run_ref=probe_run["run_ref"], source_group_ref=probe_run["run_ref"], text=text, source_ref=source_ref, actor_kind=pb.SOURCE_ACTOR_KIND_USER, actor_ref="memory-effect-user:" + sha256_text(scope.relationship_ref)[:24], constitution=effect_constitution(persona_text), delivery_receipt_refs=[probe_run["delivery"]["receipt_ref"]] if attributed and probe_run["delivery"] else [], related_source_event_refs=[probe_run["situation_source_event_ref"], probe_run["agent_act"]["source_event_ref"]] if attributed else []))
    return {"outcome_event_ref": receipt.outcome_event_ref, "episode_ref": receipt.episode_ref, "source_ref": source_ref, "text": text, "run_ref": probe_run["run_ref"], "attributed": attributed}


async def run_live_effects(*, data: MemoryEffectDataset, manifest: Mapping, memory, probe: DatabaseProbe, model: CapturedEffectModel, token_count, output: Path) -> list[dict]:
    """Replay all frozen cases; every final case and raw answer is durable."""
    output, evaluation = Path(output), manifest["evaluation_ref"]
    records = []

    async def settle(scope, count):
        state = await settle_memory_state(probe, scope, expected_episodes=count, timeout=240)
        append_jsonl(output / "settled.jsonl", {"scope": MessageToDict(scope, preserving_proto_field_name=True), "expected_episodes": count, "state": state})
        return state

    def finished(record):
        freeze_result(output / "results" / (record["case_ref"] + ".json"), record)
        records.append(record)
        print(json.dumps({"completed_case": record["case_ref"], "kind": record["kind"]}), flush=True)

    for instance in data.instances:
        ref = instance.instance_ref
        prepared = await prepare_effect_trajectory(instance=instance, evaluation_ref=evaluation, memory=memory, settle=settle)
        token = EFFECT_PURPOSE.set(ref + ":answer")
        try:
            trace = await run_effect_arms(instance=instance, prepared=prepared, counterfactual=data.counterfactuals[ref], memory=memory, answer_model=model, token_count=token_count, persist=lambda value: checkpoint(output / "progress" / (ref + ".json"), value))
        finally:
            EFFECT_PURPOSE.reset(token)
        freeze_result(output / "raw-answers" / (ref + ".json"), trace)
        # The main Oracle answer is reused in the separately frozen anti control.
        oracle_arm = next(a for a in trace["arms"] if a["mode"] == "oracle")
        anti_tendency = "DISPOSITIONS\nRELATION: " + data.counterfactuals[ref].anti_disposition
        anti_memory = "\n\n".join(v for v in (oracle_arm["foundation_text"], anti_tendency) if v)
        if token_count(anti_tendency) > TENDENCY_TOKENS or token_count(anti_memory) > MEMORY_TOKENS:
            raise ValueError("anti control exceeds frozen budget")
        instructions, input_text = build_answer_prompt(persona_text=instance.persona_text, current_request=instance.current_request, memory_text=anti_memory)
        token = EFFECT_PURPOSE.set(ref + ":anti-control")
        try:
            anti_output = await model.complete(instructions=instructions, input_text=input_text, max_output_tokens=ANSWER_TOKENS)
        finally:
            EFFECT_PURPOSE.reset(token)
        anti = {"output": anti_output, "output_sha256": sha256_text(anti_output), "attempt_ref": model.last_call["attempt_ref"], "request_sha256": model.last_call["request_sha256"], "foundation_text": oracle_arm["foundation_text"], "foundation_refs": oracle_arm["foundation_refs"], "tendency_text": anti_tendency, "memory_text": anti_memory, "memory_tokens": token_count(anti_memory), "canonical_refs": [], "authority": "eval-only, no canonical Delivery/AgentAct/Outcome"}
        freeze_result(output / "raw-answers" / (ref + "-anti.json"), anti)
        pairwise, manipulation = await judge_effect_pairs(instance=instance, rubric=data.expectations[ref]["rubric"], answers={a["mode"]: a["output"] for a in trace["arms"]}, anti_output=anti_output, model=model)
        before_feedback = await settle(prepared.scope, prepared.expected_episodes + 4)
        feedback = []
        for mode in ARMS:
            judgments = [p for p in pairwise if mode in (p["left"], p["right"])]
            # Exact external judgments, without inventing an observed human benefit.
            text = "合成外部盲评记录：" + json.dumps([{k: p[k] for k in ("left", "right", "direction", "token")} for p in judgments], ensure_ascii=False) + "。评分只评价预注册回应标准，没有观察到真实用户执行或任务收益。"
            feedback.append(await report_effect_feedback(memory=memory, trace=trace, mode=mode, text=text, token_count=token_count))
        after_feedback = await settle(prepared.scope, prepared.expected_episodes + 4)
        correction = data.corrections[ref]
        began = time.monotonic()
        correction_reply = await run_effect_probe(instance=instance, request=correction.source_text, evaluation_ref=evaluation, suffix="lasting-correction", memory=memory, model=model, token_count=token_count)
        count = prepared.expected_episodes + 5
        correction_scope = effect_scope(evaluation, ref, "lasting-correction")
        after_correction = await settle(correction_scope, count)
        latency = round((time.monotonic() - began) * 1000, 3)
        subsequent = []
        for turn, request in enumerate(correction.same_condition_requests, start=1):
            suffix = f"after-correction:{turn}"
            reply = await run_effect_probe(instance=instance, request=request, evaluation_ref=evaluation, suffix=suffix, memory=memory, model=model, token_count=token_count)
            count += 1
            state = await settle(effect_scope(evaluation, ref, suffix), count)
            subsequent.append({"turn": turn, "kind": "same_condition", "reply": reply, "after": state, "elapsed_ms": round((time.monotonic() - began) * 1000, 3)})
        suffix = "after-correction:different"
        different = await run_effect_probe(instance=instance, request=correction.different_condition_request, evaluation_ref=evaluation, suffix=suffix, memory=memory, model=model, token_count=token_count)
        after = await settle(effect_scope(evaluation, ref, suffix), count + 1)
        finished({"case_ref": ref, "kind": "common", "trace": trace, "anti": anti, "pairwise": pairwise, "manipulation": manipulation, "before_feedback": before_feedback, "feedback": feedback, "after_feedback": after_feedback, "correction": {"source_text": correction.source_text, "reply": correction_reply, "after": after_correction, "settled_elapsed_ms": latency, "same_condition_probes": subsequent, "different_condition_probe": different}, "after": after})

    for instance in data.negative_controls + data.semantic_scenarios:
        ref = instance.instance_ref
        prepared = await prepare_effect_trajectory(instance=instance, evaluation_ref=evaluation, memory=memory, settle=settle)
        kind = "negative" if ref.startswith("n") else "source_semantics"
        reply = await run_effect_probe(instance=instance, request=instance.current_request, evaluation_ref=evaluation, suffix="probe", memory=memory, model=model, token_count=token_count, scope=prepared.scope, selected=prepared.contexts["learned"], source_ref=prepared.situation_source_event_ref)
        after = await settle(prepared.scope, prepared.expected_episodes + 1)
        finished({"case_ref": ref, "kind": kind, "input_sha256": memory_effect_input_sha256(instance), "before": prepared.consolidation_states[-1], "training_states": list(prepared.consolidation_states), "training_sources": list(prepared.training_sources), "reply": reply, "after": after})

    for scenario in data.feedback_scenarios:
        ref = scenario.case_ref
        training = next(i for i in data.instances if i.instance_ref == scenario.trajectory_ref)
        instance = replace(training, instance_ref=ref, pattern_ref=ref, current_request=scenario.current_request)
        prepared = await prepare_effect_trajectory(instance=instance, evaluation_ref=evaluation, memory=memory, settle=settle)
        scripted = await run_effect_probe(instance=instance, request=scenario.current_request, evaluation_ref=evaluation, suffix="scripted", memory=memory, model=model, token_count=token_count, scope=prepared.scope, selected=prepared.contexts["learned"], source_ref=prepared.situation_source_event_ref, scripted_act=scenario.agent_act)
        count, interim, outcomes = prepared.expected_episodes + 1, [], []
        if scenario.association == "trusted_late":
            interim.append(await settle(prepared.scope, count))
            # A later run in the same real conversation Session; no new session identity.
            later = await run_effect_probe(instance=instance, request="先聊别的事：社区图书馆要怎么查开放时间？", evaluation_ref=evaluation, suffix="intervening", memory=memory, model=model, token_count=token_count, scope=prepared.scope)
            count += 1
            interim.append(await settle(prepared.scope, count))
            outcomes.append(await _report_user_outcome(memory=memory, scope=prepared.scope, probe_run=scripted, text=scenario.outcome_text, persona_text=instance.persona_text, suffix="trusted-late"))
        elif scenario.association == "missing_and_wrong":
            missing_ref = prepared.scope.session_ref + ":unassociated-feedback"
            await memory.observe_source_event(pb.ObserveSourceEventRequest(idempotency_key=missing_ref, source_event=pb.SourceEvent(scope=prepared.scope, source_ref=missing_ref, text=scenario.outcome_text, actor_kind=pb.SOURCE_ACTOR_KIND_USER, actor_ref="memory-effect-user:" + sha256_text(prepared.scope.relationship_ref)[:24], constitution=effect_constitution(instance.persona_text))))
            outcomes.append({"association": "missing", "source_ref": missing_ref, "outcome_event_ref": None, "text": scenario.outcome_text})
            wrong = {**scripted, "run_ref": scripted["run_ref"] + ":wrong"}
            try:
                await _report_user_outcome(memory=memory, scope=prepared.scope, probe_run=wrong, text=scenario.outcome_text, persona_text=instance.persona_text, suffix="wrong")
            except Exception as error:
                code = error.code().name if callable(getattr(error, "code", None)) else type(error).__name__
                outcomes.append({"association": "wrong", "rejected": True, "error_class": code})
            else:
                outcomes.append({"association": "wrong", "rejected": False})
        elif scenario.outcome_text is not None:
            outcomes.append(await _report_user_outcome(memory=memory, scope=prepared.scope, probe_run=scripted, text=scenario.outcome_text, persona_text=instance.persona_text, suffix="current"))
        after_feedback = await settle(prepared.scope, count)
        if ref == "f7":
            # Reuse the exact USER physical source as the next Situation, as Chorai does.
            correction_reply = await run_effect_probe(instance=instance, request=scenario.outcome_text, evaluation_ref=evaluation, suffix="direct-correction", memory=memory, model=model, token_count=token_count, scope=prepared.scope, source_ref=outcomes[0]["source_ref"])
            count += 1
            interim.append({"correction_reply": correction_reply, "after": await settle(prepared.scope, count)})
        following = []
        for i, request in enumerate(scenario.followup_requests):
            suffix = f"subsequent:{i}"
            reply = await run_effect_probe(instance=instance, request=request, evaluation_ref=evaluation, suffix=suffix, memory=memory, model=model, token_count=token_count)
            count += 1
            following.append({"reply": reply, "after": await settle(effect_scope(evaluation, ref, suffix), count)})
        after = following[-1]["after"] if following else interim[-1]["after"] if ref == "f7" else after_feedback
        finished({"case_ref": ref, "kind": "feedback_semantics", "scenario": asdict(scenario), "before": prepared.consolidation_states[-1], "training_sources": list(prepared.training_sources), "scripted_probe": scripted, "outcomes": outcomes, "interim": interim, "after_feedback": after_feedback, "subsequent": following, "after": after})
    validate_result_set(data, records)
    freeze_result(output / "unreviewed-results.json", records)
    checkpoint(output / "semantic-review-template.json", [semantic_review_template(data, r) for r in records if r["kind"] in {"source_semantics", "feedback_semantics"}])
    return records


def summarize_effect(data: MemoryEffectDataset, records: Sequence[Mapping], reviews: Sequence[Mapping], *, starts, completed, failed, token_count) -> dict:
    validate_result_set(data, records)
    inventory = validate_model_inventory(starts, completed, failed)
    validate_semantic_reviews(data, records, reviews)
    by_ref, calls = {r["case_ref"]: r for r in records}, {c["attempt_ref"]: c for c in completed}
    base_winners, anti_winners, rows = [], [], []
    for instance in data.instances:
        record = by_ref[instance.instance_ref]
        trace = record["trace"]
        validate_effect_trace(trace, token_count=token_count)
        if trace["input_sha256"] != memory_effect_input_sha256(instance):
            raise ValueError("common source input drift")
        pairs = validate_pairwise(record["pairwise"])
        manipulation = validate_pairwise(record["manipulation"], comparisons=(("oracle", "anti"),))
        responses = {a["mode"]: a["output"] for a in trace["arms"]} | {"anti": record["anti"]["output"]}
        for pair in record["pairwise"] + record["manipulation"]:
            instructions, input_text = build_pairwise_prompt(persona_text=instance.persona_text, current_request=instance.current_request, target_behavior=data.expectations[instance.instance_ref]["rubric"], response_a=responses[pair["a_mode"]], response_b=responses[pair["b_mode"]])
            call = calls.get(pair.get("attempt_ref"))
            if not call or call["request_sha256"] != effect_request_sha256(instructions, input_text, 512) or parse_pairwise_token(call["output"]) != pair["token"]:
                raise ValueError("pairwise result lacks exact completed blind call")
        base_winners.append(pairs["base:oracle"])
        anti_winners.append(manipulation["oracle:anti"])
        learned = next(a for a in trace["arms"] if a["mode"] == "learned")
        selected = trace["selected_contexts"]["learned"]
        rows.append({"case_ref": instance.instance_ref, "source_experiences": len(trace["training_sources"]), "active_canonical_seeds": len([s for s in trace["canonical_before"].get("dispositions", []) if s["status"] == "active"]), "selected_disposition_refs": [s["memory_ref"] for s in selected.get("dispositions", [])], "rendered_disposition_refs": learned["tendency_refs"], "delivery_ref": learned["delivery"]["receipt_ref"] if learned["delivery"] else None, "pairwise": pairs, "correction_settled_ms": record["correction"]["settled_elapsed_ms"]})
    semantic_rows = [{"case_ref": r["case_ref"], "passed": all(c["passed"] for c in r["checks"].values()), "failed_checks": [k for k,c in r["checks"].items() if not c["passed"]]} for r in reviews]
    negative_rows = [{"case_ref": i.instance_ref, "active_dispositions": sum(s["status"] == "active" for s in by_ref[i.instance_ref]["after"].get("dispositions", []))} for i in data.negative_controls]
    return {"protocol": PROTOCOL, "data_sha256": data.source_sha256, "model_inventory": inventory, "oracle": oracle_gate(base_winners, anti_winners), "common": rows, "negative_controls": negative_rows, "semantics": semantic_rows, "all_semantics_pass": all(r["passed"] for r in semantic_rows), "synthetic_only": True}


def main(argv=None) -> None:
    """Connect to a task-owned captured runtime; credentials stay in env only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core-grpc", required=True)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args(argv)
    asyncio.run(_main(args))


async def _main(args) -> None:
    import os
    import tiktoken
    data = load_memory_effect(args.dataset)
    manifest = json.loads(args.manifest.read_text())
    runtime = manifest["runtime"]
    current_sources = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in manifest["source_sha256"]}
    validate_effect_manifest(manifest, data, runtime=runtime, source_sha256=current_sources)
    model = ChatTextModel(model=runtime["model"], api_key=os.environ["OPENAI_API_KEY"], base_url=runtime["base_url"], usage_file=args.output / "usage.jsonl", request_timeout=35, max_retries=0, completion_attempts=1)
    memory = AsyncMemoryClient.connect(args.core_grpc, token_provider=lambda _: os.environ.get("MEMORY_EVAL_CORE_TOKEN", "isolated-eval"), default_timeout=20)
    probe = DatabaseProbe(os.environ["MEMORY_EVAL_DATABASE_URL"])
    encoding = tiktoken.get_encoding("cl100k_base")
    token_count = lambda value: len(encoding.encode(value))
    try:
        records = await run_live_effects(data=data, manifest=manifest, memory=memory, probe=probe, model=CapturedEffectModel(model, args.output), token_count=token_count, output=args.output)
        if args.reviews:
            summary = summarize_effect(data, records, json.loads(args.reviews.read_text()), starts=read_jsonl(args.output / "model-started.jsonl"), completed=read_jsonl(args.output / "model-completed.jsonl"), failed=read_jsonl(args.output / "model-failed.jsonl"), token_count=token_count)
            summary["rpc_inventory"] = validate_rpc_inventory(read_jsonl(args.output / "rpc-started.jsonl"), read_jsonl(args.output / "rpc-completed.jsonl"), read_jsonl(args.output / "rpc-failed.jsonl"), read_jsonl(args.output / "model-started.jsonl"))
            freeze_result(args.output / "summary.json", summary)
        else:
            print(json.dumps({"semantic_review_pending": 17, "completed_raw_cases": len(records)}), flush=True)
    finally:
        await memory.close()
        await model.close()


if __name__ == "__main__":
    main()
