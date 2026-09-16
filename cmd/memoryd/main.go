package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"connectrpc.com/connect"
	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/gen/memory/v1/memoryv1connect"
	"github.com/aig-dev/ChoraMem/internal/inference/grpcworker"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/aig-dev/ChoraMem/internal/server"
	corestorage "github.com/aig-dev/ChoraMem/internal/storage"
	storemysql "github.com/aig-dev/ChoraMem/internal/storage/mysql"
	"github.com/aig-dev/ChoraMem/internal/storage/postgres"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	memoryconnect "github.com/aig-dev/ChoraMem/internal/transport/connect"
	memorygrpc "github.com/aig-dev/ChoraMem/internal/transport/grpc"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"github.com/aig-dev/ChoraMem/provider/memoryindexgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func main() {
	configuration, err := configFromEnvironment()
	if err != nil {
		log.Fatal(err)
	}

	shutdownSignal, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(shutdownSignal, configuration); err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context, configuration config) error {
	if err := validateAuthConfiguration(configuration); err != nil {
		return err
	}
	authenticator, err := authenticatorFromConfig(configuration)
	if err != nil {
		return fmt.Errorf("configure public Memory authentication: %w", err)
	}
	var indexClient *memoryindexgrpc.Client
	var index memoryindex.Index
	if configuration.memoryIndexAddress != "" {
		var err error
		indexClient, err = memoryindexgrpc.Dial(ctx, memoryindexgrpc.Config{
			Address: configuration.memoryIndexAddress, RPCToken: configuration.memoryIndexToken,
			CallTimeout: configuration.memoryIndexTimeout,
		})
		if err != nil {
			return fmt.Errorf("configure MemoryIndex Provider connection: %w", err)
		}
		defer indexClient.Close()
		index = indexClient
	}
	store, err := openCoreStore(ctx, configuration, index)
	if err != nil {
		return err
	}
	defer store.Close()
	if err := store.Migrate(ctx); err != nil {
		return err
	}
	inferenceConnection, err := grpc.NewClient(
		configuration.inferenceAddress,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		return fmt.Errorf("configure inference Worker connection: %w", err)
	}
	defer inferenceConnection.Close()
	worker := grpcworker.New(inferencev1.NewInferenceWorkerClient(inferenceConnection))
	schedulerConfig := configuration.scheduler
	schedulerConfig.OnError = func(err error) {
		log.Printf("automatic consolidation retry: %v", err)
	}
	processor := scheduler.New(store, store, worker, schedulerConfig)
	projectorConfig := configuration.projector
	projectorConfig.OnError = func(err error) {
		log.Printf("MemoryIndex projection retry: %v", err)
	}
	var projector *memoryindex.Projector
	if index != nil {
		projector = memoryindex.NewProjector(store, store, index, projectorConfig)
	}

	httpListener, err := net.Listen("tcp", configuration.httpAddress)
	if err != nil {
		return fmt.Errorf("listen for health traffic: %w", err)
	}
	defer httpListener.Close()
	grpcListener, err := net.Listen("tcp", configuration.grpcAddress)
	if err != nil {
		return fmt.Errorf("listen for gRPC traffic: %w", err)
	}
	defer grpcListener.Close()

	transportService := memorygrpc.NewServer(store)
	connectPath, connectHandler := memoryv1connect.NewMemoryCoreHandler(
		memoryconnect.NewServer(transportService),
		connect.WithInterceptors(authenticator.ConnectInterceptor()),
	)
	httpServer := &http.Server{
		Handler: server.NewHandler(func() bool {
			pingContext, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			return store.Ping(pingContext) == nil
		}, connectPath, connectHandler),
		ReadHeaderTimeout: 5 * time.Second,
	}
	grpcServer := grpc.NewServer(grpc.UnaryInterceptor(authenticator.GRPCUnaryServerInterceptor()))
	memoryv1.RegisterMemoryCoreServer(grpcServer, transportService)
	backgroundContext, stopBackground := context.WithCancel(ctx)
	defer stopBackground()
	schedulerDone := make(chan error, 1)
	go func() {
		log.Printf("memoryd consolidation scheduler using Worker %s", configuration.inferenceAddress)
		schedulerDone <- processor.Run(backgroundContext)
	}()
	var projectorDone chan error
	if projector != nil {
		projectorDone = make(chan error, 1)
		go func() {
			log.Printf("memoryd MemoryIndex projector enabled")
			projectorDone <- projector.Run(backgroundContext)
		}()
	}

	serveError := make(chan error, 2)
	go func() {
		log.Printf("memoryd HTTP health and Connect server listening on %s", httpListener.Addr())
		if err := httpServer.Serve(httpListener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			serveError <- fmt.Errorf("serve health traffic: %w", err)
		}
	}()
	go func() {
		log.Printf("memoryd gRPC server listening on %s", grpcListener.Addr())
		if err := grpcServer.Serve(grpcListener); err != nil && !errors.Is(err, grpc.ErrServerStopped) {
			serveError <- fmt.Errorf("serve gRPC traffic: %w", err)
		}
	}()

	var runError error
	select {
	case err := <-serveError:
		runError = err
	case <-ctx.Done():
	}
	stopBackground()

	shutdownContext, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	grpcStopped := make(chan struct{})
	go func() {
		grpcServer.GracefulStop()
		close(grpcStopped)
	}()
	select {
	case <-grpcStopped:
	case <-shutdownContext.Done():
		grpcServer.Stop()
	}
	if err := httpServer.Shutdown(shutdownContext); err != nil && runError == nil {
		runError = fmt.Errorf("shutdown health server: %w", err)
	}
	select {
	case err := <-schedulerDone:
		if err != nil && runError == nil {
			runError = fmt.Errorf("stop consolidation scheduler: %w", err)
		}
	case <-shutdownContext.Done():
		if runError == nil {
			runError = errors.New("consolidation scheduler did not stop before shutdown deadline")
		}
	}
	if projectorDone != nil {
		select {
		case err := <-projectorDone:
			if err != nil && runError == nil {
				runError = fmt.Errorf("stop MemoryIndex projector: %w", err)
			}
		case <-shutdownContext.Done():
			if runError == nil {
				runError = errors.New("MemoryIndex projector did not stop before shutdown deadline")
			}
		}
	}
	return runError
}

