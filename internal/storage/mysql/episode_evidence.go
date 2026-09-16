package mysql

import (
	"context"
	"errors"
	"sort"
	"strings"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

type frozenEpisodeEvidence struct {
	MemoryRef      string
	QuerySourceRef string
	Text           string
}

func loadEpisodeEvidence(ctx context.Context, tx *transaction, request selection.SelectRequest, indexed []scopedIndexCandidate) ([]frozenEpisodeEvidence, error) {
	if request.EpisodeEvidenceMaxBytes == 0 {
		return nil, nil
	}
	ordered := append([]scopedIndexCandidate(nil), indexed...)
	sort.SliceStable(ordered, func(left, right int) bool {
		if ordered[left].rank != ordered[right].rank {
			return ordered[left].rank < ordered[right].rank
		}
		return ordered[left].laneOrder < ordered[right].laneOrder
	})
	seen := make(map[string]struct{})
	candidates := make([]frozenEpisodeEvidence, 0, len(ordered))
	for _, indexedItem := range ordered {
		if indexedItem.candidate.Kind != memoryindex.KindEpisode {
			continue
		}
		if _, duplicate := seen[indexedItem.candidate.Ref]; duplicate {
			continue
		}
		episodes, owner, err := loadConsolidationEpisodes(ctx, tx, []string{indexedItem.candidate.Ref})
		if errors.Is(err, consolidation.ErrInvalidWindow) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if owner != indexedItem.scope || len(episodes) != 1 || episodeContainsCurrentSituation(episodes[0], request) {
			continue
		}
		seen[indexedItem.candidate.Ref] = struct{}{}
		candidates = append(candidates, frozenEpisodeEvidence{
			MemoryRef: indexedItem.candidate.Ref, QuerySourceRef: indexedItem.querySourceRef,
			Text: renderEpisodeEvidence(episodes[0]),
		})
	}
	public := make([]selection.EpisodeEvidence, 0, len(candidates))
	byRef := make(map[string]frozenEpisodeEvidence, len(candidates))
	for _, item := range candidates {
		public = append(public, selection.EpisodeEvidence{MemoryRef: item.MemoryRef, Text: item.Text})
		byRef[item.MemoryRef] = item
	}
	bounded := selection.BoundEpisodeEvidence(public, request.EpisodeEvidenceMaxBytes)
	result := make([]frozenEpisodeEvidence, 0, len(bounded))
	for _, item := range bounded {
		result = append(result, byRef[item.MemoryRef])
	}
	return result, nil
}

func episodeContainsCurrentSituation(episode episodeEvidence, request selection.SelectRequest) bool {
	if episode.Owner.Kind != request.Scope.Kind || episode.Owner.TenantRef != request.Scope.TenantRef ||
		episode.Owner.AgentRef != request.Scope.AgentRef || episode.Owner.RelationshipRef != request.Scope.RelationshipRef ||
		episode.SessionRef != request.Scope.SessionRef {
		return false
	}
	for _, source := range episode.Sources {
		for _, ref := range request.SituationSourceRefs {
			if source.Ref == ref {
				return true
			}
		}
	}
	return false
}

func renderEpisodeEvidence(episode episodeEvidence) string {
	sources := append([]sourceEvidence(nil), episode.Sources...)
	sort.SliceStable(sources, func(left, right int) bool {
		leftRole, rightRole := episodeSourceRoleOrder(sources[left].Role), episodeSourceRoleOrder(sources[right].Role)
		if leftRole != rightRole {
			return leftRole < rightRole
		}
		return sources[left].Ref < sources[right].Ref
	})
	var text strings.Builder
	for index, source := range sources {
		if index > 0 {
			text.WriteByte('\n')
		}
		text.WriteString(strings.ToUpper(string(source.Role)))
		text.WriteString(" [")
		text.WriteString(string(source.ActorKind))
		text.WriteString("]\n")
		text.WriteString(source.Text)
	}
	return text.String()
}

func episodeSourceRoleOrder(role ledger.SourceRole) int {
	switch role {
	case ledger.RoleSituation:
		return 0
	case ledger.RoleAgentAct:
		return 1
	case ledger.RoleOutcome:
		return 2
	default:
		return 3
	}
}
