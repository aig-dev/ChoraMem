package selection

import (
	"math"
	"reflect"
	"testing"
)

type scoreTable map[string]float64

func (scores scoreTable) Score(query, candidate string) float64 {
	return scores[query+"\x00"+candidate]
}

func TestSelectRequiresLiveSupportEvenWhenTendencyMatches(t *testing.T) {
	query := []Query{{Ref: "query-source", Text: "concise"}}
	candidates := []Candidate{{
		Kind:       CandidateDisposition,
		VersionRef: "seed-v1",
		Text:       "concise",
	}}

	got, err := Select(query, candidates, scoreTable{"concise\x00concise": 1}, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("Select() = %#v; want no activation without a live support anchor", got)
	}
}

func TestSelectKeepsRecollectionAndDispositionRulesDistinct(t *testing.T) {
	queries := []Query{{Ref: "query", Text: "current"}}
	candidates := []Candidate{
		{
			Kind: CandidateRecollection, VersionRef: "recollection-1@1", Text: "recollection text",
			Scope:        ApplicationScopeOther,
			Support:      []Anchor{{EpisodeRef: "episode-r", SourceRef: "source-r", Text: "recollection support"}},
			Inhibition:   []Anchor{{EpisodeRef: "episode-i", SourceRef: "source-i", Text: "strong inhibition"}},
			Reenactments: -100,
		},
		{
			Kind: CandidateDisposition, VersionRef: "disposition-1@1", Text: "disposition text",
			Scope:        ApplicationScopeRelation,
			Support:      []Anchor{{EpisodeRef: "episode-d", SourceRef: "source-d", Text: "disposition support"}},
			Inhibition:   []Anchor{{EpisodeRef: "episode-x", SourceRef: "source-x", Text: "weak inhibition"}},
			Reenactments: 2,
		},
	}
	scores := scoreTable{
		"current\x00recollection text":    0.8,
		"current\x00recollection support": 0.7,
		"current\x00strong inhibition":    1.0,
		"current\x00disposition text":     0.9,
		"current\x00disposition support":  0.8,
		"current\x00weak inhibition":      0.2,
	}

	got, err := Select(queries, candidates, scores, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []struct {
		kind CandidateKind
		ref  string
	}{{CandidateDisposition, "disposition-1@1"}, {CandidateRecollection, "recollection-1@1"}}
	if len(got) != len(want) {
		t.Fatalf("Select() = %#v; want one Recollection and one Disposition", got)
	}
	for index, activation := range got {
		if activation.Kind != want[index].kind || activation.VersionRef != want[index].ref {
			t.Fatalf("Select()[%d] = %#v; want kind=%s ref=%s", index, activation, want[index].kind, want[index].ref)
		}
	}
}

func TestSelectAppliesIndependentRecollectionAndDispositionBudgets(t *testing.T) {
	queries := []Query{{Ref: "query", Text: "current"}}
	recollection := candidate("recollection", "recollection", 0)
	recollection.Kind = CandidateRecollection
	candidates := []Candidate{
		candidate("disposition-high", "disposition-high", 0),
		candidate("disposition-second", "disposition-second", 0),
		recollection,
	}
	scores := scoreTable{
		"current\x00disposition-high":   0.9,
		"current\x00disposition-second": 0.8,
		"current\x00recollection":       0.6,
	}
	policy := testPolicy()
	policy.MaxRecollections = 1
	policy.MaxDispositions = 1

	got, err := Select(queries, candidates, scores, policy)
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []struct {
		kind CandidateKind
		ref  string
	}{
		{kind: CandidateDisposition, ref: "disposition-high"},
		{kind: CandidateRecollection, ref: "recollection"},
	}
	if len(got) != len(want) {
		t.Fatalf("Select() = %#v; want independently bounded Recollection and Disposition", got)
	}
	for index, activation := range got {
		if activation.Kind != want[index].kind || activation.VersionRef != want[index].ref {
			t.Fatalf("Select()[%d] = %#v; want kind=%s ref=%s", index, activation, want[index].kind, want[index].ref)
		}
	}
}

func TestSelectKeepsBestStableSupportPath(t *testing.T) {
	queries := []Query{
		{Ref: "query-b", Text: "current-b"},
		{Ref: "query-a", Text: "current-a"},
	}
	candidates := []Candidate{{
		Kind:       CandidateDisposition,
		VersionRef: "seed-v1",
		Text:       "tendency",
		Scope:      ApplicationScopeRelation,
		Support: []Anchor{
			{EpisodeRef: "episode-b", SourceRef: "source-b", Text: "support-b"},
			{EpisodeRef: "episode-a", SourceRef: "source-a", Text: "support-a"},
		},
	}}
	scores := scoreTable{
		"current-a\x00tendency":  0.8,
		"current-b\x00tendency":  0.8,
		"current-a\x00support-a": 0.8,
		"current-a\x00support-b": 0.8,
		"current-b\x00support-a": 0.8,
		"current-b\x00support-b": 0.8,
	}

	got, err := Select(queries, candidates, scores, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []Activation{{
		Kind:               CandidateDisposition,
		VersionRef:         "seed-v1",
		Scope:              ApplicationScopeRelation,
		QuerySourceRef:     "query-a",
		MatchedRef:         "seed-v1",
		EvidenceEpisodeRef: "episode-a",
		EvidenceSourceRef:  "source-a",
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Select() = %#v; want %#v", got, want)
	}
}

func TestSelectUsesAnchorAsMatchedAndEvidencePath(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "current"}}
	candidates := []Candidate{{
		Kind:       CandidateDisposition,
		VersionRef: "seed-v1",
		Text:       "tendency",
		Support: []Anchor{{
			EpisodeRef: "episode-1",
			SourceRef:  "source-1",
			Text:       "matching situation",
		}},
	}}
	scores := scoreTable{
		"current\x00tendency":           0.4,
		"current\x00matching situation": 0.9,
	}

	got, err := Select(queries, candidates, scores, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []Activation{{
		Kind:               CandidateDisposition,
		VersionRef:         "seed-v1",
		QuerySourceRef:     "query-source",
		MatchedRef:         "source-1",
		EvidenceEpisodeRef: "episode-1",
		EvidenceSourceRef:  "source-1",
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Select() = %#v; want %#v", got, want)
	}
}

func TestSelectRecollectionMatchesItsTextNotItsProvenanceWindow(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "driving music"}}
	candidates := []Candidate{
		{
			Kind:       CandidateRecollection,
			VersionRef: "unrelated-recollection@1",
			Text:       "academic conference travel",
			Support: []Anchor{{
				EpisodeRef: "broad-window",
				SourceRef:  "music-source",
				Text:       "driving music",
			}},
		},
		{
			Kind:       CandidateRecollection,
			VersionRef: "matching-recollection@1",
			Text:       "driving music preference",
			Support: []Anchor{{
				EpisodeRef: "source-window",
				SourceRef:  "unrelated-source",
				Text:       "academic conference travel",
			}},
		},
	}
	scores := scoreTable{
		"driving music\x00academic conference travel": 0.1,
		"driving music\x00driving music":              1.0,
		"driving music\x00driving music preference":   0.8,
	}

	got, err := Select(queries, candidates, scores, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []Activation{{
		Kind:               CandidateRecollection,
		VersionRef:         "matching-recollection@1",
		QuerySourceRef:     "query-source",
		MatchedRef:         "matching-recollection@1",
		EvidenceEpisodeRef: "source-window",
		EvidenceSourceRef:  "unrelated-source",
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Select() = %#v; want only the text-matching Recollection %#v", got, want)
	}
}

func TestSelectPreservesDirectSemanticIndexOrderAheadOfLexicalFallback(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "mountain vehicle"}}
	candidates := []Candidate{
		{
			Kind:       CandidateRecollection,
			VersionRef: "semantic-recollection@1",
			Text:       "stick-shift cars",
			Support: []Anchor{{
				EpisodeRef: "semantic-episode",
				SourceRef:  "semantic-source",
				Text:       "earlier driving preference",
			}},
			IndexMatches: []IndexMatch{{
				QuerySourceRef: "query-source",
				Rank:           0,
				LaneOrder:      1,
			}},
		},
		{
			Kind:       CandidateRecollection,
			VersionRef: "lexical-recollection@1",
			Text:       "mountain vehicle",
			Support: []Anchor{{
				EpisodeRef: "lexical-episode",
				SourceRef:  "lexical-source",
				Text:       "unrelated lexical phrase",
			}},
		},
	}
	policy := testPolicy()
	policy.MaxRecollections = 1
	scores := scoreTable{
		"mountain vehicle\x00stick-shift cars": 0,
		"mountain vehicle\x00mountain vehicle": 1,
	}

	got, err := Select(queries, candidates, scores, policy)
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []Activation{{
		Kind:               CandidateRecollection,
		VersionRef:         "semantic-recollection@1",
		QuerySourceRef:     "query-source",
		MatchedRef:         "semantic-recollection@1",
		EvidenceEpisodeRef: "semantic-episode",
		EvidenceSourceRef:  "semantic-source",
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Select() = %#v; want direct semantic candidate %#v", got, want)
	}
}

func TestSelectLetsSemanticIndexActivateDispositionButStillHonorsInhibition(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "overwhelmed complicated plan"}}
	candidates := []Candidate{
		{
			Kind:       CandidateDisposition,
			VersionRef: "semantic-disposition@1",
			Text:       "name priorities before solutions",
			Support: []Anchor{{
				EpisodeRef: "support-episode",
				SourceRef:  "support-source",
				Text:       "earlier planning experience",
			}},
			IndexMatches: []IndexMatch{{
				QuerySourceRef: "query-source", Rank: 0, LaneOrder: 0,
			}},
		},
		{
			Kind:       CandidateDisposition,
			VersionRef: "inhibited-disposition@1",
			Text:       "offer detailed planning structure",
			Support: []Anchor{{
				EpisodeRef: "other-support-episode",
				SourceRef:  "other-support-source",
				Text:       "earlier structured planning",
			}},
			Inhibition: []Anchor{{
				EpisodeRef: "inhibition-episode",
				SourceRef:  "inhibition-source",
				Text:       "overwhelmed complicated plan",
			}},
			IndexMatches: []IndexMatch{{
				QuerySourceRef: "query-source", Rank: 1, LaneOrder: 0,
			}},
		},
	}
	scores := scoreTable{
		"overwhelmed complicated plan\x00name priorities before solutions":  0.1,
		"overwhelmed complicated plan\x00earlier planning experience":       0.1,
		"overwhelmed complicated plan\x00offer detailed planning structure": 0.1,
		"overwhelmed complicated plan\x00earlier structured planning":       0.1,
		"overwhelmed complicated plan\x00overwhelmed complicated plan":      1,
	}

	got, err := Select(queries, candidates, scores, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []Activation{{
		Kind:               CandidateDisposition,
		VersionRef:         "semantic-disposition@1",
		QuerySourceRef:     "query-source",
		MatchedRef:         "semantic-disposition@1",
		EvidenceEpisodeRef: "support-episode",
		EvidenceSourceRef:  "support-source",
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Select() = %#v; want semantic Disposition %#v", got, want)
	}
}

func TestSelectPreservesSemanticMatchFromExactDispositionSupportEpisode(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "mountain vehicle"}}
	candidates := []Candidate{
		{
			Kind:       CandidateDisposition,
			VersionRef: "linked-disposition@1",
			Text:       "choose manual transmission when possible",
			Scope:      ApplicationScopeRelation,
			Support: []Anchor{
				{EpisodeRef: "matched-episode", SourceRef: "matched-source", Text: "learned to drive stick shift"},
				{EpisodeRef: "other-episode", SourceRef: "other-source", Text: "another supporting situation"},
			},
			IndexMatches: []IndexMatch{{
				QuerySourceRef:    "query-source",
				SupportEpisodeRef: "matched-episode",
				Rank:              3,
				LaneOrder:         1,
			}},
		},
		{
			Kind:       CandidateDisposition,
			VersionRef: "unlinked-disposition@1",
			Text:       "prefer elaborate travel plans",
			Support: []Anchor{{
				EpisodeRef: "unlinked-episode", SourceRef: "unlinked-source", Text: "detailed itinerary",
			}},
			IndexMatches: []IndexMatch{{
				QuerySourceRef:    "query-source",
				SupportEpisodeRef: "matched-episode",
				Rank:              3,
				LaneOrder:         1,
			}},
		},
	}

	got, err := Select(queries, candidates, scoreTable{}, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []Activation{{
		Kind:               CandidateDisposition,
		VersionRef:         "linked-disposition@1",
		Scope:              ApplicationScopeRelation,
		QuerySourceRef:     "query-source",
		MatchedRef:         "matched-episode",
		EvidenceEpisodeRef: "matched-episode",
		EvidenceSourceRef:  "matched-source",
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Select() = %#v; want only the exact support-linked Disposition %#v", got, want)
	}
}

func TestSelectIgnoresIndexMatchForUnknownQuery(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "current text"}}
	candidates := []Candidate{{
		Kind:       CandidateRecollection,
		VersionRef: "recollection@1",
		Text:       "different text",
		Support: []Anchor{{
			EpisodeRef: "episode",
			SourceRef:  "source",
			Text:       "support",
		}},
		IndexMatches: []IndexMatch{{QuerySourceRef: "other-query", Rank: 0}},
	}}

	got, err := Select(queries, candidates, scoreTable{}, testPolicy())
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("Select() = %#v; want no activation from an unknown query association", got)
	}
}

