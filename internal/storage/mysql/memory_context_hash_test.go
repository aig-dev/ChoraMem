package mysql

import (
	"context"
	"encoding/hex"
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

type searchLimitRecordingIndex struct {
	queries    []memoryindex.Query
	candidates []memoryindex.Candidate
}

func (index *searchLimitRecordingIndex) Search(_ context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	index.queries = append(index.queries, query)
	return append([]memoryindex.Candidate(nil), index.candidates...), nil
}

func (*searchLimitRecordingIndex) Upsert(context.Context, []memoryindex.Document) error {
	return nil
}

func (*searchLimitRecordingIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}

func (*searchLimitRecordingIndex) Reset(context.Context) error { return nil }

func TestSearchMemoryIndexKeepsMixedProviderResultsWithinPolicyBound(t *testing.T) {
	index := &searchLimitRecordingIndex{candidates: []memoryindex.Candidate{
		{Kind: memoryindex.KindEpisode, Ref: "episode-1"},
		{Kind: memoryindex.KindRecollection, Ref: "recollection-1"},
		{Kind: memoryindex.KindDisposition, Ref: "disposition-1"},
	}}
	store := &Store{
		memoryIndex: index,
		selectionPolicy: selection.Policy{
			CanonicalCandidatesPerOwnerKind: 2,
		},
	}
	scope := ledger.Scope{
		Kind: ledger.ScopeKindAgent, TenantRef: "tenant", AgentRef: "agent",
	}

	got := store.searchMemoryIndex(context.Background(), scope, []selection.Query{{
		Ref: "query-source", Text: "current situation",
	}})

	if len(index.queries) != 1 {
		t.Fatalf("MemoryIndex query count = %d; want one agent owner lane", len(index.queries))
	}
	if index.queries[0].Limit != 2 {
		t.Fatalf("MemoryIndex query limit = %d; want policy bound 2", index.queries[0].Limit)
	}
	if len(got) != 2 || got[0].candidate.Ref != "episode-1" || got[1].candidate.Ref != "recollection-1" {
		t.Fatalf("searchMemoryIndex() = %#v; want only the first two mixed provider candidates", got)
	}
}

func TestUnionSelectionCandidatesPreservesDirectIndexMatches(t *testing.T) {
	canonical := []selection.Candidate{{
		Kind: selection.CandidateRecollection, VersionRef: "recollection@1", Text: "canonical",
	}}
	indexed := []selection.Candidate{{
		Kind: selection.CandidateRecollection, VersionRef: "recollection@1", Text: "canonical",
		IndexMatches: []selection.IndexMatch{{QuerySourceRef: "query", Rank: 2, LaneOrder: 1}},
	}}

	got := unionSelectionCandidates(canonical, indexed)
	if len(got) != 1 || !reflect.DeepEqual(got[0].IndexMatches, indexed[0].IndexMatches) {
		t.Fatalf("unionSelectionCandidates() = %#v; want one canonical candidate with direct index match", got)
	}
}

func TestAttachDirectIndexMatchesKeepsOnlyExactOwnerRecollectionHits(t *testing.T) {
	owner := ownerScope{
		Kind: ledger.ScopeKindRelationship, TenantRef: "tenant", AgentRef: "agent", RelationshipRef: "relationship",
	}
	candidates := []selection.Candidate{{
		Kind: selection.CandidateRecollection, VersionRef: "recollection@1", Text: "canonical",
	}}
	indexed := []scopedIndexCandidate{
		{
			scope: owner, candidate: memoryindex.Candidate{Kind: memoryindex.KindRecollection, Ref: "recollection@1"},
			querySourceRef: "query", rank: 3, laneOrder: 2,
		},
		{
			scope: owner, candidate: memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: "recollection@1"},
			querySourceRef: "episode-query", rank: 0, laneOrder: 0,
		},
		{
			scope:          ownerScope{Kind: owner.Kind, TenantRef: owner.TenantRef, AgentRef: owner.AgentRef, RelationshipRef: "other"},
			candidate:      memoryindex.Candidate{Kind: memoryindex.KindRecollection, Ref: "recollection@1"},
			querySourceRef: "other-query", rank: 0, laneOrder: 0,
		},
	}

	got := attachDirectIndexMatches(candidates, indexed, owner)
	want := []selection.IndexMatch{{QuerySourceRef: "query", Rank: 3, LaneOrder: 2}}
	if !reflect.DeepEqual(got[0].IndexMatches, want) {
		t.Fatalf("attachDirectIndexMatches() = %#v; want %#v", got[0].IndexMatches, want)
	}
}

