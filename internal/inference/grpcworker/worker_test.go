package grpcworker

import (
	"context"
	"errors"
	"reflect"
	"testing"

	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"google.golang.org/grpc"
)

var _ consolidation.Worker = New(nil)

func TestWorkerMapsRequestAndReturnsTaggedTextUnchanged(t *testing.T) {
	ctx := context.WithValue(context.Background(), contextKey{}, "context-marker")
	request := consolidation.WorkerRequest{
		JobRef:            "job:formation/17",
		WindowText:        "EPISODE episode:1\n\u7528\u6237\u5e0c\u671b\u5148\u770b\u6700\u5c0f\u6a21\u578b",
		AllowedTargetRefs: []string{"NEW_RECOLLECTION", "seed:existing@2"},
		AllowedBasisRefs:  []string{"episode:1", "episode:2"},
		Evidence: &consolidation.WindowEvidence{
			Episodes: []consolidation.EvidenceEpisode{{
				EpisodeRef: "episode:1", SessionRef: "session:1", Origin: "current",
				FormationRole: consolidation.EvidenceFormationAnchor,
				Sources: []consolidation.EvidenceSource{{
					SourceRef: "source:1", Role: "situation", ActorKind: "user", ActorRef: "user:1", Text: "raw text",
				}},
			}},
			Outcomes: []consolidation.EvidenceOutcome{{
				OutcomeRef: "outcome:1", EpisodeRef: "episode:1", ActorKind: "user", ActorRef: "user:1", Text: "helped",
			}},
			Recollections: []consolidation.EvidenceRecollection{{
				VersionRef: "recollection:1@2", Application: "other", Text: "user lives in Shanghai",
			}},
		},
	}
	taggedText := "TARGET\nNEW_RECOLLECTION\nAPPLICATION\nSELF\nCHANGE\nTEXT \u5148\u7ed9\u6700\u5c0f\u56e0\u679c\u6a21\u578b\nBASIS\nepisode:1\nepisode:2"
	client := &recordingClient{
		response: &inferencev1.TaggedTextResponse{TaggedText: taggedText},
	}

	got, err := New(client).ProcessConsolidationWindow(ctx, request)
	if err != nil {
		t.Fatalf("ProcessConsolidationWindow() error = %v", err)
	}
	if got != taggedText {
		t.Fatalf("ProcessConsolidationWindow() = %q, want exact tagged text %q", got, taggedText)
	}
	if client.callCount != 1 {
		t.Fatalf("client call count = %d, want 1", client.callCount)
	}
	if client.ctx != ctx {
		t.Fatal("client context differs from caller context")
	}
	wantRequest := &inferencev1.ProcessConsolidationWindowRequest{
		JobRef:            request.JobRef,
		WindowText:        request.WindowText,
		AllowedTargetRefs: []string{"NEW_RECOLLECTION", "seed:existing@2"},
		AllowedBasisRefs:  []string{"episode:1", "episode:2"},
		Evidence: &inferencev1.WindowEvidence{
			Episodes: []*inferencev1.EvidenceEpisode{{
				EpisodeRef: "episode:1", SessionRef: "session:1", Origin: "current", FormationRole: "anchor",
				Sources: []*inferencev1.EvidenceSource{{
					SourceRef: "source:1", Role: "situation", ActorKind: "user", ActorRef: "user:1", Text: "raw text",
				}},
			}},
			Outcomes: []*inferencev1.EvidenceOutcome{{
				OutcomeRef: "outcome:1", EpisodeRef: "episode:1", ActorKind: "user", ActorRef: "user:1", Text: "helped",
			}},
			Recollections: []*inferencev1.EvidenceRecollection{{
				VersionRef: "recollection:1@2", Application: "other", Text: "user lives in Shanghai",
			}},
		},
	}
	if !reflect.DeepEqual(client.request, wantRequest) {
		t.Fatalf("client request = %#v, want %#v", client.request, wantRequest)
	}
	if len(client.options) != 0 {
		t.Fatalf("client options = %d, want none", len(client.options))
	}
}

func TestWorkerReturnsClientError(t *testing.T) {
	rpcErr := errors.New("inference unavailable")
	client := &recordingClient{err: rpcErr}

	got, err := New(client).ProcessConsolidationWindow(context.Background(), consolidation.WorkerRequest{})
	if err != rpcErr {
		t.Fatalf("ProcessConsolidationWindow() error = %v, want original error %v", err, rpcErr)
	}
	if got != "" {
		t.Fatalf("ProcessConsolidationWindow() = %q, want empty text on error", got)
	}
}

func TestWorkerPreservesMissingOptionalEvidence(t *testing.T) {
	client := &recordingClient{response: &inferencev1.TaggedTextResponse{}}

	_, err := New(client).ProcessConsolidationWindow(context.Background(), consolidation.WorkerRequest{
		JobRef: "legacy-job", WindowText: "legacy window",
	})

	if err != nil {
		t.Fatalf("ProcessConsolidationWindow() error = %v", err)
	}
	if client.request.GetEvidence() != nil {
		t.Fatalf("legacy request evidence = %#v; want absent optional message", client.request.GetEvidence())
	}
}

func TestWorkerRejectsNilResponse(t *testing.T) {
	got, err := New(&recordingClient{}).ProcessConsolidationWindow(context.Background(), consolidation.WorkerRequest{})
	if err == nil {
		t.Fatal("ProcessConsolidationWindow() error = nil, want nil-response error")
	}
	if got != "" {
		t.Fatalf("ProcessConsolidationWindow() = %q, want empty text for nil response", got)
	}
}

func TestWorkerRejectsNilClient(t *testing.T) {
	got, err := New(nil).ProcessConsolidationWindow(context.Background(), consolidation.WorkerRequest{})
	if err == nil {
		t.Fatal("ProcessConsolidationWindow() error = nil, want nil-client error")
	}
	if got != "" {
		t.Fatalf("ProcessConsolidationWindow() = %q, want empty text for nil client", got)
	}
}

type contextKey struct{}

type recordingClient struct {
	response  *inferencev1.TaggedTextResponse
	err       error
	ctx       context.Context
	request   *inferencev1.ProcessConsolidationWindowRequest
	options   []grpc.CallOption
	callCount int
}

func (client *recordingClient) ProcessConsolidationWindow(
	ctx context.Context,
	request *inferencev1.ProcessConsolidationWindowRequest,
	options ...grpc.CallOption,
) (*inferencev1.TaggedTextResponse, error) {
	client.callCount++
	client.ctx = ctx
	client.request = request
	client.options = options
	return client.response, client.err
}
