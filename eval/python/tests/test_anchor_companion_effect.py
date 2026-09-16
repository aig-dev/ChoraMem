from __future__ import annotations


def _summary(*, invalid_last: bool, learned_last: str = "rag") -> dict[str, object]:
    rows = []
    for index in range(4):
        manipulable = not (invalid_last and index == 3)
        rows.append(
            {
                "instance_ref": f"case-{index}",
                "formed": True,
                "selected": True,
                "rendered": True,
                "consensus": {
                    "oracle_seed_vs_anti_seed": "oracle_seed",
                    "oracle_seed_vs_rag": (
                        "oracle_seed" if manipulable else "inconsistent"
                    ),
                    "learned_seed_vs_rag": (
                        "learned_seed" if index < 3 else learned_last
                    ),
                },
            }
        )
    return {
        "negative_false_positives": 0,
        "positive": {
            "funnel": {"formed": 4, "selected": 4, "rendered": 4},
            "rows": rows,
        },
    }


def test_anchor_companion_withholds_architecture_verdict_for_any_ceiling_case():
    from memory_core_eval.anchor_companion_effect import summarize_anchor_companion

    result = summarize_anchor_companion(_summary(invalid_last=True))

    assert result["manipulable_cases"] == 3
    assert result["learned_on_manipulable"] == {
        "learned_seed": 3,
        "rag": 0,
        "tie": 0,
        "inconsistent": 0,
    }
    assert result["decision"] == {
        "status": "evaluation_ceiling",
        "bottleneck": "oracle_vs_rag",
        "architecture_verdict": False,
    }


def test_anchor_companion_calls_architecture_only_after_all_oracles_work():
    from memory_core_eval.anchor_companion_effect import summarize_anchor_companion

    result = summarize_anchor_companion(
        _summary(invalid_last=False, learned_last="rag")
    )

    assert result["manipulable_cases"] == 4
    assert result["decision"] == {
        "status": "neutral_disposition_architecture_bottleneck",
        "bottleneck": "learned_effect",
        "architecture_verdict": True,
    }


def test_anchor_companion_reports_preliminary_advantage_when_all_gates_pass():
    from memory_core_eval.anchor_companion_effect import summarize_anchor_companion

    result = summarize_anchor_companion(
        _summary(invalid_last=False, learned_last="learned_seed")
    )

    assert result["manipulable_cases"] == 4
    assert result["learned_on_manipulable"]["learned_seed"] == 4
    assert result["decision"] == {
        "status": "preliminary_learned_companion_advantage",
        "bottleneck": None,
        "architecture_verdict": False,
    }


def test_anchor_companion_stops_at_formation_before_behavior():
    from memory_core_eval.anchor_companion_effect import summarize_anchor_companion

    summary = _summary(invalid_last=False, learned_last="learned_seed")
    summary["positive"]["funnel"]["formed"] = 3  # type: ignore[index]

    result = summarize_anchor_companion(summary)

    assert result["decision"] == {
        "status": "engineering_bottleneck",
        "bottleneck": "formed",
        "architecture_verdict": False,
    }