func TestAttachEpisodeSupportIndexMatchesKeepsOnlyExactOwnerDispositionBasis(t *testing.T) {
	owner := ownerScope{
		Kind: ledger.ScopeKindRelationship, TenantRef: "tenant", AgentRef: "agent", RelationshipRef: "relationship",
	}
	candidates := []selection.Candidate{
		{
			Kind: selection.CandidateDisposition, VersionRef: "linked@1", Text: "linked",
			Support: []selection.Anchor{{EpisodeRef: "matched-episode", SourceRef: "source", Text: "support"}},
		},
		{
			Kind: selection.CandidateDisposition, VersionRef: "sibling@1", Text: "sibling",
			Support: []selection.Anchor{{EpisodeRef: "other-episode", SourceRef: "other-source", Text: "other support"}},
		},
		{
			Kind: selection.CandidateRecollection, VersionRef: "recollection@1", Text: "recollection",
			Support: []selection.Anchor{{EpisodeRef: "matched-episode", SourceRef: "source", Text: "support"}},
		},
	}
	indexed := []scopedIndexCandidate{
		{
			scope: owner, candidate: memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: "matched-episode"},
			querySourceRef: "query", rank: 4, laneOrder: 2,
		},
		{
			scope:          ownerScope{Kind: owner.Kind, TenantRef: owner.TenantRef, AgentRef: owner.AgentRef, RelationshipRef: "other"},
			candidate:      memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: "matched-episode"},
			querySourceRef: "other-query", rank: 0, laneOrder: 0,
		},
	}

	got := attachEpisodeSupportIndexMatches(candidates, indexed, owner)
	want := []selection.IndexMatch{{
		QuerySourceRef: "query", SupportEpisodeRef: "matched-episode", Rank: 4, LaneOrder: 2,
	}}
	if !reflect.DeepEqual(got[0].IndexMatches, want) {
		t.Fatalf("linked IndexMatches = %#v; want %#v", got[0].IndexMatches, want)
	}
	if len(got[1].IndexMatches) != 0 || len(got[2].IndexMatches) != 0 {
		t.Fatalf("support Episode leaked to sibling candidates: %#v", got)
	}
}

func TestMemoryRequestHashKeepsLegacyValueAtZeroEpisodeEvidenceBudget(t *testing.T) {
	request := selection.SelectRequest{
		Scope: ledger.Scope{
			Kind: ledger.ScopeKindRelationship, TenantRef: "tenant", AgentRef: "agent",
			RelationshipRef: "relationship", SessionRef: "session",
		},
		RunRef: "run", SituationSourceRefs: []string{"situation-a", "situation-b"},
		Constitution: selection.Constitution{MemoryRef: "constitution", Text: "baseline"},
	}

	hash := memoryRequestHash(request)
	got := hex.EncodeToString(hash[:])
	const want = "c1bb31dff509434ffd3ca8eea3b118e80df9ee8e11d1febd7de488161db180b1"
	if got != want {
		t.Fatalf("zero-budget memory request hash = %s; want legacy %s", got, want)
	}
	request.EpisodeEvidenceMaxBytes = 1
	nextHash := memoryRequestHash(request)
	if next := hex.EncodeToString(nextHash[:]); next == want {
		t.Fatal("nonzero Episode evidence budget did not enter memory request hash")
	}
}

func TestMemoryRequestHashSeparatesNonzeroBudgetFromValidZeroBudgetSituationRefs(t *testing.T) {
	enabled := selection.SelectRequest{
		Scope: ledger.Scope{
			Kind: ledger.ScopeKindRelationship, TenantRef: "tenant", AgentRef: "agent",
			RelationshipRef: "relationship", SessionRef: "session",
		},
		RunRef: "run", SituationSourceRefs: []string{"q"}, EpisodeEvidenceMaxBytes: 1024,
	}
	disabled := enabled
	disabled.SituationSourceRefs = []string{"q", "episode-evidence.v1", "1024"}
	disabled.EpisodeEvidenceMaxBytes = 0

	if memoryRequestHash(enabled) == memoryRequestHash(disabled) {
		t.Fatal("nonzero Episode evidence request hash collides with a valid zero-budget situation-ref shape")
	}
}
