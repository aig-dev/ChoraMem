from __future__ import annotations

from types import SimpleNamespace

import grpc
import pytest

from memory_core_worker.model import OpenAIResponsesTextModel
from memory_core_worker.service import (
    MAX_TAGGED_TEXT_BYTES,
    InferenceService,
    build_model_input,
)
from memory_core_worker.v1 import inference_pb2, inference_pb2_grpc


class RecordingTextModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    async def complete(self, text: str) -> str:
        self.calls.append(text)
        return self.response


class SequenceTextModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, text: str) -> str:
        self.calls.append(text)
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return self.responses.pop(0)


def _typed_recollection_request() -> inference_pb2.ProcessConsolidationWindowRequest:
    request = inference_pb2.ProcessConsolidationWindowRequest(
        job_ref="job-typed-recollection",
        window_text="legacy canonical window",
        allowed_target_refs=["NEW_RECOLLECTION", "NEW_DISPOSITION"],
        allowed_basis_refs=["episode-current", "episode-related"],
    )
    current = request.evidence.episodes.add(
        episode_ref="episode-current", session_ref="session-current", origin="current"
    )
    current.sources.add(
        source_ref="source-user-current",
        role="situation",
        actor_kind="user",
        actor_ref="user-1",
        text="我长期在上海生活，也喜欢周末徒步。",
    )
    current.sources.add(
        source_ref="source-agent-current",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-1",
        text="我会记住。",
    )
    related = request.evidence.episodes.add(
        episode_ref="episode-related", session_ref="session-related", origin="related"
    )
    related.sources.add(
        source_ref="source-user-related",
        role="situation",
        actor_kind="user",
        actor_ref="user-1",
        text="我上个月搬到了上海。",
    )
    related.sources.add(
        source_ref="source-agent-related",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-1",
        text="新城市需要适应。",
    )
    request.evidence.recollections.add(
        version_ref="recollection-existing@1",
        application="other",
        text="用户已经长期住在上海。",
    )
    request.allowed_target_refs.append("recollection-existing@1")
    return request