func TestSelectRequiresSupportToBeatThresholdAndInhibitionStrictly(t *testing.T) {
	queries := []Query{{Ref: "query-source", Text: "current"}}
	candidates := []Candidate{
		{
			Kind:       CandidateDisposition,
			VersionRef: "below-minimum",
			Text:       "below tendency",
			Support:    []Anchor{{EpisodeRef: "episode-1", SourceRef: "support-1", Text: "below support"}},
		},
		{
			Kind:       CandidateDisposition,
			VersionRef: "inhibition-tie",
			Text:       "tie tendency",
			Support:    []Anchor{{EpisodeRef: "episode-2", SourceRef: "support-2", Text: "tie support"}},
			Inhibition: []Anchor{{EpisodeRef: "episode-3", SourceRef: "inhibit-2", Text: "tie inhibit"}},
		},
		{
			Kind:       CandidateDisposition,
			VersionRef: "support-wins",
			Text:       "winning tendency",
			Support:    []Anchor{{EpisodeRef: "episode-4", SourceRef: "support-3", Text: "winning support"}},
			Inhibition: []Anchor{{EpisodeRef: "episode-5", SourceRef: "inhibit-3", Text: "weaker inhibit"}},
		},
	}
	scores := scoreTable{
		"current\x00below tendency":   0.49,
		"current\x00below support":    0.49,
		"current\x00tie tendency":     0.7,
		"current\x00tie support":      0.7,
		"current\x00tie inhibit":      0.7,
		"current\x00winning tendency": 0.8,
		"current\x00winning support":  0.9,
		"current\x00weaker inhibit":   0.8,
	}
	policy := testPolicy()
	policy.MinimumScore = 0.5

	got, err := Select(queries, candidates, scores, policy)
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	if len(got) != 1 || got[0].VersionRef != "support-wins" {
		t.Fatalf("Select() = %#v; want only support-wins", got)
	}
}

