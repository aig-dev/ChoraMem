"""Live semantic regression for current-scoped refinements.

The fixture is domain-neutral: a request to revise the artifact currently under
discussion may reveal useful declarative context, but it does not by itself
authorize a long-term Agent response Disposition.
"""

from adaptive_cases import AdaptiveCase, MULTI, NEW, TARGETS, UNKNOWN, episode


def _in_session(text: str, episode_ref: str, session_ref: str) -> str:
    return text.replace(
        f"SESSION session-{episode_ref}", f"SESSION {session_ref}", 1
    )


CASES = (
    AdaptiveCase(
        "cross_session_repeated_refinement",
        UNKNOWN
        + _in_session(episode(
            "e-reliability-request",
            (
                "Prepare an equipment reliability report from these quarterly "
                "maintenance figures."
            ),
            "Here is the reliability analysis and a summary of the figures.",
        ), "e-reliability-request", "session-reliability")
        + _in_session(episode(
            "e-reliability-structure",
            (
                "For this reliability report, please present the findings in "
                "tables and clearly segmented sections so the technical review "
                "is easier to follow."
            ),
            "I will restructure this report with tables and clear sections.",
        ), "e-reliability-structure", "session-reliability")
        + _in_session(episode(
            "e-maintenance-request",
            (
                "Analyze these maintenance inspection results and explain the "
                "main operational risks."
            ),
            "Here is the inspection analysis and the main operational risks.",
        ), "e-maintenance-request", "session-maintenance")
        + _in_session(episode(
            "e-maintenance-structure",
            (
                "For this separate maintenance analysis, organize the findings "
                "into clear sections and bullet points so it follows our "
                "technical reporting conventions."
            ),
            "I will reorganize this analysis into sections and bullet points.",
        ), "e-maintenance-structure", "session-maintenance")
        + NEW
        + MULTI
        + (
            "ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\n"
            "APPLICATION RELATION\n"
            "DIRECT_EPISODE e-reliability-request\n"
            "DIRECT_EPISODE e-reliability-structure\n"
            "DIRECT_EPISODE e-maintenance-request\n"
            "DIRECT_EPISODE e-maintenance-structure\n"
        ),
        TARGETS,
        ("e-reliability-structure", "e-maintenance-structure"),
        (("NEW_DISPOSITION", "TEXT"),),
        (
            "Each request is scoped to its current artifact, so neither permits "
            "ADAPT. The same presentation adjustment recurs in two independent "
            "sessions and therefore supports one inferred conditional response "
            "Disposition via TEXT."
        ),
    ),
    AdaptiveCase(
        "current_scope_refinement",
        UNKNOWN
        + _in_session(episode(
            "e-report-request",
            (
                "I need to analyze this quarterly sales data and prepare a "
                "technical review. The figures are 18, 24, 21, and 29 units."
            ),
            "The mean is 23 units and the final period is the highest.",
        ), "e-report-request", "session-report")
        + _in_session(episode(
            "e-report-revision",
            (
                "The analysis is clear and avoids oversimplification, which is "
                "appreciated. However, presenting the figures in tables and "
                "segmented sections could enhance clarity and adherence to "
                "professional reporting standards. Could you refine the "
                "presentation accordingly?"
            ),
            "I will revise the current document with tables and clear sections.",
        ), "e-report-revision", "session-report")
        + _in_session(episode(
            "e-proposal-request",
            (
                "Help me write a proposal for a new forecasting method using "
                "historical records, sensor data, and cross-validation."
            ),
            "Here is a proposal covering the data, method, and expected results.",
        ), "e-proposal-request", "session-proposal")
        + _in_session(episode(
            "e-proposal-revision",
            (
                "The proposal is promising. Could you elaborate on how the "
                "methodology in this proposal will be validated in practice?"
            ),
            "I will add validation techniques and metrics to the proposal.",
        ), "e-proposal-revision", "session-proposal")
        + NEW
        + MULTI
        + (
            "ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\n"
            "APPLICATION RELATION\n"
            "DIRECT_EPISODE e-report-request\n"
            "DIRECT_EPISODE e-report-revision\n"
            "DIRECT_EPISODE e-proposal-request\n"
            "DIRECT_EPISODE e-proposal-revision\n"
        ),
        TARGETS,
        (
            "e-report-request",
            "e-report-revision",
            "e-proposal-request",
            "e-proposal-revision",
        ),
        (),
        (
            "This is a refinement of the current artifact, not an explicit "
            "future interaction rule. The other session requests a different "
            "revision and cannot supply repeated evidence for the same response "
            "adjustment. It is Episode evidence only and must not create a "
            "Disposition or Recollection."
        ),
    ),
    AdaptiveCase(
        "same_session_tone_refinement",
        UNKNOWN
        + _in_session(episode(
            "e-guideline-request",
            (
                "Help me create communication guidelines for instructors at a "
                "regulated industrial safety training center. Include examples "
                "for different training situations."
            ),
            (
                "Here are approachable, clear, and collaborative communication "
                "guidelines for the instructors."
            ),
        ), "e-guideline-request", "session-guideline")
        + _in_session(episode(
            "e-guideline-revision",
            (
                "The guidelines are helpful, but consider refining the tone to "
                "better reflect the formal and authoritative standards expected "
                "in this safety training environment. Emphasizing discipline and "
                "professionalism would align the document with its context."
            ),
            (
                "Here are the revised guidelines using a formal, authoritative "
                "tone that emphasizes discipline and professionalism."
            ),
        ), "e-guideline-revision", "session-guideline")
        + NEW
        + MULTI
        + (
            "ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\n"
            "APPLICATION RELATION\n"
            "DIRECT_EPISODE e-guideline-request\n"
            "DIRECT_EPISODE e-guideline-revision\n"
        ),
        TARGETS,
        ("e-guideline-request", "e-guideline-revision"),
        (),
        (
            "Both episodes belong to one current document revision in one "
            "session. The contextual tone requirement must not become a "
            "long-term Disposition."
        ),
    ),
    AdaptiveCase(
        "same_session_repeated_refinement_with_unrelated_history",
        UNKNOWN
        + _in_session(episode(
            "e-procedure-request",
            (
                "Compare legacy operator practices with the new automated "
                "procedures for this plant safety review."
            ),
            "Here is a comparison of the legacy and automated procedures.",
        ), "e-procedure-request", "session-procedure")
        + _in_session(episode(
            "e-procedure-revision-one",
            (
                "The integration is useful. Starting this review with a clear "
                "acknowledgment of the value of operator knowledge would give "
                "the comparison a stronger practical foundation."
            ),
            "I will begin this review by acknowledging operator knowledge.",
        ), "e-procedure-revision-one", "session-procedure")
        + _in_session(episode(
            "e-procedure-revision-two",
            (
                "The two approaches are now connected well. To strengthen this "
                "review, start with an explicit acknowledgment of experienced "
                "operators before presenting the comparison."
            ),
            "I will revise the opening of this review accordingly.",
        ), "e-procedure-revision-two", "session-procedure")
        + _in_session(episode(
            "e-survey-revision",
            (
                "The survey proposal is promising. Could you add the sampling "
                "and validation details for this proposal?"
            ),
            "I will add sampling and validation details to the survey proposal.",
        ), "e-survey-revision", "session-survey")
        + NEW
        + MULTI
        + (
            "ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION\n"
            "APPLICATION RELATION\n"
            "DIRECT_EPISODE e-procedure-request\n"
            "DIRECT_EPISODE e-procedure-revision-one\n"
            "DIRECT_EPISODE e-procedure-revision-two\n"
            "DIRECT_EPISODE e-survey-revision\n"
        ),
        TARGETS,
        (
            "e-procedure-request",
            "e-procedure-revision-one",
            "e-procedure-revision-two",
            "e-survey-revision",
        ),
        (),
        (
            "Repeated turns inside one artifact session are one interaction. "
            "The unrelated second session cannot supply the missing evidence, "
            "so no Disposition may be formed."
        ),
    ),
)
