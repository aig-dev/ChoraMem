package consolidation

import (
	"reflect"
	"testing"
)

func TestDispositionFormationBasisUsesOnlyMarkedCompletePairs(t *testing.T) {
	evidence := &WindowEvidence{
		Episodes: []EvidenceEpisode{
			{EpisodeRef: "episode-candidate-1", SessionRef: "session-1", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationCandidate},
			{EpisodeRef: "episode-distractor", SessionRef: "session-noise"},
			{EpisodeRef: "episode-anchor", SessionRef: "session-3", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor},
			{EpisodeRef: "episode-candidate-2", SessionRef: "session-2", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationCandidate},
		},
		Outcomes: []EvidenceOutcome{
			{OutcomeRef: "outcome-noise", EpisodeRef: "episode-distractor", ActorKind: "user", Text: "irrelevant"},
			{OutcomeRef: "outcome-candidate-2", EpisodeRef: "episode-candidate-2", ActorKind: "external", Text: "helped"},
			{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"},
			{OutcomeRef: "outcome-candidate-1", EpisodeRef: "episode-candidate-1", ActorKind: "user", Text: "helped"},
		},
	}

	got, ok := DispositionFormationBasis(evidence)

	want := []string{
		"episode-anchor", "outcome-anchor",
		"episode-candidate-1", "outcome-candidate-1",
		"episode-candidate-2", "outcome-candidate-2",
	}
	if !ok || !reflect.DeepEqual(got, want) {
		t.Fatalf("DispositionFormationBasis() = %#v, %v; want %#v, true", got, ok, want)
	}
}

func TestDispositionFormationBasisRejectsIncompleteOrAmbiguousGroup(t *testing.T) {
	tests := []struct {
		name     string
		evidence *WindowEvidence
	}{
		{
			name: "anchor only",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{{EpisodeRef: "episode-anchor", SessionRef: "session-1", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor}},
				Outcomes: []EvidenceOutcome{{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"}},
			},
		},
		{
			name: "candidate outcome missing",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{
					{EpisodeRef: "episode-anchor", SessionRef: "session-1", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor},
					{EpisodeRef: "episode-candidate", SessionRef: "session-2", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationCandidate},
				},
				Outcomes: []EvidenceOutcome{{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"}},
			},
		},
		{
			name: "multiple candidate outcomes",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{
					{EpisodeRef: "episode-anchor", SessionRef: "session-1", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor},
					{EpisodeRef: "episode-candidate", SessionRef: "session-2", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationCandidate},
				},
				Outcomes: []EvidenceOutcome{
					{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"},
					{OutcomeRef: "outcome-candidate-1", EpisodeRef: "episode-candidate", ActorKind: "user", Text: "helped"},
					{OutcomeRef: "outcome-candidate-2", EpisodeRef: "episode-candidate", ActorKind: "external", Text: "also helped"},
				},
			},
		},
		{
			name: "same session",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{
					{EpisodeRef: "episode-anchor", SessionRef: "same", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor},
					{EpisodeRef: "episode-candidate", SessionRef: "same", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationCandidate},
				},
				Outcomes: []EvidenceOutcome{
					{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"},
					{OutcomeRef: "outcome-candidate", EpisodeRef: "episode-candidate", ActorKind: "user", Text: "helped"},
				},
			},
		},
		{
			name: "anchor is not current",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{
					{EpisodeRef: "episode-anchor", SessionRef: "session-1", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationAnchor},
					{EpisodeRef: "episode-candidate", SessionRef: "session-2", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationCandidate},
				},
				Outcomes: []EvidenceOutcome{
					{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"},
					{OutcomeRef: "outcome-candidate", EpisodeRef: "episode-candidate", ActorKind: "user", Text: "helped"},
				},
			},
		},
		{
			name: "candidate has unsupported origin",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{
					{EpisodeRef: "episode-anchor", SessionRef: "session-1", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor},
					{EpisodeRef: "episode-candidate", SessionRef: "session-2", Origin: EvidenceOriginRevisionAnchor, FormationRole: EvidenceFormationCandidate},
				},
				Outcomes: []EvidenceOutcome{
					{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"},
					{OutcomeRef: "outcome-candidate", EpisodeRef: "episode-candidate", ActorKind: "user", Text: "helped"},
				},
			},
		},
		{
			name: "outcome actor missing",
			evidence: &WindowEvidence{
				Episodes: []EvidenceEpisode{
					{EpisodeRef: "episode-anchor", SessionRef: "session-1", Origin: EvidenceOriginCurrent, FormationRole: EvidenceFormationAnchor},
					{EpisodeRef: "episode-candidate", SessionRef: "session-2", Origin: EvidenceOriginRelated, FormationRole: EvidenceFormationCandidate},
				},
				Outcomes: []EvidenceOutcome{
					{OutcomeRef: "outcome-anchor", EpisodeRef: "episode-anchor", ActorKind: "user", Text: "helped"},
					{OutcomeRef: "outcome-candidate", EpisodeRef: "episode-candidate", Text: "helped"},
				},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got, ok := DispositionFormationBasis(test.evidence); ok || got != nil {
				t.Fatalf("DispositionFormationBasis() = %#v, %v; want nil, false", got, ok)
			}
		})
	}
}

func TestAppendWorkerEvidenceHashFieldsIncludesFormationRole(t *testing.T) {
	left := &WindowEvidence{Episodes: []EvidenceEpisode{{EpisodeRef: "episode-1", SessionRef: "session-1"}}}
	right := &WindowEvidence{Episodes: []EvidenceEpisode{{
		EpisodeRef: "episode-1", SessionRef: "session-1", FormationRole: EvidenceFormationAnchor,
	}}}

	if reflect.DeepEqual(
		AppendWorkerEvidenceHashFields(nil, left),
		AppendWorkerEvidenceHashFields(nil, right),
	) {
		t.Fatal("worker evidence hash fields ignored formation role")
	}
}