func TestSelectOrdersBySupportThenBoundedAvailabilityThenVersionRef(t *testing.T) {
	queries := []Query{{Ref: "query", Text: "current"}}
	candidates := []Candidate{
		candidate("score-low", "low", 100),
		candidate("version-c", "equal-c", 100),
		candidate("version-a", "equal-a", 2),
		candidate("version-b", "equal-b", 1),
	}
	scores := scoreTable{
		"current\x00low":     0.8,
		"current\x00equal-a": 0.9,
		"current\x00equal-b": 0.9,
		"current\x00equal-c": 0.9,
	}
	policy := testPolicy()
	policy.AvailabilityCap = 2
	policy.MaxDispositions = 3

	got, err := Select(queries, candidates, scores, policy)
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	want := []string{"version-a", "version-c", "version-b"}
	refs := make([]string, 0, len(got))
	for _, activation := range got {
		refs = append(refs, activation.VersionRef)
	}
	if !reflect.DeepEqual(refs, want) {
		t.Fatalf("Select() refs = %#v; want %#v", refs, want)
	}
}

func TestSelectNormalizesInvalidRankerScores(t *testing.T) {
	queries := []Query{{Ref: "query", Text: "current"}}
	candidates := []Candidate{
		candidate("nan", "nan", 0),
		candidate("above-one", "above", 0),
		candidate("below-zero", "below", 0),
	}
	scores := scoreTable{
		"current\x00nan":   math.NaN(),
		"current\x00above": 1.4,
		"current\x00below": -0.2,
	}
	policy := testPolicy()
	policy.MinimumScore = 0.1

	got, err := Select(queries, candidates, scores, policy)
	if err != nil {
		t.Fatalf("Select() error = %v", err)
	}
	if len(got) != 1 || got[0].VersionRef != "above-one" {
		t.Fatalf("Select() = %#v; want only finite clamped above-one score", got)
	}
}

