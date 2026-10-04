package memorygrpc

import (
	"context"
	"testing"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestReplayMapsOriginalRequestAndHistoricalConstitution(t *testing.T) {
	core := &recordingCore{memoryContext: selection.MemoryContext{Ref: "context-1", RunRef: "run-1", Scope: relationshipScope(), Constitution: selection.Constitution{MemoryRef: "historical-v1", Text: "historical role"}}}
	server := NewServer(core)
	ctx := auth.ContextWithIdentity(context.Background(), auth.Identity{TenantRef: "tenant-1"})
	request := validSelectRequest()
	request.Constitution = nil
	response, err := server.ReplayMemoryContext(ctx, request)
	if err != nil || response.GetConstitution().GetMemoryRef() != "historical-v1" || core.calls != 1 {
		t.Fatalf("historical replay = (%#v, %v), calls %d", response, err, core.calls)
	}
	if core.replayRequest.Constitution != (selection.Constitution{}) || core.replayRequest.EpisodeEvidenceMaxBytes != int(request.EpisodeEvidenceMaxBytes) || core.replayRequest.RunRef != request.RunRef || core.replayRequest.Scope != relationshipScope() {
		t.Fatalf("replay request = %#v", core.replayRequest)
	}
	for _, constitution := range []*memoryv1.Constitution{{}, {MemoryRef: "caller", Text: "caller role"}} {
		request.Constitution = constitution
		if _, err := server.ReplayMemoryContext(ctx, request); status.Code(err) != codes.InvalidArgument || core.calls != 1 {
			t.Fatalf("caller Constitution reached read: %v, calls %d", err, core.calls)
		}
	}
	request.Constitution = nil
	core.err = selection.ErrMemoryContextNotFound
	if _, err := server.ReplayMemoryContext(ctx, request); status.Code(err) != codes.NotFound {
		t.Fatalf("missing replay = %v", err)
	}
	core.err = selection.ErrRunConflict
	if _, err := server.ReplayMemoryContext(ctx, request); status.Code(err) != codes.AlreadyExists {
		t.Fatalf("changed replay = %v", err)
	}
}
