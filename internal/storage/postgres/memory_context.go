package postgres

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"github.com/jackc/pgx/v5"
)

type scopedIndexCandidate struct {
	scope          ownerScope
	candidate      memoryindex.Candidate
	querySourceRef string
	rank           int
	laneOrder      int
}

// SelectMemory freezes one deterministic selection result for an exact
// run. Association scores are transient and never cross this boundary.
func (store *Store) SelectMemory(ctx context.Context, request selection.SelectRequest) (selection.MemoryContext, error) {
	if !validSelectRequest(request) {
		return selection.MemoryContext{}, selection.ErrInvalidSelectRequest
	}

	requestHash := memoryRequestHash(request)
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return selection.MemoryContext{}, storageError("begin MemoryContext assembly", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", memoryRunLockKey(request.Scope, request.RunRef)); err != nil {
		return selection.MemoryContext{}, storageError("lock MemoryContext run", err)
	}
	if contextValue, found, err := loadMemoryContext(ctx, tx, request, requestHash); err != nil {
		return selection.MemoryContext{}, err
	} else if found {
		if err := tx.Commit(ctx); err != nil {
			return selection.MemoryContext{}, storageError("commit frozen MemoryContext read", err)
		}
		return contextValue, nil
	}
	queries, err := loadSituationQueries(ctx, tx, request.Scope, request.SituationSourceRefs)
	if err != nil {
		return selection.MemoryContext{}, err
	}
	indexedRefs := store.searchMemoryIndex(ctx, request.Scope, queries)
	episodeEvidence, err := loadEpisodeEvidence(ctx, tx, request, indexedRefs)
	if err != nil {
		return selection.MemoryContext{}, err
	}

	// FORM commits under these same owner locks. Taking them in this fixed
	// order makes the candidate set stable until the Context is frozen, while
	// shared read locks allow unrelated runs to select concurrently.
	for _, owner := range selectionOwners(request.Scope) {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", consolidationOwnerLockKey(owner)); err != nil {
			return selection.MemoryContext{}, storageError("lock Memory owner", err)
		}
	}

	candidates, err := loadSelectionCandidates(ctx, tx, request.Scope, store.selectionPolicy.CanonicalCandidatesPerOwnerKind)
	if err != nil {
		return selection.MemoryContext{}, err
	}
	indexedCandidates, err := loadIndexedSelectionCandidates(ctx, tx, indexedRefs)
	if err != nil {
		return selection.MemoryContext{}, err
	}
	candidates = unionSelectionCandidates(candidates, indexedCandidates)
	activations, err := selection.Select(queries, candidates, store.selectionRanker, store.selectionPolicy)
	if err != nil {
		return selection.MemoryContext{}, fmt.Errorf("select MemoryContext: %w", err)
	}
	if err := validateSelectedMemoryRefs(request.Constitution, activations, episodeEvidence); err != nil {
		return selection.MemoryContext{}, err
	}

	contextRef := memoryContextRef(requestHash)
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_contexts (
			tenant_ref, context_ref, scope_kind, agent_ref, relationship_ref,
			session_ref, run_ref, request_hash, policy_ref,
			constitution_ref, constitution_text
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
	`,
		request.Scope.TenantRef,
		contextRef,
		request.Scope.Kind,
		request.Scope.AgentRef,
		request.Scope.RelationshipRef,
		request.Scope.SessionRef,
		request.RunRef,
		requestHash[:],
		store.selectionPolicy.Ref,
		request.Constitution.MemoryRef,
		request.Constitution.Text,
	); err != nil {
		return selection.MemoryContext{}, storageError("freeze MemoryContext", err)
	}
	for index, activation := range activations {
		if _, err := tx.Exec(ctx, `
			INSERT INTO memory_context_items (
				tenant_ref, context_ref, item_kind, memory_ref, item_order,
				query_source_ref, matched_memory_ref,
				evidence_episode_ref, evidence_source_ref
			) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
		`,
			request.Scope.TenantRef,
			contextRef,
			activation.Kind,
			activation.VersionRef,
			index,
			activation.QuerySourceRef,
			activation.MatchedRef,
			activation.EvidenceEpisodeRef,
			activation.EvidenceSourceRef,
		); err != nil {
			return selection.MemoryContext{}, storageError("freeze MemoryContext item", err)
		}
	}
	for index, item := range episodeEvidence {
		if _, err := tx.Exec(ctx, `
			INSERT INTO memory_context_episode_evidence (
				tenant_ref, context_ref, episode_ref, evidence_order,
				query_source_ref, evidence_text
			) VALUES ($1, $2, $3, $4, $5, $6)
		`, request.Scope.TenantRef, contextRef, item.MemoryRef, index, item.QuerySourceRef, item.Text); err != nil {
			return selection.MemoryContext{}, storageError("freeze MemoryContext Episode evidence", err)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return selection.MemoryContext{}, storageError("commit MemoryContext assembly", err)
	}
	return buildMemoryContext(contextRef, request, activations, candidates, episodeEvidence), nil
}

func validSelectRequest(request selection.SelectRequest) bool {
	if !validStableRef(request.RunRef) || len(request.SituationSourceRefs) == 0 || !uniqueStableRefs(request.SituationSourceRefs) {
		return false
	}
	if !selection.ValidEpisodeEvidenceMaxBytes(request.EpisodeEvidenceMaxBytes) {
		return false
	}
	if request.Scope.TenantRef == "" || request.Scope.AgentRef == "" {
		return false
	}
	switch request.Scope.Kind {
	case ledger.ScopeKindAgent:
		if request.Scope.RelationshipRef != "" {
			return false
		}
	case ledger.ScopeKindRelationship:
		if request.Scope.RelationshipRef == "" {
			return false
		}
	default:
		return false
	}
	hasConstitutionRef := request.Constitution.MemoryRef != ""
	hasConstitutionText := request.Constitution.Text != ""
	return hasConstitutionRef == hasConstitutionText && (!hasConstitutionRef || validStableRef(request.Constitution.MemoryRef))
}

func loadSituationQueries(ctx context.Context, tx pgx.Tx, scope ledger.Scope, refs []string) ([]selection.Query, error) {
	queries := make([]selection.Query, 0, len(refs))
	for _, ref := range refs {
		var text string
		err := tx.QueryRow(ctx, `
			SELECT source_text
			FROM source_events
			WHERE tenant_ref = $1
			  AND scope_kind = $2
			  AND agent_ref = $3
			  AND relationship_ref = $4
			  AND session_ref = $5
			  AND source_ref = $6
		`, scope.TenantRef, scope.Kind, scope.AgentRef, scope.RelationshipRef, scope.SessionRef, ref).Scan(&text)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, fmt.Errorf("%w: unknown situation source %s", selection.ErrInvalidSelectRequest, ref)
		}
		if err != nil {
			return nil, storageError("load MemoryContext Situation", err)
		}
		queries = append(queries, selection.Query{Ref: ref, Text: text})
	}
	return queries, nil
}

func loadSelectionCandidates(ctx context.Context, tx pgx.Tx, scope ledger.Scope, perOwnerKindLimit int) ([]selection.Candidate, error) {
	recollections, err := loadRecollectionCandidates(ctx, tx, scope, perOwnerKindLimit, nil)
	if err != nil {
		return nil, err
	}
	dispositions, err := loadDispositionCandidates(ctx, tx, scope, perOwnerKindLimit, nil)
	if err != nil {
		return nil, err
	}
	return append(recollections, dispositions...), nil
}

func loadRecollectionCandidates(ctx context.Context, tx pgx.Tx, scope ledger.Scope, limit int, refs []string) ([]selection.Candidate, error) {
	rows, err := tx.Query(ctx, `
		WITH ranked_versions AS (
			SELECT
				version.recollection_version_ref,
				version.text,
				version.application_scope,
				recollection.scope_kind,
				recollection.agent_ref,
				recollection.relationship_ref,
				CASE WHEN recollection.scope_kind = 'relationship' THEN 0 ELSE 1 END AS owner_order,
				row_number() OVER (
					PARTITION BY recollection.scope_kind, recollection.relationship_ref
					ORDER BY version.recollection_version_ref
				) AS owner_rank
			FROM recollections AS recollection
			JOIN recollection_versions AS version
			  ON version.tenant_ref = recollection.tenant_ref
			 AND version.recollection_ref = recollection.recollection_ref
			WHERE recollection.tenant_ref = $1
			  AND recollection.agent_ref = $2
			  AND version.status = 'active'
			  AND ($6::text[] IS NULL OR version.recollection_version_ref = ANY($6::text[]))
			  AND (
				(recollection.scope_kind = 'agent' AND recollection.relationship_ref = '')
				OR (
					$3 = 'relationship'
					AND recollection.scope_kind = 'relationship'
					AND recollection.relationship_ref = $4
				)
			  )
		),
		bounded_versions AS (
			SELECT * FROM ranked_versions WHERE owner_rank <= $5
		)
		SELECT
			bounded.recollection_version_ref,
			bounded.text,
			bounded.application_scope,
			bounded.owner_order,
			episode.episode_ref,
			source.source_ref,
			source.source_text
		FROM bounded_versions AS bounded
		JOIN recollection_basis_links AS basis
		  ON basis.tenant_ref = $1
		 AND basis.recollection_version_ref = bounded.recollection_version_ref
		JOIN episodes AS episode
		  ON episode.tenant_ref = basis.tenant_ref
		 AND episode.episode_ref = basis.episode_ref
		 AND episode.scope_kind = bounded.scope_kind
		 AND episode.agent_ref = bounded.agent_ref
		 AND episode.relationship_ref = bounded.relationship_ref
		JOIN episode_links AS link
		  ON link.tenant_ref = episode.tenant_ref
		 AND link.scope_kind = episode.scope_kind
		 AND link.agent_ref = episode.agent_ref
		 AND link.relationship_ref = episode.relationship_ref
		 AND link.session_ref = episode.session_ref
		 AND link.run_ref = episode.run_ref
		 AND link.source_group_ref = episode.source_group_ref
		 AND link.episode_ref = episode.episode_ref
		 AND link.role = 'situation'
		JOIN source_events AS source
		  ON source.tenant_ref = link.tenant_ref
		 AND source.scope_kind = link.scope_kind
		 AND source.agent_ref = link.agent_ref
		 AND source.relationship_ref = link.relationship_ref
		 AND source.session_ref = link.session_ref
		 AND source.source_ref = link.source_event_ref
		ORDER BY bounded.owner_order, bounded.recollection_version_ref, episode.episode_ref, source.source_ref
	`, scope.TenantRef, scope.AgentRef, scope.Kind, scope.RelationshipRef, limit, refs)
	if err != nil {
		return nil, storageError("load Recollection candidates", err)
	}
	defer rows.Close()

	byVersion := make(map[string]int)
	candidates := make([]selection.Candidate, 0)
	for rows.Next() {
		var versionRef, text, application, episodeRef, sourceRef, sourceText string
		var ownerOrder int
		if err := rows.Scan(&versionRef, &text, &application, &ownerOrder, &episodeRef, &sourceRef, &sourceText); err != nil {
			return nil, storageError("scan Recollection candidate", err)
		}
		index, exists := byVersion[versionRef]
		if !exists {
			index = len(candidates)
			byVersion[versionRef] = index
			candidates = append(candidates, selection.Candidate{
				Kind: selection.CandidateRecollection, VersionRef: versionRef, Text: text,
				Scope: selection.ApplicationScope(application), OwnerOrder: ownerOrder,
			})
		}
		candidates[index].Support = append(candidates[index].Support, selection.Anchor{
			EpisodeRef: episodeRef, SourceRef: sourceRef, Text: sourceText,
		})
	}
	if err := rows.Err(); err != nil {
		return nil, storageError("iterate Recollection candidates", err)
	}
	return candidates, nil
}

func loadDispositionCandidates(ctx context.Context, tx pgx.Tx, scope ledger.Scope, limit int, refs []string) ([]selection.Candidate, error) {
	rows, err := tx.Query(ctx, `
		WITH ranked_versions AS (
			SELECT
				version.seed_version_ref,
				version.tendency_text,
				seed.scope_kind,
				seed.agent_ref,
				seed.relationship_ref,
				CASE WHEN seed.scope_kind = 'relationship' THEN 0 ELSE 1 END AS owner_order,
				row_number() OVER (
					PARTITION BY seed.scope_kind, seed.relationship_ref
					ORDER BY version.seed_version_ref
				) AS owner_rank
			FROM disposition_seeds AS seed
			JOIN seed_versions AS version
			  ON version.tenant_ref = seed.tenant_ref
			 AND version.seed_ref = seed.seed_ref
			WHERE seed.tenant_ref = $1
			  AND seed.agent_ref = $2
			  AND version.status = 'active'
			  AND ($6::text[] IS NULL OR version.seed_version_ref = ANY($6::text[]))
			  AND (
				(seed.scope_kind = 'agent' AND seed.relationship_ref = '')
				OR (
					$3 = 'relationship'
					AND seed.scope_kind = 'relationship'
					AND seed.relationship_ref = $4
				)
			  )
		),
		bounded_versions AS (
			SELECT * FROM ranked_versions WHERE owner_rank <= $5
		)
		SELECT
			bounded.seed_version_ref,
			bounded.tendency_text,
			bounded.scope_kind,
			bounded.owner_order,
			basis.role,
			episode.episode_ref,
			source.source_ref,
			source.source_text
		FROM bounded_versions AS bounded
		JOIN seed_basis_links AS basis
		  ON basis.tenant_ref = $1
		 AND basis.seed_version_ref = bounded.seed_version_ref
		JOIN episodes AS episode
		  ON episode.tenant_ref = basis.tenant_ref
		 AND episode.episode_ref = basis.episode_ref
		 AND episode.scope_kind = bounded.scope_kind
		 AND episode.agent_ref = bounded.agent_ref
		 AND episode.relationship_ref = bounded.relationship_ref
		JOIN episode_links AS link
		  ON link.tenant_ref = episode.tenant_ref
		 AND link.scope_kind = episode.scope_kind
		 AND link.agent_ref = episode.agent_ref
		 AND link.relationship_ref = episode.relationship_ref
		 AND link.session_ref = episode.session_ref
		 AND link.run_ref = episode.run_ref
		 AND link.source_group_ref = episode.source_group_ref
		 AND link.episode_ref = episode.episode_ref
		 AND link.role = 'situation'
		JOIN source_events AS source
		  ON source.tenant_ref = link.tenant_ref
		 AND source.scope_kind = link.scope_kind
		 AND source.agent_ref = link.agent_ref
		 AND source.relationship_ref = link.relationship_ref
		 AND source.session_ref = link.session_ref
		 AND source.source_ref = link.source_event_ref
		ORDER BY bounded.owner_order, bounded.seed_version_ref, basis.role, episode.episode_ref, source.source_ref
	`, scope.TenantRef, scope.AgentRef, scope.Kind, scope.RelationshipRef, limit, refs)
	if err != nil {
		return nil, storageError("load Disposition candidates", err)
	}
	defer rows.Close()

	byVersion := make(map[string]int)
	reenactedEpisodes := make(map[string]map[string]struct{})
	candidates := make([]selection.Candidate, 0)
	for rows.Next() {
		var versionRef, text, basisRole, episodeRef, sourceRef, sourceText string
		var scopeKind ledger.ScopeKind
		var ownerOrder int
		if err := rows.Scan(&versionRef, &text, &scopeKind, &ownerOrder, &basisRole, &episodeRef, &sourceRef, &sourceText); err != nil {
			return nil, storageError("scan Disposition candidate", err)
		}
		index, exists := byVersion[versionRef]
		if !exists {
			index = len(candidates)
			byVersion[versionRef] = index
			candidates = append(candidates, selection.Candidate{
				Kind: selection.CandidateDisposition, VersionRef: versionRef, Text: text,
				Scope: dispositionApplicationScope(scopeKind), OwnerOrder: ownerOrder,
			})
		}
		anchor := selection.Anchor{EpisodeRef: episodeRef, SourceRef: sourceRef, Text: sourceText}
		switch basisRole {
		case "formation", "revision", "reenactment":
			candidates[index].Support = append(candidates[index].Support, anchor)
			if basisRole == "reenactment" {
				seen := reenactedEpisodes[versionRef]
				if seen == nil {
					seen = make(map[string]struct{})
					reenactedEpisodes[versionRef] = seen
				}
				if _, duplicate := seen[episodeRef]; !duplicate {
					seen[episodeRef] = struct{}{}
					candidates[index].Reenactments++
				}
			}
		case "inhibition":
			candidates[index].Inhibition = append(candidates[index].Inhibition, anchor)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, storageError("iterate Disposition candidates", err)
	}
	return candidates, nil
}

func (store *Store) searchMemoryIndex(ctx context.Context, scope ledger.Scope, queries []selection.Query) []scopedIndexCandidate {
	if store.memoryIndex == nil {
		return nil
	}
	result := make([]scopedIndexCandidate, 0)
	seen := make(map[string]struct{})
	laneOrder := 0
	for _, owner := range selectionOwners(scope) {
		indexScope := memoryindex.Scope{
			TenantRef: owner.TenantRef, AgentRef: owner.AgentRef, RelationshipRef: owner.RelationshipRef,
		}
		for _, query := range queries {
			limit := store.selectionPolicy.CanonicalCandidatesPerOwnerKind
			candidates, _ := store.memoryIndex.Search(ctx, memoryindex.Query{
				Scope: indexScope, Text: query.Text,
				Limit: limit,
			})
			if len(candidates) > limit {
				candidates = candidates[:limit]
			}
			for rank, candidate := range candidates {
				if candidate.Ref == "" || (candidate.Kind != memoryindex.KindEpisode &&
					candidate.Kind != memoryindex.KindRecollection && candidate.Kind != memoryindex.KindDisposition) {
					continue
				}
				key := string(owner.Kind) + "\x00" + owner.TenantRef + "\x00" + owner.AgentRef + "\x00" +
					owner.RelationshipRef + "\x00" + string(candidate.Kind) + "\x00" + candidate.Ref
				key += "\x00" + query.Ref
				if _, duplicate := seen[key]; duplicate {
					continue
				}
				seen[key] = struct{}{}
				result = append(result, scopedIndexCandidate{
					scope: owner, candidate: candidate, querySourceRef: query.Ref, rank: rank, laneOrder: laneOrder,
				})
			}
			laneOrder++
		}
	}
	return result
}

func loadIndexedSelectionCandidates(ctx context.Context, tx pgx.Tx, indexed []scopedIndexCandidate) ([]selection.Candidate, error) {
	type ownerRefs struct {
		owner         ownerScope
		episodes      []string
		recollections []string
		dispositions  []string
	}
	groups := make(map[string]*ownerRefs)
	order := make([]string, 0)
	for _, item := range indexed {
		key := string(item.scope.Kind) + "\x00" + item.scope.TenantRef + "\x00" + item.scope.AgentRef + "\x00" + item.scope.RelationshipRef
		group := groups[key]
		if group == nil {
			group = &ownerRefs{owner: item.scope}
			groups[key] = group
			order = append(order, key)
		}
		switch item.candidate.Kind {
		case memoryindex.KindEpisode:
			group.episodes = append(group.episodes, item.candidate.Ref)
		case memoryindex.KindRecollection:
			group.recollections = append(group.recollections, item.candidate.Ref)
		case memoryindex.KindDisposition:
			group.dispositions = append(group.dispositions, item.candidate.Ref)
		}
	}

	result := make([]selection.Candidate, 0, len(indexed))
	for _, key := range order {
		group := groups[key]
		if len(group.episodes) > 0 {
			recollections, dispositions, err := loadEpisodeLinkedMemoryRefs(ctx, tx, group.owner, group.episodes)
			if err != nil {
				return nil, err
			}
			group.recollections = append(group.recollections, recollections...)
			group.dispositions = append(group.dispositions, dispositions...)
		}
		scope := ledger.Scope{
			Kind: group.owner.Kind, TenantRef: group.owner.TenantRef,
			AgentRef: group.owner.AgentRef, RelationshipRef: group.owner.RelationshipRef,
		}
		if refs := uniqueRefs(group.recollections); len(refs) > 0 {
			candidates, err := loadRecollectionCandidates(ctx, tx, scope, len(refs), refs)
			if err != nil {
				return nil, err
			}
			candidates = attachDirectIndexMatches(candidates, indexed, group.owner)
			result = append(result, candidates...)
		}
		if refs := uniqueRefs(group.dispositions); len(refs) > 0 {
			candidates, err := loadDispositionCandidates(ctx, tx, scope, len(refs), refs)
			if err != nil {
				return nil, err
			}
			candidates = attachDirectIndexMatches(candidates, indexed, group.owner)
			candidates = attachEpisodeSupportIndexMatches(candidates, indexed, group.owner)
			result = append(result, candidates...)
		}
	}
	return result, nil
}

func attachDirectIndexMatches(candidates []selection.Candidate, indexed []scopedIndexCandidate, owner ownerScope) []selection.Candidate {
	matches := make(map[string][]selection.IndexMatch)
	for _, item := range indexed {
		if item.scope != owner || item.candidate.Ref == "" ||
			(item.candidate.Kind != memoryindex.KindRecollection && item.candidate.Kind != memoryindex.KindDisposition) {
			continue
		}
		key := string(item.candidate.Kind) + "\x00" + item.candidate.Ref
		matches[key] = append(matches[key], selection.IndexMatch{
			QuerySourceRef: item.querySourceRef,
			Rank:           item.rank,
			LaneOrder:      item.laneOrder,
		})
	}
	for index := range candidates {
		var kind memoryindex.Kind
		switch candidates[index].Kind {
		case selection.CandidateRecollection:
			kind = memoryindex.KindRecollection
		case selection.CandidateDisposition:
			kind = memoryindex.KindDisposition
		default:
			continue
		}
		key := string(kind) + "\x00" + candidates[index].VersionRef
		candidates[index].IndexMatches = append(candidates[index].IndexMatches, matches[key]...)
	}
	return candidates
}

func attachEpisodeSupportIndexMatches(candidates []selection.Candidate, indexed []scopedIndexCandidate, owner ownerScope) []selection.Candidate {
	matches := make(map[string][]selection.IndexMatch)
	for _, item := range indexed {
		if item.scope != owner || item.candidate.Kind != memoryindex.KindEpisode || item.candidate.Ref == "" {
			continue
		}
		matches[item.candidate.Ref] = append(matches[item.candidate.Ref], selection.IndexMatch{
			QuerySourceRef: item.querySourceRef, SupportEpisodeRef: item.candidate.Ref,
			Rank: item.rank, LaneOrder: item.laneOrder,
		})
	}
	for index := range candidates {
		if candidates[index].Kind != selection.CandidateDisposition {
			continue
		}
		seen := make(map[string]struct{})
		for _, anchor := range candidates[index].Support {
			if _, duplicate := seen[anchor.EpisodeRef]; duplicate {
				continue
			}
			seen[anchor.EpisodeRef] = struct{}{}
			candidates[index].IndexMatches = append(candidates[index].IndexMatches, matches[anchor.EpisodeRef]...)
		}
	}
	return candidates
}

func loadEpisodeLinkedMemoryRefs(ctx context.Context, tx pgx.Tx, owner ownerScope, episodeRefs []string) ([]string, []string, error) {
	recollectionRows, err := tx.Query(ctx, `
		SELECT DISTINCT basis.recollection_version_ref
		FROM episodes AS episode
		JOIN recollection_basis_links AS basis
		  ON basis.tenant_ref = episode.tenant_ref
		 AND basis.episode_ref = episode.episode_ref
		WHERE episode.tenant_ref = $1
		  AND episode.scope_kind = $2
		  AND episode.agent_ref = $3
		  AND episode.relationship_ref = $4
		  AND episode.episode_ref = ANY($5::text[])
		ORDER BY basis.recollection_version_ref
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, episodeRefs)
	if err != nil {
		return nil, nil, storageError("load indexed Episode Recollection links", err)
	}
	recollections, err := scanRefs(recollectionRows)
	if err != nil {
		return nil, nil, storageError("scan indexed Episode Recollection links", err)
	}

	dispositionRows, err := tx.Query(ctx, `
		SELECT DISTINCT basis.seed_version_ref
		FROM episodes AS episode
		JOIN seed_basis_links AS basis
		  ON basis.tenant_ref = episode.tenant_ref
		 AND basis.episode_ref = episode.episode_ref
		WHERE episode.tenant_ref = $1
		  AND episode.scope_kind = $2
		  AND episode.agent_ref = $3
		  AND episode.relationship_ref = $4
		  AND episode.episode_ref = ANY($5::text[])
		ORDER BY basis.seed_version_ref
	`, owner.TenantRef, owner.Kind, owner.AgentRef, owner.RelationshipRef, episodeRefs)
	if err != nil {
		return nil, nil, storageError("load indexed Episode Disposition links", err)
	}
	dispositions, err := scanRefs(dispositionRows)
	if err != nil {
		return nil, nil, storageError("scan indexed Episode Disposition links", err)
	}
	return recollections, dispositions, nil
}

