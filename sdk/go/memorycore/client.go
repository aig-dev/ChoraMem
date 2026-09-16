// Package memorycore is the thin Go SDK for memory.v1.MemoryCore.
// It mirrors the public RPCs and deliberately contains no Memory state machine.
package memorycore

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/metadata"
)

// TokenProvider returns one bearer token for the exact request tenant.
type TokenProvider interface {
	Token(context.Context, string) (string, error)
}

// TokenProviderFunc adapts a function to TokenProvider.
type TokenProviderFunc func(context.Context, string) (string, error)

func (provider TokenProviderFunc) Token(ctx context.Context, tenantRef string) (string, error) {
	return provider(ctx, tenantRef)
}

type Config struct {
	Endpoint             string
	DefaultTimeout       time.Duration
	TokenProvider        TokenProvider
	TransportCredentials credentials.TransportCredentials
	DialOptions          []grpc.DialOption
}

const DefaultTimeout = 10 * time.Second

var (
	ErrInvalidRequest = errors.New("invalid MemoryCore request")
	ErrInvalidToken   = errors.New("invalid MemoryCore bearer token")
)

type Client struct {
	rpc             memoryv1.MemoryCoreClient
	config          Config
	ownedConnection *grpc.ClientConn
	closeOnce       sync.Once
	closeErr        error
}

// New wraps an existing generated client. Callers retain transport ownership.
func New(rpc memoryv1.MemoryCoreClient, config Config) (*Client, error) {
	if rpc == nil {
		return nil, errors.New("generated MemoryCore client is required")
	}
	if config.TokenProvider == nil {
		return nil, errors.New("tenant token provider is required")
	}
	if config.DefaultTimeout < 0 {
		return nil, errors.New("default timeout must not be negative")
	}
	if config.DefaultTimeout == 0 {
		config.DefaultTimeout = DefaultTimeout
	}
	return &Client{rpc: rpc, config: config}, nil
}

// NewFromConnection builds a caller-owned client from an explicit connection.
func NewFromConnection(connection grpc.ClientConnInterface, config Config) (*Client, error) {
	if connection == nil {
		return nil, errors.New("gRPC connection is required")
	}
	return New(memoryv1.NewMemoryCoreClient(connection), config)
}

// Dial creates and owns one gRPC connection. Transport credentials are always
// explicit; the SDK has no implicit insecure network default.
func Dial(_ context.Context, config Config) (*Client, error) {
	endpoint := strings.TrimSpace(config.Endpoint)
	if endpoint == "" {
		return nil, errors.New("MemoryCore endpoint is required")
	}
	if config.TransportCredentials == nil {
		return nil, errors.New("MemoryCore transport credentials are required")
	}
	options := make([]grpc.DialOption, 0, len(config.DialOptions)+1)
	options = append(options, config.DialOptions...)
	// TransportCredentials is a first-class security boundary, not an ordinary
	// extension option. Append it last so an opaque DialOption cannot silently
	// downgrade the explicitly configured transport.
	options = append(options, grpc.WithTransportCredentials(config.TransportCredentials))
	connection, err := grpc.NewClient(endpoint, options...)
	if err != nil {
		return nil, fmt.Errorf("dial MemoryCore: %w", err)
	}
	client, err := New(memoryv1.NewMemoryCoreClient(connection), config)
	if err != nil {
		_ = connection.Close()
		return nil, err
	}
	client.ownedConnection = connection
	return client, nil
}

// Close releases a Dial-owned connection once. It is a safe no-op for
// caller-owned transports.
func (client *Client) Close() error {
	if client == nil || client.ownedConnection == nil {
		return nil
	}
	client.closeOnce.Do(func() { client.closeErr = client.ownedConnection.Close() })
	return client.closeErr
}

func (client *Client) ObserveSourceEvent(ctx context.Context, request *memoryv1.ObserveSourceEventRequest, options ...grpc.CallOption) (*memoryv1.SourceEventReceipt, error) {
	if request == nil || request.GetSourceEvent() == nil {
		return nil, fmt.Errorf("%w: ObserveSourceEvent requires source_event.scope.tenant_ref", ErrInvalidRequest)
	}
	tenantRef, err := requestTenant(request.GetSourceEvent().GetScope())
	if err != nil {
		return nil, err
	}
	callContext, cancel, err := client.callContext(ctx, tenantRef)
	if err != nil {
		return nil, err
	}
	defer cancel()
	return client.rpc.ObserveSourceEvent(callContext, request, options...)
}

func (client *Client) SelectMemory(ctx context.Context, request *memoryv1.SelectMemoryRequest, options ...grpc.CallOption) (*memoryv1.MemoryContext, error) {
	if request == nil {
		return nil, fmt.Errorf("%w: SelectMemory requires scope.tenant_ref", ErrInvalidRequest)
	}
	tenantRef, err := requestTenant(request.GetScope())
	if err != nil {
		return nil, err
	}
	callContext, cancel, err := client.callContext(ctx, tenantRef)
	if err != nil {
		return nil, err
	}
	defer cancel()
	return client.rpc.SelectMemory(callContext, request, options...)
}

func (client *Client) RecordMemoryDelivery(ctx context.Context, request *memoryv1.MemoryDeliveryReceipt, options ...grpc.CallOption) (*memoryv1.ReceiptAck, error) {
	if request == nil {
		return nil, fmt.Errorf("%w: RecordMemoryDelivery requires scope.tenant_ref", ErrInvalidRequest)
	}
	tenantRef, err := requestTenant(request.GetScope())
	if err != nil {
		return nil, err
	}
	callContext, cancel, err := client.callContext(ctx, tenantRef)
	if err != nil {
		return nil, err
	}
	defer cancel()
	return client.rpc.RecordMemoryDelivery(callContext, request, options...)
}

func (client *Client) ReportOutcome(ctx context.Context, request *memoryv1.ReportOutcomeRequest, options ...grpc.CallOption) (*memoryv1.OutcomeReceipt, error) {
	if request == nil {
		return nil, fmt.Errorf("%w: ReportOutcome requires scope.tenant_ref", ErrInvalidRequest)
	}
	tenantRef, err := requestTenant(request.GetScope())
	if err != nil {
		return nil, err
	}
	callContext, cancel, err := client.callContext(ctx, tenantRef)
	if err != nil {
		return nil, err
	}
	defer cancel()
	return client.rpc.ReportOutcome(callContext, request, options...)
}

func requestTenant(scope *memoryv1.MemoryScope) (string, error) {
	if scope == nil || strings.TrimSpace(scope.GetTenantRef()) == "" {
		return "", fmt.Errorf("%w: nonblank tenant_ref is required", ErrInvalidRequest)
	}
	return scope.GetTenantRef(), nil
}

func (client *Client) callContext(ctx context.Context, tenantRef string) (context.Context, context.CancelFunc, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	callContext, cancel := context.WithTimeout(ctx, client.config.DefaultTimeout)
	token, err := client.config.TokenProvider.Token(callContext, tenantRef)
	if err != nil {
		cancel()
		return nil, nil, err
	}
	if token == "" || strings.IndexFunc(token, unicode.IsSpace) >= 0 {
		cancel()
		return nil, nil, ErrInvalidToken
	}
	outgoing, _ := metadata.FromOutgoingContext(callContext)
	outgoing = outgoing.Copy()
	outgoing.Set("authorization", "Bearer "+token)
	return metadata.NewOutgoingContext(callContext, outgoing), cancel, nil
}