def _paired_formation_request() -> inference_pb2.ProcessConsolidationWindowRequest:
    request = inference_pb2.ProcessConsolidationWindowRequest(
        job_ref="job-paired-service",
        window_text=(
            "CONSTITUTION\nUNKNOWN\n\n"
            "EPISODE episode-current\nSESSION session-current\n"
            "SITUATION\nACTOR user user-1\nSOURCE source-current-user\n"
            "I live in Shanghai. Today every deadline is colliding.\n"
            "AGENT_ACT\nACTOR agent agent-1\nSOURCE source-current-agent\n"
            "Which deadline has the biggest consequence?\n\n"
            "RELATED_EPISODES\n"
            "RELATED_EPISODE episode-related\nSESSION session-related\n"
            "SITUATION\nACTOR user user-1\nSOURCE source-related-user\n"
            "The launch tasks are all competing in my head.\n"
            "AGENT_ACT\nACTOR agent agent-1\nSOURCE source-related-agent\n"
            "Which item blocks the most other work?\n\n"
            "ELIGIBLE_NEW_RECOLLECTION NEW_RECOLLECTION\n"
            "APPLICATIONS SELF OTHER RELATION SITUATION\n\n"
            "ELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\nAPPLICATION RELATION\n\n"
            "OUTCOME outcome-current\nOUTCOME_EPISODE episode-current\n"
            "ACTOR user user-1\nThat one question helped.\n\n"
            "OUTCOME outcome-related\nOUTCOME_EPISODE episode-related\n"
            "ACTOR user user-1\nThat narrowed the noise immediately.\n"
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
        actor_ref="user-1",
        text="I live in Shanghai. Today every deadline is colliding.",
    )
    current.sources.add(
        source_ref="source-current-agent",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-1",
        text="Which deadline has the biggest consequence?",
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
        actor_ref="user-1",
        text="The launch tasks are all competing in my head.",
    )
    related.sources.add(
        source_ref="source-related-agent",
        role="agent_act",
        actor_kind="agent",
        actor_ref="agent-1",
        text="Which item blocks the most other work?",
    )
    request.evidence.outcomes.add(
        outcome_ref="outcome-current",
        episode_ref="episode-current",
        actor_kind="user",
        actor_ref="user-1",
        text="That one question helped.",
    )
    request.evidence.outcomes.add(
        outcome_ref="outcome-related",
        episode_ref="episode-related",
        actor_kind="user",
        actor_ref="user-1",
        text="That narrowed the noise immediately.",
    )
    return request


@pytest.mark.asyncio
async def test_typed_window_uses_plain_text_recollection_fallback_after_primary_noop() -> None:
    model = SequenceTextModel(["NO_CHANGE", "用户长期生活在上海，并喜欢周末徒步。"])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text == (
        "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "用户长期生活在上海，并喜欢周末徒步。\nBASIS\n"
        "episode-current"
    )
    assert len(model.calls) == 2
    assert "legacy canonical window" in model.calls[0]
    assert "我长期在上海生活，也喜欢周末徒步。" in model.calls[1]
    assert "episode-current" not in model.calls[1]
    assert "source-user-current" not in model.calls[1]
    assert "用户已经长期住在上海。" in model.calls[1]
    assert "recollection-existing@1" not in model.calls[1]
    assert "我会记住。" not in model.calls[1]
    assert "我上个月搬到了上海。" not in model.calls[1]
    assert "新城市需要适应。" not in model.calls[1]
    assert "A direct request to forget" in model.calls[1]
    assert "one durable meaning per line" in model.calls[1]


@pytest.mark.asyncio
async def test_worker_keeps_indirect_response_patterns_out_of_recollection() -> None:
    model = SequenceTextModel(["NO_CHANGE", "NO_MEMORY"])

    await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert "event-level report that a technique helped stays Episode evidence" in model.calls[0]
    assert "Declarative Recollection and response Disposition are not redundant" in model.calls[0]
    assert "Do not recast a current task or artifact correction" in model.calls[0]
    assert "A requested property of the Agent's output" in model.calls[0]
    assert "If it does not qualify as a Disposition" in model.calls[0]
    assert "FACT-ONLY RECOLLECTION FALLBACK" in model.calls[1]
    assert "has no authority to extract preferences" in model.calls[1]
    assert "own speech act explicitly asserts or confirms" in model.calls[1]
    assert "Requests, questions, instructions, artifact drafts" in model.calls[1]
    assert "are not factual evidence" in model.calls[1]
    assert "Process clauses independently" in model.calls[1]
    assert "must still be extracted" in model.calls[1]
    assert "I live in Paris. Could you recommend a cafe?" in model.calls[1]
    assert "A direct request to forget" in model.calls[1]
    assert "one durable meaning per line" in model.calls[1]


@pytest.mark.asyncio
async def test_worker_prompt_freezes_unambiguous_feedback_basis_contract() -> None:
    model = SequenceTextModel(["NO_CHANGE", "NO_MEMORY"])

    await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    prompt = model.calls[0]
    assert "An Outcome never authorizes ADAPT" in prompt
    assert "A matching AgentAct must produce one feedback operation" in prompt
    assert "A positive, neutral, or absent paired Outcome uses REENACT" in prompt
    assert "REENACT: cite only the bare FEEDBACK_EPISODE ref" in prompt
    assert "TEXT or INHIBIT: cite only the bare paired FEEDBACK_EPISODE and FEEDBACK_OUTCOME refs" in prompt
    assert "Never copy labels such as DIRECT_EPISODE into BASIS" in prompt
    assert "A request for help in the current situation is not ADAPT" in prompt
    assert "ADAPT has a strict durability gate" in prompt
    assert "Do not invent future scope by paraphrasing it" in prompt
    assert "LAST ADAPT GATE" in prompt
    assert "A successful Outcome, a personal event, or your inference" in prompt
    assert "Two turns about one artifact inside one SESSION" in prompt
    assert "Multiple Episodes inside one SESSION are one interaction" in prompt
    assert "Repeated current-scoped refinements across distinct tasks" in prompt
    assert "their repetition supplies the generalization" in prompt
    assert "Every formation Basis Episode must support the same whole tendency" in prompt
    assert "One inferred Disposition contains one response adjustment" in prompt
    assert "FINAL OPERATION CHECK" in prompt
    assert "'the user' in English or '用户/对方' in Chinese" in prompt
    assert "Express user state as 用户/对方" not in prompt


@pytest.mark.asyncio
async def test_worker_rechecks_eligible_feedback_when_general_pass_omits_it() -> None:
    reenact = (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\nREENACT\n"
        "BASIS\nepisode-feedback"
    )
    model = SequenceTextModel(["NO_CHANGE", reenact])
    request = inference_pb2.ProcessConsolidationWindowRequest(
        window_text=(
            "EPISODE episode-feedback\nSITUATION\nThe user is overwhelmed.\n"
            "AGENT_ACT\nLet's name the top priority first.\n\n"
            "ELIGIBLE_DISPOSITION seed-feedback@1\nAPPLICATION RELATION\n"
            "TEXT When the user is overwhelmed, name the top priority first.\n"
            "FEEDBACK_EPISODE episode-feedback\nREVISION_ANCHOR episode-anchor"
        ),
        allowed_target_refs=["seed-feedback@1"],
        allowed_basis_refs=["episode-feedback", "episode-anchor"],
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == reenact
    assert len(model.calls) == 2
    assert "feedback-only recovery pass" in model.calls[1]
    assert "NEW_RECOLLECTION" not in model.calls[1]
    assert "ALLOWED_TARGET\nseed-feedback@1" in model.calls[1]
    assert "Every block begins with TARGET on its own line" in model.calls[1]
    assert "TARGET\n<exact offered target>\nAPPLICATION" in model.calls[1]
    assert "ALLOWED_BASIS\nepisode-feedback" in model.calls[1]
    assert "ALLOWED_BASIS\nepisode-anchor" not in model.calls[1]


@pytest.mark.asyncio
async def test_worker_does_not_recheck_feedback_already_emitted() -> None:
    reenact = (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\nREENACT\n"
        "BASIS\nepisode-feedback"
    )
    model = SequenceTextModel([reenact])
    request = inference_pb2.ProcessConsolidationWindowRequest(
        window_text=(
            "ELIGIBLE_DISPOSITION seed-feedback@1\nAPPLICATION RELATION\n"
            "TEXT Help name the priority.\nFEEDBACK_EPISODE episode-feedback"
        ),
        allowed_target_refs=["seed-feedback@1"],
        allowed_basis_refs=["episode-feedback"],
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == reenact
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_worker_normalizes_primary_feedback_to_core_offered_causal_pair() -> None:
    raw = (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When overwhelmed, offer one concrete next step.\nBASIS\n"
        "episode-stale\nepisode-feedback\noutcome-feedback\nepisode-anchor\n"
        "outcome-feedback"
    )
    model = SequenceTextModel([raw])
    request = inference_pb2.ProcessConsolidationWindowRequest(
        window_text=(
            "ELIGIBLE_DISPOSITION seed-feedback@1\nAPPLICATION RELATION\n"
            "TEXT Name the top priority first.\n"
            "FEEDBACK_EPISODE episode-feedback\n"
            "FEEDBACK_OUTCOME outcome-feedback\n"
            "OUTCOME_EPISODE episode-feedback\n"
            "REVISION_ANCHOR episode-anchor"
        ),
        allowed_target_refs=["seed-feedback@1"],
        allowed_basis_refs=[
            "episode-stale",
            "episode-feedback",
            "outcome-feedback",
            "episode-anchor",
        ],
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When overwhelmed, offer one concrete next step.\nBASIS\n"
        "episode-feedback\noutcome-feedback"
    )
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_worker_rechecks_feedback_when_primary_feedback_block_is_malformed() -> None:
    malformed = (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When overwhelmed, offer one concrete next step.\nBASIS\nepisode-feedback"
    )
    reenact = (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\nREENACT\n"
        "BASIS\nepisode-feedback"
    )
    model = SequenceTextModel([malformed, reenact])
    request = inference_pb2.ProcessConsolidationWindowRequest(
        window_text=(
            "ELIGIBLE_DISPOSITION seed-feedback@1\nAPPLICATION RELATION\n"
            "TEXT Name the top priority first.\n"
            "FEEDBACK_EPISODE episode-feedback"
        ),
        allowed_target_refs=["seed-feedback@1"],
        allowed_basis_refs=["episode-feedback"],
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == reenact
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "raw_basis", "expected_basis"),
    [
        (
            "REENACT",
            "episode-feedback\noutcome-feedback\nepisode-anchor",
            "episode-feedback",
        ),
        (
            "INHIBIT",
            "episode-stale\nepisode-feedback\noutcome-feedback\nepisode-anchor",
            "episode-feedback\noutcome-feedback",
        ),
    ],
)
async def test_feedback_recovery_binds_only_causally_qualified_basis(
    operation, raw_basis, expected_basis
) -> None:
    raw = (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\n"
        f"{operation}\nBASIS\n{raw_basis}"
    )
    model = SequenceTextModel(["NO_CHANGE", raw])
    request = inference_pb2.ProcessConsolidationWindowRequest(
        window_text=(
            "ELIGIBLE_DISPOSITION seed-feedback@1\nAPPLICATION RELATION\n"
            "TEXT Name the top priority first.\n"
            "FEEDBACK_EPISODE episode-stale\n"
            "FEEDBACK_EPISODE episode-feedback\n"
            "FEEDBACK_OUTCOME outcome-feedback\n"
            "OUTCOME_EPISODE episode-feedback\n"
            "REVISION_ANCHOR episode-anchor"
        ),
        allowed_target_refs=["seed-feedback@1"],
        allowed_basis_refs=[
            "episode-stale",
            "episode-feedback",
            "outcome-feedback",
            "episode-anchor",
        ],
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == (
        "TARGET\nseed-feedback@1\nAPPLICATION\nRELATION\nCHANGE\n"
        f"{operation}\nBASIS\n{expected_basis}"
    )


@pytest.mark.asyncio
async def test_plain_text_recollection_fallback_binds_each_meaning_separately() -> None:
    model = SequenceTextModel([
        "NO_CHANGE",
        "用户长期生活在上海。\n用户喜欢周末徒步。",
    ])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text == (
        "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "用户长期生活在上海。\nBASIS\nepisode-current\n"
        "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "用户喜欢周末徒步。\nBASIS\nepisode-current"
    )


@pytest.mark.asyncio
async def test_plain_text_recollection_fallback_deduplicates_exact_lines() -> None:
    model = SequenceTextModel([
        "NO_CHANGE",
        "用户喜欢周末徒步。\n用户喜欢周末徒步。",
    ])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text.count("TARGET\nNEW_RECOLLECTION") == 1
    assert response.tagged_text.count("用户喜欢周末徒步。") == 1


@pytest.mark.asyncio
async def test_plain_text_recollection_fallback_does_not_drop_seventh_window_meaning() -> None:
    model = SequenceTextModel([
        "NO_CHANGE",
        "\n".join(f"稳定记忆 {index}" for index in range(7)),
    ])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text.count("TARGET\nNEW_RECOLLECTION") == 7
    assert "稳定记忆 0" in response.tagged_text
    assert "稳定记忆 5" in response.tagged_text
    assert "稳定记忆 6" in response.tagged_text


@pytest.mark.asyncio
async def test_recollection_fallback_preserves_primary_disposition_change() -> None:
    disposition = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "用户焦虑时先倾听再建议\nBASIS\nepisode-current\nepisode-related"
    )
    model = SequenceTextModel([disposition, "用户长期生活在上海。"])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text == disposition + (
        "\nTARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "用户长期生活在上海。\nBASIS\nepisode-current"
    )
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_paired_formation_and_recollection_share_one_worker_response() -> None:
    model = SequenceTextModel(
        [
            "NO_CHANGE",
            "When the user is overloaded, the Agent asks one prioritizing question first.",
            "The user lives in Shanghai.",
        ]
    )

    response = await InferenceService(model).ProcessConsolidationWindow(
        _paired_formation_request(), None
    )

    assert response.tagged_text == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user is overloaded, the Agent asks one prioritizing question first.\n"
        "BASIS\nepisode-current\noutcome-current\nepisode-related\noutcome-related\n"
        "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "The user lives in Shanghai.\nBASIS\nepisode-current"
    )
    assert len(model.calls) == 3
    assert "EXPERIENCES_BEGIN" in model.calls[1]
    assert "CURRENT_USER_SOURCES_BEGIN" in model.calls[2]


@pytest.mark.asyncio
async def test_unused_direct_offer_does_not_disable_paired_formation() -> None:
    request = _paired_formation_request()
    request.window_text += (
        "\nELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\nAPPLICATION RELATION\n"
        "DIRECT_EPISODE episode-current\n"
    )
    request.allowed_target_refs.remove("NEW_RECOLLECTION")
    model = SequenceTextModel(
        [
            "NO_CHANGE",
            "When the user is overloaded, the Agent asks one prioritizing question first.",
        ]
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user is overloaded, the Agent asks one prioritizing question first.\n"
        "BASIS\nepisode-current\noutcome-current\nepisode-related\noutcome-related"
    )
    assert len(model.calls) == 2
    assert "EXPERIENCES_BEGIN" in model.calls[1]


@pytest.mark.asyncio
async def test_paired_formation_replaces_only_primary_inferred_new_seed() -> None:
    request = _paired_formation_request()
    request.allowed_target_refs.append("recollection-existing@1")
    request.evidence.recollections.add(
        version_ref="recollection-existing@1",
        application="other",
        text="The user lives in Shanghai.",
    )
    primary = (
        "TARGET\nrecollection-existing@1\nAPPLICATION\nOTHER\nCHANGE\nKEEP\n"
        "BASIS\nepisode-current\n"
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "The Agent always asks questions.\nBASIS\nepisode-current\nepisode-related"
    )
    model = SequenceTextModel(
        [
            primary,
            "When the user is overloaded, the Agent asks one prioritizing question first.",
        ]
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert "The Agent always asks questions." not in response.tagged_text
    assert "TARGET\nrecollection-existing@1" in response.tagged_text
    assert "When the user is overloaded" in response.tagged_text
    assert response.tagged_text.count("TARGET\nNEW_DISPOSITION") == 1
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_paired_no_change_blocks_primary_overgeneralization_only() -> None:
    primary = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "The Agent mirrors any repeated behavior.\n"
        "BASIS\nepisode-current\nepisode-related"
    )
    model = SequenceTextModel([primary, "NO_CHANGE", "NO_MEMORY"])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _paired_formation_request(), None
    )

    assert response.tagged_text == ""
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_anchor_without_complete_history_blocks_primary_broad_formation() -> None:
    request = _paired_formation_request()
    request.evidence.episodes[1].formation_role = ""
    request.allowed_target_refs.remove("NEW_RECOLLECTION")
    primary = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "The Agent mirrors whatever appears in the whole mixed window.\n"
        "BASIS\nepisode-current\nepisode-related"
    )
    model = SequenceTextModel([primary])

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == ""
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_new_direct_adaptation_does_not_bypass_paired_formation() -> None:
    request = _paired_formation_request()
    request.window_text += (
        "\nELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\nAPPLICATION RELATION\n"
        "DIRECT_EPISODE episode-current\n"
    )
    request.allowed_target_refs.remove("NEW_RECOLLECTION")
    direct = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nADAPT\n"
        "When I am overloaded, ask one question before offering solutions.\n"
        "BASIS\nepisode-current"
    )
    model = SequenceTextModel(
        [
            direct,
            "When the user is overloaded, the Agent asks one question before offering solutions.",
        ]
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user is overloaded, the Agent asks one question before offering solutions.\n"
        "BASIS\nepisode-current\noutcome-current\nepisode-related\noutcome-related"
    )
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_single_episode_new_direct_adaptation_is_a_noop() -> None:
    direct = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nADAPT\n"
        "When I am overwhelmed, give me one tiny step.\n"
        "BASIS\nepisode-current"
    )
    model = SequenceTextModel([direct])
    request = inference_pb2.ProcessConsolidationWindowRequest(
        window_text=(
            "EPISODE episode-current\nSITUATION\nACTOR user user-1\n"
            "I am overwhelmed today.\n"
            "ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\nAPPLICATION RELATION\n"
            "DIRECT_EPISODE episode-current"
        ),
        allowed_target_refs=["NEW_DISPOSITION"],
        allowed_basis_refs=["episode-current"],
    )

    response = await InferenceService(model).ProcessConsolidationWindow(request, None)

    assert response.tagged_text == ""
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_primary_recollection_change_suppresses_plain_text_fallback() -> None:
    primary = (
        "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "用户长期生活在上海。\nBASIS\nepisode-current"
    )
    model = SequenceTextModel([primary])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text == primary
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_primary_existing_recollection_revision_suppresses_plain_text_fallback() -> None:
    primary = (
        "TARGET\nrecollection-existing@1\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
        "用户现在长期住在北京。\nBASIS\nepisode-current"
    )
    model = SequenceTextModel([primary])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text == primary
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_legacy_request_keeps_single_model_call() -> None:
    model = SequenceTextModel(["NO_CHANGE"])

    response = await InferenceService(model).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            window_text="legacy",
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == ""
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_body", ["NO_MEMORY", "NO_CHANGE", "TARGET", "```memory```"])
async def test_invalid_or_empty_plain_recollection_cannot_poison_primary_result(
    fallback_body: str,
) -> None:
    disposition = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "用户焦虑时先倾听再建议\nBASIS\nepisode-current\nepisode-related"
    )
    model = SequenceTextModel([disposition, fallback_body])

    response = await InferenceService(model).ProcessConsolidationWindow(
        _typed_recollection_request(), None
    )

    assert response.tagged_text == disposition


