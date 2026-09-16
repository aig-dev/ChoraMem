package auth

import (
	"context"
	"errors"
	"strings"

	"connectrpc.com/connect"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type Authenticator struct {
	verifier Verifier
	trusted  bool
}

func NewBearerAuthenticator(verifier Verifier) (*Authenticator, error) {
	if verifier == nil {
		return nil, errors.New("bearer verifier is required")
	}
	return &Authenticator{verifier: verifier}, nil
}

func NewTrustedLoopbackAuthenticator() *Authenticator {
	return &Authenticator{trusted: true}
}

func (authenticator *Authenticator) GRPCUnaryServerInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		authenticated, err := authenticator.authenticate(ctx, grpcAuthorizationValues(ctx))
		if err != nil {
			return nil, status.Error(codes.Unauthenticated, "valid bearer authentication is required")
		}
		return handler(authenticated, request)
	}
}

func (authenticator *Authenticator) ConnectInterceptor() connect.Interceptor {
	return connect.UnaryInterceptorFunc(func(next connect.UnaryFunc) connect.UnaryFunc {
		return func(ctx context.Context, request connect.AnyRequest) (connect.AnyResponse, error) {
			authenticated, err := authenticator.authenticate(ctx, request.Header().Values("Authorization"))
			if err != nil {
				return nil, connect.NewError(connect.CodeUnauthenticated, errors.New("valid bearer authentication is required"))
			}
			return next(authenticated, request)
		}
	})
}

func (authenticator *Authenticator) authenticate(ctx context.Context, authorizationValues []string) (context.Context, error) {
	if authenticator != nil && authenticator.trusted {
		return trustedLoopbackContext(ctx), nil
	}
	if authenticator == nil || authenticator.verifier == nil || len(authorizationValues) != 1 {
		return nil, ErrUnauthenticated
	}
	parts := strings.Fields(authorizationValues[0])
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") || parts[1] == "" {
		return nil, ErrUnauthenticated
	}
	identity, err := authenticator.verifier.Verify(ctx, parts[1])
	if err != nil || strings.TrimSpace(identity.TenantRef) == "" {
		return nil, ErrUnauthenticated
	}
	return ContextWithIdentity(ctx, identity), nil
}

func grpcAuthorizationValues(ctx context.Context) []string {
	values, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil
	}
	return values.Get("authorization")
}
