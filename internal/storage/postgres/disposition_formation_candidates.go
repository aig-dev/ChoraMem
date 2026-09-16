package postgres

import (
	"context"
	"errors"
	"strings"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

const maxDispositionFormationHistory = 2

type dispositionFormationPair struct {
	Episode episodeEvidence
	Outcome feedbackOutcome
	Role    string
}

func (store *Store) searchDispositionFormationEpisodes(
	ctx context.Context,
	owner ownerScope,
	episodes []episodeEvidence,
	outcomes map[string]feedbackOutcome,
) ([]string, bool) {
	if store.memoryIndex == nil {
		return nil, false
	}
	anchor, ok := currentDispositionFormationAnchor(episodes, outcomes)
	if !ok {
		return nil, false
	}
	scope := memoryindex.Scope{
		TenantRef: owner.TenantRef, AgentRef: owner.AgentRef, RelationshipRef: owner.RelationshipRef,
	}
	causalCandidates, err := store.memoryIndex.Search(ctx, memoryindex.Query{
		Scope: scope, Text: dispositionFormationQuery(anchor), Limit: maxConsolidationIndexCandidates,
	})
	if err != nil {
		return nil, false
	}
	rankings := [][]memoryindex.Candidate{causalCandidates}
	if condition := dispositionFormationConditionQuery(anchor); condition != "" {
		conditionCandidates, conditionErr := store.memoryIndex.Search(ctx, memoryindex.Query{
			Scope: scope, Text: condition, Limit: maxConsolidationIndexCandidates,
		})
		if conditionErr == nil {
			rankings = append(rankings, conditionCandidates)
		}
	}
	refs := consolidation.FuseEpisodeCandidateRanks(maxConsolidationIndexCandidates, rankings...)
	return refs, len(refs) > 0
}

func currentDispositionFormationAnchor(
	episodes []episodeEvidence,
	outcomes map[string]feedbackOutcome,
) (dispositionFormationPair, bool) {
	for index := len(episodes) - 1; index >= 0; index-- {
		episode := episodes[index]
		outcome, ok := uniqueNonAgentOutcomeFromMap(episode.Ref, outcomes)
		if !ok || episode.SessionRef == "" || !episodeHasFormationRoles(episode) ||
			!episodeHasSituationActor(episode, false) {
			continue
		}
		return dispositionFormationPair{
			Episode: episode, Outcome: outcome, Role: consolidation.EvidenceFormationAnchor,
		}, true
	}
	return dispositionFormationPair{}, false
}

func uniqueNonAgentOutcomeFromMap(
	episodeRef string,
	outcomes map[string]feedbackOutcome,
) (feedbackOutcome, bool) {
	var found feedbackOutcome
	count := 0
	for _, outcome := range outcomes {
		if outcome.EpisodeRef != episodeRef || outcome.ActorKind == "" ||
			outcome.ActorKind == ledger.ActorKindAgent || strings.TrimSpace(outcome.Text) == "" {
			continue
		}
		found = outcome
		count++
	}
	return found, count == 1
}

func dispositionFormationQuery(pair dispositionFormationPair) string {
	var text strings.Builder
	text.WriteString("USER_CONDITION\n")
	for _, source := range pair.Episode.Sources {
		if source.Role == ledger.RoleSituation && source.ActorKind != ledger.ActorKindAgent && strings.TrimSpace(source.Text) != "" {
			text.WriteString(source.Text)
			text.WriteByte('\n')
		}
	}
	text.WriteString("AGENT_RESPONSE\n")
	for _, source := range pair.Episode.Sources {
		if source.Role == ledger.RoleAgentAct && source.ActorKind == ledger.ActorKindAgent &&
			source.ActorRef == pair.Episode.Owner.AgentRef && strings.TrimSpace(source.Text) != "" {
			text.WriteString(source.Text)
			text.WriteByte('\n')
		}
	}
	text.WriteString("USER_OUTCOME\n")
	text.WriteString(pair.Outcome.Text)
	return text.String()
}

func dispositionFormationConditionQuery(pair dispositionFormationPair) string {
	conditions := make([]string, 0, len(pair.Episode.Sources))
	for _, source := range pair.Episode.Sources {
		if source.Role == ledger.RoleSituation && source.ActorKind != ledger.ActorKindAgent && strings.TrimSpace(source.Text) != "" {
			conditions = append(conditions, strings.TrimSpace(source.Text))
		}
	}
	return strings.Join(conditions, "\n")
}

func loadDispositionFormationPairs(
	ctx context.Context,
	querier consolidationQuerier,
	owner ownerScope,
	current []episodeEvidence,
	currentOutcomes map[string]feedbackOutcome,
	indexedRefs []string,
) ([]dispositionFormationPair, []episodeEvidence, error) {
	anchor, ok := currentDispositionFormationAnchor(current, currentOutcomes)
	if !ok {
		return nil, nil, nil
	}
	canonicalAnchor, found, err := loadUniqueNonAgentFormationOutcome(ctx, querier, owner, anchor.Episode.Ref)
	if err != nil {
		return nil, nil, err
	}
	if !found || canonicalAnchor.Ref != anchor.Outcome.Ref {
		return nil, nil, nil
	}
	anchor.Outcome = canonicalAnchor
	pairs := []dispositionFormationPair{anchor}
	currentByRef := make(map[string]episodeEvidence, len(current))
	for _, episode := range current {
		currentByRef[episode.Ref] = episode
	}
	seenRefs := map[string]struct{}{anchor.Episode.Ref: {}}
	seenSessions := map[string]struct{}{anchor.Episode.SessionRef: {}}
	related := make([]episodeEvidence, 0, maxDispositionFormationHistory)
	for _, ref := range indexedRefs {
		if len(pairs)-1 >= maxDispositionFormationHistory {
			break
		}
		if _, duplicate := seenRefs[ref]; duplicate {
			continue
		}
		episode, isCurrent := currentByRef[ref]
		if !isCurrent {
			loaded, indexedOwner, loadErr := loadConsolidationEpisodes(ctx, querier, []string{ref})
			if errors.Is(loadErr, consolidation.ErrInvalidWindow) {
				continue
			}
			if loadErr != nil {
				return nil, nil, loadErr
			}
			if indexedOwner != owner || len(loaded) != 1 {
				continue
			}
			episode = loaded[0]
		}
		if episode.SessionRef == "" || !episodeHasFormationRoles(episode) ||
			!episodeHasSituationActor(episode, false) {
			continue
		}
		if _, duplicate := seenSessions[episode.SessionRef]; duplicate {
			continue
		}
		outcome, found, loadErr := loadUniqueNonAgentFormationOutcome(ctx, querier, owner, episode.Ref)
		if loadErr != nil {
			return nil, nil, loadErr
		}
		if !found {
			continue
		}
		pairs = append(pairs, dispositionFormationPair{
			Episode: episode, Outcome: outcome, Role: consolidation.EvidenceFormationCandidate,
		})
		seenRefs[episode.Ref] = struct{}{}
		seenSessions[episode.SessionRef] = struct{}{}
		if !isCurrent {
			related = append(related, episode)
		}
	}
	return pairs, related, nil
}

func loadUniqueNonAgentFormationOutcome(
	ctx context.Context,
	querier consolidationQuerier,
	owner ownerScope,
	episodeRef string,
) (feedbackOutcome, bool, error) {
	rows, err := querier.Query(ctx, `
		SELECT outcome.outcome_event_ref, outcome.episode_ref,
		       source.source_text, source.actor_kind, source.actor_ref
		FROM outcome_events AS outcome
		JOIN source_events AS source
		  ON source.tenant_ref = outcome.tenant_ref
		 AND source.scope_kind = outcome.scope_kind
		 AND source.agent_ref = outcome.agent_ref
		 AND source.relationship_ref = outcome.relationship_ref
		 AND source.session_ref = outcome.session_ref
		 AND source.source_ref = outcome.source_event_ref
		WHERE outcome.tenant_ref = $1
		  AND outcome.scope_kind = $2
		  AND outcome.agent_ref = $3
		  AND outcome.relationship_ref = $4
		  AND outcome.episode_ref = $5
		ORDER BY outcome.outcome_event_ref
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, episodeRef)
	if err != nil {
		return feedbackOutcome{}, false, storageError("load Disposition formation Outcomes", err)
	}
	defer rows.Close()
	var found feedbackOutcome
	count := 0
	for rows.Next() {
		var outcome feedbackOutcome
		if err := rows.Scan(&outcome.Ref, &outcome.EpisodeRef, &outcome.Text, &outcome.ActorKind, &outcome.ActorRef); err != nil {
			return feedbackOutcome{}, false, storageError("scan Disposition formation Outcome", err)
		}
		if outcome.ActorKind == "" || outcome.ActorKind == ledger.ActorKindAgent || strings.TrimSpace(outcome.Text) == "" {
			continue
		}
		found = outcome
		count++
	}
	if err := rows.Err(); err != nil {
		return feedbackOutcome{}, false, storageError("iterate Disposition formation Outcomes", err)
	}
	if count != 1 {
		return feedbackOutcome{}, false, nil
	}
	return found, true, nil
}

func mergeDispositionFormationEvidence(
	related []episodeEvidence,
	outcomes map[string]feedbackOutcome,
	pairs []dispositionFormationPair,
	formationRelated []episodeEvidence,
) ([]episodeEvidence, map[string]feedbackOutcome) {
	mergedRelated := make([]episodeEvidence, 0, len(formationRelated)+len(related))
	seen := make(map[string]struct{}, len(formationRelated)+len(related))
	for _, values := range [][]episodeEvidence{formationRelated, related} {
		for _, episode := range values {
			if _, duplicate := seen[episode.Ref]; duplicate {
				continue
			}
			seen[episode.Ref] = struct{}{}
			mergedRelated = append(mergedRelated, episode)
		}
	}
	if outcomes == nil {
		outcomes = make(map[string]feedbackOutcome)
	}
	for _, pair := range pairs {
		outcomes[pair.Outcome.Ref] = pair.Outcome
	}
	return mergedRelated, outcomes
}

func markDispositionFormationRoles(
	evidence *consolidation.WindowEvidence,
	pairs []dispositionFormationPair,
) {
	if evidence == nil {
		return
	}
	roles := make(map[string]string, len(pairs))
	for _, pair := range pairs {
		roles[pair.Episode.Ref] = pair.Role
	}
	for index := range evidence.Episodes {
		evidence.Episodes[index].FormationRole = roles[evidence.Episodes[index].EpisodeRef]
	}
}

func exactDispositionFormationBasis(evidence *consolidation.WindowEvidence, refs []string) bool {
	want, ok := consolidation.DispositionFormationBasis(evidence)
	if !ok || len(want) != len(refs) {
		return false
	}
	for index := range want {
		if want[index] != refs[index] {
			return false
		}
	}
	return true
}
