package memorycore

import (
	"context"
	"errors"
	"net"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/test/bufconn"
)

func TestClientDelegatesFourGeneratedMethodsExactlyOnce(t *testing.T) {
	rpc := &fakeRPC{}
	client, err := New(rpc, Config{TokenProvider: staticToken("token")})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	ctx := context.Background()
	scope := &memoryv1.MemoryScope{TenantRef: "tenant-1"}
	baseline := &memoryv1.Constitution{MemoryRef: "role-v1", Text: "Listen first"}
	observe := &memoryv1.ObserveSourceEventRequest{SourceEvent: &memoryv1.SourceEvent{Scope: scope, Constitution: baseline}}
	selectRequest := &memoryv1.SelectMemoryRequest{Scope: scope}
	delivery := &memoryv1.MemoryDeliveryReceipt{Scope: scope}
	outcome := &memoryv1.ReportOutcomeRequest{Scope: scope, Constitution: baseline}

	if _, err := client.ObserveSourceEvent(ctx, observe); err != nil {
		t.Fatalf("ObserveSourceEvent: %v", err)
	}
	if _, err := client.SelectMemory(ctx, selectRequest); err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if _, err := client.RecordMemoryDelivery(ctx, delivery); err != nil {
		t.Fatalf("RecordMemoryDelivery: %v", err)
	}
	if _, err := client.ReportOutcome(ctx, outcome); err != nil {
		t.Fatalf("ReportOutcome: %v", err)
	}

	if rpc.observe != observe || rpc.observeCalls != 1 {
		t.Fatalf("Observe delegation = (%p, %d)", rpc.observe, rpc.observeCalls)
	}
	if rpc.selectRequest != selectRequest || rpc.selectCalls != 1 {
		t.Fatalf("Select delegation = (%p, %d)", rpc.selectRequest, rpc.selectCalls)
	}
	if rpc.delivery != delivery || rpc.deliveryCalls != 1 {
		t.Fatalf("Delivery delegation = (%p, %d)", rpc.delivery, rpc.deliveryCalls)
	}
	if rpc.outcome != outcome || rpc.outcomeCalls != 1 {
		t.Fatalf("Outcome delegation = (%p, %d)", rpc.outcome, rpc.outcomeCalls)
	}
}

func TestClientRejectsMissingOrMalformedTenantBeforeTokenOrRPC(t *testing.T) {
	providerCalls := 0
	rpc := &fakeRPC{}
	client, err := New(rpc, Config{TokenProvider: TokenProviderFunc(func(context.Context, string) (string, error) {
		providerCalls++
		return "token", nil
	})})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	tests := []struct {
		name string
		call func() error
	}{
		{name: "observe nil", call: func() error { _, err := client.ObserveSourceEvent(context.Background(), nil); return err }},
		{name: "observe missing nested scope", call: func() error {
			_, err := client.ObserveSourceEvent(context.Background(), &memoryv1.ObserveSourceEventRequest{SourceEvent: &memoryv1.SourceEvent{}})
			return err
		}},
		{name: "select blank tenant", call: func() error {
			_, err := client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{Scope: &memoryv1.MemoryScope{}})
			return err
		}},
		{name: "delivery all-whitespace tenant", call: func() error {
			_, err := client.RecordMemoryDelivery(context.Background(), &memoryv1.MemoryDeliveryReceipt{Scope: &memoryv1.MemoryScope{TenantRef: " \t "}})
			return err
		}},
		{name: "outcome missing scope", call: func() error {
			_, err := client.ReportOutcome(context.Background(), &memoryv1.ReportOutcomeRequest{})
			return err
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.call(); !errors.Is(err, ErrInvalidRequest) {
				t.Fatalf("error = %v; want ErrInvalidRequest", err)
			}
		})
	}
	if providerCalls != 0 || rpc.totalCalls() != 0 {
		t.Fatalf("malformed calls reached provider=%d rpc=%d", providerCalls, rpc.totalCalls())
	}
}