func scanRefs(rows pgx.Rows) ([]string, error) {
	defer rows.Close()
	refs := make([]string, 0)
	for rows.Next() {
		var ref string
		if err := rows.Scan(&ref); err != nil {
			return nil, err
		}
		refs = append(refs, ref)
	}
	return refs, rows.Err()
}

func uniqueRefs(refs []string) []string {
	result := make([]string, 0, len(refs))
	seen := make(map[string]struct{}, len(refs))
	for _, ref := range refs {
		if ref == "" {
			continue
		}
		if _, duplicate := seen[ref]; duplicate {
			continue
		}
		seen[ref] = struct{}{}
		result = append(result, ref)
	}
	return result
}

func unionSelectionCandidates(canonical, indexed []selection.Candidate) []selection.Candidate {
	result := make([]selection.Candidate, 0, len(canonical)+len(indexed))
	seen := make(map[string]int, len(canonical)+len(indexed))
	for _, candidates := range [][]selection.Candidate{canonical, indexed} {
		for _, candidate := range candidates {
			key := string(candidate.Kind) + "\x00" + candidate.VersionRef
			if index, duplicate := seen[key]; duplicate {
				result[index].IndexMatches = append(result[index].IndexMatches, candidate.IndexMatches...)
				continue
			}
			seen[key] = len(result)
			result = append(result, candidate)
		}
	}
	return result
}

