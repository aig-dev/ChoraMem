package memoryconnect

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"connectrpc.com/connect"
	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/gen/memory/v1/memoryv1connect"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	memorygrpc "github.com/aig-dev/ChoraMem/internal/transport/grpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"
	"google.golang.org/protobuf/proto"
)

func TestConnectAndNativeGRPCShareSuccessAndErrorSemantics(t *testing.T) {
	core := &parityCore{contextValue: selection.MemoryContext{
		Ref: "context-1", RunRef: "run-1", Scope: parityScope(),
		Constitution:  selection.Constitution{MemoryRef: "constitution-1", Text: "Stay honest."},
		Recollections: []selection.Recollection{{MemoryRef: "recollection-1", Text: "A prior event.", Application: selection.ApplicationScopeSituation}},
		Dispositions:  []selection.Disposition{{MemoryRef: "disposition-1", Text: "Ask first.", Application: selection.ApplicationScopeRelation}},
	}}
	grpcClient, connectClient, _ := startParityServers(t, core)

	grpcResponse, err := grpcClient.SelectMemory(bearerContext("valid"), paritySelectRequest("tenant-1"))
	if err != nil {
		t.Fatalf("native gRPC SelectMemory: %v", err)
	}
	connectRequest := connect.NewRequest(paritySelectRequest("tenant-1"))
	connectRequest.Header().Set("Authorization", "Bearer valid")
	connectResponse, err := connectClient.SelectMemory(context.Background(), connectRequest)
	if err != nil {
		t.Fatalf("Connect SelectMemory: %v", err)
	}
	if !proto.Equal(grpcResponse, connectResponse.Msg) {
		t.Fatalf("gRPC response %#v != Connect response %#v", grpcResponse, connectResponse.Msg)
	}

	core.setError(selection.ErrRunConflict)
	_, grpcErr := grpcClient.SelectMemory(bearerContext("valid"), paritySelectRequest("tenant-1"))
	connectRequest = connect.NewRequest(paritySelectRequest("tenant-1"))
	connectRequest.Header().Set("Authorization", "Bearer valid")
	_, connectErr := connectClient.SelectMemory(context.Background(), connectRequest)
	if status.Code(grpcErr) != codes.AlreadyExists || connect.CodeOf(connectErr) != connect.CodeAlreadyExists {
		t.Fatalf("error codes = grpc:%v connect:%v", status.Code(grpcErr), connect.CodeOf(connectErr))
	}
}

func TestBothEntriesRejectTenantMismatchBeforeCore(t *testing.T) {
	core := &parityCore{}
	grpcClient, connectClient, _ := startParityServers(t, core)

	before := core.callCount()
	_, grpcErr := grpcClient.SelectMemory(bearerContext("valid"), paritySelectRequest("tenant-other"))
	if status.Code(grpcErr) != codes.PermissionDenied {
		t.Fatalf("gRPC status = %v, want PermissionDenied", status.Code(grpcErr))
	}
	connectRequest := connect.NewRequest(paritySelectRequest("tenant-other"))
	connectRequest.Header().Set("Authorization", "Bearer valid")
	_, connectErr := connectClient.SelectMemory(context.Background(), connectRequest)
	if connect.CodeOf(connectErr) != connect.CodePermissionDenied {
		t.Fatalf("Connect status = %v, want PermissionDenied", connect.CodeOf(connectErr))
	}
	if core.callCount() != before {
		t.Fatalf("Core calls = %d, want unchanged %d", core.callCount(), before)
	}
}