func TestClientPropagatesTokenProviderFailureBeforeRPC(t *testing.T) {
	wantErr := errors.New("sign tenant token")
	rpc := &fakeRPC{}
	client, err := New(rpc, Config{TokenProvider: TokenProviderFunc(func(context.Context, string) (string, error) {
		return "", wantErr
	})})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	_, err = client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{
		Scope: &memoryv1.MemoryScope{TenantRef: "tenant-1"},
	})
	if !errors.Is(err, wantErr) {
		t.Fatalf("provider error = %v; want %v", err, wantErr)
	}
	if rpc.totalCalls() != 0 {
		t.Fatalf("provider failure invoked generated RPC %d times", rpc.totalCalls())
	}
}

func TestClientRejectsBlankProviderTokenBeforeRPC(t *testing.T) {
	rpc := &fakeRPC{}
	client, err := New(rpc, Config{TokenProvider: staticToken("   ")})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	_, err = client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{
		Scope: &memoryv1.MemoryScope{TenantRef: "tenant-1"},
	})
	if !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("blank token error = %v; want ErrInvalidToken", err)
	}
	if rpc.totalCalls() != 0 {
		t.Fatalf("blank token invoked generated RPC %d times", rpc.totalCalls())
	}
}

func TestCallerOwnedGeneratedClientCloseIsIdempotentNoOp(t *testing.T) {
	rpc := &fakeRPC{}
	client, err := New(rpc, Config{TokenProvider: staticToken("token")})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("first Close: %v", err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	if _, err := client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{
		Scope: &memoryv1.MemoryScope{TenantRef: "tenant-1"},
	}); err != nil {
		t.Fatalf("caller-owned generated client was closed: %v", err)
	}
	if rpc.selectCalls != 1 {
		t.Fatalf("caller-owned Select calls = %d", rpc.selectCalls)
	}
}

func TestDialOwnsConnectionAndCloseIsIdempotent(t *testing.T) {
	listener := bufconn.Listen(1024 * 1024)
	server := grpc.NewServer()
	service := &testMemoryCoreServer{}
	memoryv1.RegisterMemoryCoreServer(server, service)
	serveDone := make(chan error, 1)
	go func() { serveDone <- server.Serve(listener) }()
	t.Cleanup(func() {
		server.Stop()
		_ = listener.Close()
		<-serveDone
	})

	client, err := Dial(context.Background(), Config{
		Endpoint:             "passthrough:///memory-core-test",
		TokenProvider:        staticToken("owned-token"),
		TransportCredentials: insecure.NewCredentials(),
		DialOptions: []grpc.DialOption{grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) {
			return listener.Dial()
		})},
	})
	if err != nil {
		t.Fatalf("Dial: %v", err)
	}
	request := &memoryv1.SelectMemoryRequest{Scope: &memoryv1.MemoryScope{TenantRef: "tenant-owned"}}
	if _, err := client.SelectMemory(context.Background(), request); err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if service.selectCalls != 1 || service.authorization != "Bearer owned-token" {
		t.Fatalf("owned call count=%d authorization=%q", service.selectCalls, service.authorization)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("first Close: %v", err)
	}
	if err := client.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	if _, err := client.SelectMemory(context.Background(), request); err == nil {
		t.Fatal("owned connection remained usable after Close")
	}
}

func TestDialTransportCredentialsCannotBeOverriddenByDialOptions(t *testing.T) {
	listener := bufconn.Listen(1024 * 1024)
	server := grpc.NewServer()
	memoryv1.RegisterMemoryCoreServer(server, &testMemoryCoreServer{})
	serveDone := make(chan error, 1)
	go func() { serveDone <- server.Serve(listener) }()
	t.Cleanup(func() {
		server.Stop()
		_ = listener.Close()
		<-serveDone
	})

	authoritative := &countingCredentials{TransportCredentials: insecure.NewCredentials()}
	client, err := Dial(context.Background(), Config{
		Endpoint:             "passthrough:///memory-core-credential-order",
		TokenProvider:        staticToken("token"),
		TransportCredentials: authoritative,
		DialOptions: []grpc.DialOption{
			grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return listener.Dial() }),
			grpc.WithTransportCredentials(insecure.NewCredentials()),
		},
	})
	if err != nil {
		t.Fatalf("Dial: %v", err)
	}
	t.Cleanup(func() { _ = client.Close() })
	if _, err := client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{
		Scope: &memoryv1.MemoryScope{TenantRef: "tenant-1"},
	}); err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if authoritative.handshakes.Load() == 0 {
		t.Fatal("DialOptions overrode Config.TransportCredentials")
	}
}