@pytest.mark.asyncio
async def test_service_calls_model_once_and_returns_tagged_text_unchanged() -> None:
    tagged_text = (
        "\nTARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT 先确认约束\n"
        "BASIS\nepisode-1\nepisode-2\n"
    )
    model = RecordingTextModel(tagged_text)
    service = InferenceService(model)

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            job_ref="job-private-correlation",
            window_text="EPISODE episode-1\nSITUATION\n用户要求先确认约束",
            allowed_target_refs=["NEW_RECOLLECTION", "seed-version-7"],
            allowed_basis_refs=["episode-1", "episode-2"],
        ),
        None,
    )

    assert response.tagged_text == tagged_text
    assert len(model.calls) == 1
    model_input = model.calls[0]
    assert "job-private-correlation" not in model_input
    assert "EPISODE episode-1\nSITUATION\n用户要求先确认约束" in model_input
    assert "ALLOWED_TARGET\nNEW_RECOLLECTION" in model_input
    assert "ALLOWED_TARGET\nseed-version-7" in model_input
    assert "ALLOWED_BASIS\nepisode-1" in model_input
    assert "ALLOWED_BASIS\nepisode-2" in model_input
    assert "NEW_DISPOSITION" in model_input


@pytest.mark.asyncio
async def test_service_adds_missing_initial_target_for_offered_new_recollection() -> None:
    tagged_text = (
        "NEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea preference\n"
        "BASIS\nepisode-1\n"
    )
    service = InferenceService(RecordingTextModel(tagged_text))

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == "TARGET\n" + tagged_text


