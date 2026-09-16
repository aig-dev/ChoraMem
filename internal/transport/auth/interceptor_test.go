package auth

import (
	"context"
	"errors"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func TestGRPCBearerInterceptorRejectsMalformedHeadersBeforeHandler(t *testing.T) {
	verifierCalls := 0
	authenticator, err := NewBearerAuthenticator(VerifyFunc(func(_ context.Context, token string) (Identity, error) {
		verifierCalls++
		if token != "valid" {
			return Identity{}, ErrUnauthenticated
		}
		return Identity{TenantRef: "tenant-1"}, nil
	}))
	if err != nil {
		t.Fatalf("NewBearerAuthenticator: %v", err)
	}
	interceptor := authenticator.GRPCUnaryServerInterceptor()

	tests := []struct {
		name    string
		values  []string
		wantVer int
	}{
		{name: "missing"},
		{name: "duplicate", values: []string{"Bearer valid", "Bearer valid"}},
		{name: "wrong scheme", values: []string{"Basic valid"}},
		{name: "blank token", values: []string{"Bearer  "}},
		{name: "invalid token", values: []string{"Bearer invalid"}, wantVer: 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			beforeVerify := verifierCalls
			handlerCalls := 0
			ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs())
			if test.values != nil {
				ctx = metadata.NewIncomingContext(context.Background(), metadata.MD{"authorization": test.values})
			}
			_, callErr := interceptor(ctx, nil, &grpc.UnaryServerInfo{FullMethod: "/memory.v1.MemoryCore/SelectMemory"}, func(context.Context, any) (any, error) {
				handlerCalls++
				return nil, nil
			})
			if status.Code(callErr) != codes.Unauthenticated {
				t.Fatalf("status = %v, want Unauthenticated", status.Code(callErr))
			}
			if handlerCalls != 0 {
				t.Fatalf("handler calls = %d, want 0", handlerCalls)
			}
			if got := verifierCalls - beforeVerify; got != test.wantVer {
				t.Fatalf("verifier calls = %d, want %d", got, test.wantVer)
			}
		})
	}
}

func TestGRPCBearerInterceptorInjectsVerifiedIdentity(t *testing.T) {
	authenticator, err := NewBearerAuthenticator(VerifyFunc(func(_ context.Context, token string) (Identity, error) {
		if token != "valid" {
			return Identity{}, errors.New("invalid")
		}
		return Identity{TenantRef: "tenant-1"}, nil
	}))
	if err != nil {
		t.Fatalf("NewBearerAuthenticator: %v", err)
	}
	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("authorization", "Bearer valid"))
	_, err = authenticator.GRPCUnaryServerInterceptor()(ctx, nil, &grpc.UnaryServerInfo{}, func(ctx context.Context, _ any) (any, error) {
		return nil, AuthorizeTenant(ctx, "tenant-1")
	})
	if err != nil {
		t.Fatalf("interceptor rejected valid bearer: %v", err)
	}
}

func TestTrustedLoopbackInterceptorMarksRequestWithoutBearer(t *testing.T) {
	authenticator := NewTrustedLoopbackAuthenticator()
	_, err := authenticator.GRPCUnaryServerInterceptor()(context.Background(), nil, &grpc.UnaryServerInfo{}, func(ctx context.Context, _ any) (any, error) {
		return nil, AuthorizeTenant(ctx, "any-tenant")
	})
	if err != nil {
		t.Fatalf("trusted loopback interceptor: %v", err)
	}
}
