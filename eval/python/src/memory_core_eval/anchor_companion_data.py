"""ANCHOR companion probes over frozen Seed-formation histories."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from .delayed_seed_data import DelayedSeedCounterfactual, DelayedSeedLabel
from .seed_generalization_data import (
    GeneralizationDataset,
    load_seed_generalization,
)


PROTOCOL = "anchor-companion-v1"


def load_anchor_companion(path: Path) -> GeneralizationDataset:
    """Change only answer probes; formation histories and controls stay frozen."""

    source = Path(path).resolve()
    payload = source.read_bytes()
    raw = json.loads(payload)
    if not isinstance(raw, Mapping):
        raise ValueError("ANCHOR companion data must be an object")
    if raw.get("schema_version") != 1 or raw.get("protocol") != PROTOCOL:
        raise ValueError("ANCHOR companion schema or protocol drift")

    base_name = _nonblank(raw.get("base_data"), "base_data")
    if Path(base_name).name != base_name:
        raise ValueError("ANCHOR companion base_data must be a sibling filename")
    base_path = source.parent / base_name
    expected_base_hash = _sha256_text(raw.get("base_sha256"), "base_sha256")
    if hashlib.sha256(base_path.read_bytes()).hexdigest() != expected_base_hash:
        raise ValueError("ANCHOR companion base data drift")
    base = load_seed_generalization(base_path)

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(base.positive_refs):
        raise ValueError("ANCHOR companion requires four behavior cases")
    cases: dict[str, Mapping[str, object]] = {}
    for value in raw_cases:
        if not isinstance(value, Mapping):
            raise ValueError("ANCHOR companion case must be an object")
        instance_ref = _nonblank(value.get("instance_ref"), "instance_ref")
        if instance_ref in cases:
            raise ValueError("ANCHOR companion instance_ref must be unique")
        cases[instance_ref] = value
    if set(cases) != set(base.positive_refs):
        raise ValueError("ANCHOR companion cases must match positive formation refs")

    instances = []
    labels = dict(base.labels)
    counterfactuals = dict(base.counterfactuals)
    for instance in base.instances:
        raw_case = cases.get(instance.instance_ref)
        if raw_case is None:
            instances.append(instance)
            continue
        current_request = _nonblank(raw_case.get("current_request"), "current_request")
        target = _nonblank(raw_case.get("target_behavior"), "target_behavior")
        oracle = _nonblank(raw_case.get("oracle_disposition"), "oracle_disposition")
        anti = _nonblank(raw_case.get("anti_disposition"), "anti_disposition")
        if oracle == anti:
            raise ValueError("ANCHOR companion oracle and anti must differ")
        answer_input = instance.persona_text + "\n" + current_request
        if any(value in answer_input for value in (target, oracle, anti)):
            raise ValueError("ANCHOR companion scorer-only text leaked into answer input")
        instances.append(replace(instance, current_request=current_request))
        labels[instance.instance_ref] = DelayedSeedLabel(
            instance_ref=instance.instance_ref,
            pattern_ref=instance.pattern_ref,
            target_behavior=target,
        )
        counterfactuals[instance.instance_ref] = DelayedSeedCounterfactual(
            instance_ref=instance.instance_ref,
            oracle_disposition=oracle,
            anti_disposition=anti,
        )

    return replace(
        base,
        instances=tuple(instances),
        labels=labels,
        counterfactuals=counterfactuals,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        benchmark=_nonblank(raw.get("benchmark"), "benchmark"),
    )


def _nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")
    return value.strip()


def _sha256_text(value: object, name: str) -> str:
    text = _nonblank(value, name)
    if len(text) != 64:
        raise ValueError(f"{name} must be a SHA-256 digest")
    try:
        int(text, 16)
    except ValueError as error:
        raise ValueError(f"{name} must be a SHA-256 digest") from error
    return text