@pytest.mark.asyncio
async def test_service_adds_missing_initial_target_for_offered_new_disposition() -> None:
    tagged_text = (
        "NEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\nListen first\n"
        "BASIS\nepisode-1\nepisode-2\n"
    )
    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_DISPOSITION"],
            allowed_basis_refs=["episode-1", "episode-2"],
        ),
        None,
    )

    assert response.tagged_text == "TARGET\n" + tagged_text


@pytest.mark.asyncio
async def test_worker_can_cite_non_agent_outcomes_when_forming_a_disposition() -> None:
    tagged_text = (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When competing priorities freeze the user, the Agent first helps name one priority.\n"
        "BASIS\nepisode-1\nepisode-2\noutcome-1\noutcome-2\n"
    )
    model = RecordingTextModel(tagged_text)
    response = await InferenceService(model).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            window_text=(
                "EPISODE episode-1\nSESSION session-1\nSITUATION\nACTOR user user-1\n"
                "Too many deadlines are colliding.\nAGENT_ACT\nACTOR agent agent-1\n"
                "Which one matters most?\nEPISODE episode-2\nSESSION session-2\n"
                "SITUATION\nACTOR user user-1\nI froze again.\nAGENT_ACT\n"
                "ACTOR agent agent-1\nWhich priority is blocking the rest?\n"
                "OUTCOME outcome-1\nOUTCOME_EPISODE episode-1\nACTOR user user-1\n"
                "That helped me start.\nOUTCOME outcome-2\nOUTCOME_EPISODE episode-2\n"
                "ACTOR external observer-1\nThe user resumed work.\n"
                "ELIGIBLE_NEW_DISPOSITION NEW_DISPOSITION\nAPPLICATION RELATION"
            ),
            allowed_target_refs=["NEW_DISPOSITION"],
            allowed_basis_refs=["episode-1", "episode-2", "outcome-1", "outcome-2"],
        ),
        None,
    )

    assert response.tagged_text == tagged_text
    assert "An OUTCOME can support NEW_DISPOSITION TEXT" in model.calls[0]
    assert "include its paired Episode and Outcome refs in BASIS" in model.calls[0]


