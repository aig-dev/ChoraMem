from __future__ import annotations

import pytest

from memory_core_worker.disposition_materials import (
    build_disposition_formation_input,
    disposition_formation_material,
)
from memory_core_worker.v1 import inference_pb2


def _request() -> inference_pb2.ProcessConsolidationWindowRequest:
    request = inference_pb2.ProcessConsolidationWindowRequest(
        job_ref="job-paired-formation",
        window_text=(
            "CONSTITUTION\n"
            "MEMORY_REF constitution-private-ref\n"
            "> You are a calm practical companion.\n"
            "END_CONSTITUTION\n\n"
            "EPISODE episode-current\nSESSION session-current\n"
            "SITUATION\nACTOR user user-private\nSOURCE source-current-user\n"
            "Everything is colliding and I cannot decide where to start.\n"
            "AGENT_ACT\nACTOR agent agent-private\nSOURCE source-current-agent\n"
            "Which unfinished item would have the biggest consequence?\n\n"
            "RELATED_EPISODES\n"
            "RELATED_EPISODE episode-related\nSESSION session-related\n"
            "SITUATION\nACTOR user user-private\nSOURCE source-related-user\n"
            "The launch list is making my thoughts scatter.\n"
            "AGENT_ACT\nACTOR agent agent-private\nSOURCE source-related-agent\n"
            "Which blocker holds up the most other work?\n\n"
            "ELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\nAPPLICATION RELATION\n\n"
            "OUTCOME outcome-current\nOUTCOME_EPISODE episode-current\n"
            "ACTOR user user-private\nThat single question helped me think again.\n\n"
            "OUTCOME outcome-related\nOUTCOME_EPISODE episode-related\n"
            "ACTOR user user-private\nNaming one blocker immediately cleared the noise.\n\n"
            "ACTIVE_DISPOSITION_HINT seed-existing@1\nAPPLICATION RELATION\n"
            "TEXT When the user is tired, the Agent shortens its response.\n"
        ),
        allowed_target_refs=["NEW_RECOLLECTION", "NEW_DISPOSITION"],
        allowed_basis_refs=[
            "episode-current",
            "episode-related",
            "outcome-current",
            "outcome-related",
        ],
    )
    current = request.evidence.episodes.add(
        episode_ref="episode-current",
        session_ref="session-current",
        origin="current",
        formation_role="anchor",
    )
    current.sources.add(
        source_ref="source-current-user",
        role="situation",
        actor_kind="user",
        actor_ref="user-private",
        text="Everything is colliding and I cannot decide where to start.",
    )
    current.sources.add(
        source_ref="source-current-agent",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-private",
        text="Which unfinished item would have the biggest consequence?",
    )
    related = request.evidence.episodes.add(
        episode_ref="episode-related",
        session_ref="session-related",
        origin="related",
        formation_role="candidate",
    )
    related.sources.add(
        source_ref="source-related-user",
        role="situation",
        actor_kind="user",
        actor_ref="user-private",
        text="The launch list is making my thoughts scatter.",
    )
    related.sources.add(
        source_ref="source-related-agent",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-private",
        text="Which blocker holds up the most other work?",
    )
    request.evidence.outcomes.add(
        outcome_ref="outcome-current",
        episode_ref="episode-current",
        actor_kind="user",
        actor_ref="user-private",
        text="That single question helped me think again.",
    )
    request.evidence.outcomes.add(
        outcome_ref="outcome-related",
        episode_ref="episode-related",
        actor_kind="user",
        actor_ref="user-private",
        text="Naming one blocker immediately cleared the noise.",
    )
    return request


def test_paired_material_hides_refs_and_binds_core_owned_protocol() -> None:
    material = disposition_formation_material(_request())

    assert material is not None
    assert material.application == "RELATION"
    assert material.basis_refs == (
        "episode-current",
        "outcome-current",
        "episode-related",
        "outcome-related",
    )
    model_input = build_disposition_formation_input(material)
    assert "You are a calm practical companion." in model_input
    assert "Everything is colliding" in model_input
    assert "Which unfinished item" in model_input
    assert "That single question helped" in model_input
    assert "When the user is tired, the Agent shortens its response." in model_input
    for hidden_ref in (
        "job-paired-formation",
        "constitution-private-ref",
        "episode-current",
        "episode-related",
        "outcome-current",
        "outcome-related",
        "source-current-user",
        "source-current-agent",
        "user-private",
        "agent-private",
        "seed-existing@1",
    ):
        assert hidden_ref not in model_input

    assert material.bind_disposition(
        "When the user is overloaded, the Agent asks one prioritizing question first."
    ) == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user is overloaded, the Agent asks one prioritizing question first.\n"
        "BASIS\nepisode-current\noutcome-current\nepisode-related\noutcome-related"
    )