func openCoreStore(ctx context.Context, configuration config, index memoryindex.Index) (corestorage.CoreStore, error) {
	switch configuration.databaseDriver {
	case databaseDriverPostgres:
		options := make([]postgres.Option, 0, 1)
		if index != nil {
			options = append(options, postgres.WithMemoryIndex(index))
		}
		return postgres.New(ctx, configuration.databaseURL, options...)
	case databaseDriverMySQL:
		options := make([]storemysql.Option, 0, 1)
		if index != nil {
			options = append(options, storemysql.WithMemoryIndex(index))
		}
		return storemysql.New(ctx, configuration.databaseURL, options...)
	default:
		return nil, errors.New("unsupported Memory Core database driver")
	}
}

func authenticatorFromConfig(configuration config) (*auth.Authenticator, error) {
	switch configuration.authMode {
	case authModeTrustedLoopback:
		return auth.NewTrustedLoopbackAuthenticator(), nil
	case authModeJWT:
		verifier := configuration.authVerifier
		if verifier == nil {
			var err error
			verifier, err = auth.NewHS256Verifier(auth.HS256Config{
				Secret: configuration.jwtSecret, Issuer: configuration.jwtIssuer, Audience: configuration.jwtAudience,
			})
			if err != nil {
				return nil, err
			}
		}
		return auth.NewBearerAuthenticator(verifier)
	default:
		return nil, errors.New("unknown public Memory auth mode")
	}
}