func TestClientAppliesDefaultDeadlineWithoutReplacingShorterCallerDeadline(t *testing.T) {
	var remaining []time.Duration
	rpc := &fakeRPC{inspectContext: func(ctx context.Context) {
		deadline, ok := ctx.Deadline()
		if !ok {
			t.Fatal("generated call has no deadline")
		}
		remaining = append(remaining, time.Until(deadline))
	}}
	client, err := New(rpc, Config{TokenProvider: staticToken("token"), DefaultTimeout: 500 * time.Millisecond})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	request := &memoryv1.SelectMemoryRequest{Scope: &memoryv1.MemoryScope{TenantRef: "tenant-1"}}
	if _, err := client.SelectMemory(context.Background(), request); err != nil {
		t.Fatalf("default deadline call: %v", err)
	}
	shortContext, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if _, err := client.SelectMemory(shortContext, request); err != nil {
		t.Fatalf("short deadline call: %v", err)
	}
	if len(remaining) != 2 || remaining[0] <= 200*time.Millisecond || remaining[0] > 500*time.Millisecond {
		t.Fatalf("default deadline remaining = %#v", remaining)
	}
	if remaining[1] <= 0 || remaining[1] > 100*time.Millisecond {
		t.Fatalf("short caller deadline remaining = %v", remaining[1])
	}
}

func TestClientRequestsTokenForExactTenantAndSetsBearerOnEveryCall(t *testing.T) {
	var tenants []string
	provider := TokenProviderFunc(func(_ context.Context, tenantRef string) (string, error) {
		tenants = append(tenants, tenantRef)
		return "token-for-" + tenantRef, nil
	})
	rpc := &fakeRPC{inspectContext: func(ctx context.Context) {
		outgoing, _ := metadata.FromOutgoingContext(ctx)
		values := outgoing.Get("authorization")
		if len(values) != 1 || values[0] != "Bearer token-for-"+tenants[len(tenants)-1] {
			t.Fatalf("authorization metadata = %#v for tenants %#v", values, tenants)
		}
	}}
	client, err := New(rpc, Config{TokenProvider: provider})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	requests := []func() error{
		func() error {
			_, err := client.ObserveSourceEvent(context.Background(), &memoryv1.ObserveSourceEventRequest{SourceEvent: &memoryv1.SourceEvent{Scope: &memoryv1.MemoryScope{TenantRef: "observe-tenant"}}})
			return err
		},
		func() error {
			_, err := client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{Scope: &memoryv1.MemoryScope{TenantRef: "select-tenant"}})
			return err
		},
		func() error {
			_, err := client.RecordMemoryDelivery(context.Background(), &memoryv1.MemoryDeliveryReceipt{Scope: &memoryv1.MemoryScope{TenantRef: "delivery-tenant"}})
			return err
		},
		func() error {
			_, err := client.ReportOutcome(context.Background(), &memoryv1.ReportOutcomeRequest{Scope: &memoryv1.MemoryScope{TenantRef: "outcome-tenant"}})
			return err
		},
	}
	for _, call := range requests {
		if err := call(); err != nil {
			t.Fatalf("SDK call: %v", err)
		}
	}
	want := []string{"observe-tenant", "select-tenant", "delivery-tenant", "outcome-tenant"}
	if !reflect.DeepEqual(tenants, want) {
		t.Fatalf("token tenants = %#v; want %#v", tenants, want)
	}
}

