package memoryindexgrpc

import (
	"context"
	"reflect"
	"testing"
	"time"

	memoryindexv1 "github.com/aig-dev/ChoraMem/gen/memoryindex/v1"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
)

func TestClientUsesGeneratedProtobufSearchAndPreservesCandidateOrder(t *testing.T) {
	generated := &fakeGeneratedClient{search: func(ctx context.Context, request *memoryindexv1.SearchRequest) (*memoryindexv1.SearchResponse, error) {
		want := &memoryindexv1.SearchRequest{Query: &memoryindexv1.Query{
			Scope: &memoryindexv1.Scope{
				TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1",
			},
			Text: "先确认分歧", Limit: 24,
		}}
		if !reflect.DeepEqual(request, want) {
			t.Fatalf("request = %#v; want %#v", request, want)
		}
		outgoing, ok := metadata.FromOutgoingContext(ctx)
		if !ok || !reflect.DeepEqual(outgoing.Get("x-agent-rpc-token"), []string{"test-token"}) {
			t.Fatalf("outgoing metadata = %#v", outgoing)
		}
		if deadline, ok := ctx.Deadline(); !ok || time.Until(deadline) > time.Second {
			t.Fatalf("deadline = %v, %v; want configured timeout", deadline, ok)
		}
		return &memoryindexv1.SearchResponse{Candidates: []*memoryindexv1.Candidate{
			{Kind: memoryindexv1.MemoryKind_MEMORY_KIND_EPISODE, Ref: "episode-2"},
			{Kind: memoryindexv1.MemoryKind_MEMORY_KIND_DISPOSITION, Ref: "disposition-version-1"},
		}}, nil
	}}
	client := New(generated, Config{RPCToken: " test-token ", CallTimeout: time.Second})

	got, err := client.Search(context.Background(), memoryindex.Query{
		Scope: memoryindex.Scope{
			TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1",
		},
		Text: "先确认分歧", Limit: 24,
	})
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	want := []memoryindex.Candidate{
		{Kind: memoryindex.KindEpisode, Ref: "episode-2"},
		{Kind: memoryindex.KindDisposition, Ref: "disposition-version-1"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("candidates = %#v; want %#v", got, want)
	}
}

func TestClientMapsAllProjectionMutationsToGeneratedMessages(t *testing.T) {
	generated := &fakeGeneratedClient{}
	client := New(generated, Config{})
	scope := memoryindex.Scope{TenantRef: "tenant-1", AgentRef: "agent-1"}
	documents := []memoryindex.Document{{
		Kind: memoryindex.KindRecollection, Ref: "recollection-version-1",
		Scope: scope, Text: "记得先复述约束",
	}}
	candidates := []memoryindex.Candidate{{Kind: memoryindex.KindRecollection, Ref: "recollection-version-1"}}

	if err := client.Upsert(context.Background(), documents); err != nil {
		t.Fatalf("Upsert: %v", err)
	}
	if err := client.Delete(context.Background(), scope, candidates); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if err := client.Reset(context.Background()); err != nil {
		t.Fatalf("Reset: %v", err)
	}
	if generated.upsert == nil || generated.upsert.Documents[0].Kind != memoryindexv1.MemoryKind_MEMORY_KIND_RECOLLECTION ||
		generated.upsert.Documents[0].Ref != "recollection-version-1" {
		t.Fatalf("upsert request = %#v", generated.upsert)
	}
	if generated.delete == nil || !reflect.DeepEqual(generated.delete.Scope, &memoryindexv1.Scope{
		TenantRef: "tenant-1", AgentRef: "agent-1",
	}) || generated.delete.Candidates[0].Ref != "recollection-version-1" {
		t.Fatalf("delete request = %#v", generated.delete)
	}
	if generated.resetCalls != 1 {
		t.Fatalf("reset calls = %d; want 1", generated.resetCalls)
	}
}

type fakeGeneratedClient struct {
	search     func(context.Context, *memoryindexv1.SearchRequest) (*memoryindexv1.SearchResponse, error)
	upsert     *memoryindexv1.UpsertRequest
	delete     *memoryindexv1.DeleteRequest
	resetCalls int
}

func (client *fakeGeneratedClient) Search(ctx context.Context, request *memoryindexv1.SearchRequest, _ ...grpc.CallOption) (*memoryindexv1.SearchResponse, error) {
	return client.search(ctx, request)
}

func (client *fakeGeneratedClient) Upsert(_ context.Context, request *memoryindexv1.UpsertRequest, _ ...grpc.CallOption) (*memoryindexv1.UpsertResponse, error) {
	client.upsert = request
	return &memoryindexv1.UpsertResponse{}, nil
}

func (client *fakeGeneratedClient) Delete(_ context.Context, request *memoryindexv1.DeleteRequest, _ ...grpc.CallOption) (*memoryindexv1.DeleteResponse, error) {
	client.delete = request
	return &memoryindexv1.DeleteResponse{}, nil
}

func (client *fakeGeneratedClient) Reset(context.Context, *memoryindexv1.ResetRequest, ...grpc.CallOption) (*memoryindexv1.ResetResponse, error) {
	client.resetCalls++
	return &memoryindexv1.ResetResponse{}, nil
}
