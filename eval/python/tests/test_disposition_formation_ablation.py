from __future__ import annotations

import json

import pytest


def _omnibus_input(*, episode_count: int = 3, outcome_count: int = 3) -> str:
    episodes = []
    for index in range(episode_count):
        episodes.append(
            "\n".join(
                (
                    f"EPISODE episode-{index}",
                    f"SESSION session-{index}",
                    "AGENT_ACT",
                    "ACTOR agent agent-1",
                    f"SOURCE act-{index}",
                    "Ask one prioritizing question.",
                    "SITUATION",
                    "ACTOR user user-1",
                    f"SOURCE situation-{index}",
                    "Everything feels equally urgent.",
                )
            )
        )
    outcomes = []
    for index in range(outcome_count):
        outcomes.append(
            "\n".join(
                (
                    f"OUTCOME outcome-{index}",
                    f"OUTCOME_EPISODE episode-{index}",
                    "ACTOR user user-1",
                    "That one question helped me choose.",
                )
            )
        )
    window = "\n\n".join(
        (
            "CONSTITUTION\nUNKNOWN",
            *episodes,
            "ELIGIBLE_NEW_RECOLLECTION NEW_RECOLLECTION\nAPPLICATIONS SELF OTHER RELATION SITUATION",
            "ELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\nAPPLICATION RELATION",
            "ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\nAPPLICATION RELATION\nDIRECT_EPISODE episode-0",
            "ELIGIBLE_RECOLLECTION recollection-1@1\nAPPLICATION OTHER\nTEXT A durable fact.",
            *outcomes,
        )
    )
    allowed_basis = []
    for ref in (
        *(f"episode-{index}" for index in range(episode_count)),
        *(f"outcome-{index}" for index in range(outcome_count)),
    ):
        allowed_basis.extend(("ALLOWED_BASIS", ref))
    return "\n".join(
        (
            "You are Memory Core's single background consolidation Worker.",
            "SECRET OMNIBUS RULES",
            "ALLOWED_TARGETS_BEGIN",
            "ALLOWED_TARGET",
            "NEW_RECOLLECTION",
            "ALLOWED_TARGET",
            "NEW_DISPOSITION",
            "ALLOWED_TARGETS_END",
            "ALLOWED_BASIS_BEGIN",
            *allowed_basis,
            "ALLOWED_BASIS_END",
            "WINDOW_BEGIN",
            window,
            "WINDOW_END",
            "SECRET FINAL CHECK",
        )
    )


def test_focused_prompt_contains_only_constitution_episode_and_outcome_evidence():
    """Catches task leakage from the omnibus prompt into the focused arm."""
    from memory_core_eval.disposition_formation_ablation import (
        build_focused_disposition_input,
    )

    focused = build_focused_disposition_input(_omnibus_input())

    assert "CONSTITUTION\nUNKNOWN" in focused
    assert focused.count("\nEPISODE ") == 3
    assert focused.count("\nOUTCOME ") == 3
    assert "ELIGIBLE_NEW_DISPOSITION" not in focused
    assert "NEW_RECOLLECTION" not in focused
    assert "ELIGIBLE_RECOLLECTION" not in focused
    assert "DIRECT_EPISODE" not in focused
    assert "SECRET OMNIBUS RULES" not in focused
    assert "SECRET FINAL CHECK" not in focused
    assert "TARGET" not in focused
    assert "BASIS" not in focused


def test_select_final_windows_keeps_each_three_episode_owner_and_excludes_other_calls():
    """Catches early windows or Recollection fallback calls entering the ablation."""
    from memory_core_eval.disposition_formation_ablation import (
        select_final_formation_inputs,
    )

    final_one = _omnibus_input()
    final_two = _omnibus_input().replace("user-1", "user-2")
    records = [
        {"input": _omnibus_input(episode_count=1, outcome_count=0), "output": "NO_CHANGE"},
        {"input": "FACT-ONLY RECOLLECTION FALLBACK\nCURRENT_USER_SOURCES_BEGIN", "output": "NO_MEMORY"},
        {"input": final_one, "output": "NO_CHANGE"},
        {"input": final_two, "output": "NO_CHANGE"},
    ]

    selected = select_final_formation_inputs(records)

    assert [item.model_input for item in selected] == [final_one, final_two]
    assert len({item.input_sha256 for item in selected}) == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NO_CHANGE", None),
        (
            "When the user is overloaded, the agent asks one prioritizing question before advice.",
            "When the user is overloaded, the agent asks one prioritizing question before advice.",
        ),
    ],
)
def test_parse_focused_output_accepts_only_no_change_or_one_plain_text_line(raw, expected):
    """Catches protocol/JSON work creeping back into the focused model job."""
    from memory_core_eval.disposition_formation_ablation import parse_focused_output

    assert parse_focused_output(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "TARGET\nNEW_DISPOSITION",
        '{"tendency": "ask one question"}',
        "A tendency\nA second tendency",
        "NO_CHANGE\nA tendency",
        "",
    ],
)
def test_parse_focused_output_rejects_non_plain_or_multiple_results(raw):
    """Catches malformed output being counted as successful formation."""
    from memory_core_eval.disposition_formation_ablation import parse_focused_output

    with pytest.raises(ValueError, match="plain disposition output"):
        parse_focused_output(raw)


