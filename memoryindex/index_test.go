package memoryindex_test

import (
	"context"
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/memoryindex"
)

func TestIndexContractCarriesOnlyTypedStableRefsAcrossSearch(t *testing.T) {
	want := []memoryindex.Candidate{
		{Kind: memoryindex.KindEpisode, Ref: "episode-2"},
		{Kind: memoryindex.KindDisposition, Ref: "disposition-version-1"},
	}
	index := recordingIndex{candidates: want}
	query := memoryindex.Query{
		Scope: memoryindex.Scope{
			TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1",
		},
		Text: "先确认分歧", Limit: 24,
	}

	got, err := index.Search(context.Background(), query)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("candidates = %#v; want ordered %#v", got, want)
	}
}

func TestDocumentKindsAreExactlyTheProjectionKinds(t *testing.T) {
	got := []memoryindex.Kind{
		memoryindex.KindEpisode,
		memoryindex.KindRecollection,
		memoryindex.KindDisposition,
	}
	want := []memoryindex.Kind{"EPISODE", "RECOLLECTION", "DISPOSITION"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("kinds = %#v; want %#v", got, want)
	}
}

type recordingIndex struct {
	candidates []memoryindex.Candidate
}

func (index recordingIndex) Search(context.Context, memoryindex.Query) ([]memoryindex.Candidate, error) {
	return append([]memoryindex.Candidate(nil), index.candidates...), nil
}

func (recordingIndex) Upsert(context.Context, []memoryindex.Document) error { return nil }
func (recordingIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (recordingIndex) Reset(context.Context) error { return nil }

var _ memoryindex.Index = recordingIndex{}
