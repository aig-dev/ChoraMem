package main

import (
	"context"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"
)

func TestConfigFromEnvironmentEnablesAutomaticConsolidationByDefault(t *testing.T) {
	setTrustedLoopback(t)
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_HTTP_ADDR", "")
	t.Setenv("MEMORYD_GRPC_ADDR", "")
	t.Setenv("MEMORYD_INFERENCE_GRPC_ADDR", "")
	t.Setenv("MEMORYD_CONSOLIDATION_QUIET_PERIOD", "")
	t.Setenv("MEMORYD_CONSOLIDATION_MAX_EPISODES", "")

	configuration, err := configFromEnvironment()
	if err != nil {
		t.Fatalf("configFromEnvironment: %v", err)
	}
	if configuration.inferenceAddress != "127.0.0.1:8082" {
		t.Fatalf("inference address = %q", configuration.inferenceAddress)
	}
	if configuration.scheduler.QuietPeriod != 2*time.Minute || configuration.scheduler.MaxEpisodes != 32 {
		t.Fatalf("scheduler defaults = %#v", configuration.scheduler)
	}
}

func TestConfigFromEnvironmentRequiresAnExplicitSupportedDatabaseDriver(t *testing.T) {
	setTrustedLoopback(t)
	t.Setenv("MEMORYD_DATABASE_URL", "database-url")
	t.Setenv("MEMORYD_DATABASE_DRIVER", "")
	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("missing MEMORYD_DATABASE_DRIVER was accepted")
	}
	t.Setenv("MEMORYD_DATABASE_DRIVER", "sqlite")
	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("unknown MEMORYD_DATABASE_DRIVER was accepted")
	}
	for _, driver := range []string{"postgres", "mysql"} {
		t.Setenv("MEMORYD_DATABASE_DRIVER", driver)
		configuration, err := configFromEnvironment()
		if err != nil {
			t.Fatalf("driver %s: %v", driver, err)
		}
		if string(configuration.databaseDriver) != driver {
			t.Fatalf("database driver = %q; want %q", configuration.databaseDriver, driver)
		}
	}
}

func TestConfigFromEnvironmentParsesWindowControls(t *testing.T) {
	setTrustedLoopback(t)
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_INFERENCE_GRPC_ADDR", "worker.internal:9000")
	t.Setenv("MEMORYD_CONSOLIDATION_QUIET_PERIOD", "45s")
	t.Setenv("MEMORYD_CONSOLIDATION_MAX_EPISODES", "24")
	t.Setenv("MEMORYD_CONSOLIDATION_OVERLAP", "2")

	configuration, err := configFromEnvironment()
	if err != nil {
		t.Fatalf("configFromEnvironment: %v", err)
	}
	if configuration.inferenceAddress != "worker.internal:9000" ||
		configuration.scheduler.QuietPeriod != 45*time.Second ||
		configuration.scheduler.MaxEpisodes != 24 ||
		configuration.scheduler.Overlap != 2 {
		t.Fatalf("parsed configuration = %#v", configuration)
	}
}

func TestConfigFromEnvironmentParsesOptionalMemoryIndexProvider(t *testing.T) {
	setTrustedLoopback(t)
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_MEMORY_INDEX_GRPC_ADDR", "memory-index:50051")
	t.Setenv("MEMORYD_MEMORY_INDEX_RPC_TOKEN", "test-token")
	t.Setenv("MEMORYD_MEMORY_INDEX_TIMEOUT", "3s")
	t.Setenv("MEMORYD_MEMORY_INDEX_PROJECTOR_POLL_INTERVAL", "250ms")
	t.Setenv("MEMORYD_MEMORY_INDEX_PROJECTOR_LEASE_DURATION", "45s")
	t.Setenv("MEMORYD_MEMORY_INDEX_PROJECTOR_RETRY_DELAY", "2s")
	t.Setenv("MEMORYD_MEMORY_INDEX_PROJECTOR_PAGE_SIZE", "64")

	configuration, err := configFromEnvironment()
	if err != nil {
		t.Fatalf("configFromEnvironment: %v", err)
	}
	if configuration.memoryIndexAddress != "memory-index:50051" ||
		configuration.memoryIndexToken != "test-token" ||
		configuration.memoryIndexTimeout != 3*time.Second {
		t.Fatalf("MemoryIndex configuration = %#v", configuration)
	}
	if configuration.projector.PollInterval != 250*time.Millisecond ||
		configuration.projector.LeaseDuration != 45*time.Second ||
		configuration.projector.RetryDelay != 2*time.Second || configuration.projector.PageSize != 64 {
		t.Fatalf("MemoryIndex projector configuration = %#v", configuration.projector)
	}
}

func TestConfigFromEnvironmentRejectsInvalidMemoryIndexTimeout(t *testing.T) {
	setTrustedLoopback(t)
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_MEMORY_INDEX_TIMEOUT", "eventually")

	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("invalid MemoryIndex timeout was accepted")
	}
}