func loadMemoryContext(ctx context.Context, tx pgx.Tx, request selection.SelectRequest, requestHash [sha256.Size]byte) (selection.MemoryContext, bool, error) {
	var contextRef, constitutionRef, constitutionText string
	var storedHash []byte
	err := tx.QueryRow(ctx, `
		SELECT context_ref, request_hash, constitution_ref, constitution_text
		FROM memory_contexts
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND run_ref = $6
	`,
		request.Scope.TenantRef,
		request.Scope.Kind,
		request.Scope.AgentRef,
		request.Scope.RelationshipRef,
		request.Scope.SessionRef,
		request.RunRef,
	).Scan(&contextRef, &storedHash, &constitutionRef, &constitutionText)
	if errors.Is(err, pgx.ErrNoRows) {
		return selection.MemoryContext{}, false, nil
	}
	if err != nil {
		return selection.MemoryContext{}, false, storageError("load frozen MemoryContext", err)
	}
	if !bytes.Equal(storedHash, requestHash[:]) {
		return selection.MemoryContext{}, false, fmt.Errorf("%w: %s", selection.ErrRunConflict, request.RunRef)
	}

	contextValue := selection.MemoryContext{Ref: contextRef, RunRef: request.RunRef, Scope: request.Scope}
	semanticKinds := make(map[string]string)
	if constitutionRef != "" {
		contextValue.Constitution = selection.Constitution{MemoryRef: constitutionRef, Text: constitutionText}
		semanticKinds[constitutionRef] = "constitution"
	}
	rows, err := tx.Query(ctx, `
		SELECT
			item.item_kind,
			item.memory_ref,
			COALESCE(recollection_version.text, disposition_version.tendency_text),
			COALESCE(
				recollection_version.application_scope,
				CASE disposition.scope_kind WHEN 'agent' THEN 'self' ELSE 'relation' END
			)
		FROM memory_context_items AS item
		LEFT JOIN recollection_versions AS recollection_version
		  ON item.item_kind = 'recollection'
		 AND recollection_version.tenant_ref = item.tenant_ref
		 AND recollection_version.recollection_version_ref = item.memory_ref
		LEFT JOIN seed_versions AS disposition_version
		  ON item.item_kind = 'disposition'
		 AND disposition_version.tenant_ref = item.tenant_ref
		 AND disposition_version.seed_version_ref = item.memory_ref
		LEFT JOIN disposition_seeds AS disposition
		  ON disposition.tenant_ref = disposition_version.tenant_ref
		 AND disposition.seed_ref = disposition_version.seed_ref
		WHERE item.tenant_ref = $1
		  AND item.context_ref = $2
		ORDER BY item.item_order
	`, request.Scope.TenantRef, contextRef)
	if err != nil {
		return selection.MemoryContext{}, false, storageError("load frozen MemoryContext items", err)
	}
	defer rows.Close()
	for rows.Next() {
		var kind selection.CandidateKind
		var memoryRef, text, application string
		if err := rows.Scan(&kind, &memoryRef, &text, &application); err != nil {
			return selection.MemoryContext{}, false, storageError("scan frozen MemoryContext item", err)
		}
		if err := claimSemanticMemoryRef(semanticKinds, memoryRef, string(kind)); err != nil {
			return selection.MemoryContext{}, false, err
		}
		switch kind {
		case selection.CandidateRecollection:
			contextValue.Recollections = append(contextValue.Recollections, selection.Recollection{
				MemoryRef: memoryRef, Text: text, Application: selection.ApplicationScope(application),
			})
		case selection.CandidateDisposition:
			contextValue.Dispositions = append(contextValue.Dispositions, selection.Disposition{
				MemoryRef: memoryRef, Text: text, Application: selection.ApplicationScope(application),
			})
		default:
			return selection.MemoryContext{}, false, storageError("load frozen MemoryContext item kind", errors.New("unknown memory kind"))
		}
	}
	if err := rows.Err(); err != nil {
		return selection.MemoryContext{}, false, storageError("iterate frozen MemoryContext items", err)
	}
	rows.Close()
	evidenceRows, err := tx.Query(ctx, `
		SELECT episode_ref, evidence_text
		FROM memory_context_episode_evidence
		WHERE tenant_ref = $1 AND context_ref = $2
		ORDER BY evidence_order
	`, request.Scope.TenantRef, contextRef)
	if err != nil {
		return selection.MemoryContext{}, false, storageError("load frozen MemoryContext Episode evidence", err)
	}
	defer evidenceRows.Close()
	for evidenceRows.Next() {
		var item selection.EpisodeEvidence
		if err := evidenceRows.Scan(&item.MemoryRef, &item.Text); err != nil {
			return selection.MemoryContext{}, false, storageError("scan frozen MemoryContext Episode evidence", err)
		}
		if err := claimSemanticMemoryRef(semanticKinds, item.MemoryRef, "episode_evidence"); err != nil {
			return selection.MemoryContext{}, false, err
		}
		contextValue.EpisodeEvidence = append(contextValue.EpisodeEvidence, item)
	}
	if err := evidenceRows.Err(); err != nil {
		return selection.MemoryContext{}, false, storageError("iterate frozen MemoryContext Episode evidence", err)
	}
	return contextValue, true, nil
}

