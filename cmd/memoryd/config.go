package main

import (
	"errors"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

type authMode string
type databaseDriver string

const (
	authModeJWT             authMode       = "jwt"
	authModeTrustedLoopback authMode       = "trusted_loopback"
	databaseDriverPostgres  databaseDriver = "postgres"
	databaseDriverMySQL     databaseDriver = "mysql"
)

type config struct {
	databaseDriver     databaseDriver
	databaseURL        string
	httpAddress        string
	grpcAddress        string
	inferenceAddress   string
	memoryIndexAddress string
	memoryIndexToken   string
	memoryIndexTimeout time.Duration
	authMode           authMode
	jwtSecret          []byte
	jwtIssuer          string
	jwtAudience        string
	authVerifier       auth.Verifier
	projector          memoryindex.ProjectorConfig
	scheduler          scheduler.Config
}

func configFromEnvironment() (config, error) {
	schedulerConfig := scheduler.DefaultConfig()
	memoryIndexTimeout := 5 * time.Second
	projectorConfig := memoryindex.DefaultProjectorConfig()
	if value := os.Getenv("MEMORYD_CONSOLIDATION_QUIET_PERIOD"); value != "" {
		parsed, err := time.ParseDuration(value)
		if err != nil || parsed <= 0 {
			return config{}, errors.New("MEMORYD_CONSOLIDATION_QUIET_PERIOD must be a positive duration")
		}
		schedulerConfig.QuietPeriod = parsed
	}
	if value := os.Getenv("MEMORYD_CONSOLIDATION_MAX_EPISODES"); value != "" {
		parsed, err := strconv.Atoi(value)
		if err != nil || parsed < 2 {
			return config{}, errors.New("MEMORYD_CONSOLIDATION_MAX_EPISODES must be an integer of at least 2")
		}
		schedulerConfig.MaxEpisodes = parsed
		if schedulerConfig.Overlap >= parsed {
			schedulerConfig.Overlap = parsed - 1
		}
	}
	if value := os.Getenv("MEMORYD_CONSOLIDATION_OVERLAP"); value != "" {
		parsed, err := strconv.Atoi(value)
		if err != nil || parsed < 0 || parsed >= schedulerConfig.MaxEpisodes {
			return config{}, errors.New("MEMORYD_CONSOLIDATION_OVERLAP must be a non-negative integer smaller than MEMORYD_CONSOLIDATION_MAX_EPISODES")
		}
		schedulerConfig.Overlap = parsed
	}
	if value := os.Getenv("MEMORYD_MEMORY_INDEX_TIMEOUT"); value != "" {
		parsed, err := time.ParseDuration(value)
		if err != nil || parsed <= 0 {
			return config{}, errors.New("MEMORYD_MEMORY_INDEX_TIMEOUT must be a positive duration")
		}
		memoryIndexTimeout = parsed
	}
	for name, target := range map[string]*time.Duration{
		"MEMORYD_MEMORY_INDEX_PROJECTOR_POLL_INTERVAL":  &projectorConfig.PollInterval,
		"MEMORYD_MEMORY_INDEX_PROJECTOR_LEASE_DURATION": &projectorConfig.LeaseDuration,
		"MEMORYD_MEMORY_INDEX_PROJECTOR_RETRY_DELAY":    &projectorConfig.RetryDelay,
	} {
		if value := os.Getenv(name); value != "" {
			parsed, err := time.ParseDuration(value)
			if err != nil || parsed <= 0 {
				return config{}, errors.New(name + " must be a positive duration")
			}
			*target = parsed
		}
	}
	if value := os.Getenv("MEMORYD_MEMORY_INDEX_PROJECTOR_PAGE_SIZE"); value != "" {
		parsed, err := strconv.Atoi(value)
		if err != nil || parsed <= 0 || parsed > 1000 {
			return config{}, errors.New("MEMORYD_MEMORY_INDEX_PROJECTOR_PAGE_SIZE must be between 1 and 1000")
		}
		projectorConfig.PageSize = parsed
	}
	configuration := config{
		databaseDriver:     databaseDriver(strings.ToLower(strings.TrimSpace(os.Getenv("MEMORYD_DATABASE_DRIVER")))),
		databaseURL:        os.Getenv("MEMORYD_DATABASE_URL"),
		httpAddress:        os.Getenv("MEMORYD_HTTP_ADDR"),
		grpcAddress:        os.Getenv("MEMORYD_GRPC_ADDR"),
		inferenceAddress:   os.Getenv("MEMORYD_INFERENCE_GRPC_ADDR"),
		memoryIndexAddress: os.Getenv("MEMORYD_MEMORY_INDEX_GRPC_ADDR"),
		memoryIndexToken:   os.Getenv("MEMORYD_MEMORY_INDEX_RPC_TOKEN"),
		memoryIndexTimeout: memoryIndexTimeout,
		authMode:           authMode(os.Getenv("MEMORYD_AUTH_MODE")),
		jwtSecret:          []byte(os.Getenv("MEMORYD_JWT_HS256_SECRET")),
		jwtIssuer:          os.Getenv("MEMORYD_JWT_ISSUER"),
		jwtAudience:        os.Getenv("MEMORYD_JWT_AUDIENCE"),
		projector:          projectorConfig,
		scheduler:          schedulerConfig,
	}
	switch configuration.databaseDriver {
	case databaseDriverPostgres, databaseDriverMySQL:
	default:
		return config{}, errors.New("MEMORYD_DATABASE_DRIVER must be postgres or mysql")
	}
	if configuration.databaseURL == "" {
		return config{}, errors.New("MEMORYD_DATABASE_URL is required")
	}
	if configuration.httpAddress == "" {
		configuration.httpAddress = "127.0.0.1:8080"
	}
	if configuration.grpcAddress == "" {
		configuration.grpcAddress = "127.0.0.1:8081"
	}
	if configuration.inferenceAddress == "" {
		configuration.inferenceAddress = "127.0.0.1:8082"
	}
	if configuration.authMode == "" {
		configuration.authMode = authModeJWT
	}
	if err := validateAuthConfiguration(configuration); err != nil {
		return config{}, err
	}
	return configuration, nil
}

func validateAuthConfiguration(configuration config) error {
	switch configuration.authMode {
	case authModeJWT:
		if configuration.authVerifier != nil {
			return nil
		}
		_, err := auth.NewHS256Verifier(auth.HS256Config{
			Secret: configuration.jwtSecret, Issuer: configuration.jwtIssuer, Audience: configuration.jwtAudience,
		})
		if err != nil {
			return errors.New("JWT auth requires a 32-byte MEMORYD_JWT_HS256_SECRET, MEMORYD_JWT_ISSUER, and MEMORYD_JWT_AUDIENCE")
		}
		return nil
	case authModeTrustedLoopback:
		if !isLoopbackListener(configuration.httpAddress) || !isLoopbackListener(configuration.grpcAddress) {
			return errors.New("trusted_loopback auth requires loopback HTTP and gRPC listeners")
		}
		return nil
	default:
		return errors.New("MEMORYD_AUTH_MODE must be jwt or trusted_loopback")
	}
}

func isLoopbackListener(address string) bool {
	host, _, err := net.SplitHostPort(address)
	if err != nil || strings.TrimSpace(host) == "" {
		return false
	}
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
