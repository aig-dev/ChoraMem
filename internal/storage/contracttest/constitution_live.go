package contracttest

import (
	"context"
	"encoding/json"
	"net"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"connectrpc.com/connect"
	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/gen/memory/v1/memoryv1connect"
	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	memoryconnect "github.com/aig-dev/ChoraMem/internal/transport/connect"
	memorygrpc "github.com/aig-dev/ChoraMem/internal/transport/grpc"
	"google.golang.org/grpc"
)

// RunLiveConstitutionHarnesses is opt-in because integration normally precedes
// SDK dependency installation/build. It uses only the factory's disposable DB.
func RunLiveConstitutionHarnesses(t *testing.T, factory IndexedFactory) {
	if os.Getenv("MEMORY_TEST_LIVE_SDKS") != "1" {
		return
	}
	_, file, _, _ := runtime.Caller(0)
	root := filepath.Clean(filepath.Join(filepath.Dir(file), "../../.."))
	for _, language := range []string{"python", "typescript"} {
		t.Run("constitution_live_"+language, func(t *testing.T) {
			store := migratedIndexedStore(t, factory, &scriptedIndex{})
			shared := memorygrpc.NewServer(store)
			authenticator := auth.NewTrustedLoopbackAuthenticator()
			listener, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			server := grpc.NewServer(grpc.UnaryInterceptor(authenticator.GRPCUnaryServerInterceptor()))
			memoryv1.RegisterMemoryCoreServer(server, shared)
			go func() { _ = server.Serve(listener) }()
			t.Cleanup(server.Stop)
			_, handler := memoryv1connect.NewMemoryCoreHandler(memoryconnect.NewServer(shared), connect.WithInterceptors(authenticator.ConnectInterceptor()))
			httpServer := httptest.NewServer(handler)
			t.Cleanup(httpServer.Close)
			tenant := "live-" + language
			var command *exec.Cmd
			if language == "python" {
				python := os.Getenv("MEMORY_TEST_SDK_PYTHON")
				if python == "" {
					python = "python3"
				}
				command = exec.CommandContext(testContext(t), python, filepath.Join(root, "sdk/python/tests/constitution_live.py"), listener.Addr().String(), tenant)
				command.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "sdk/python/src"))
			} else {
				command = exec.CommandContext(testContext(t), "node", filepath.Join(root, "sdk/typescript/test/constitution_live.mjs"), httpServer.URL, tenant)
			}
			output, err := command.Output()
			if err != nil {
				if failure, ok := err.(*exec.ExitError); ok {
					t.Fatalf("%s Harness: %v\n%s", language, err, failure.Stderr)
				}
				t.Fatal(err)
			}
			var result struct{ Episode, Ref, Text string }
			if err := json.Unmarshal(output, &result); err != nil {
				t.Fatalf("decode Harness: %v %s", err, output)
			}
			if result.Episode == "" {
				t.Fatal("Harness failed to materialize Episode")
			}
			calls := 0
			_, err = store.ConsolidateWindow(context.Background(), consolidation.Window{JobRef: "live-" + language, EpisodeRefs: []string{result.Episode}}, callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
				calls++
				want := "CONSTITUTION\nMEMORY_REF " + result.Ref + "\n> " + strings.ReplaceAll(result.Text, "\n", "\n> ") + "\nEND_CONSTITUTION\n"
				if !strings.HasPrefix(request.WindowText, want) {
					t.Fatalf("Harness baseline failed to reach Worker: %s", request.WindowText)
				}
				t.Logf("%s Harness -> real transport -> Core DB -> same Worker: %s", language, result.Ref)
				return "", nil
			}))
			if err != nil || calls != 1 {
				t.Fatalf("Worker calls=%d error=%v", calls, err)
			}
		})
	}
}