func TestConfigFromEnvironmentRejectsInvalidWindowControls(t *testing.T) {
	setTrustedLoopback(t)
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_CONSOLIDATION_QUIET_PERIOD", "soon")
	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("invalid quiet period was accepted")
	}

	t.Setenv("MEMORYD_CONSOLIDATION_QUIET_PERIOD", "1m")
	t.Setenv("MEMORYD_CONSOLIDATION_MAX_EPISODES", "1")
	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("one-Episode maximum was accepted")
	}

	t.Setenv("MEMORYD_CONSOLIDATION_MAX_EPISODES", "4")
	t.Setenv("MEMORYD_CONSOLIDATION_OVERLAP", "4")
	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("overlap equal to the window maximum was accepted")
	}
}

func TestConfigDefaultsToSecureJWTAndRequiresReferenceClaims(t *testing.T) {
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_DATABASE_DRIVER", "postgres")
	t.Setenv("MEMORYD_AUTH_MODE", "")
	t.Setenv("MEMORYD_JWT_HS256_SECRET", "")
	t.Setenv("MEMORYD_JWT_ISSUER", "")
	t.Setenv("MEMORYD_JWT_AUDIENCE", "")

	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("default JWT mode accepted missing reference verifier configuration")
	}

	t.Setenv("MEMORYD_JWT_HS256_SECRET", "0123456789abcdef0123456789abcdef")
	t.Setenv("MEMORYD_JWT_ISSUER", "memory-auth")
	t.Setenv("MEMORYD_JWT_AUDIENCE", "memory-core")
	configuration, err := configFromEnvironment()
	if err != nil {
		t.Fatalf("secure JWT config: %v", err)
	}
	if configuration.authMode != authModeJWT || configuration.jwtIssuer != "memory-auth" || configuration.jwtAudience != "memory-core" {
		t.Fatalf("auth configuration = %#v", configuration)
	}
}

func TestTrustedLoopbackModeRequiresBothPublicListenersToBeLoopback(t *testing.T) {
	tests := []struct {
		name string
		http string
		grpc string
		ok   bool
	}{
		{name: "IPv4 loopback", http: "127.0.0.1:8080", grpc: "127.0.0.1:8081", ok: true},
		{name: "IPv6 loopback", http: "[::1]:8080", grpc: "[::1]:8081", ok: true},
		{name: "public HTTP", http: "0.0.0.0:8080", grpc: "127.0.0.1:8081"},
		{name: "public gRPC", http: "127.0.0.1:8080", grpc: ":8081"},
		{name: "nonloopback IP", http: "192.0.2.10:8080", grpc: "127.0.0.1:8081"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
			t.Setenv("MEMORYD_DATABASE_DRIVER", "postgres")
			t.Setenv("MEMORYD_AUTH_MODE", "trusted_loopback")
			t.Setenv("MEMORYD_HTTP_ADDR", test.http)
			t.Setenv("MEMORYD_GRPC_ADDR", test.grpc)
			_, err := configFromEnvironment()
			if test.ok && err != nil {
				t.Fatalf("loopback config rejected: %v", err)
			}
			if !test.ok && err == nil {
				t.Fatal("insecure non-loopback config accepted")
			}
		})
	}
}

func TestConfigRejectsUnknownAuthMode(t *testing.T) {
	t.Setenv("MEMORYD_DATABASE_URL", "postgres://memory")
	t.Setenv("MEMORYD_DATABASE_DRIVER", "postgres")
	t.Setenv("MEMORYD_AUTH_MODE", "disabled")
	if _, err := configFromEnvironment(); err == nil {
		t.Fatal("unknown auth mode accepted")
	}
}

func TestAuthenticatorFromConfigUsesInjectedVerifier(t *testing.T) {
	called := false
	configuration := config{
		authMode: authModeJWT,
		authVerifier: auth.VerifyFunc(func(_ context.Context, token string) (auth.Identity, error) {
			called = token == "injected"
			return auth.Identity{TenantRef: "tenant-1"}, nil
		}),
	}
	authenticator, err := authenticatorFromConfig(configuration)
	if err != nil {
		t.Fatalf("authenticatorFromConfig: %v", err)
	}
	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("authorization", "Bearer injected"))
	_, err = authenticator.GRPCUnaryServerInterceptor()(ctx, nil, &grpc.UnaryServerInfo{}, func(context.Context, any) (any, error) {
		return nil, nil
	})
	if err != nil || !called {
		t.Fatalf("injected verifier result = called:%v error:%v", called, err)
	}
}

func setTrustedLoopback(t *testing.T) {
	t.Helper()
	t.Setenv("MEMORYD_DATABASE_DRIVER", "postgres")
	t.Setenv("MEMORYD_AUTH_MODE", "trusted_loopback")
	t.Setenv("MEMORYD_HTTP_ADDR", "127.0.0.1:8080")
	t.Setenv("MEMORYD_GRPC_ADDR", "127.0.0.1:8081")
}
