package auth

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

const minimumHS256SecretBytes = 32

type Verifier interface {
	Verify(context.Context, string) (Identity, error)
}

type VerifyFunc func(context.Context, string) (Identity, error)

func (verify VerifyFunc) Verify(ctx context.Context, token string) (Identity, error) {
	return verify(ctx, token)
}

type HS256Config struct {
	Secret   []byte
	Issuer   string
	Audience string
	Now      func() time.Time
}

type hs256Verifier struct {
	secret []byte
	parser *jwt.Parser
}

type tenantClaims struct {
	TenantRef string `json:"tenant_ref"`
	jwt.RegisteredClaims
}

func NewHS256Verifier(configuration HS256Config) (Verifier, error) {
	if len(configuration.Secret) < minimumHS256SecretBytes {
		return nil, fmt.Errorf("HS256 secret must contain at least %d bytes", minimumHS256SecretBytes)
	}
	if strings.TrimSpace(configuration.Issuer) == "" || strings.TrimSpace(configuration.Audience) == "" {
		return nil, errors.New("JWT issuer and audience are required")
	}
	now := configuration.Now
	if now == nil {
		now = time.Now
	}
	return &hs256Verifier{
		secret: append([]byte(nil), configuration.Secret...),
		parser: jwt.NewParser(
			jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
			jwt.WithIssuer(configuration.Issuer),
			jwt.WithAudience(configuration.Audience),
			jwt.WithExpirationRequired(),
			jwt.WithTimeFunc(now),
		),
	}, nil
}

func (verifier *hs256Verifier) Verify(_ context.Context, serialized string) (Identity, error) {
	claims := &tenantClaims{}
	token, err := verifier.parser.ParseWithClaims(serialized, claims, func(token *jwt.Token) (any, error) {
		if token.Method != jwt.SigningMethodHS256 {
			return nil, ErrUnauthenticated
		}
		return verifier.secret, nil
	})
	if err != nil || token == nil || !token.Valid || strings.TrimSpace(claims.TenantRef) == "" {
		return Identity{}, ErrUnauthenticated
	}
	return Identity{TenantRef: claims.TenantRef}, nil
}