func validateSelectedMemoryRefs(constitution selection.Constitution, activations []selection.Activation, evidence []frozenEpisodeEvidence) error {
	semanticKinds := make(map[string]string, len(activations)+len(evidence)+1)
	if constitution.MemoryRef != "" {
		semanticKinds[constitution.MemoryRef] = "constitution"
	}
	for _, activation := range activations {
		if err := claimSemanticMemoryRef(semanticKinds, activation.VersionRef, string(activation.Kind)); err != nil {
			return err
		}
	}
	for _, item := range evidence {
		if err := claimSemanticMemoryRef(semanticKinds, item.MemoryRef, "episode_evidence"); err != nil {
			return err
		}
	}
	return nil
}

func claimSemanticMemoryRef(kinds map[string]string, ref, kind string) error {
	if previous, exists := kinds[ref]; exists && previous != kind {
		return fmt.Errorf("%w: %s is both %s and %s", selection.ErrAmbiguousMemoryRef, ref, previous, kind)
	}
	kinds[ref] = kind
	return nil
}

func buildMemoryContext(contextRef string, request selection.SelectRequest, activations []selection.Activation, candidates []selection.Candidate, evidence []frozenEpisodeEvidence) selection.MemoryContext {
	contextValue := selection.MemoryContext{
		Ref: contextRef, RunRef: request.RunRef, Scope: request.Scope, Constitution: request.Constitution,
	}
	texts := make(map[selection.CandidateKind]map[string]string, 2)
	for _, candidate := range candidates {
		if texts[candidate.Kind] == nil {
			texts[candidate.Kind] = make(map[string]string)
		}
		texts[candidate.Kind][candidate.VersionRef] = candidate.Text
	}
	for _, activation := range activations {
		switch activation.Kind {
		case selection.CandidateRecollection:
			contextValue.Recollections = append(contextValue.Recollections, selection.Recollection{
				MemoryRef: activation.VersionRef, Text: texts[activation.Kind][activation.VersionRef], Application: activation.Scope,
			})
		case selection.CandidateDisposition:
			contextValue.Dispositions = append(contextValue.Dispositions, selection.Disposition{
				MemoryRef: activation.VersionRef, Text: texts[activation.Kind][activation.VersionRef], Application: activation.Scope,
			})
		}
	}
	for _, item := range evidence {
		contextValue.EpisodeEvidence = append(contextValue.EpisodeEvidence, selection.EpisodeEvidence{MemoryRef: item.MemoryRef, Text: item.Text})
	}
	return contextValue
}