@pytest.mark.asyncio
async def test_service_splits_copied_inline_application_in_new_disposition() -> None:
    tagged_text = (
        "NEW_DISPOSITION\nAPPLICATION RELATION\nCHANGE\nTEXT\n"
        "When the user asks for biographies, include related interviews.\n"
        "BASIS\nepisode-1\nepisode-2\n"
    )

    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_DISPOSITION"],
            allowed_basis_refs=["episode-1", "episode-2"],
        ),
        None,
    )

    assert response.tagged_text == (
        "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\n"
        "When the user asks for biographies, include related interviews.\n"
        "BASIS\nepisode-1\nepisode-2\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tagged_text", "allowed_targets"),
    [
        (
            "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
            "The body says NEW_DISPOSITION and APPLICATION\nBASIS\nepisode-1\n",
            ["NEW_RECOLLECTION", "NEW_DISPOSITION"],
        ),
        (
            "NEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea\nBASIS\nepisode-1\n",
            ["NEW_DISPOSITION"],
        ),
        (
            "seed-version-7\nAPPLICATION\nSELF\nCHANGE\nADAPT\nListen\nBASIS\nepisode-1\n",
            ["seed-version-7"],
        ),
        (
            "NEW_RECOLLECTION\nOTHER\nAPPLICATION\nCHANGE\nTEXT\nTea\nBASIS\nepisode-1\n",
            ["NEW_RECOLLECTION"],
        ),
    ],
)
async def test_service_does_not_broaden_initial_target_repair(tagged_text, allowed_targets) -> None:
    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=allowed_targets,
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == tagged_text


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["\x1c", "\x1d", "\x1e", "\x1f"])
async def test_initial_target_repair_does_not_treat_non_go_controls_as_whitespace(control) -> None:
    tagged_text = (
        f"{control}\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea\n"
        "BASIS\nepisode-1\n"
    )
    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == tagged_text