def test_focused_rules_keep_fact_confirmation_in_recollection() -> None:
    material = disposition_formation_material(_request())
    assert material is not None

    model_input = build_disposition_formation_input(material)

    assert "durable facts belong in recollection" in model_input.lower()
    assert "stores, repeats, paraphrases, or confirms" in model_input
    assert "output NO_CHANGE" in model_input


def test_focused_rules_do_not_invert_repeated_failure_into_a_new_seed() -> None:
    material = disposition_formation_material(_request())
    assert material is not None

    model_input = build_disposition_formation_input(material)

    assert "Failure is not a new opposite tendency in V1" in model_input
    assert "never invert the failed response" in model_input.lower()


def test_paired_material_ignores_unmarked_mixed_window_evidence() -> None:
    request = _request()
    noise = request.evidence.episodes.add(
        episode_ref="episode-noise",
        session_ref="session-noise",
        origin="current",
    )
    noise.sources.add(
        source_ref="source-noise-user",
        role="situation",
        actor_kind="user",
        actor_ref="user-private",
        text="I bought a new lamp.",
    )
    noise.sources.add(
        source_ref="source-noise-agent",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-private",
        text="Warm light can make the room calmer.",
    )
    request.evidence.outcomes.add(
        outcome_ref="outcome-noise",
        episode_ref="episode-noise",
        actor_kind="user",
        actor_ref="user-private",
        text="The room looks brighter.",
    )
    request.allowed_basis_refs.extend(("episode-noise", "outcome-noise"))

    material = disposition_formation_material(request)

    assert material is not None
    assert len(material.experiences) == 2
    assert material.basis_refs == (
        "episode-current",
        "outcome-current",
        "episode-related",
        "outcome-related",
    )
    model_input = build_disposition_formation_input(material)
    assert "I bought a new lamp" not in model_input
    assert "The room looks brighter" not in model_input


def test_paired_material_accepts_cross_session_candidate_carried_by_overlap() -> None:
    request = _request()
    request.evidence.episodes[1].origin = "current"

    material = disposition_formation_material(request)

    assert material is not None
    assert len(material.experiences) == 2


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request.evidence.outcomes.pop(),
        lambda request: setattr(
            request.evidence.episodes[1], "session_ref", "session-current"
        ),
        lambda request: setattr(
            request.evidence.episodes[1], "formation_role", ""
        ),
        lambda request: setattr(
            request.evidence.episodes[1], "origin", "revision_anchor"
        ),
        lambda request: setattr(
            request.evidence.outcomes[1], "actor_kind", "agent"
        ),
        lambda request: request.allowed_target_refs.remove("NEW_DISPOSITION"),
        lambda request: request.allowed_basis_refs.remove("outcome-related"),
    ],
)
def test_paired_material_declines_incomplete_evidence(
    mutate: object,
) -> None:
    request = _request()
    mutate(request)  # type: ignore[operator]

    assert disposition_formation_material(request) is None


def test_paired_material_survives_structural_direct_authority_offer() -> None:
    request = _request()
    request.window_text += (
        "\nELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\nAPPLICATION RELATION\n"
        "DIRECT_EPISODE episode-current\n"
    )

    assert disposition_formation_material(request) is not None


@pytest.mark.parametrize(
    "body",
    ["", "   ", "TARGET", "NO_CHANGE\ntext", "line one\nline two", "x" * 4097],
)
def test_bind_disposition_rejects_non_plain_or_oversized_output(body: str) -> None:
    material = disposition_formation_material(_request())
    assert material is not None

    with pytest.raises((TypeError, ValueError)):
        material.bind_disposition(body)


def test_bind_disposition_maps_exact_no_change_to_empty_result() -> None:
    material = disposition_formation_material(_request())
    assert material is not None

    assert material.bind_disposition("NO_CHANGE") == ""