func dispositionApplicationScope(kind ledger.ScopeKind) selection.ApplicationScope {
	if kind == ledger.ScopeKindAgent {
		return selection.ApplicationScopeSelf
	}
	return selection.ApplicationScopeRelation
}

func selectionOwners(scope ledger.Scope) []ownerScope {
	owners := []ownerScope{{
		Kind: ledger.ScopeKindAgent, TenantRef: scope.TenantRef, AgentRef: scope.AgentRef,
	}}
	if scope.Kind == ledger.ScopeKindRelationship {
		owners = append(owners, ownerScope{
			Kind:            ledger.ScopeKindRelationship,
			TenantRef:       scope.TenantRef,
			AgentRef:        scope.AgentRef,
			RelationshipRef: scope.RelationshipRef,
		})
	}
	sortOwnerScopes(owners)
	return owners
}

func memoryRequestHash(request selection.SelectRequest) [sha256.Size]byte {
	fields := []string{
		"memory-context-request.v2",
		string(request.Scope.Kind),
		request.Scope.TenantRef,
		request.Scope.AgentRef,
		request.Scope.RelationshipRef,
		request.Scope.SessionRef,
		request.RunRef,
		request.Constitution.MemoryRef,
		request.Constitution.Text,
	}
	fields = append(fields, request.SituationSourceRefs...)
	if request.EpisodeEvidenceMaxBytes == 0 {
		return hashFields(fields...)
	}
	evidenceFields := []string{
		"memory-context-request.episode-evidence.v1",
		strconv.Itoa(request.EpisodeEvidenceMaxBytes),
	}
	evidenceFields = append(evidenceFields, fields...)
	return hashFields(evidenceFields...)
}

func memoryContextRef(requestHash [sha256.Size]byte) string {
	return "context_" + hex.EncodeToString(requestHash[:16])
}

func memoryRunLockKey(scope ledger.Scope, runRef string) int64 {
	hash := hashFields(
		"memory-context-run-lock.v2",
		string(scope.Kind),
		scope.TenantRef,
		scope.AgentRef,
		scope.RelationshipRef,
		scope.SessionRef,
		runRef,
	)
	return int64(binary.BigEndian.Uint64(hash[:8]))
}