def test_jsonl_loader_rejects_incomplete_records(tmp_path):
    """Catches silently treating interrupted model calls as experimental evidence."""
    from memory_core_eval.disposition_formation_ablation import load_window_records

    path = tmp_path / "windows.jsonl"
    path.write_text(
        json.dumps({"input": _omnibus_input(), "output": "NO_CHANGE"})
        + "\n"
        + json.dumps({"input": _omnibus_input()})
        + "\n"
    )

    with pytest.raises(ValueError, match="completed input/output"):
        load_window_records(path)


def test_parse_omnibus_output_extracts_only_a_complete_new_disposition():
    """Catches prose or a partial tagged block being counted as formation."""
    from memory_core_eval.disposition_formation_ablation import (
        parse_omnibus_disposition_output,
    )

    text = "\n".join(
        (
            "TARGET",
            "NEW_DISPOSITION",
            "APPLICATION",
            "RELATION",
            "CHANGE",
            "TEXT",
            "When the user is overloaded, the agent asks one question.",
            "BASIS",
            "episode-0",
            "episode-1",
        )
    )
    assert parse_omnibus_disposition_output(text) == (
        "When the user is overloaded, the agent asks one question."
    )
    assert parse_omnibus_disposition_output("NO_CHANGE") is None
    assert parse_omnibus_disposition_output(
        "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nA fact.\nBASIS\nepisode-0"
    ) is None
    with pytest.raises(ValueError, match="complete NEW_DISPOSITION"):
        parse_omnibus_disposition_output("TARGET\nNEW_DISPOSITION\nCHANGE\nTEXT")


@pytest.mark.asyncio
async def test_ablation_interleaves_both_arms_and_resumes_without_model_calls(tmp_path):
    """Catches arm-order bias or paid calls being repeated after interruption."""
    from memory_core_eval.disposition_formation_ablation import (
        FormationInput,
        run_formation_ablation,
    )

    inputs = (_omnibus_input(), _omnibus_input().replace("user-1", "user-2"))
    cases = tuple(
        FormationInput(
            model_input=value,
            input_sha256=__import__("hashlib").sha256(value.encode()).hexdigest(),
        )
        for value in inputs
    )

    class Model:
        def __init__(self):
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["input_text"].startswith("You are a background memory text processor."):
                return "When the user is overloaded, the agent asks one prioritizing question."
            return "NO_CHANGE"

    output = tmp_path / "results.jsonl"
    model = Model()
    summary = await run_formation_ablation(
        cases=cases,
        pattern_refs={cases[0].input_sha256: "pattern-a", cases[1].input_sha256: "pattern-b"},
        model=model,
        trials=2,
        results_path=output,
    )

    assert len(model.calls) == 8
    assert all(call["instructions"] == "" for call in model.calls)
    assert all(call["max_output_tokens"] == 8_192 for call in model.calls)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert {(item["arm"], item["trial"], item["case_index"]) for item in records} == {
        (arm, trial, case)
        for arm in ("omnibus", "focused")
        for trial in range(2)
        for case in range(2)
    }
    assert summary["arms"]["omnibus"]["formed"] == 0
    assert summary["arms"]["focused"]["formed"] == 4
    assert summary["arms"]["focused"]["stable_decision_cases"] == 2

    class MustNotRun:
        async def complete(self, **_kwargs):
            raise AssertionError("completed ablation records must resume without model calls")

    resumed = await run_formation_ablation(
        cases=cases,
        pattern_refs={cases[0].input_sha256: "pattern-a", cases[1].input_sha256: "pattern-b"},
        model=MustNotRun(),
        trials=2,
        results_path=output,
    )
    assert resumed == summary


