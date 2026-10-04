package memoryconnect

import (
	"context"
	"testing"

	"connectrpc.com/connect"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

func TestReplayBothEntriesRestoreSnapshotAndEnforceAuthAndTenant(t *testing.T) {
	core := &parityCore{contextValue: selection.MemoryContext{Ref: "context-1", RunRef: "run-1", Scope: parityScope(), Constitution: selection.Constitution{MemoryRef: "role:historical", Text: "historical baseline"}}}
	grpcClient, connectClient, _ := startParityServers(t, core)
	request := paritySelectRequest("tenant-1")
	request.Constitution = nil
	grpcValue, err := grpcClient.ReplayMemoryContext(bearerContext("valid"), request)
	if err != nil {
		t.Fatal(err)
	}
	connectRequest := connect.NewRequest(request)
	connectRequest.Header().Set("Authorization", "Bearer valid")
	connectValue, err := connectClient.ReplayMemoryContext(context.Background(), connectRequest)
	if err != nil || !proto.Equal(grpcValue, connectValue.Msg) || grpcValue.GetConstitution().GetMemoryRef() != "role:historical" {
		t.Fatalf("read replay parity = (%#v, %v)", connectValue, err)
	}
	for _, test := range []struct {
		name, tenant string
		tokens       []string
		grpcCode     codes.Code
		connectCode  connect.Code
	}{
		{name: "wrong tenant", tenant: "tenant-other", tokens: []string{"Bearer valid"}, grpcCode: codes.PermissionDenied, connectCode: connect.CodePermissionDenied},
		{name: "missing bearer", tenant: "tenant-1", grpcCode: codes.Unauthenticated, connectCode: connect.CodeUnauthenticated},
		{name: "invalid bearer", tenant: "tenant-1", tokens: []string{"Bearer invalid"}, grpcCode: codes.Unauthenticated, connectCode: connect.CodeUnauthenticated},
		{name: "duplicate bearer", tenant: "tenant-1", tokens: []string{"Bearer valid", "Bearer valid"}, grpcCode: codes.Unauthenticated, connectCode: connect.CodeUnauthenticated},
	} {
		t.Run(test.name, func(t *testing.T) {
			before := core.callCount()
			request := paritySelectRequest(test.tenant)
			request.Constitution = nil
			_, grpcErr := grpcClient.ReplayMemoryContext(metadata.NewOutgoingContext(context.Background(), metadata.MD{"authorization": test.tokens}), request)
			connectRequest := connect.NewRequest(request)
			for _, token := range test.tokens {
				connectRequest.Header().Add("Authorization", token)
			}
			_, connectErr := connectClient.ReplayMemoryContext(context.Background(), connectRequest)
			if status.Code(grpcErr) != test.grpcCode || connect.CodeOf(connectErr) != test.connectCode || core.callCount() != before {
				t.Fatalf("read auth codes grpc=%v connect=%v calls=%d/%d", status.Code(grpcErr), connect.CodeOf(connectErr), core.callCount(), before)
			}
		})
	}
	core.setError(selection.ErrMemoryContextNotFound)
	_, grpcErr := grpcClient.ReplayMemoryContext(bearerContext("valid"), request)
	_, connectErr := connectClient.ReplayMemoryContext(context.Background(), connectRequest)
	if status.Code(grpcErr) != codes.NotFound || connect.CodeOf(connectErr) != connect.CodeNotFound {
		t.Fatalf("missing replay codes = %v, %v", grpcErr, connectErr)
	}
}
