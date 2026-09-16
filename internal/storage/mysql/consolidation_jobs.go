package mysql

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
)

// enqueueConsolidationEvidence joins newly durable evidence to the sole
// collecting Job for its long-term owner. It must run in the same transaction
// as the intake write so a crash can lose neither both nor one.
func enqueueConsolidationEvidence(
	ctx context.Context,
	tx *transaction,
	scope ledger.Scope,
	episodeRefs []string,
	outcomeRefs []string,
) error {
	if len(episodeRefs) == 0 {
		return nil
	}
	owner := ownerScope{
		Kind: scope.Kind, TenantRef: scope.TenantRef, AgentRef: scope.AgentRef,
		RelationshipRef: scope.RelationshipRef,
	}
	if err := tx.acquireLock(ctx, consolidationCollectionLockKey(owner)); err != nil {
		return storageError("lock collecting consolidation Job", err)
	}

	var jobRef string
	var storedEpisodes, storedOutcomes []string
	err := tx.QueryRow(ctx, `
		SELECT job_ref, episode_refs, outcome_event_refs
		FROM consolidation_jobs
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND window_hash IS NULL
		  AND completed_at IS NULL
		FOR UPDATE
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef).Scan(
		&jobRef, &storedEpisodes, &storedOutcomes,
	)
	if errors.Is(err, errNoRows) {
		jobRef, err = randomStableRef("consolidation-job")
		if err != nil {
			return err
		}
		storedEpisodes = nil
		storedOutcomes = nil
	} else if err != nil {
		return storageError("load collecting consolidation Job", err)
	}

	mergedEpisodes, episodesChanged := appendUniqueRefs(storedEpisodes, episodeRefs)
	mergedOutcomes, outcomesChanged := appendUniqueRefs(storedOutcomes, outcomeRefs)
	if mergedOutcomes == nil {
		mergedOutcomes = []string{}
	}
	if !episodesChanged && !outcomesChanged {
		return nil
	}
	if len(storedEpisodes) == 0 {
		tag, err := tx.Exec(ctx, `
		INSERT INTO consolidation_jobs (
				job_ref, tenant_ref, scope_kind, agent_ref, relationship_ref,
				episode_refs, outcome_event_refs
			) VALUES ($1, $2, $3, $4, $5, $6, $7)
			ON DUPLICATE KEY UPDATE job_ref = consolidation_jobs.job_ref
		`, jobRef, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef,
			mergedEpisodes, mergedOutcomes)
		if err != nil {
			return storageError("create collecting consolidation Job", err)
		}
		if tag.RowsAffected() == 1 {
			return nil
		}

		// A lease can atomically freeze the row seen above and create its
		// remainder before this insert resumes. Join that new collecting row
		// instead of failing or creating a second owner queue.
		if err := tx.QueryRow(ctx, `
			SELECT job_ref, episode_refs, outcome_event_refs
			FROM consolidation_jobs
			WHERE tenant_ref = $1
			  AND scope_kind = $2
			  AND agent_ref = $3
			  AND relationship_ref = $4
			  AND window_hash IS NULL
			  AND completed_at IS NULL
			FOR UPDATE
		`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef).Scan(
			&jobRef, &storedEpisodes, &storedOutcomes,
		); err != nil {
			return storageError("load concurrent consolidation Job remainder", err)
		}
		mergedEpisodes, episodesChanged = appendUniqueRefs(storedEpisodes, episodeRefs)
		mergedOutcomes, outcomesChanged = appendUniqueRefs(storedOutcomes, outcomeRefs)
		if mergedOutcomes == nil {
			mergedOutcomes = []string{}
		}
		if !episodesChanged && !outcomesChanged {
			return nil
		}
	}
	if _, err := tx.Exec(ctx, `
		UPDATE consolidation_jobs
		SET episode_refs = $2,
		    outcome_event_refs = $3,
		    not_before = CURRENT_TIMESTAMP(6),
		    updated_at = CURRENT_TIMESTAMP(6)
		WHERE job_ref = $1
	`, jobRef, mergedEpisodes, mergedOutcomes); err != nil {
		return storageError("extend collecting consolidation Job", err)
	}
	return nil
}

func enqueueEpisodeState(ctx context.Context, tx *transaction, scope ledger.Scope, episodeRef string) error {
	if episodeRef == "" {
		return nil
	}
	rows, err := tx.Query(ctx, `
		SELECT outcome_event_ref
		FROM outcome_events
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND episode_ref = $5
		ORDER BY outcome_event_ref
	`, scope.TenantRef, scope.Kind, scope.AgentRef, scope.RelationshipRef, episodeRef)
	if err != nil {
		return storageError("load queued Episode Outcomes", err)
	}
	defer rows.Close()
	var outcomes []string
	for rows.Next() {
		var ref string
		if err := rows.Scan(&ref); err != nil {
			return storageError("scan queued Episode Outcome", err)
		}
		outcomes = append(outcomes, ref)
	}
	if err := rows.Err(); err != nil {
		return storageError("iterate queued Episode Outcomes", err)
	}
	return enqueueConsolidationEvidence(ctx, tx, scope, []string{episodeRef}, outcomes)
}

func enqueueRunEpisodes(ctx context.Context, tx *transaction, scope ledger.Scope, runRef string) error {
	rows, err := tx.Query(ctx, `
		SELECT episode_ref
		FROM episodes
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND run_ref = $6
		ORDER BY source_group_ref
	`, scope.TenantRef, scope.Kind, scope.AgentRef, scope.RelationshipRef,
		scope.SessionRef, runRef)
	if err != nil {
		return storageError("load delivered run Episodes", err)
	}
	defer rows.Close()
	var refs []string
	for rows.Next() {
		var ref string
		if err := rows.Scan(&ref); err != nil {
			return storageError("scan delivered run Episode", err)
		}
		refs = append(refs, ref)
	}
	if err := rows.Err(); err != nil {
		return storageError("iterate delivered run Episodes", err)
	}
	for _, ref := range refs {
		if err := enqueueEpisodeState(ctx, tx, scope, ref); err != nil {
			return err
		}
	}
	return nil
}

// LeaseConsolidationJob freezes and leases one ready window. Frozen retries
// keep the same refs and hash. One complete Episode becomes eligible after
// quiet time; source-only intake never enters the collecting queue.
func (store *Store) LeaseConsolidationJob(ctx context.Context, request scheduler.LeaseRequest) (scheduler.Job, bool, error) {
	if request.MaxEpisodes < 2 || request.Overlap < 0 || request.LeaseUntil.IsZero() {
		return scheduler.Job{}, false, errors.New("invalid consolidation lease request")
	}
	tx, err := store.pool.BeginTx(ctx, txOptions{})
	if err != nil {
		return scheduler.Job{}, false, storageError("begin consolidation Job lease", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var job scheduler.Job
	var owner ownerScope
	var windowHash []byte
	err = tx.QueryRow(ctx, `
		SELECT
			job_ref, tenant_ref, scope_kind, agent_ref, relationship_ref,
			episode_refs, outcome_event_refs, window_hash
		FROM consolidation_jobs AS job
		WHERE job.completed_at IS NULL
		  AND job.not_before <= CURRENT_TIMESTAMP(6)
		  AND (job.lease_until IS NULL OR job.lease_until <= CURRENT_TIMESTAMP(6))
		  AND NOT EXISTS (
			SELECT 1
			FROM consolidation_jobs AS earlier
			WHERE earlier.tenant_ref = job.tenant_ref
			  AND earlier.scope_kind = job.scope_kind
			  AND earlier.agent_ref = job.agent_ref
			  AND earlier.relationship_ref = job.relationship_ref
			  AND earlier.completed_at IS NULL
			  AND earlier.job_order < job.job_order
		  )
		  AND (
			job.window_hash IS NOT NULL OR (
				(job.updated_at <= $1 OR JSON_LENGTH(job.episode_refs) >= $2) AND
				JSON_LENGTH(job.episode_refs) >= 1
			)
		  )
		ORDER BY job.job_order
		LIMIT 1
		FOR UPDATE SKIP LOCKED
	`, request.QuietBefore, request.MaxEpisodes).Scan(
		&job.Ref, &owner.TenantRef, &owner.Kind, &owner.AgentRef, &owner.RelationshipRef,
		&job.EpisodeRefs, &job.OutcomeEventRefs, &windowHash,
	)
	if errors.Is(err, errNoRows) {
		if err := tx.Commit(ctx); err != nil {
			return scheduler.Job{}, false, storageError("commit empty consolidation Job lease", err)
		}
		return scheduler.Job{}, false, nil
	}
	if err != nil {
		return scheduler.Job{}, false, storageError("select ready consolidation Job", err)
	}

	var remainderEpisodes, remainderOutcomes []string
	if windowHash == nil {
		collectedEpisodes := append([]string(nil), job.EpisodeRefs...)
		overlap := request.Overlap
		if overlap >= request.MaxEpisodes {
			overlap = request.MaxEpisodes - 1
		}
		combined, overlapOutcomes, err := prependPreviousOverlap(ctx, tx, owner, collectedEpisodes, overlap)
		if err != nil {
			return scheduler.Job{}, false, err
		}
		if len(combined) > request.MaxEpisodes {
			job.EpisodeRefs = append([]string(nil), combined[:request.MaxEpisodes]...)
		} else {
			job.EpisodeRefs = combined
		}
		selectedEpisodes := refSet(job.EpisodeRefs)
		for _, ref := range collectedEpisodes {
			if _, selected := selectedEpisodes[ref]; !selected {
				remainderEpisodes = append(remainderEpisodes, ref)
			}
		}
		candidateOutcomes, _ := appendUniqueRefs(overlapOutcomes, job.OutcomeEventRefs)
		job.OutcomeEventRefs, remainderOutcomes, err = partitionConsolidationOutcomes(
			ctx, tx, owner, combined, candidateOutcomes, selectedEpisodes,
		)
		if err != nil {
			return scheduler.Job{}, false, err
		}
		hash := consolidationQueueWindowHash(owner, job.EpisodeRefs, job.OutcomeEventRefs)
		windowHash = hash[:]
	}
	job.LeaseToken, err = randomStableRef("lease")
	if err != nil {
		return scheduler.Job{}, false, err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE consolidation_jobs
		SET episode_refs = $2,
		    outcome_event_refs = $3,
		    window_hash = $4,
		    lease_token = $5,
		    lease_until = $6,
		    attempts = attempts + 1,
		    updated_at = CURRENT_TIMESTAMP(6)
		WHERE job_ref = $1
	`, job.Ref, job.EpisodeRefs, job.OutcomeEventRefs, windowHash, job.LeaseToken, request.LeaseUntil); err != nil {
		return scheduler.Job{}, false, storageError("freeze and lease consolidation Job", err)
	}
	if len(remainderEpisodes) > 0 {
		remainderRef, err := randomStableRef("consolidation-job")
		if err != nil {
			return scheduler.Job{}, false, err
		}
		if remainderOutcomes == nil {
			remainderOutcomes = []string{}
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO consolidation_jobs (
				job_ref, tenant_ref, scope_kind, agent_ref, relationship_ref,
				episode_refs, outcome_event_refs
			) VALUES ($1, $2, $3, $4, $5, $6, $7)
		`, remainderRef, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef,
			remainderEpisodes, remainderOutcomes); err != nil {
			return scheduler.Job{}, false, storageError("carry consolidation Job remainder", err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return scheduler.Job{}, false, storageError("commit consolidation Job lease", err)
	}
	return job, true, nil
}

// CompleteConsolidationJob acknowledges only the caller's live lease.
func (store *Store) CompleteConsolidationJob(ctx context.Context, jobRef, leaseToken string) error {
	tag, err := store.pool.Exec(ctx, `
		UPDATE consolidation_jobs
		SET completed_at = CURRENT_TIMESTAMP(6), lease_token = '', lease_until = NULL, updated_at = CURRENT_TIMESTAMP(6)
		WHERE job_ref = $1 AND lease_token = $2 AND completed_at IS NULL
	`, jobRef, leaseToken)
	if err != nil {
		return storageError("complete consolidation Job", err)
	}
	if tag.RowsAffected() != 1 {
		return scheduler.ErrLeaseLost
	}
	return nil
}

// RetryConsolidationJob releases the same frozen window after a failed call.
func (store *Store) RetryConsolidationJob(ctx context.Context, jobRef, leaseToken string, notBefore time.Time) error {
	tag, err := store.pool.Exec(ctx, `
		UPDATE consolidation_jobs
		SET not_before = $3, lease_token = '', lease_until = NULL, updated_at = CURRENT_TIMESTAMP(6)
		WHERE job_ref = $1 AND lease_token = $2 AND completed_at IS NULL
	`, jobRef, leaseToken, notBefore)
	if err != nil {
		return storageError("retry consolidation Job", err)
	}
	if tag.RowsAffected() != 1 {
		return scheduler.ErrLeaseLost
	}
	return nil
}

func prependPreviousOverlap(ctx context.Context, tx *transaction, owner ownerScope, current []string, limit int) ([]string, []string, error) {
	if limit <= 0 {
		return current, nil, nil
	}
	var previous, previousOutcomes []string
	err := tx.QueryRow(ctx, `
		SELECT episode_refs, outcome_event_refs
		FROM consolidation_jobs
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND completed_at IS NOT NULL
		ORDER BY completed_at DESC, created_at DESC
		LIMIT 1
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef).Scan(&previous, &previousOutcomes)
	if errors.Is(err, errNoRows) {
		return current, nil, nil
	}
	if err != nil {
		return nil, nil, storageError("load previous consolidation overlap", err)
	}
	previousWindow := append([]string(nil), previous...)
	if len(previous) > limit {
		previous = previous[len(previous)-limit:]
	}
	overlapOutcomes, _, err := partitionConsolidationOutcomes(
		ctx, tx, owner, previousWindow, previousOutcomes, refSet(previous),
	)
	if err != nil {
		return nil, nil, err
	}
	combined := make([]string, 0, len(previous)+len(current))
	seen := make(map[string]struct{}, len(previous)+len(current))
	for _, refs := range [][]string{previous, current} {
		for _, ref := range refs {
			if _, exists := seen[ref]; exists {
				continue
			}
			seen[ref] = struct{}{}
			combined = append(combined, ref)
		}
	}
	return combined, overlapOutcomes, nil
}

func partitionConsolidationOutcomes(
	ctx context.Context,
	tx *transaction,
	owner ownerScope,
	collectedEpisodes []string,
	outcomeRefs []string,
	selectedEpisodes map[string]struct{},
) ([]string, []string, error) {
	if len(outcomeRefs) == 0 {
		return []string{}, nil, nil
	}
	rows, err := tx.Query(ctx, `
		SELECT outcome_event_ref, episode_ref
		FROM outcome_events
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND JSON_CONTAINS($5, JSON_QUOTE(CONVERT(outcome_event_ref USING utf8mb4)))
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, outcomeRefs)
	if err != nil {
		return nil, nil, storageError("load consolidation Outcome partitions", err)
	}
	defer rows.Close()
	outcomeEpisodes := make(map[string]string, len(outcomeRefs))
	for rows.Next() {
		var outcomeRef, episodeRef string
		if err := rows.Scan(&outcomeRef, &episodeRef); err != nil {
			return nil, nil, storageError("scan consolidation Outcome partition", err)
		}
		outcomeEpisodes[outcomeRef] = episodeRef
	}
	if err := rows.Err(); err != nil {
		return nil, nil, storageError("iterate consolidation Outcome partitions", err)
	}

	collectedSet := refSet(collectedEpisodes)
	selectedOutcomes := make([]string, 0, len(outcomeRefs))
	remainderOutcomes := make([]string, 0, len(outcomeRefs))
	for _, outcomeRef := range outcomeRefs {
		episodeRef, exists := outcomeEpisodes[outcomeRef]
		if !exists {
			return nil, nil, fmt.Errorf("consolidation Outcome %s is outside its exact owner", outcomeRef)
		}
		if _, collected := collectedSet[episodeRef]; !collected {
			return nil, nil, fmt.Errorf("consolidation Outcome %s has no collected Episode", outcomeRef)
		}
		if _, selected := selectedEpisodes[episodeRef]; selected {
			selectedOutcomes = append(selectedOutcomes, outcomeRef)
		} else {
			remainderOutcomes = append(remainderOutcomes, outcomeRef)
		}
	}
	return selectedOutcomes, remainderOutcomes, nil
}

func refSet(refs []string) map[string]struct{} {
	set := make(map[string]struct{}, len(refs))
	for _, ref := range refs {
		set[ref] = struct{}{}
	}
	return set
}

func appendUniqueRefs(existing, added []string) ([]string, bool) {
	result := append([]string(nil), existing...)
	seen := make(map[string]struct{}, len(existing)+len(added))
	for _, ref := range existing {
		seen[ref] = struct{}{}
	}
	changed := false
	for _, ref := range added {
		if ref == "" {
			continue
		}
		if _, exists := seen[ref]; exists {
			continue
		}
		seen[ref] = struct{}{}
		result = append(result, ref)
		changed = true
	}
	return result, changed
}

func consolidationCollectionLockKey(owner ownerScope) int64 {
	hash := hashFields("consolidation-collection-lock.v1", owner.TenantRef,
		string(owner.Kind), owner.AgentRef, owner.RelationshipRef)
	return int64FromHash(hash)
}

func consolidationQueueWindowHash(owner ownerScope, episodes, outcomes []string) [32]byte {
	fields := []string{"consolidation-queue-window.v1", owner.TenantRef,
		string(owner.Kind), owner.AgentRef, owner.RelationshipRef}
	fields = append(fields, episodes...)
	fields = append(fields, "OUTCOMES")
	fields = append(fields, outcomes...)
	return hashFields(fields...)
}

func int64FromHash(hash [32]byte) int64 {
	return int64(uint64(hash[0])<<56 | uint64(hash[1])<<48 | uint64(hash[2])<<40 |
		uint64(hash[3])<<32 | uint64(hash[4])<<24 | uint64(hash[5])<<16 |
		uint64(hash[6])<<8 | uint64(hash[7]))
}

func randomStableRef(prefix string) (string, error) {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", fmt.Errorf("generate %s ref: %w", prefix, err)
	}
	return prefix + "-" + hex.EncodeToString(value[:]), nil
}