def test_paired_formation_job_joins_each_episode_to_its_outcome_without_ids():
    """Catches separated evidence or provider-visible provenance creeping back in."""
    from memory_core_eval.disposition_formation_ablation import (
        prepare_paired_disposition_formation,
    )

    job = prepare_paired_disposition_formation(_omnibus_input())

    assert job is not None
    assert job.application == "RELATION"
    assert job.basis_refs == (
        "episode-0",
        "episode-1",
        "episode-2",
        "outcome-0",
        "outcome-1",
        "outcome-2",
    )
    assert job.model_input.count("\nEXPERIENCE\n") == 3
    assert job.model_input.count("\nEXPERIENCE_END") == 3
    assert "Everything feels equally urgent." in job.model_input
    assert "Ask one prioritizing question." in job.model_input
    assert "That one question helped me choose." in job.model_input
    assert "episode-0" not in job.model_input
    assert "outcome-0" not in job.model_input
    assert "ELIGIBLE_" not in job.model_input
    assert "ALLOWED_" not in job.model_input


def test_paired_formation_job_requires_complete_cross_session_causal_pairs():
    """Catches the focused path bypassing deterministic causal eligibility."""
    from memory_core_eval.disposition_formation_ablation import (
        prepare_paired_disposition_formation,
    )

    assert prepare_paired_disposition_formation(
        _omnibus_input(outcome_count=2)
    ) is None


def test_paired_formation_job_includes_existing_disposition_text_without_ref():
    """Catches focused formation losing the semantic input needed for deduplication."""
    from memory_core_eval.disposition_formation_ablation import (
        prepare_paired_disposition_formation,
    )

    existing_text = (
        "When the user is overloaded, the Agent asks for the single most "
        "consequential blocker."
    )
    omnibus = _omnibus_input().replace(
        "ELIGIBLE_RECOLLECTION recollection-1@1",
        "ELIGIBLE_ADAPTATION seed-existing@1\n"
        "APPLICATION RELATION\n"
        f"TEXT {existing_text}\n"
        "DIRECT_EPISODE episode-2\n\n"
        "ELIGIBLE_RECOLLECTION recollection-1@1",
    )

    job = prepare_paired_disposition_formation(omnibus)

    assert job is not None
    assert existing_text in job.model_input
    assert "EXISTING_DISPOSITIONS_BEGIN" in job.model_input
    assert "seed-existing@1" not in job.model_input
    assert prepare_paired_disposition_formation(
        _omnibus_input().replace("SESSION session-1", "SESSION session-0").replace(
            "SESSION session-2", "SESSION session-0"
        )
    ) is None
    assert prepare_paired_disposition_formation(
        _omnibus_input().replace(
            "ELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION", "NO_DISPOSITION_FORMATION"
        )
    ) is None


@pytest.mark.asyncio
async def test_paired_formation_model_renders_core_owned_target_application_and_basis():
    """Catches asking the provider to reproduce IDs or tagged write protocol."""
    from memory_core_eval.disposition_formation_ablation import (
        PairedDispositionFormationModel,
    )

    class Model:
        def __init__(self):
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            return "When the user is overloaded, the Agent asks one prioritizing question."

    delegate = Model()
    model = PairedDispositionFormationModel(delegate)

    result = await model.complete(
        instructions="",
        input_text=_omnibus_input(),
        max_output_tokens=8192,
    )

    assert len(delegate.calls) == 1
    assert delegate.calls[0]["instructions"] == ""
    assert delegate.calls[0]["max_output_tokens"] == 8192
    assert delegate.calls[0]["input_text"].startswith(
        "You are a background memory text processor."
    )
    assert result == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user is overloaded, the Agent asks one prioritizing question.\n"
        "BASIS\nepisode-0\nepisode-1\nepisode-2\n"
        "outcome-0\noutcome-1\noutcome-2"
    )


@pytest.mark.asyncio
async def test_paired_formation_model_preserves_no_change_and_delegates_other_jobs():
    """Catches focused formation changing unrelated consolidation behavior."""
    from memory_core_eval.disposition_formation_ablation import (
        PairedDispositionFormationModel,
    )

    class Model:
        def __init__(self):
            self.calls = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["input_text"].startswith(
                "You are a background memory text processor."
            ):
                return "NO_CHANGE"
            return "ORIGINAL_WORKER_RESULT"

    delegate = Model()
    model = PairedDispositionFormationModel(delegate)

    assert await model.complete(
        instructions="", input_text=_omnibus_input(), max_output_tokens=8192
    ) == "NO_CHANGE"
    unrelated = "FACT-ONLY RECOLLECTION FALLBACK\nCURRENT_USER_SOURCES_BEGIN"
    assert await model.complete(
        instructions="", input_text=unrelated, max_output_tokens=8192
    ) == "ORIGINAL_WORKER_RESULT"
    assert delegate.calls[-1]["input_text"] == unrelated
