// adaptivehistory runs the existing scheduler with a bounded evaluation envelope.
// It is deliberately not memoryd configuration and never retries a failed window.
package main

import (
	"context"
	"flag"
	"fmt"
	"net"
	"os"
	"time"

	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/internal/inference/grpcworker"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/aig-dev/ChoraMem/internal/storage/postgres"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	memorygrpc "github.com/aig-dev/ChoraMem/internal/transport/grpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	database := flag.String("database-url", "", "existing disposable PostgreSQL database, already migrated")
	listen := flag.String("listen", "127.0.0.1:18881", "trusted loopback intake address")
	endpoint := flag.String("worker", "127.0.0.1:18882", "evaluation Worker address")
	disposable := flag.Bool("disposable-database", false, "acknowledge disposable database only")
	flag.Parse()
	if !*disposable || *database == "" {
		return fmt.Errorf("explicit disposable database required")
	}
	host, _, err := net.SplitHostPort(*listen)
	if err != nil || host != "127.0.0.1" {
		return fmt.Errorf("intake must use explicit IPv4 loopback")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Minute)
	defer cancel()
	store, err := postgres.New(ctx, *database)
	if err != nil {
		return err
	}
	defer store.Close()
	connection, err := grpc.NewClient(*endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer connection.Close()
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		return err
	}
	defer listener.Close()
	server := grpc.NewServer(grpc.UnaryInterceptor(auth.NewTrustedLoopbackAuthenticator().GRPCUnaryServerInterceptor()))
	memoryv1.RegisterMemoryCoreServer(server, memorygrpc.NewServer(store))
	go func() { _ = server.Serve(listener) }()
	defer server.Stop()
	config := scheduler.DefaultConfig()
	config.QuietPeriod = 2 * time.Second
	config.WorkerTimeout = 330 * time.Second
	config.LeaseDuration = 360 * time.Second
	processor := scheduler.New(store, store, grpcworker.New(inferencev1.NewInferenceWorkerClient(connection)), config)
	fmt.Println("bounded recovery: at most 4 windows; timeout330s lease360s; first failure stops; no background retry")
	for completed := 0; completed < 4; {
		worked, err := processor.ProcessOne(ctx)
		if err != nil {
			return fmt.Errorf("bounded history stopped after %d completed windows: %w", completed, err)
		}
		if worked {
			completed++
			fmt.Printf("completed window %d/4\n", completed)
			continue
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
	return nil
}
