from __future__ import annotations

from memory_core import memory_pb2 as pb

from memory_core_eval.personamem_data import History
from memory_core_eval.reference import HistoricalTurn


def episode(ref: str, text: str):
    from memory_core_eval.personamem_effect import EpisodeDocument

    return EpisodeDocument(memory_ref=ref, text=text)


def test_effect_modes_build_six_isolated_contexts_and_only_core_is_budgeted():
    from memory_core_eval.personamem_effect import EffectMode, build_effect_context

    assert [mode.value for mode in EffectMode] == [
        "none",
        "full_history",
        "oracle_episode",
        "semantic_top40",
        "current_core",
        "learned_core",
    ]
    history = History(
        "shared profile, supplied outside memory context",
        (
            HistoricalTurn("old user one", "old assistant one"),
            HistoricalTurn("old user two", "old assistant two"),
        ),
        "unanswered tail",
    )
    oracle = (episode("oracle-1", "ORACLE CLUE"),)
    semantic = (
        episode("semantic-2", "SECOND SEMANTIC CLUE"),
        episode("semantic-1", "FIRST SEMANTIC CLUE"),
    )
    core = pb.MemoryContext(
        recollections=[pb.Recollection(
            memory_ref="recollection-1",
            text="LEARNED FACT",
            application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER,
        )],
        dispositions=[pb.Disposition(
            memory_ref="disposition-1",
            text="LEARNED TENDENCY",
            application_scope=pb.MEMORY_APPLICATION_SCOPE_RELATION,
        )],
        episode_evidence=[pb.EpisodeEvidence(
            memory_ref="core-episode-1",
            text="CORE RAW CLUE",
        )],
    )

    contexts = {
        mode: build_effect_context(
            mode,
            history=history,
            oracle_episodes=oracle,
            semantic_episodes=semantic,
            core_context=core,
            token_count=len,
            core_memory_tokens=10_000,
        )
        for mode in EffectMode
    }

    assert contexts[EffectMode.NONE].text == ""
    assert contexts[EffectMode.NONE].memory_refs == ()

    full = contexts[EffectMode.FULL_HISTORY]
    assert "old user one" in full.text and "old assistant two" in full.text
    assert "unanswered tail" in full.text
    assert "shared profile" not in full.text
    assert "ORACLE CLUE" not in full.text and "LEARNED FACT" not in full.text

    oracle_context = contexts[EffectMode.ORACLE_EPISODE]
    assert "ORACLE CLUE" in oracle_context.text
    assert "SECOND SEMANTIC CLUE" not in oracle_context.text
    assert oracle_context.memory_refs == ("oracle-1",)

    semantic_context = contexts[EffectMode.SEMANTIC_TOP40]
    assert semantic_context.text.index("SECOND SEMANTIC CLUE") < semantic_context.text.index(
        "FIRST SEMANTIC CLUE"
    )
    assert semantic_context.memory_refs == ("semantic-2", "semantic-1")

    current = contexts[EffectMode.CURRENT_CORE]
    assert "LEARNED FACT" in current.text
    assert "LEARNED TENDENCY" in current.text
    assert "CORE RAW CLUE" in current.text
    assert current.selected_episode_refs == ("core-episode-1",)
    assert current.delivered_episode_refs == ("core-episode-1",)

    learned = contexts[EffectMode.LEARNED_CORE]
    assert "LEARNED FACT" in learned.text and "LEARNED TENDENCY" in learned.text
    assert "CORE RAW CLUE" not in learned.text
    assert learned.selected_episode_refs == learned.delivered_episode_refs == ()


def test_semantic_top40_is_unbounded_but_core_tracks_budget_loss_exactly():
    from memory_core_eval.personamem_effect import EffectMode, build_effect_context

    long_text = "x" * 200
    semantic = build_effect_context(
        EffectMode.SEMANTIC_TOP40,
        semantic_episodes=(episode("semantic-long", long_text),),
        token_count=len,
        core_memory_tokens=1,
    )
    assert long_text in semantic.text
    assert semantic.selected_episode_refs == semantic.delivered_episode_refs == (
        "semantic-long",
    )

    core = pb.MemoryContext(
        recollections=[pb.Recollection(
            memory_ref="small",
            text="ok",
            application_scope=pb.MEMORY_APPLICATION_SCOPE_OTHER,
        )],
        episode_evidence=[pb.EpisodeEvidence(
            memory_ref="too-large",
            text=long_text,
        )],
    )
    bounded = build_effect_context(
        EffectMode.CURRENT_CORE,
        core_context=core,
        token_count=len,
        core_memory_tokens=32,
    )
    assert bounded.selected_episode_refs == ("too-large",)
    assert bounded.delivered_episode_refs == ()
    assert bounded.memory_refs == ("small",)
    assert long_text not in bounded.text
