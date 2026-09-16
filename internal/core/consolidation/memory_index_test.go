package consolidation

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/memoryindex"
)

func TestSearchMemoryIndexDeduplicatesBoundsAndFailsOpen(t *testing.T) {
	index := &recordingMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		switch query.Text {
		case "same situation":
			return []memoryindex.Candidate{
				{Kind: memoryindex.KindEpisode, Ref: "episode-1"},
				{Kind: memoryindex.KindEpisode, Ref: "episode-2"},
				{Kind: memoryindex.KindEpisode, Ref: "episode-ignored-beyond-response-limit"},
			}, nil
		case "provider failure":
			return nil, errors.New("offline")
		default:
			return nil, nil
		}
	}}
	scope := memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent", RelationshipRef: "relationship"}

	got := SearchMemoryIndex(context.Background(), index, scope, []string{
		"same situation", " same situation ", "", "provider failure",
	}, 2)

	if !reflect.DeepEqual(got.EpisodeRefs, []string{"episode-1", "episode-2"}) ||
		len(got.RecollectionRefs) != 0 || len(got.DispositionRefs) != 0 {
		t.Fatalf("SearchMemoryIndex = %#v", got)
	}
	wantQueries := []memoryindex.Query{
		{Scope: scope, Text: "same situation", Limit: 2},
		{Scope: scope, Text: "provider failure", Limit: 2},
	}
	if !reflect.DeepEqual(index.queries, wantQueries) {
		t.Fatalf("queries = %#v; want %#v", index.queries, wantQueries)
	}
}

func TestFuseEpisodeCandidateRanksRecoversCandidateSharedAcrossSemanticViews(t *testing.T) {
	primary := []memoryindex.Candidate{
		{Kind: memoryindex.KindEpisode, Ref: "episode-anchor"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-target-one"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-distractor"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-target-two"},
		{Kind: memoryindex.KindRecollection, Ref: "recollection-ignored"},
	}
	condition := []memoryindex.Candidate{
		{Kind: memoryindex.KindEpisode, Ref: "episode-anchor"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-target-two"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-target-one"},
		{Kind: memoryindex.KindDisposition, Ref: "seed-ignored"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-distractor"},
	}

	got := FuseEpisodeCandidateRanks(5, primary, condition)

	want := []string{
		"episode-anchor",
		"episode-target-one",
		"episode-target-two",
		"episode-distractor",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("FuseEpisodeCandidateRanks() = %#v; want %#v", got, want)
	}
}

func TestFuseEpisodeCandidateRanksPreservesPrimaryFallbackAndBounds(t *testing.T) {
	primary := []memoryindex.Candidate{
		{Kind: memoryindex.KindEpisode, Ref: "episode-one"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-two"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-one"},
		{Kind: memoryindex.KindEpisode, Ref: "episode-three"},
	}

	got := FuseEpisodeCandidateRanks(2, primary)

	if !reflect.DeepEqual(got, []string{"episode-one", "episode-two"}) {
		t.Fatalf("FuseEpisodeCandidateRanks() = %#v", got)
	}
}

type recordingMemoryIndex struct {
	search  func(memoryindex.Query) ([]memoryindex.Candidate, error)
	queries []memoryindex.Query
}

func (index *recordingMemoryIndex) Search(_ context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	index.queries = append(index.queries, query)
	return index.search(query)
}

func (*recordingMemoryIndex) Upsert(context.Context, []memoryindex.Document) error { return nil }
func (*recordingMemoryIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (*recordingMemoryIndex) Reset(context.Context) error { return nil }