@pytest.mark.asyncio
@pytest.mark.parametrize("space", ["\v", "\f", "\u0085", "\u00a0", "\u2003", "\u2028", "\u3000"])
async def test_initial_target_repair_uses_go_trim_space_for_grammar_lines(space) -> None:
    tagged_text = (
        f"{space}\n{space}NEW_RECOLLECTION{space}\n{space}\n"
        f"{space}APPLICATION{space}\nOTHER\nCHANGE\nTEXT\nTea\nBASIS\nepisode-1\n"
    )
    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == tagged_text.replace(
        f"{space}NEW_RECOLLECTION", f"TARGET\n{space}NEW_RECOLLECTION", 1
    )


@pytest.mark.asyncio
async def test_initial_target_repair_preserves_crlf_and_outer_whitespace() -> None:
    tagged_text = (
        " \t\r\n\r\nNEW_RECOLLECTION\r\n\t\r\nAPPLICATION\r\nOTHER\r\n"
        "CHANGE\r\nTEXT\r\nTea\r\nBASIS\r\nepisode-1\r\n  "
    )
    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == tagged_text.replace(
        "NEW_RECOLLECTION", "TARGET\r\nNEW_RECOLLECTION", 1
    )


@pytest.mark.asyncio
async def test_initial_target_repair_still_enforces_output_byte_budget() -> None:
    prefix = "NEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
    suffix = "\nBASIS\nepisode-1"
    tagged_text = prefix + ("x" * (MAX_TAGGED_TEXT_BYTES - len(prefix) - len(suffix))) + suffix
    assert len(tagged_text.encode("utf-8")) == MAX_TAGGED_TEXT_BYTES

    response = await InferenceService(RecordingTextModel(tagged_text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == ""


@pytest.mark.asyncio
async def test_empty_model_text_is_an_unchanged_noop() -> None:
    model = RecordingTextModel("")
    service = InferenceService(model)

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            job_ref="job-1",
            window_text="EPISODE episode-1",
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == ""
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_no_change_model_token_is_normalized_to_empty_noop() -> None:
    model = RecordingTextModel("\nNO_CHANGE\n")
    service = InferenceService(model)

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            job_ref="job-no-change",
            window_text="EPISODE episode-1",
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == ""
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_oversized_model_input_becomes_noop_without_calling_model() -> None:
    model = RecordingTextModel("must not be returned")
    service = InferenceService(model, max_model_input_bytes=64)

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            job_ref="job-oversized",
            window_text="EPISODE episode-1\n" + ("x" * 128),
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == ""
    assert model.calls == []


@pytest.mark.asyncio
async def test_oversized_model_output_becomes_noop() -> None:
    model = RecordingTextModel("x" * (MAX_TAGGED_TEXT_BYTES + 1))
    service = InferenceService(model)

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            job_ref="job-oversized-output",
            window_text="EPISODE episode-1",
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1"],
        ),
        None,
    )

    assert response.tagged_text == ""
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_real_grpc_round_trip_uses_inference_worker_contract() -> None:
    tagged_text = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT 简洁回答\nBASIS\nepisode-1\nepisode-2"
    model = RecordingTextModel(tagged_text)
    server = grpc.aio.server()
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(
        InferenceService(model), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")

    try:
        stub = inference_pb2_grpc.InferenceWorkerStub(channel)
        response = await stub.ProcessConsolidationWindow(
            inference_pb2.ProcessConsolidationWindowRequest(
                job_ref="job-1",
                window_text="EPISODE episode-1",
                allowed_target_refs=["NEW_RECOLLECTION"],
                allowed_basis_refs=["episode-1", "episode-2"],
            )
        )
    finally:
        await channel.close()
        await server.stop(grace=None)

    assert response.tagged_text == tagged_text
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        ("episode-1\nepisode-2\nepisode-1\n", "episode-1\nepisode-2\n"),
        ("episode-1\nunknown\nunknown\n", "episode-1\nunknown\nunknown\n"),
        ("episode-1\nepisode-partial\n", "episode-1\nepisode-partial\n"),
    ],
)
async def test_service_only_deduplicates_exact_offered_refs_within_each_basis(basis, expected) -> None:
    first = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea preference\nBASIS\n"
    # An independent target may legitimately cite the same source. A ref-like
    # TEXT value is not a BASIS and must also survive byte-for-byte.
    second = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nepisode-1\nBASIS\nepisode-1\n"
    service = InferenceService(RecordingTextModel(first + basis + second))

    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            window_text="EPISODE episode-1",
            allowed_target_refs=["NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1", "episode-2"],
        ), None,
    )

    assert response.tagged_text == first + expected + second


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\v", "\r"])
async def test_basis_dedup_uses_only_core_newlines_not_unicode_text_separators(separator) -> None:
    # Go's grammar splits LF, not Python's wider notion of line boundaries.
    text = ("TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\n"
            f"BASIS{separator}episode-1{separator}episode-1\nBASIS\nepisode-1\n")
    service = InferenceService(RecordingTextModel(text))
    response = await service.ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(allowed_basis_refs=["episode-1"]), None,
    )
    assert response.tagged_text == text


