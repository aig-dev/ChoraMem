package mysql

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"unicode"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

type ownerScope struct {
	Kind            ledger.ScopeKind
	TenantRef       string
	AgentRef        string
	RelationshipRef string
}

type episodeEvidence struct {
	Ref        string
	SessionRef string
	RunRef     string
	GroupRef   string
	Owner      ownerScope
	Sources    []sourceEvidence
}

type sourceEvidence struct {
	Ref            string
	Role           ledger.SourceRole
	ActorKind      ledger.ActorKind
	ActorRef       string
	Text           string
	Constitution   ledger.Constitution
	AdmissionOrder int64
}

type activeDisposition struct {
	SeedRef       string
	VersionNumber int
	VersionRef    string
	Tendency      string
	Owner         ownerScope
}

// The bounded active catalogue provides duplicate hints and exact-owner ADAPT
// candidates. Behavioral feedback still requires its separate causal chain.
const maxActiveDispositionHints = 64

const maxConsolidationIndexCandidates = 64

type consolidationQuerier interface {
	Query(context.Context, string, ...any) (rows, error)
	QueryRow(context.Context, string, ...any) row
}

// ConsolidateWindow is the only AI-assisted Seed evolution entry. The Worker
// returns text; Core derives ownership, validates causal eligibility, allocates
// refs, and commits effects automatically.
func (store *Store) ConsolidateWindow(ctx context.Context, window consolidation.Window, worker consolidation.Worker) (consolidation.Receipt, error) {
	if worker == nil || !validStableRef(window.JobRef) || len(window.EpisodeRefs) == 0 ||
		!uniqueStableRefs(window.EpisodeRefs) || !uniqueStableRefsAllowEmpty(window.OutcomeEventRefs) {
		return consolidation.Receipt{}, consolidation.ErrInvalidWindow
	}

	snapshot, err := store.pool.BeginTx(ctx, txOptions{
		IsoLevel: repeatableRead, AccessMode: readOnly,
	})
	if err != nil {
		return consolidation.Receipt{}, storageError("begin consolidation snapshot", err)
	}
	defer func() { _ = snapshot.Rollback(ctx) }()

	episodes, owner, err := loadConsolidationEpisodes(ctx, snapshot, window.EpisodeRefs)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	indexedRefs := store.searchConsolidationMemoryIndex(ctx, owner, episodes)
	relatedEpisodes, err := loadIndexedConsolidationEpisodes(ctx, snapshot, owner, indexedRefs.EpisodeRefs, episodes)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	if len(indexedRefs.EpisodeRefs) > 0 {
		linkedRecollections, linkedDispositions, err := loadEpisodeLinkedMemoryRefs(ctx, snapshot, owner, indexedRefs.EpisodeRefs)
		if err != nil {
			return consolidation.Receipt{}, err
		}
		indexedRefs.RecollectionRefs = uniqueRefs(append(indexedRefs.RecollectionRefs, linkedRecollections...))
		indexedRefs.DispositionRefs = uniqueRefs(append(indexedRefs.DispositionRefs, linkedDispositions...))
	}
	requestHash := consolidationWindowHash(owner, window)
	if receipt, found, err := loadConsolidationReceipt(ctx, snapshot, owner.TenantRef, window.JobRef, requestHash); err != nil {
		return consolidation.Receipt{}, err
	} else if found {
		if err := snapshot.Commit(ctx); err != nil {
			return consolidation.Receipt{}, storageError("commit frozen consolidation snapshot", err)
		}
		return receipt, nil
	}
	activeDispositions, err := loadActiveDispositions(ctx, snapshot, owner, episodes, indexedRefs.DispositionRefs)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	targets, outcomes, err := loadFeedbackState(ctx, snapshot, episodes, window.OutcomeEventRefs, activeDispositions)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	formationIndexedRefs, formationIndexed := store.searchDispositionFormationEpisodes(ctx, owner, episodes, outcomes)
	var formationPairs []dispositionFormationPair
	if formationIndexed {
		var formationRelated []episodeEvidence
		formationPairs, formationRelated, err = loadDispositionFormationPairs(
			ctx, snapshot, owner, episodes, outcomes, formationIndexedRefs,
		)
		if err != nil {
			return consolidation.Receipt{}, err
		}
		relatedEpisodes, outcomes = mergeDispositionFormationEvidence(
			relatedEpisodes, outcomes, formationPairs, formationRelated,
		)
	}
	activeRecollections, err := loadActiveRecollections(ctx, snapshot, owner, episodes, indexedRefs.RecollectionRefs)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	stateHash := hashFeedbackState(targets)
	recollectionStateHash := hashRecollectionState(activeRecollections)
	workerRequest := consolidationWorkerRequest(
		window.JobRef, episodes, relatedEpisodes, outcomes, targets, activeDispositions, activeRecollections,
		formationPairs,
	)
	workerRequestHash := hashConsolidationWorkerRequest(workerRequest)
	if err := snapshot.Commit(ctx); err != nil {
		return consolidation.Receipt{}, storageError("commit consolidation snapshot", err)
	}

	taggedText := ""
	if consolidation.FitsWorkerRequestLimit(workerRequest) && !consolidation.ConstitutionRefCollision(currentConstitution(episodes), workerRequest) {
		taggedText, err = worker.ProcessConsolidationWindow(ctx, workerRequest)
		if err != nil {
			return consolidation.Receipt{}, fmt.Errorf("process consolidation window: %w", err)
		}
	}
	changes, parseErr := consolidation.ParseTaggedText(taggedText)
	if parseErr != nil {
		changes = nil
	}

	tx, err := store.pool.BeginTx(ctx, txOptions{})
	if err != nil {
		return consolidation.Receipt{}, storageError("begin consolidation commit", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if err := tx.acquireLock(ctx, consolidationJobLockKey(owner.TenantRef, window.JobRef)); err != nil {
		return consolidation.Receipt{}, storageError("lock consolidation job", err)
	}
	if receipt, found, err := loadConsolidationReceipt(ctx, tx, owner.TenantRef, window.JobRef, requestHash); err != nil {
		return consolidation.Receipt{}, err
	} else if found {
		if err := tx.Commit(ctx); err != nil {
			return consolidation.Receipt{}, storageError("commit frozen consolidation receipt read", err)
		}
		return receipt, nil
	}

	// Rehydrate the exact canonical snapshot inside the commit transaction.
	episodes, committedOwner, err := loadConsolidationEpisodes(ctx, tx, window.EpisodeRefs)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	if committedOwner != owner {
		return consolidation.Receipt{}, consolidation.ErrInvalidWindow
	}
	committedRelatedEpisodes, err := loadIndexedConsolidationEpisodes(ctx, tx, owner, indexedRefs.EpisodeRefs, episodes)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	for _, lockOwner := range consolidationLockOwners(owner, targets) {
		if err := tx.acquireLock(ctx, consolidationOwnerLockKey(lockOwner)); err != nil {
			return consolidation.Receipt{}, storageError("lock Memory owner scope", err)
		}
	}
	committedActiveDispositions, err := loadActiveDispositions(ctx, tx, owner, episodes, indexedRefs.DispositionRefs)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	committedTargets, committedOutcomes, err := loadFeedbackState(ctx, tx, episodes, window.OutcomeEventRefs, committedActiveDispositions)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	var committedFormationPairs []dispositionFormationPair
	if formationIndexed {
		var committedFormationRelated []episodeEvidence
		committedFormationPairs, committedFormationRelated, err = loadDispositionFormationPairs(
			ctx, tx, owner, episodes, committedOutcomes, formationIndexedRefs,
		)
		if err != nil {
			return consolidation.Receipt{}, err
		}
		committedRelatedEpisodes, committedOutcomes = mergeDispositionFormationEvidence(
			committedRelatedEpisodes, committedOutcomes, committedFormationPairs, committedFormationRelated,
		)
	}
	committedActiveRecollections, err := loadActiveRecollections(ctx, tx, owner, episodes, indexedRefs.RecollectionRefs)
	if err != nil {
		return consolidation.Receipt{}, err
	}
	committedWorkerRequest := consolidationWorkerRequest(
		window.JobRef, episodes, committedRelatedEpisodes, committedOutcomes, committedTargets,
		committedActiveDispositions, committedActiveRecollections, committedFormationPairs,
	)
	if hashFeedbackState(committedTargets) != stateHash ||
		hashRecollectionState(committedActiveRecollections) != recollectionStateHash ||
		hashConsolidationWorkerRequest(committedWorkerRequest) != workerRequestHash {
		return consolidation.Receipt{}, consolidation.ErrWindowStale
	}

	episodeByRef := make(map[string]episodeEvidence, len(episodes)+len(committedRelatedEpisodes))
	currentEpisodeRefs := make(map[string]struct{}, len(episodes))
	for _, episode := range episodes {
		episodeByRef[episode.Ref] = episode
		currentEpisodeRefs[episode.Ref] = struct{}{}
	}
	for _, episode := range committedRelatedEpisodes {
		episodeByRef[episode.Ref] = episode
	}
	recollectionByVersion := make(map[string]*activeRecollection, len(committedActiveRecollections))
	for index := range committedActiveRecollections {
		recollection := &committedActiveRecollections[index]
		recollectionByVersion[recollection.VersionRef] = recollection
	}
	receipt := consolidation.Receipt{}
	if !validConsolidationChanges(
		changes, owner, episodeByRef, currentEpisodeRefs, recollectionByVersion,
		committedTargets, committedOutcomes, committedWorkerRequest.Evidence,
	) {
		changes = nil
	}
	for _, change := range changes {
		var versionRef string
		var applied bool
		switch {
		case change.Target == consolidation.TargetNewRecollection:
			versionRef, applied, err = applyRecollectionFormation(ctx, tx, owner, window.JobRef, change, episodeByRef)
			if err == nil && applied {
				err = enqueueMemoryFormation(ctx, tx, memoryindex.KindRecollection, versionRef, owner)
			}
			if applied && versionRef != "" {
				receipt.RecollectionVersionRefs = append(receipt.RecollectionVersionRefs, versionRef)
			}
		case change.Target == consolidation.TargetNewDisposition:
			versionRef, applied, err = applyDispositionFormation(ctx, tx, owner, window.JobRef, change, episodeByRef, committedOutcomes)
			if err == nil && applied {
				err = enqueueMemoryFormation(ctx, tx, memoryindex.KindDisposition, versionRef, owner)
			}
			if applied && versionRef != "" {
				receipt.DispositionVersionRefs = append(receipt.DispositionVersionRefs, versionRef)
			}
		case recollectionByVersion[change.Target] != nil:
			target := recollectionByVersion[change.Target]
			switch change.Operation {
			case consolidation.ChangeKeep:
				applied, err = applyRecollectionKeep(ctx, tx, target, change.BasisRefs)
			case consolidation.ChangeText:
				versionRef, applied, err = applyRecollectionRevision(ctx, tx, window.JobRef, target, change)
				if err == nil && applied {
					err = enqueueMemoryRevision(
						ctx, tx, memoryindex.KindRecollection, target.Recollection,
						target.VersionRef, versionRef, target.Owner,
					)
				}
			}
			if applied && versionRef != "" {
				receipt.RecollectionVersionRefs = append(receipt.RecollectionVersionRefs, versionRef)
			}
		case committedTargets[change.Target] != nil:
			target := committedTargets[change.Target]
			switch change.Operation {
			case consolidation.ChangeReenact:
				applied, err = applyReenactment(ctx, tx, target.Owner, target, change.BasisRefs)
			case consolidation.ChangeInhibit:
				applied, err = applyInhibition(ctx, tx, target.Owner, target, change.BasisRefs)
			case consolidation.ChangeText, consolidation.ChangeAdapt:
				versionRef, applied, err = applyRevision(ctx, tx, target.Owner, window.JobRef, target, change)
				if err == nil && applied && versionRef != "" {
					err = enqueueMemoryRevision(
						ctx, tx, memoryindex.KindDisposition, target.SeedRef,
						target.VersionRef, versionRef, target.Owner,
					)
				}
			}
			if applied && versionRef != "" {
				receipt.DispositionVersionRefs = append(receipt.DispositionVersionRefs, versionRef)
			}
		}
		if err != nil {
			return consolidation.Receipt{}, err
		}
	}
	sort.Strings(receipt.RecollectionVersionRefs)
	sort.Strings(receipt.DispositionVersionRefs)

	if _, err := tx.Exec(ctx, `
		INSERT INTO consolidation_receipts (
			tenant_ref, job_ref, scope_kind, agent_ref, relationship_ref, request_hash
		) VALUES ($1, $2, $3, $4, $5, $6)
	`, owner.TenantRef, window.JobRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, requestHash[:]); err != nil {
		return consolidation.Receipt{}, storageError("freeze consolidation receipt", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return consolidation.Receipt{}, storageError("commit consolidation effects", err)
	}
	return receipt, nil
}

// searchConsolidationMemoryIndex opens a bounded semantic lane for existing
// evidence, Recollections, and Dispositions. Returned refs still have to
// resolve through the exact canonical owner. Indexed Dispositions need a
// current user Situation for ADAPT; behavioral feedback still needs its chain.
func (store *Store) searchConsolidationMemoryIndex(
	ctx context.Context,
	owner ownerScope,
	episodes []episodeEvidence,
) consolidation.MemoryIndexCandidates {
	if store.memoryIndex == nil {
		return consolidation.MemoryIndexCandidates{}
	}
	scope := memoryindex.Scope{
		TenantRef: owner.TenantRef, AgentRef: owner.AgentRef, RelationshipRef: owner.RelationshipRef,
	}
	situationTexts := make([]string, 0, len(episodes))
	for _, episode := range episodes {
		for _, source := range episode.Sources {
			if source.Role == ledger.RoleSituation {
				situationTexts = append(situationTexts, source.Text)
			}
		}
	}
	return consolidation.SearchMemoryIndex(ctx, store.memoryIndex, scope, situationTexts, maxConsolidationIndexCandidates)
}

// loadIndexedConsolidationEpisodes turns untrusted semantic refs into bounded
// canonical evidence. A related Episode can support formation only after exact
// owner rehydration; invalid, missing, or cross-owner refs fail open.
func loadIndexedConsolidationEpisodes(
	ctx context.Context,
	querier consolidationQuerier,
	owner ownerScope,
	refs []string,
	current []episodeEvidence,
) ([]episodeEvidence, error) {
	excluded := make(map[string]struct{}, len(current))
	for _, episode := range current {
		excluded[episode.Ref] = struct{}{}
	}
	result := make([]episodeEvidence, 0, len(refs))
	for _, ref := range refs {
		if _, duplicate := excluded[ref]; duplicate {
			continue
		}
		episodes, indexedOwner, err := loadConsolidationEpisodes(ctx, querier, []string{ref})
		if errors.Is(err, consolidation.ErrInvalidWindow) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if indexedOwner != owner || len(episodes) != 1 {
			continue
		}
		result = append(result, episodes[0])
		excluded[ref] = struct{}{}
	}
	return result, nil
}

func applyDispositionFormation(
	ctx context.Context,
	tx *transaction,
	owner ownerScope,
	jobRef string,
	change consolidation.Change,
	episodeByRef map[string]episodeEvidence,
	outcomeByRef map[string]feedbackOutcome,
) (string, bool, error) {
	if change.Target != consolidation.TargetNewDisposition ||
		(change.Operation != consolidation.ChangeText && change.Operation != consolidation.ChangeAdapt) ||
		change.Application != dispositionApplication(owner) {
		return "", false, nil
	}
	tendency := strings.TrimSpace(change.Text)
	episodeBasis, outcomeBasis, eligible := eligibleDispositionFormationBasis(change.BasisRefs, episodeByRef, outcomeByRef)
	if change.Operation == consolidation.ChangeAdapt {
		episodeBasis, eligible = eligibleAdaptationBasis(change.BasisRefs, episodeByRef, nil)
		outcomeBasis = nil
	}
	if tendency == "" || !eligible {
		return "", false, nil
	}

	var duplicate bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM disposition_seeds AS seed
			JOIN seed_versions AS version
			  ON version.tenant_ref = seed.tenant_ref
			 AND version.seed_ref = seed.seed_ref
			WHERE seed.tenant_ref = $1
			  AND seed.scope_kind = $2
			  AND seed.agent_ref = $3
			  AND seed.relationship_ref = $4
			  AND version.status = 'active'
			  AND version.tendency_text = $5
		)
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, tendency).Scan(&duplicate); err != nil {
		return "", false, storageError("check exact active Seed duplicate", err)
	}
	if duplicate {
		return "", false, nil
	}

	seedRef, versionRef := formationRefs(owner, jobRef, tendency, episodeBasis, outcomeBasis)
	if _, err := tx.Exec(ctx, `
		INSERT INTO disposition_seeds (
			tenant_ref, scope_kind, agent_ref, relationship_ref, seed_ref
		) VALUES ($1, $2, $3, $4, $5)
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, seedRef); err != nil {
		return "", false, storageError("insert DispositionSeed", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO seed_versions (
			tenant_ref, seed_ref, seed_version_ref, version_number,
			tendency_text, status, origin_job_ref
		) VALUES ($1, $2, $3, 1, $4, 'active', $5)
	`, owner.TenantRef, seedRef, versionRef, tendency, jobRef); err != nil {
		return "", false, storageError("insert SeedVersion v1", err)
	}
	for _, episodeRef := range episodeBasis {
		if _, err := tx.Exec(ctx, `
			INSERT INTO seed_basis_links (
				tenant_ref, seed_version_ref, episode_ref, role
			) VALUES ($1, $2, $3, 'formation')
		`, owner.TenantRef, versionRef, episodeRef); err != nil {
			return "", false, storageError("insert formation Basis", err)
		}
	}
	if _, err := insertOutcomeBasis(ctx, tx, owner.TenantRef, versionRef, outcomeBasis, "formation"); err != nil {
		return "", false, err
	}
	return versionRef, true, nil
}

func eligibleDispositionFormationBasis(
	refs []string,
	episodeByRef map[string]episodeEvidence,
	outcomeByRef map[string]feedbackOutcome,
) ([]string, []string, bool) {
	if !uniqueStableRefs(refs) {
		return nil, nil, false
	}
	episodes := make([]string, 0, len(refs))
	outcomes := make([]string, 0, len(refs))
	for _, ref := range refs {
		if _, exists := episodeByRef[ref]; exists {
			episodes = append(episodes, ref)
			continue
		}
		outcome, exists := outcomeByRef[ref]
		if !exists || outcome.ActorKind == "" || outcome.ActorKind == ledger.ActorKindAgent {
			return nil, nil, false
		}
		outcomes = append(outcomes, ref)
	}
	episodes, eligible := eligibleFormationBasis(episodes, episodeByRef)
	if !eligible {
		return nil, nil, false
	}
	episodeSet := refSet(episodes)
	for _, ref := range outcomes {
		outcome := outcomeByRef[ref]
		episode, exists := episodeByRef[outcome.EpisodeRef]
		if _, cited := episodeSet[outcome.EpisodeRef]; !exists || !cited ||
			!episodeHasFormationRoles(episode) || !episodeHasSituationActor(episode, false) {
			return nil, nil, false
		}
	}
	sort.Strings(outcomes)
	return episodes, outcomes, true
}

func eligibleFormationBasis(refs []string, episodeByRef map[string]episodeEvidence) ([]string, bool) {
	if len(refs) < 2 || !uniqueStableRefs(refs) {
		return nil, false
	}
	basisRefs := append([]string(nil), refs...)
	sort.Strings(basisRefs)
	seenSources := make(map[string]struct{})
	seenSessions := make(map[string]struct{})
	for _, episodeRef := range basisRefs {
		episode, allowed := episodeByRef[episodeRef]
		if !allowed || !episodeHasFormationRoles(episode) || !episodeHasSituationActor(episode, false) {
			return nil, false
		}
		seenSessions[episode.SessionRef] = struct{}{}
		for _, source := range episode.Sources {
			scopedSourceRef := episode.SessionRef + "\x00" + source.Ref
			if _, duplicate := seenSources[scopedSourceRef]; duplicate {
				return nil, false
			}
			seenSources[scopedSourceRef] = struct{}{}
		}
	}
	if len(seenSessions) < 2 {
		return nil, false
	}
	return basisRefs, true
}

// A complete Episode is not necessarily external evidence: Agent-authored
// Situations remain observable facts but cannot bootstrap learned tendencies.
// Only a trusted user Situation opens direct long-term requirement semantics.
func episodeHasSituationActor(episode episodeEvidence, userOnly bool) bool {
	for _, source := range episode.Sources {
		if source.Role == ledger.RoleSituation && source.ActorKind != ledger.ActorKindAgent &&
			source.ActorKind != "" && (!userOnly || source.ActorKind == ledger.ActorKindUser) {
			return true
		}
	}
	return false
}

func eligibleAdaptationBasis(refs []string, episodes map[string]episodeEvidence, current map[string]struct{}) ([]string, bool) {
	if len(refs) != 1 {
		return nil, false
	}
	basis, eligible := eligibleRecollectionBasis(refs, episodes)
	if !eligible {
		return nil, false
	}
	for _, ref := range basis {
		if (current == nil || hasRef(current, ref)) && episodeHasSituationActor(episodes[ref], true) {
			return basis, true
		}
	}
	return nil, false
}

func hasFormationEvidence(current, related []episodeEvidence) bool {
	byRef := make(map[string]episodeEvidence, len(current)+len(related))
	for _, episode := range current {
		byRef[episode.Ref] = episode
	}
	for _, episode := range related {
		byRef[episode.Ref] = episode
	}
	for _, episode := range current {
		for ref := range byRef {
			if _, eligible := eligibleFormationBasis([]string{episode.Ref, ref}, byRef); eligible {
				return true
			}
		}
	}
	return false
}

func episodeHasFormationRoles(episode episodeEvidence) bool {
	situationRefs := make([]string, 0, 1)
	agentActRefs := make([]string, 0, 1)
	for _, source := range episode.Sources {
		switch source.Role {
		case ledger.RoleSituation:
			situationRefs = append(situationRefs, source.Ref)
		case ledger.RoleAgentAct:
			if source.ActorKind == ledger.ActorKindAgent && source.ActorRef == episode.Owner.AgentRef {
				agentActRefs = append(agentActRefs, source.Ref)
			}
		}
	}
	for _, situationRef := range situationRefs {
		for _, agentActRef := range agentActRefs {
			if situationRef != agentActRef {
				return true
			}
		}
	}
	return false
}

func loadConsolidationEpisodes(ctx context.Context, querier consolidationQuerier, refs []string) ([]episodeEvidence, ownerScope, error) {
	episodes := make([]episodeEvidence, 0, len(refs))
	var owner ownerScope
	for index, ref := range refs {
		rows, err := querier.Query(ctx, `
			SELECT
				e.tenant_ref, e.scope_kind, e.agent_ref, e.relationship_ref, e.session_ref,
				e.run_ref, e.source_group_ref, e.episode_ref, link.source_event_ref, link.role,
				source.actor_kind, source.actor_ref, source.source_text,
				source.constitution_ref, source.constitution_text, source.admission_order
			FROM episodes AS e
			JOIN episode_links AS link
			  ON link.tenant_ref = e.tenant_ref
			 AND link.scope_kind = e.scope_kind
			 AND link.agent_ref = e.agent_ref
			 AND link.relationship_ref = e.relationship_ref
			 AND link.session_ref = e.session_ref
			 AND link.run_ref = e.run_ref
			 AND link.source_group_ref = e.source_group_ref
			 AND link.episode_ref = e.episode_ref
			JOIN source_events AS source
			  ON source.tenant_ref = link.tenant_ref
			 AND source.scope_kind = link.scope_kind
			 AND source.agent_ref = link.agent_ref
			 AND source.relationship_ref = link.relationship_ref
			 AND source.session_ref = link.session_ref
			 AND source.source_ref = link.source_event_ref
			WHERE e.episode_ref = $1
			ORDER BY link.role, link.source_event_ref
		`, ref)
		if err != nil {
			return nil, ownerScope{}, storageError("load consolidation Episode", err)
		}

		episode := episodeEvidence{}
		for rows.Next() {
			var currentOwner ownerScope
			var source sourceEvidence
			if err := rows.Scan(
				&currentOwner.TenantRef,
				&currentOwner.Kind,
				&currentOwner.AgentRef,
				&currentOwner.RelationshipRef,
				&episode.SessionRef,
				&episode.RunRef,
				&episode.GroupRef,
				&episode.Ref,
				&source.Ref,
				&source.Role,
				&source.ActorKind,
				&source.ActorRef,
				&source.Text,
				&source.Constitution.MemoryRef, &source.Constitution.Text, &source.AdmissionOrder,
			); err != nil {
				rows.Close()
				return nil, ownerScope{}, storageError("scan consolidation Episode", err)
			}
			episode.Owner = currentOwner
			episode.Sources = append(episode.Sources, source)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return nil, ownerScope{}, storageError("iterate consolidation Episode", err)
		}
		rows.Close()
		if episode.Ref == "" || episode.Ref != ref || !episodeHasFormationRoles(episode) {
			return nil, ownerScope{}, consolidation.ErrInvalidWindow
		}
		if index == 0 {
			owner = episode.Owner
		} else if episode.Owner != owner {
			return nil, ownerScope{}, consolidation.ErrInvalidWindow
		}
		episodes = append(episodes, episode)
	}
	return episodes, owner, nil
}

func loadConsolidationReceipt(ctx context.Context, querier consolidationQuerier, tenantRef, jobRef string, requestHash [sha256.Size]byte) (consolidation.Receipt, bool, error) {
	var storedHash []byte
	err := querier.QueryRow(ctx, `
		SELECT request_hash
		FROM consolidation_receipts
		WHERE tenant_ref = $1 AND job_ref = $2
	`, tenantRef, jobRef).Scan(&storedHash)
	if errors.Is(err, errNoRows) {
		return consolidation.Receipt{}, false, nil
	}
	if err != nil {
		return consolidation.Receipt{}, false, storageError("load consolidation receipt", err)
	}
	if !bytes.Equal(storedHash, requestHash[:]) {
		return consolidation.Receipt{}, false, fmt.Errorf("%w: %s", consolidation.ErrWindowConflict, jobRef)
	}

	rows, err := querier.Query(ctx, `
		SELECT seed_version_ref
		FROM seed_versions
		WHERE tenant_ref = $1 AND origin_job_ref = $2
		ORDER BY seed_version_ref
	`, tenantRef, jobRef)
	if err != nil {
		return consolidation.Receipt{}, false, storageError("load consolidation effects", err)
	}
	defer rows.Close()
	receipt := consolidation.Receipt{}
	for rows.Next() {
		var ref string
		if err := rows.Scan(&ref); err != nil {
			return consolidation.Receipt{}, false, storageError("scan consolidation effect", err)
		}
		receipt.DispositionVersionRefs = append(receipt.DispositionVersionRefs, ref)
	}
	if err := rows.Err(); err != nil {
		return consolidation.Receipt{}, false, storageError("iterate consolidation effects", err)
	}
	rows.Close()
	rows, err = querier.Query(ctx, `
		SELECT recollection_version_ref
		FROM recollection_versions
		WHERE tenant_ref = $1 AND origin_job_ref = $2
		ORDER BY recollection_version_ref
	`, tenantRef, jobRef)
	if err != nil {
		return consolidation.Receipt{}, false, storageError("load Recollection consolidation effects", err)
	}
	defer rows.Close()
	for rows.Next() {
		var ref string
		if err := rows.Scan(&ref); err != nil {
			return consolidation.Receipt{}, false, storageError("scan Recollection consolidation effect", err)
		}
		receipt.RecollectionVersionRefs = append(receipt.RecollectionVersionRefs, ref)
	}
	if err := rows.Err(); err != nil {
		return consolidation.Receipt{}, false, storageError("iterate Recollection consolidation effects", err)
	}
	return receipt, true, nil
}

func loadActiveDispositions(
	ctx context.Context,
	querier consolidationQuerier,
	owner ownerScope,
	episodes []episodeEvidence,
	indexedRefs []string,
) ([]activeDisposition, error) {
	rows, err := querier.Query(ctx, `
		SELECT version.seed_ref, version.version_number, version.seed_version_ref, version.tendency_text
		FROM disposition_seeds AS seed
		JOIN seed_versions AS version
		  ON version.tenant_ref = seed.tenant_ref
		 AND version.seed_ref = seed.seed_ref
		WHERE seed.tenant_ref = $1
		  AND seed.scope_kind = $2
		  AND seed.agent_ref = $3
		  AND seed.relationship_ref = $4
		  AND version.status = 'active'
		ORDER BY version.seed_version_ref
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef)
	if err != nil {
		return nil, storageError("load active Seeds for consolidation", err)
	}
	defer rows.Close()
	dispositions := make([]activeDisposition, 0)
	for rows.Next() {
		var disposition activeDisposition
		if err := rows.Scan(&disposition.SeedRef, &disposition.VersionNumber, &disposition.VersionRef, &disposition.Tendency); err != nil {
			return nil, storageError("scan active Seed for consolidation", err)
		}
		disposition.Owner = owner
		dispositions = append(dispositions, disposition)
	}
	if err := rows.Err(); err != nil {
		return nil, storageError("iterate active Seeds for consolidation", err)
	}
	rows.Close()

	// Like Recollection candidates, rank the exact-owner active catalogue by
	// current Situation before bounding it. Hash-like ref order must not make
	// an otherwise relevant existing Seed unreachable to a direct correction.
	query := recollectionWindowQuery(episodes)
	ranker := selection.DefaultRanker()
	scores := make(map[string]float64, len(dispositions))
	for _, disposition := range dispositions {
		scores[disposition.VersionRef] = ranker.Score(query, disposition.Tendency)
	}
	sort.Slice(dispositions, func(left, right int) bool {
		leftScore, rightScore := scores[dispositions[left].VersionRef], scores[dispositions[right].VersionRef]
		if leftScore != rightScore {
			return leftScore > rightScore
		}
		return dispositions[left].VersionRef < dispositions[right].VersionRef
	})
	if len(dispositions) > maxActiveDispositionHints {
		dispositions = dispositions[:maxActiveDispositionHints]
	}
	// Stable presentation/hash ordering; relevance remains transient.
	sort.Slice(dispositions, func(left, right int) bool {
		return dispositions[left].VersionRef < dispositions[right].VersionRef
	})

	if len(indexedRefs) == 0 {
		return dispositions, nil
	}
	rows, err = querier.Query(ctx, `
		SELECT version.seed_ref, version.version_number, version.seed_version_ref, version.tendency_text
		FROM disposition_seeds AS seed
		JOIN seed_versions AS version
		  ON version.tenant_ref = seed.tenant_ref
		 AND version.seed_ref = seed.seed_ref
		WHERE seed.tenant_ref = $1
		  AND seed.scope_kind = $2
		  AND seed.agent_ref = $3
		  AND seed.relationship_ref = $4
		  AND version.status = 'active'
		  AND JSON_CONTAINS($5, JSON_QUOTE(CONVERT(version.seed_version_ref USING utf8mb4)))
		ORDER BY version.seed_version_ref
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, indexedRefs)
	if err != nil {
		return nil, storageError("load indexed active Seeds for consolidation", err)
	}
	defer rows.Close()
	seen := make(map[string]struct{}, len(dispositions)+len(indexedRefs))
	for _, disposition := range dispositions {
		seen[disposition.VersionRef] = struct{}{}
	}
	for rows.Next() {
		var disposition activeDisposition
		if err := rows.Scan(&disposition.SeedRef, &disposition.VersionNumber, &disposition.VersionRef, &disposition.Tendency); err != nil {
			return nil, storageError("scan indexed active Seed for consolidation", err)
		}
		if _, duplicate := seen[disposition.VersionRef]; duplicate {
			continue
		}
		disposition.Owner = owner
		dispositions = append(dispositions, disposition)
		seen[disposition.VersionRef] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, storageError("iterate indexed active Seeds for consolidation", err)
	}
	sort.Slice(dispositions, func(left, right int) bool {
		return dispositions[left].VersionRef < dispositions[right].VersionRef
	})
	return dispositions, nil
}

func renderConsolidationWindow(episodes []episodeEvidence, activeDispositions []activeDisposition, includeActors bool) string {
	var text strings.Builder
	for index, episode := range episodes {
		if index > 0 {
			text.WriteByte('\n')
		}
		fmt.Fprintf(&text, "EPISODE %s\nSESSION %s\n", episode.Ref, episode.SessionRef)
		for _, source := range episode.Sources {
			fmt.Fprintf(&text, "%s\n", strings.ToUpper(string(source.Role)))
			if includeActors {
				fmt.Fprintf(&text, "ACTOR %s %s\n", source.ActorKind, source.ActorRef)
			}
			fmt.Fprintf(&text, "SOURCE %s\n%s\n", source.Ref, source.Text)
		}
	}
	if len(activeDispositions) > 0 {
		text.WriteString("\nACTIVE_DISPOSITION_HINTS\n")
		for _, disposition := range activeDispositions {
			fmt.Fprintf(
				&text, "ACTIVE_DISPOSITION_HINT %s\nAPPLICATION %s\nTEXT %s\n",
				disposition.VersionRef, dispositionApplication(disposition.Owner), disposition.Tendency,
			)
		}
	}
	return text.String()
}

func formationRefs(owner ownerScope, jobRef, tendency string, episodeRefs, outcomeRefs []string) (string, string) {
	fields := []string{
		"seed-form.v1",
		string(owner.Kind),
		owner.TenantRef,
		owner.AgentRef,
		owner.RelationshipRef,
		jobRef,
		tendency,
	}
	fields = append(fields, episodeRefs...)
	if len(outcomeRefs) > 0 {
		fields = append(fields, "OUTCOMES")
		fields = append(fields, outcomeRefs...)
	}
	hash := hashFields(fields...)
	seedRef := "seed_" + hex.EncodeToString(hash[:16])
	return seedRef, seedRef + "@1"
}

func consolidationWindowHash(owner ownerScope, window consolidation.Window) [sha256.Size]byte {
	fields := []string{
		"consolidation-window.v2",
		string(owner.Kind),
		owner.TenantRef,
		owner.AgentRef,
		owner.RelationshipRef,
	}
	fields = append(fields, window.EpisodeRefs...)
	fields = append(fields, "outcomes")
	fields = append(fields, window.OutcomeEventRefs...)
	return hashFields(fields...)
}

func consolidationJobLockKey(tenantRef, jobRef string) int64 {
	hash := hashFields("consolidation-job-lock.v1", tenantRef, jobRef)
	return int64(binary.BigEndian.Uint64(hash[:8]))
}

func consolidationOwnerLockKey(owner ownerScope) int64 {
	hash := hashFields(
		"consolidation-owner-lock.v1",
		string(owner.Kind),
		owner.TenantRef,
		owner.AgentRef,
		owner.RelationshipRef,
	)
	return int64(binary.BigEndian.Uint64(hash[:8]))
}

func uniqueStableRefs(refs []string) bool {
	seen := make(map[string]struct{}, len(refs))
	for _, ref := range refs {
		if !validStableRef(ref) {
			return false
		}
		if _, duplicate := seen[ref]; duplicate {
			return false
		}
		seen[ref] = struct{}{}
	}
	return true
}

func validStableRef(ref string) bool {
	return ledger.ValidStableRef(ref) && strings.IndexFunc(ref, unicode.IsSpace) == -1
}