func TestBothEntriesRejectMissingInvalidAndDuplicateBearerBeforeCore(t *testing.T) {
	core := &parityCore{}
	grpcClient, connectClient, _ := startParityServers(t, core)

	tests := []struct {
		name   string
		values []string
	}{
		{name: "missing"},
		{name: "invalid scheme", values: []string{"Basic valid"}},
		{name: "invalid token", values: []string{"Bearer invalid"}},
		{name: "duplicate", values: []string{"Bearer valid", "Bearer valid"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			before := core.callCount()
			grpcCtx := context.Background()
			if test.values != nil {
				grpcCtx = metadata.NewOutgoingContext(grpcCtx, metadata.MD{"authorization": test.values})
			}
			_, grpcErr := grpcClient.SelectMemory(grpcCtx, paritySelectRequest("tenant-1"))
			if status.Code(grpcErr) != codes.Unauthenticated {
				t.Fatalf("gRPC status = %v, want Unauthenticated", status.Code(grpcErr))
			}
			connectRequest := connect.NewRequest(paritySelectRequest("tenant-1"))
			for _, value := range test.values {
				connectRequest.Header().Add("Authorization", value)
			}
			_, connectErr := connectClient.SelectMemory(context.Background(), connectRequest)
			if connect.CodeOf(connectErr) != connect.CodeUnauthenticated {
				t.Fatalf("Connect status = %v, want Unauthenticated", connect.CodeOf(connectErr))
			}
			if core.callCount() != before {
				t.Fatalf("Core calls = %d, want unchanged %d", core.callCount(), before)
			}
		})
	}
}

func TestEveryDataPlaneMethodRequiresBearerOnBothEntries(t *testing.T) {
	core := &parityCore{}
	grpcClient, connectClient, _ := startParityServers(t, core)
	grpcCalls := []func() error{
		func() error {
			_, err := grpcClient.ObserveSourceEvent(context.Background(), &memoryv1.ObserveSourceEventRequest{})
			return err
		},
		func() error {
			_, err := grpcClient.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{})
			return err
		},
		func() error {
			_, err := grpcClient.RecordMemoryDelivery(context.Background(), &memoryv1.MemoryDeliveryReceipt{})
			return err
		},
		func() error {
			_, err := grpcClient.ReportOutcome(context.Background(), &memoryv1.ReportOutcomeRequest{})
			return err
		},
	}
	connectCalls := []func() error{
		func() error {
			_, err := connectClient.ObserveSourceEvent(context.Background(), connect.NewRequest(&memoryv1.ObserveSourceEventRequest{}))
			return err
		},
		func() error {
			_, err := connectClient.SelectMemory(context.Background(), connect.NewRequest(&memoryv1.SelectMemoryRequest{}))
			return err
		},
		func() error {
			_, err := connectClient.RecordMemoryDelivery(context.Background(), connect.NewRequest(&memoryv1.MemoryDeliveryReceipt{}))
			return err
		},
		func() error {
			_, err := connectClient.ReportOutcome(context.Background(), connect.NewRequest(&memoryv1.ReportOutcomeRequest{}))
			return err
		},
	}
	for index := range grpcCalls {
		if got := status.Code(grpcCalls[index]()); got != codes.Unauthenticated {
			t.Fatalf("gRPC method %d status = %v", index, got)
		}
		if got := connect.CodeOf(connectCalls[index]()); got != connect.CodeUnauthenticated {
			t.Fatalf("Connect method %d status = %v", index, got)
		}
	}
	if core.callCount() != 0 {
		t.Fatalf("Core calls = %d, want 0", core.callCount())
	}
}

