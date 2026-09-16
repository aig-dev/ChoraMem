package auth

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

func TestHS256VerifierAcceptsOnlyExactConfiguredClaims(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	verifier, err := NewHS256Verifier(HS256Config{
		Secret:   []byte("0123456789abcdef0123456789abcdef"),
		Issuer:   "memory-auth",
		Audience: "memory-core",
		Now:      func() time.Time { return now },
	})
	if err != nil {
		t.Fatalf("NewHS256Verifier: %v", err)
	}

	tests := []struct {
		name   string
		header map[string]any
		claims map[string]any
		secret []byte
		ok     bool
	}{
		{
			name:   "valid string audience",
			header: map[string]any{"alg": "HS256", "typ": "JWT"},
			claims: map[string]any{"iss": "memory-auth", "aud": "memory-core", "exp": now.Add(time.Minute).Unix(), "tenant_ref": "tenant-1"},
			secret: []byte("0123456789abcdef0123456789abcdef"), ok: true,
		},
		{
			name:   "valid audience list",
			header: map[string]any{"alg": "HS256"},
			claims: map[string]any{"iss": "memory-auth", "aud": []string{"other", "memory-core"}, "exp": now.Add(time.Minute).Unix(), "tenant_ref": "tenant-1"},
			secret: []byte("0123456789abcdef0123456789abcdef"), ok: true,
		},
		{name: "wrong algorithm", header: map[string]any{"alg": "HS512"}, claims: validClaims(now), secret: []byte("0123456789abcdef0123456789abcdef")},
		{name: "wrong signature", header: map[string]any{"alg": "HS256"}, claims: validClaims(now), secret: []byte("different-secret-different-secret")},
		{name: "expired", header: map[string]any{"alg": "HS256"}, claims: claimsWith(now, "exp", now.Unix()), secret: []byte("0123456789abcdef0123456789abcdef")},
		{name: "wrong issuer", header: map[string]any{"alg": "HS256"}, claims: claimsWith(now, "iss", "other"), secret: []byte("0123456789abcdef0123456789abcdef")},
		{name: "wrong audience", header: map[string]any{"alg": "HS256"}, claims: claimsWith(now, "aud", "other"), secret: []byte("0123456789abcdef0123456789abcdef")},
		{name: "missing expiry", header: map[string]any{"alg": "HS256"}, claims: claimsWith(now, "exp", nil), secret: []byte("0123456789abcdef0123456789abcdef")},
		{name: "blank tenant", header: map[string]any{"alg": "HS256"}, claims: claimsWith(now, "tenant_ref", "  "), secret: []byte("0123456789abcdef0123456789abcdef")},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			token := signedToken(t, test.header, test.claims, test.secret)
			identity, verifyErr := verifier.Verify(context.Background(), token)
			if test.ok {
				if verifyErr != nil || identity.TenantRef != "tenant-1" {
					t.Fatalf("Verify() = (%#v, %v), want tenant-1", identity, verifyErr)
				}
				return
			}
			if !errors.Is(verifyErr, ErrUnauthenticated) {
				t.Fatalf("Verify() error = %v, want ErrUnauthenticated", verifyErr)
			}
		})
	}
}

func TestHS256VerifierRejectsUnsafeConfiguration(t *testing.T) {
	for _, configuration := range []HS256Config{
		{},
		{Secret: []byte("secret"), Issuer: "issuer", Audience: "audience"},
		{Secret: []byte("0123456789abcdef0123456789abcdef"), Audience: "audience"},
		{Secret: []byte("0123456789abcdef0123456789abcdef"), Issuer: "issuer"},
	} {
		if _, err := NewHS256Verifier(configuration); err == nil {
			t.Fatalf("unsafe configuration accepted: %#v", configuration)
		}
	}
}

func TestAuthorizeTenantRequiresExactVerifiedTenant(t *testing.T) {
	if err := AuthorizeTenant(context.Background(), "tenant-1"); !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("missing identity error = %v, want ErrUnauthenticated", err)
	}
	ctx := ContextWithIdentity(context.Background(), Identity{TenantRef: "tenant-1"})
	if err := AuthorizeTenant(ctx, "tenant-2"); !errors.Is(err, ErrPermissionDenied) {
		t.Fatalf("mismatch error = %v, want ErrPermissionDenied", err)
	}
	if err := AuthorizeTenant(ctx, "tenant-1"); err != nil {
		t.Fatalf("exact tenant rejected: %v", err)
	}
}

func validClaims(now time.Time) map[string]any {
	return map[string]any{
		"iss": "memory-auth", "aud": "memory-core", "exp": now.Add(time.Minute).Unix(), "tenant_ref": "tenant-1",
	}
}

func claimsWith(now time.Time, key string, value any) map[string]any {
	claims := validClaims(now)
	if value == nil {
		delete(claims, key)
	} else {
		claims[key] = value
	}
	return claims
}

func signedToken(t *testing.T, header, claims map[string]any, secret []byte) string {
	t.Helper()
	encode := func(value map[string]any) string {
		payload, err := json.Marshal(value)
		if err != nil {
			t.Fatalf("marshal JWT segment: %v", err)
		}
		return base64.RawURLEncoding.EncodeToString(payload)
	}
	signingInput := encode(header) + "." + encode(claims)
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(signingInput))
	return signingInput + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}
