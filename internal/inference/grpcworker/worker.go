// Package grpcworker adapts the generated inference gRPC client to the Core's
// text-only consolidation Worker boundary.
package grpcworker

import (
	"context"
	"errors"

	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
)

var (
	errClientRequired = errors.New("inference worker client is required")
	errNilResponse    = errors.New("inference worker returned a nil response")
)

// Worker is a thin transport bridge. Semantic parsing and state transitions
// remain owned by Core.
type Worker struct {
	client inferencev1.InferenceWorkerClient
}

var _ consolidation.Worker = (*Worker)(nil)

// New returns a Worker backed by the generated inference client.
func New(client inferencev1.InferenceWorkerClient) *Worker {
	return &Worker{client: client}
}

// ProcessConsolidationWindow forwards the Core-bounded text request and
// returns the worker's tagged text unchanged.
func (worker *Worker) ProcessConsolidationWindow(
	ctx context.Context,
	request consolidation.WorkerRequest,
) (string, error) {
	if worker == nil || worker.client == nil {
		return "", errClientRequired
	}

	response, err := worker.client.ProcessConsolidationWindow(ctx, &inferencev1.ProcessConsolidationWindowRequest{
		JobRef:            request.JobRef,
		WindowText:        request.WindowText,
		AllowedTargetRefs: request.AllowedTargetRefs,
		AllowedBasisRefs:  request.AllowedBasisRefs,
		Evidence:          mapWindowEvidence(request.Evidence),
	})
	if err != nil {
		return "", err
	}
	if response == nil {
		return "", errNilResponse
	}
	return response.GetTaggedText(), nil
}

func mapWindowEvidence(evidence *consolidation.WindowEvidence) *inferencev1.WindowEvidence {
	if evidence == nil {
		return nil
	}
	mapped := &inferencev1.WindowEvidence{
		Episodes:      make([]*inferencev1.EvidenceEpisode, 0, len(evidence.Episodes)),
		Outcomes:      make([]*inferencev1.EvidenceOutcome, 0, len(evidence.Outcomes)),
		Recollections: make([]*inferencev1.EvidenceRecollection, 0, len(evidence.Recollections)),
	}
	for _, episode := range evidence.Episodes {
		mappedEpisode := &inferencev1.EvidenceEpisode{
			EpisodeRef:    episode.EpisodeRef,
			SessionRef:    episode.SessionRef,
			Origin:        episode.Origin,
			FormationRole: episode.FormationRole,
			Sources:       make([]*inferencev1.EvidenceSource, 0, len(episode.Sources)),
		}
		for _, source := range episode.Sources {
			mappedEpisode.Sources = append(mappedEpisode.Sources, &inferencev1.EvidenceSource{
				SourceRef: source.SourceRef,
				Role:      source.Role,
				ActorKind: source.ActorKind,
				ActorRef:  source.ActorRef,
				Text:      source.Text,
			})
		}
		mapped.Episodes = append(mapped.Episodes, mappedEpisode)
	}
	for _, outcome := range evidence.Outcomes {
		mapped.Outcomes = append(mapped.Outcomes, &inferencev1.EvidenceOutcome{
			OutcomeRef: outcome.OutcomeRef,
			EpisodeRef: outcome.EpisodeRef,
			ActorKind:  outcome.ActorKind,
			ActorRef:   outcome.ActorRef,
			Text:       outcome.Text,
		})
	}
	for _, recollection := range evidence.Recollections {
		mapped.Recollections = append(mapped.Recollections, &inferencev1.EvidenceRecollection{
			VersionRef:  recollection.VersionRef,
			Application: recollection.Application,
			Text:        recollection.Text,
		})
	}
	return mapped
}
