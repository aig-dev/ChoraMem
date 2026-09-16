package auth

import (
	"context"
	"errors"
	"strings"
)

var (
	ErrUnauthenticated  = errors.New("authentication is required")
	ErrPermissionDenied = errors.New("authenticated tenant does not match request tenant")
)

type Identity struct {
	TenantRef string
}

type identityContextKey struct{}
type trustedLoopbackContextKey struct{}

func ContextWithIdentity(ctx context.Context, identity Identity) context.Context {
	return context.WithValue(ctx, identityContextKey{}, identity)
}

func trustedLoopbackContext(ctx context.Context) context.Context {
	return context.WithValue(ctx, trustedLoopbackContextKey{}, true)
}

func AuthorizeTenant(ctx context.Context, tenantRef string) error {
	if trusted, _ := ctx.Value(trustedLoopbackContextKey{}).(bool); trusted {
		return nil
	}
	identity, ok := ctx.Value(identityContextKey{}).(Identity)
	if !ok || strings.TrimSpace(identity.TenantRef) == "" {
		return ErrUnauthenticated
	}
	if identity.TenantRef != tenantRef {
		return ErrPermissionDenied
	}
	return nil
}