@pytest.mark.asyncio
async def test_basis_dedup_does_not_repair_a_reference_with_embedded_unicode_separator() -> None:
    text = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea\nBASIS\nepisode-1\u2028episode-1\n"
    response = await InferenceService(RecordingTextModel(text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(allowed_basis_refs=["episode-1"]), None,
    )
    assert response.tagged_text == text


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["\x1c", "\x1d", "\x1e", "\x1f"])
async def test_basis_dedup_preserves_invalid_control_suffixed_reference(control) -> None:
    text = f"TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea\nBASIS\nepisode-1\nepisode-1{control}\n"
    response = await InferenceService(RecordingTextModel(text)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(allowed_basis_refs=["episode-1"]), None,
    )
    assert response.tagged_text == text


@pytest.mark.asyncio
async def test_basis_dedup_preserves_crlf() -> None:
    prefix = "TARGET\r\nNEW_RECOLLECTION\r\nAPPLICATION\r\nOTHER\r\nCHANGE\r\nTEXT\r\nTea\r\nBASIS\r\n"
    response = await InferenceService(RecordingTextModel(prefix + "episode-1\r\nepisode-1\r\n")).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(allowed_basis_refs=["episode-1"]), None,
    )
    assert response.tagged_text == prefix + "episode-1\r\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(("basis", "expected"), [
    ("episode-1\nBASIS\nepisode-2\n", "episode-1\nepisode-2\n"),
    ("episode-1\nBASIS\nepisode-1\n", "episode-1\n"),
    ("episode-1\nBASIS\nepisode-2\nBASIS\nepisode-1\n", "episode-1\nepisode-2\n"),
    ("episode-1\nBASIS\nunknown\n", "episode-1\nBASIS\nunknown\n"),
    ("episode-1\nBASIS\n", "episode-1\nBASIS\n"),
    ("episode-1\nBASIS\nepisode-2\nunknown\n", "episode-1\nBASIS\nepisode-2\nunknown\n"),
])
async def test_repeated_basis_labels_only_join_complete_offered_reference_lists(basis, expected) -> None:
    prefix = "TARGET\nNEW_DISPOSITION\nAPPLICATION\nRELATION\nCHANGE\nTEXT\nGrounded future tendency\nBASIS\n"
    second = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nBASIS\nBASIS\nepisode-1\n"
    model = RecordingTextModel(prefix + basis + second)
    response = await InferenceService(model).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(
            allowed_target_refs=["NEW_DISPOSITION", "NEW_RECOLLECTION"],
            allowed_basis_refs=["episode-1", "episode-2"],
        ), None,
    )
    assert response.tagged_text == prefix + expected + second
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("space", ["\u00a0", "\u2003", "\u2028"])
async def test_basis_dedup_cannot_leak_across_unrecognized_unicode_padded_tags(space) -> None:
    first = "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nTea\nBASIS\nepisode-1\n"
    second = f"TARGET{space}\nNEW_RECOLLECTION\nAPPLICATION\nOTHER\nCHANGE\nTEXT\nCoffee\nBASIS{space}\nepisode-1\n"
    response = await InferenceService(RecordingTextModel(first + second)).ProcessConsolidationWindow(
        inference_pb2.ProcessConsolidationWindowRequest(allowed_basis_refs=["episode-1"]), None,
    )
    assert response.tagged_text == first + second


@pytest.mark.asyncio
async def test_incomplete_provider_output_fails_rpc_and_same_request_can_be_retried() -> None:
    class Responses:
        def __init__(self):
            self.statuses = iter(["incomplete", "completed"])

        async def create(self, **kwargs):
            return SimpleNamespace(status=next(self.statuses), output_text="NO_CHANGE")

    model = OpenAIResponsesTextModel("test-model", client=SimpleNamespace(responses=Responses()))
    server = grpc.aio.server()
    inference_pb2_grpc.add_InferenceWorkerServicer_to_server(InferenceService(model), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    request = inference_pb2.ProcessConsolidationWindowRequest(window_text="EPISODE episode-1")
    try:
        stub = inference_pb2_grpc.InferenceWorkerStub(channel)
        with pytest.raises(grpc.aio.AioRpcError, match="not completed"):
            await stub.ProcessConsolidationWindow(request)
        response = await stub.ProcessConsolidationWindow(request)
        assert response.tagged_text == ""
    finally:
        await channel.close()
        await server.stop(grace=None)
