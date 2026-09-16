package consolidation

import (
	"context"
	"errors"
)

const (
	// MaxWorkerRequestBytes stays below gRPC's default 4 MiB message ceiling.
	// The reference Worker may impose a smaller model-specific input budget.
	MaxWorkerRequestBytes = 3 * 1024 * 1024
	// MaxTaggedTextBytes bounds the untrusted semantic result before parsing or
	// persistence. A valid V1 change set should be far smaller than this.
	MaxTaggedTextBytes = 64 * 1024
	// MaxChangeTextBytes keeps one generated memory text usable in later Select
	// and consolidation requests without assigning it a synthetic score.
	MaxChangeTextBytes = 4 * 1024
)

var (
	ErrInvalidWindow  = errors.New("invalid consolidation window")
	ErrWindowConflict = errors.New("consolidation job ref conflicts with its frozen window")
	ErrWindowStale    = errors.New("consolidation snapshot changed while Worker was running")
)

// Window identifies one ordered, Core-owned consolidation snapshot. Outcomes
// are optional, but when supplied must already be bound to an Episode in the
// same window.
type Window struct {
	JobRef           string
	EpisodeRefs      []string
	OutcomeEventRefs []string
}

// WorkerRequest is the shallow text boundary sent to the semantic worker.
type WorkerRequest struct {
	JobRef            string
	WindowText        string
	AllowedTargetRefs []string
	AllowedBasisRefs  []string
	Evidence          *WindowEvidence
}

// FitsWorkerRequestLimit applies the Core-owned transport safety boundary to
// the exact strings sent over the internal RPC. Oversized windows become a
// durable no-op rather than an endlessly retried transport failure.
func FitsWorkerRequestLimit(request WorkerRequest) bool {
	size := len(request.JobRef) + len(request.WindowText)
	for _, ref := range request.AllowedTargetRefs {
		size += len(ref)
	}
	for _, ref := range request.AllowedBasisRefs {
		size += len(ref)
	}
	if request.Evidence != nil {
		for _, episode := range request.Evidence.Episodes {
			size += len(episode.EpisodeRef) + len(episode.SessionRef) + len(episode.Origin)
			for _, source := range episode.Sources {
				size += len(source.SourceRef) + len(source.Role) + len(source.ActorKind) + len(source.ActorRef) + len(source.Text)
			}
		}
		for _, outcome := range request.Evidence.Outcomes {
			size += len(outcome.OutcomeRef) + len(outcome.EpisodeRef) + len(outcome.ActorKind) + len(outcome.ActorRef) + len(outcome.Text)
		}
		for _, recollection := range request.Evidence.Recollections {
			size += len(recollection.VersionRef) + len(recollection.Application) + len(recollection.Text)
		}
	}
	return size <= MaxWorkerRequestBytes
}

// Worker has no authority beyond returning tagged text.
type Worker interface {
	ProcessConsolidationWindow(context.Context, WorkerRequest) (string, error)
}

// Receipt contains only effects committed by Core for this frozen Job.
type Receipt struct {
	RecollectionVersionRefs []string
	DispositionVersionRefs  []string
}
