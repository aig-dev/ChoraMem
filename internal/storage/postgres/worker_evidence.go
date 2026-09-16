package postgres

import (
	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func workerEvidenceEpisodes(episodes []episodeEvidence) []consolidation.EvidenceEpisode {
	result := make([]consolidation.EvidenceEpisode, 0, len(episodes))
	for _, episode := range episodes {
		result = append(result, workerEvidenceEpisode(episode))
	}
	return result
}

func workerEvidenceEpisode(episode episodeEvidence) consolidation.EvidenceEpisode {
	result := consolidation.EvidenceEpisode{
		EpisodeRef: episode.Ref,
		SessionRef: episode.SessionRef,
		Sources:    make([]consolidation.EvidenceSource, 0, len(episode.Sources)),
	}
	for _, source := range episode.Sources {
		if source.Role != ledger.RoleSituation && source.Role != ledger.RoleAgentAct {
			continue
		}
		result.Sources = append(result.Sources, consolidation.EvidenceSource{
			SourceRef: source.Ref,
			Role:      string(source.Role),
			ActorKind: string(source.ActorKind),
			ActorRef:  source.ActorRef,
			Text:      source.Text,
		})
	}
	return result
}

func workerEvidenceOutcomes(
	outcomes map[string]feedbackOutcome,
	eligible map[string]struct{},
) []consolidation.EvidenceOutcome {
	result := make([]consolidation.EvidenceOutcome, 0, len(eligible))
	for _, ref := range sortedMapKeys(eligible) {
		outcome := outcomes[ref]
		result = append(result, consolidation.EvidenceOutcome{
			OutcomeRef: outcome.Ref,
			EpisodeRef: outcome.EpisodeRef,
			ActorKind:  string(outcome.ActorKind),
			ActorRef:   outcome.ActorRef,
			Text:       outcome.Text,
		})
	}
	return result
}

func workerEvidenceRecollections(recollections []activeRecollection) []consolidation.EvidenceRecollection {
	result := make([]consolidation.EvidenceRecollection, 0, len(recollections))
	for _, recollection := range recollections {
		result = append(result, consolidation.EvidenceRecollection{
			VersionRef:  recollection.VersionRef,
			Application: recollection.Application,
			Text:        recollection.Text,
		})
	}
	return result
}
