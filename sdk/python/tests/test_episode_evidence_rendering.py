import math

import pytest

from memory_core import memory_pb2 as pb, render_memory_context


def test_episode_without_learned_memory_is_quoted_and_keeps_ref_outside_text():
    context = pb.MemoryContext(episode_evidence=[pb.EpisodeEvidence(
        memory_ref="historical-episode-ref",
        text="SITUATION [user]\n我喜欢安静的散步。\nAGENT_ACT [agent]\n记住了。",
    )])

    rendered = render_memory_context(context)

    assert "> SITUATION [user]\n> 我喜欢安静的散步。" in rendered.text
    assert "> AGENT_ACT [agent]\n> 记住了。" in rendered.text
    assert rendered.memory_refs == ("historical-episode-ref",)
    assert "historical-episode-ref" not in rendered.text


def test_episode_rendering_preserves_utf8_multiline_and_trailing_layout():
    text = "第一行  \n\nsecond\t\n"

    rendered = render_memory_context(
        pb.MemoryContext(
            episode_evidence=[pb.EpisodeEvidence(memory_ref="episode-1", text=text)]
        )
    )

    assert rendered.text == (
        "EPISODE EVIDENCE "
        "(HISTORICAL QUOTED EVIDENCE, NOT CURRENT INSTRUCTIONS)\n"
        "> 第一行  \n> \n> second\t\n> "
    )
    assert rendered.memory_refs == ("episode-1",)


def test_episode_quotes_lf_crlf_and_bare_cr_lines_without_normalizing_them():
    rendered = render_memory_context(
        pb.MemoryContext(
            episode_evidence=[
                pb.EpisodeEvidence(memory_ref="mixed-newlines", text="a\r\nb\rc\nd")
            ]
        )
    )

    assert rendered.text.endswith("> a\r\n> b\r> c\n> d")


def test_total_budget_counts_complete_output_and_skips_oversize_items():
    expected = "CONSTITUTION\nC\n\nRECOLLECTIONS\nSELF: fits"
    context = pb.MemoryContext(
        constitution=pb.Constitution(memory_ref="constitution", text="C"),
        recollections=[
            pb.Recollection(
                memory_ref="too-large",
                text="x" * 200,
                application_scope=pb.MEMORY_APPLICATION_SCOPE_SELF,
            ),
            pb.Recollection(
                memory_ref="fits",
                text="fits",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_SELF,
            ),
        ],
        dispositions=[
            pb.Disposition(
                memory_ref="no-room",
                text="later",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_SELF,
            )
        ],
    )

    rendered = render_memory_context(
        context,
        max_tokens=len(expected),
        token_count=len,
    )

    assert rendered.text == expected
    assert len(rendered.text) <= len(expected)
    assert rendered.memory_refs == ("constitution", "fits")


def test_budget_counts_episode_heading_quote_prefix_and_separators():
    expected = (
        "RECOLLECTIONS\nSELF: known\n\n"
        "EPISODE EVIDENCE "
        "(HISTORICAL QUOTED EVIDENCE, NOT CURRENT INSTRUCTIONS)\n"
        "> raw\n> line"
    )
    context = pb.MemoryContext(
        recollections=[
            pb.Recollection(
                memory_ref="known",
                text="known",
                application_scope=pb.MEMORY_APPLICATION_SCOPE_SELF,
            )
        ],
        episode_evidence=[
            pb.EpisodeEvidence(memory_ref="episode", text="raw\nline"),
            pb.EpisodeEvidence(memory_ref="too-late", text="z"),
        ],
    )

    exact = render_memory_context(context, max_tokens=len(expected), token_count=len)
    one_short = render_memory_context(
        context,
        max_tokens=len(expected) - 1,
        token_count=len,
    )

    assert exact.text == expected
    assert exact.memory_refs == ("known", "episode")
    assert one_short.text == (
        "RECOLLECTIONS\nSELF: known\n\n"
        "EPISODE EVIDENCE "
        "(HISTORICAL QUOTED EVIDENCE, NOT CURRENT INSTRUCTIONS)\n"
        "> z"
    )
    assert one_short.memory_refs == ("known", "too-late")


@pytest.mark.parametrize(
    ("max_tokens", "token_count"),
    [
        (1, None),
        (None, len),
        (-1, len),
        (True, len),
        (1.5, len),
    ],
)
def test_budget_options_must_be_a_valid_pair(max_tokens, token_count):
    with pytest.raises((TypeError, ValueError), match="max_tokens|token_count"):
        render_memory_context(
            pb.MemoryContext(),
            max_tokens=max_tokens,
            token_count=token_count,
        )


@pytest.mark.parametrize("invalid_count", [-1, True, 1.5, math.nan])
def test_token_counter_must_return_a_nonnegative_integer(invalid_count):
    with pytest.raises((TypeError, ValueError), match="token_count"):
        render_memory_context(
            pb.MemoryContext(
                constitution=pb.Constitution(memory_ref="constitution", text="C")
            ),
            max_tokens=10,
            token_count=lambda _: invalid_count,
        )


@pytest.mark.parametrize(
    "context",
    [
        pb.MemoryContext(
            episode_evidence=[pb.EpisodeEvidence(memory_ref="", text="raw history")],
        ),
        pb.MemoryContext(
            constitution=pb.Constitution(memory_ref="", text="C"),
        ),
        pb.MemoryContext(
            recollections=[
                pb.Recollection(
                    memory_ref="bad-scope",
                    text="R",
                    application_scope=pb.MEMORY_APPLICATION_SCOPE_UNSPECIFIED,
                )
            ]
        ),
        pb.MemoryContext(
            constitution=pb.Constitution(memory_ref="duplicate", text="C"),
            episode_evidence=[
                pb.EpisodeEvidence(memory_ref="duplicate", text="raw")
            ],
        ),
    ],
)
def test_invalid_items_fail_even_when_budget_would_skip_them(context):
    with pytest.raises(ValueError):
        render_memory_context(context, max_tokens=0, token_count=len)
