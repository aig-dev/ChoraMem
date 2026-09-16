package grpcworker

import (
	"context"
	"os"
	"reflect"
	"testing"
	"time"

	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func TestPythonWorkerProcessContract(t *testing.T) {
	address := os.Getenv("MEMORY_TEST_INFERENCE_GRPC_ADDR")
	if address == "" {
		t.Skip("MEMORY_TEST_INFERENCE_GRPC_ADDR is not set")
	}

	connection, err := grpc.NewClient(
		address,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("connect to Python Worker: %v", err)
	}
	defer connection.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	taggedText, err := New(inferencev1.NewInferenceWorkerClient(connection)).ProcessConsolidationWindow(
		ctx,
		consolidation.WorkerRequest{
			JobRef:            "job-python-smoke",
			WindowText:        "EPISODE episode-python-one\nSITUATION\n验证跨进程协议",
			AllowedTargetRefs: []string{"NEW_RECOLLECTION"},
			AllowedBasisRefs:  []string{"episode-python-one", "episode-python-two"},
		},
	)
	if err != nil {
		t.Fatalf("call Python Worker: %v", err)
	}
	want := "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT\n验证跨进程协议\nBASIS\nepisode-python-one\nepisode-python-two"
	if taggedText != want {
		t.Fatalf("tagged text = %q; want %q", taggedText, want)
	}
}

func TestPythonWorkerSourceMaterialContract(t *testing.T) {
	address := os.Getenv("MEMORY_TEST_INFERENCE_GRPC_ADDR")
	if address == "" {
		t.Skip("MEMORY_TEST_INFERENCE_GRPC_ADDR is not set")
	}

	connection, err := grpc.NewClient(address, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("connect to Python Worker: %v", err)
	}
	defer connection.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	taggedText, err := New(inferencev1.NewInferenceWorkerClient(connection)).ProcessConsolidationWindow(ctx, consolidation.WorkerRequest{
		JobRef: "job-python-source-material", WindowText: "legacy window remains unchanged",
		AllowedTargetRefs: []string{consolidation.TargetNewRecollection},
		AllowedBasisRefs:  []string{"episode-python-current", "episode-python-related", "episode-python-anchor"},
		Evidence: &consolidation.WindowEvidence{
			Episodes: []consolidation.EvidenceEpisode{
				{EpisodeRef: "episode-python-current", SessionRef: "session-current", Origin: consolidation.EvidenceOriginCurrent, Sources: []consolidation.EvidenceSource{
					{SourceRef: "source-python-current", Role: "situation", ActorKind: "user", ActorRef: "user-1", Text: "用户喜欢茶。"},
					{SourceRef: "source-python-current-act", Role: "agent_act", ActorKind: "agent", ActorRef: "agent-1", Text: "我记下了。"},
				}},
				{EpisodeRef: "episode-python-related", SessionRef: "session-related", Origin: consolidation.EvidenceOriginRelated, Sources: []consolidation.EvidenceSource{
					{SourceRef: "source-python-related", Role: "situation", ActorKind: "user", ActorRef: "user-1", Text: "用户也喜欢安静的茶馆。"},
					{SourceRef: "source-python-related-act", Role: "agent_act", ActorKind: "agent", ActorRef: "agent-1", Text: "我会留意。"},
				}},
				{EpisodeRef: "episode-python-anchor", SessionRef: "session-anchor", Origin: consolidation.EvidenceOriginRevisionAnchor, Sources: []consolidation.EvidenceSource{
					{SourceRef: "source-python-anchor", Role: "situation", ActorKind: "user", ActorRef: "user-1", Text: "锚点不可成为新记忆 Basis。"},
					{SourceRef: "source-python-anchor-act", Role: "agent_act", ActorKind: "agent", ActorRef: "agent-1", Text: "旧回复。"},
				}},
			},
			Outcomes: []consolidation.EvidenceOutcome{{OutcomeRef: "outcome-python", EpisodeRef: "episode-python-current", ActorKind: "user", ActorRef: "user-1", Text: "Outcome 不可成为新记忆 Basis。"}},
		},
	})
	if err != nil {
		t.Fatalf("call Python Worker source material fixture: %v", err)
	}
	changes, err := consolidation.ParseTaggedText(taggedText)
	if err != nil {
		t.Fatalf("parse Python-bound tagged text: %v", err)
	}
	want := []consolidation.Change{{
		Target: consolidation.TargetNewRecollection, Application: consolidation.ApplicationOther,
		Operation: consolidation.ChangeText, Text: "用户喜欢茶。",
		BasisRefs: []string{"episode-python-current"},
	}}
	if !reflect.DeepEqual(changes, want) {
		t.Fatalf("parsed source-bound change = %#v; want %#v (raw %q)", changes, want, taggedText)
	}
}