func TestGeneratedConnectRouteAcceptsJSON(t *testing.T) {
	core := &parityCore{contextValue: selection.MemoryContext{
		Ref: "context-json", RunRef: "run-1", Scope: parityScope(),
		Recollections: []selection.Recollection{{MemoryRef: "recollection-1", Text: "A prior event.", Application: selection.ApplicationScopeSituation}},
	}}
	_, _, baseURL := startParityServers(t, core)
	body := `{"scope":{"tenantRef":"tenant-1","agentRef":"agent-1","relationshipRef":"relationship-1","sessionRef":"session-1","kind":"MEMORY_SCOPE_KIND_RELATIONSHIP"},"runRef":"run-1","situationSourceEventRefs":["situation-1"]}`
	request, err := http.NewRequest(http.MethodPost, baseURL+memoryv1connect.MemoryCoreSelectMemoryProcedure, strings.NewReader(body))
	if err != nil {
		t.Fatalf("new JSON request: %v", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Connect-Protocol-Version", "1")
	request.Header.Set("Authorization", "Bearer valid")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("Connect JSON request: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("Connect JSON status = %d", response.StatusCode)
	}
	var payload struct {
		ContextRef    string `json:"contextRef"`
		Recollections []struct {
			MemoryRef string `json:"memoryRef"`
		} `json:"recollections"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatalf("decode Connect JSON response: %v", err)
	}
	if payload.ContextRef != "context-json" || len(payload.Recollections) != 1 || payload.Recollections[0].MemoryRef != "recollection-1" {
		t.Fatalf("Connect JSON response = %#v", payload)
	}
}

func startParityServers(t *testing.T, core *parityCore) (memoryv1.MemoryCoreClient, memoryv1connect.MemoryCoreClient, string) {
	t.Helper()
	authenticator, err := auth.NewBearerAuthenticator(auth.VerifyFunc(func(_ context.Context, token string) (auth.Identity, error) {
		if token != "valid" {
			return auth.Identity{}, auth.ErrUnauthenticated
		}
		return auth.Identity{TenantRef: "tenant-1"}, nil
	}))
	if err != nil {
		t.Fatalf("NewBearerAuthenticator: %v", err)
	}
	shared := memorygrpc.NewServer(core)

	listener := bufconn.Listen(1024 * 1024)
	grpcServer := grpc.NewServer(grpc.UnaryInterceptor(authenticator.GRPCUnaryServerInterceptor()))
	memoryv1.RegisterMemoryCoreServer(grpcServer, shared)
	go func() { _ = grpcServer.Serve(listener) }()
	t.Cleanup(grpcServer.Stop)
	connection, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("dial bufconn: %v", err)
	}
	t.Cleanup(func() { _ = connection.Close() })

	connectServer := NewServer(shared)
	path, handler := memoryv1connect.NewMemoryCoreHandler(connectServer, connect.WithInterceptors(authenticator.ConnectInterceptor()))
	mux := http.NewServeMux()
	mux.Handle(path, handler)
	httpServer := httptest.NewServer(mux)
	t.Cleanup(httpServer.Close)
	return memoryv1.NewMemoryCoreClient(connection), memoryv1connect.NewMemoryCoreClient(httpServer.Client(), httpServer.URL), httpServer.URL
}

func bearerContext(token string) context.Context {
	return metadata.NewOutgoingContext(context.Background(), metadata.Pairs("authorization", "Bearer "+token))
}

func paritySelectRequest(tenant string) *memoryv1.SelectMemoryRequest {
	return &memoryv1.SelectMemoryRequest{
		Scope:  &memoryv1.MemoryScope{Kind: memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_RELATIONSHIP, TenantRef: tenant, AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-1"},
		RunRef: "run-1", SituationSourceEventRefs: []string{"situation-1"}, Constitution: &memoryv1.Constitution{MemoryRef: "constitution-1", Text: "Stay honest."},
	}
}

func parityScope() ledger.Scope {
	return ledger.Scope{Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-1"}
}

type parityCore struct {
	mu           sync.Mutex
	calls        int
	err          error
	contextValue selection.MemoryContext
}

func (core *parityCore) setError(err error) { core.mu.Lock(); defer core.mu.Unlock(); core.err = err }
func (core *parityCore) callCount() int     { core.mu.Lock(); defer core.mu.Unlock(); return core.calls }
func (core *parityCore) nextError() error {
	core.mu.Lock()
	defer core.mu.Unlock()
	core.calls++
	return core.err
}

func (core *parityCore) Observe(context.Context, string, ledger.SourceEvent, ledger.EpisodeBinding) (ledger.ObserveReceipt, error) {
	return ledger.ObserveReceipt{SourceEventRef: "source-1"}, core.nextError()
}
func (core *parityCore) SelectMemory(context.Context, selection.SelectRequest) (selection.MemoryContext, error) {
	err := core.nextError()
	core.mu.Lock()
	value := core.contextValue
	core.mu.Unlock()
	return value, err
}
func (core *parityCore) RecordMemoryDelivery(context.Context, ledger.MemoryDelivery) (ledger.MemoryDeliveryReceipt, error) {
	return ledger.MemoryDeliveryReceipt{Ref: "delivery-1"}, core.nextError()
}
func (core *parityCore) ReportOutcome(context.Context, ledger.OutcomeReport) (ledger.OutcomeReceipt, error) {
	return ledger.OutcomeReceipt{OutcomeEventRef: "outcome-1"}, core.nextError()
}