func TestSelectRejectsInvalidPolicyAndMissingRanker(t *testing.T) {
	valid := testPolicy()
	tests := []struct {
		name   string
		ranker Ranker
		policy Policy
	}{
		{name: "nil ranker", policy: valid},
		{name: "missing policy ref", ranker: scoreTable{}, policy: Policy{MinimumScore: 0.2, MaxRecollections: 1, MaxDispositions: 1}},
		{name: "nan minimum", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: math.NaN(), MaxRecollections: 1, MaxDispositions: 1}},
		{name: "negative minimum", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: -0.1, MaxRecollections: 1, MaxDispositions: 1}},
		{name: "minimum above one", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: 1.1, MaxRecollections: 1, MaxDispositions: 1}},
		{name: "zero budget", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: 0.2}},
		{name: "zero Recollection budget", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: 0.2, MaxDispositions: 1, AvailabilityCap: 1, CanonicalCandidatesPerOwnerKind: 1}},
		{name: "zero Disposition budget", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: 0.2, MaxRecollections: 1, AvailabilityCap: 1, CanonicalCandidatesPerOwnerKind: 1}},
		{name: "negative availability cap", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: 0.2, MaxRecollections: 1, MaxDispositions: 1, AvailabilityCap: -1}},
		{name: "zero canonical lane bound", ranker: scoreTable{}, policy: Policy{Ref: "test", MinimumScore: 0.2, MaxRecollections: 1, MaxDispositions: 1, AvailabilityCap: 1}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := Select(nil, nil, tt.ranker, tt.policy); err == nil {
				t.Fatal("Select() error = nil; want invalid selection configuration error")
			}
		})
	}
}

func candidate(version, tendency string, reenactments int) Candidate {
	return Candidate{
		Kind:         CandidateDisposition,
		VersionRef:   version,
		Text:         tendency,
		Reenactments: reenactments,
		Support: []Anchor{{
			EpisodeRef: "episode-" + version,
			SourceRef:  "source-" + version,
			Text:       tendency,
		}},
	}
}

func testPolicy() Policy {
	return Policy{
		Ref: "test-v1", MinimumScore: 0.2,
		MaxRecollections: 10, MaxDispositions: 10,
		AvailabilityCap: 4, CanonicalCandidatesPerOwnerKind: 40,
	}
}