func TestClientPreservesProtocolValidTenantWithInternalWhitespace(t *testing.T) {
	const tenantRef = "tenant division one"
	var providerTenant string
	rpc := &fakeRPC{}
	client, err := New(rpc, Config{TokenProvider: TokenProviderFunc(func(_ context.Context, got string) (string, error) {
		providerTenant = got
		return "valid-token", nil
	})})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if _, err := client.SelectMemory(context.Background(), &memoryv1.SelectMemoryRequest{
		Scope: &memoryv1.MemoryScope{TenantRef: tenantRef},
	}); err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if providerTenant != tenantRef || rpc.selectCalls != 1 {
		t.Fatalf("provider tenant/calls = (%q, %d), want (%q, 1)", providerTenant, rpc.selectCalls, tenantRef)
	}
}

type staticToken string

func (token staticToken) Token(context.Context, string) (string, error) { return string(token), nil }

type countingCredentials struct {
	credentials.TransportCredentials
	handshakes atomic.Int32
}

func (counting *countingCredentials) ClientHandshake(
	ctx context.Context,
	authority string,
	rawConn net.Conn,
) (net.Conn, credentials.AuthInfo, error) {
	counting.handshakes.Add(1)
	return counting.TransportCredentials.ClientHandshake(ctx, authority, rawConn)
}

type fakeRPC struct {
	observe        *memoryv1.ObserveSourceEventRequest
	selectRequest  *memoryv1.SelectMemoryRequest
	delivery       *memoryv1.MemoryDeliveryReceipt
	outcome        *memoryv1.ReportOutcomeRequest
	observeCalls   int
	selectCalls    int
	deliveryCalls  int
	outcomeCalls   int
	inspectContext func(context.Context)
}

func (fake *fakeRPC) totalCalls() int {
	return fake.observeCalls + fake.selectCalls + fake.deliveryCalls + fake.outcomeCalls
}

func (fake *fakeRPC) inspect(ctx context.Context) {
	if fake.inspectContext != nil {
		fake.inspectContext(ctx)
	}
}

func (fake *fakeRPC) ObserveSourceEvent(ctx context.Context, request *memoryv1.ObserveSourceEventRequest, _ ...grpc.CallOption) (*memoryv1.SourceEventReceipt, error) {
	fake.inspect(ctx)
	fake.observe, fake.observeCalls = request, fake.observeCalls+1
	return &memoryv1.SourceEventReceipt{}, nil
}

func (fake *fakeRPC) SelectMemory(ctx context.Context, request *memoryv1.SelectMemoryRequest, _ ...grpc.CallOption) (*memoryv1.MemoryContext, error) {
	fake.inspect(ctx)
	fake.selectRequest, fake.selectCalls = request, fake.selectCalls+1
	return &memoryv1.MemoryContext{}, nil
}

func (fake *fakeRPC) RecordMemoryDelivery(ctx context.Context, request *memoryv1.MemoryDeliveryReceipt, _ ...grpc.CallOption) (*memoryv1.ReceiptAck, error) {
	fake.inspect(ctx)
	fake.delivery, fake.deliveryCalls = request, fake.deliveryCalls+1
	return &memoryv1.ReceiptAck{}, nil
}

func (fake *fakeRPC) ReportOutcome(ctx context.Context, request *memoryv1.ReportOutcomeRequest, _ ...grpc.CallOption) (*memoryv1.OutcomeReceipt, error) {
	fake.inspect(ctx)
	fake.outcome, fake.outcomeCalls = request, fake.outcomeCalls+1
	return &memoryv1.OutcomeReceipt{}, nil
}

type testMemoryCoreServer struct {
	memoryv1.UnimplementedMemoryCoreServer
	selectCalls   int
	authorization string
}

func (server *testMemoryCoreServer) SelectMemory(ctx context.Context, request *memoryv1.SelectMemoryRequest) (*memoryv1.MemoryContext, error) {
	server.selectCalls++
	incoming, _ := metadata.FromIncomingContext(ctx)
	server.authorization = incoming.Get("authorization")[0]
	return &memoryv1.MemoryContext{Scope: request.GetScope()}, nil
}
