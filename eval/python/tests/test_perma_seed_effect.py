from memory_core import memory_pb2 as pb

from memory_core_eval.perma_seed_data import PERSONA_SPLITS
from memory_core_eval.perma_seed_effect import (
    SeedEffectMode,
    build_seed_contexts,
    effect_mode_order,
    extract_option_token,
    summarize_seed_effect,
)


def _memory(kind, text, ref):
    return kind(
        memory_ref=ref,
        text=text,
        application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
    )


def test_three_arms_filter_one_select_without_leaking_seed_into_recollection():
    selected = pb.MemoryContext(
        recollections=[_memory(pb.Recollection, "remembers tea", "rec@1")],
        dispositions=[_memory(pb.Disposition, "offer tea before advice", "seed@1")],
        episode_evidence=[pb.EpisodeEvidence(memory_ref="episode-1", text="raw")],
    )

    contexts = build_seed_contexts(
        selected,
        max_tokens=2_048,
        token_count=lambda text: len(text.split()),
    )

    assert tuple(contexts) == (
        SeedEffectMode.NONE,
        SeedEffectMode.RECOLLECTION_ONLY,
        SeedEffectMode.SEED_ENABLED,
    )
    assert contexts[SeedEffectMode.NONE].text == ""
    assert contexts[SeedEffectMode.RECOLLECTION_ONLY].memory_refs == ("rec@1",)
    assert contexts[SeedEffectMode.RECOLLECTION_ONLY].disposition_refs == ()
    assert "offer tea" not in contexts[SeedEffectMode.RECOLLECTION_ONLY].text
    assert contexts[SeedEffectMode.SEED_ENABLED].memory_refs == ("rec@1", "seed@1")
    assert contexts[SeedEffectMode.SEED_ENABLED].disposition_refs == ("seed@1",)
    assert "raw" not in contexts[SeedEffectMode.SEED_ENABLED].text
    assert selected.episode_evidence[0].memory_ref == "episode-1"


def test_arm_order_and_option_parser_are_strict_and_deterministic():
    assert effect_mode_order("1377:SD-Books-task-3:2") == (
        SeedEffectMode.RECOLLECTION_ONLY,
        SeedEffectMode.SEED_ENABLED,
        SeedEffectMode.NONE,
    )
    assert extract_option_token(" B \n") == "B"
    for invalid in ("", "option B", "B.", "A B", "1"):
        assert extract_option_token(invalid) == ""


def _complete_results(*, h2_improves: bool = True):
    results = []
    for split, personas in PERSONA_SPLITS.items():
        for persona_id in personas:
            for offset in range(20):
                stage = 2 if offset < 10 else 3
                question_ref = f"{persona_id}:task-{offset % 10}:{stage}"
                seed_correct = split == "H1" or h2_improves
                results.append({
                    "split": split,
                    "persona_id": persona_id,
                    "question_ref": question_ref,
                    "stage": stage,
                    "answers": [
                        {"mode": "none", "correct": False, "error": ""},
                        {
                            "mode": "recollection_only",
                            "correct": False,
                            "error": "",
                        },
                        {
                            "mode": "seed_enabled",
                            "correct": seed_correct,
                            "error": "",
                            "rendered_disposition_refs": ["seed@1"],
                        },
                    ],
                })
    return results


def test_summary_passes_only_when_both_unseen_splits_and_pooled_ci_are_positive():
    summary = summarize_seed_effect(_complete_results(), bootstrap_samples=200)

    assert summary["questions"] == 100
    assert summary["splits"]["H1"]["seed-minus-recollection"] == 1.0
    assert summary["splits"]["H2"]["seed-minus-recollection"] == 1.0
    assert summary["paired"]["seed-minus-recollection"]["ci95"] == [1.0, 1.0]
    assert summary["net_correct_flips"] == {"H1": 40, "H2": 60}
    assert summary["decision"] == {"passed": True, "reasons": []}

    failed = summarize_seed_effect(
        _complete_results(h2_improves=False), bootstrap_samples=200
    )
    assert failed["splits"]["H2"]["seed-minus-recollection"] == 0.0
    assert failed["decision"]["passed"] is False
    assert "split H2 did not improve" in failed["decision"]["reasons"]
