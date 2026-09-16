package mysql

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

func enqueueMemoryIndexOperation(
	ctx context.Context,
	tx *transaction,
	action memoryindex.Action,
	kind memoryindex.Kind,
	documentRef string,
	owner ownerScope,
	streamIdentity string,
	causeFields ...string,
) error {
	if documentRef == "" || streamIdentity == "" || len(causeFields) == 0 {
		return errors.New("MemoryIndex operation identity is incomplete")
	}
	for _, field := range causeFields {
		if field == "" {
			return errors.New("MemoryIndex operation cause identity is incomplete")
		}
	}
	streamHash := hashFields("memory-index-stream.v1", owner.TenantRef, string(kind), streamIdentity)
	streamRef := "memory_index_stream_" + hex.EncodeToString(streamHash[:16])
	operationIdentity := []string{
		"memory-index-operation.v1", streamRef, string(action), string(kind), documentRef,
	}
	operationIdentity = append(operationIdentity, causeFields...)
	operationHash := hashFields(operationIdentity...)
	operationID := "memory_index_operation_" + hex.EncodeToString(operationHash[:16])

	if err := tx.acquireLock(ctx, projectionStreamLockKey(streamRef)); err != nil {
		return storageError("lock MemoryIndex operation stream", err)
	}
	var exists bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (SELECT 1 FROM memory_index_operations WHERE operation_id = $1)
	`, operationID).Scan(&exists); err != nil {
		return storageError("check MemoryIndex operation replay", err)
	}
	if exists {
		return nil
	}
	var sequence int64
	if err := tx.QueryRow(ctx, `
		SELECT COALESCE(max(stream_sequence), 0) + 1
		FROM memory_index_operations
		WHERE stream_ref = $1
	`, streamRef).Scan(&sequence); err != nil {
		return storageError("allocate MemoryIndex operation sequence", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_index_operations (
			operation_id, stream_ref, stream_sequence, operation_kind,
			document_kind, document_ref, tenant_ref, agent_ref, relationship_ref
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
	`, operationID, streamRef, sequence, action, kind, documentRef,
		owner.TenantRef, owner.AgentRef, owner.RelationshipRef); err != nil {
		return storageError("enqueue MemoryIndex operation", err)
	}
	return nil
}

func enqueueEpisodeProjection(
	ctx context.Context, tx *transaction, scope ledger.Scope,
	episodeRef string, role ledger.SourceRole, sourceRef string,
) error {
	owner := ownerScope{
		Kind: scope.Kind, TenantRef: scope.TenantRef, AgentRef: scope.AgentRef,
		RelationshipRef: scope.RelationshipRef,
	}
	return enqueueMemoryIndexOperation(
		ctx, tx, memoryindex.ActionUpsert, memoryindex.KindEpisode,
		episodeRef, owner, episodeRef, "typed-link", string(role), sourceRef,
	)
}

func enqueueMemoryFormation(
	ctx context.Context, tx *transaction, kind memoryindex.Kind, versionRef string, owner ownerScope,
) error {
	rootRef := versionRootRef(versionRef)
	return enqueueMemoryIndexOperation(
		ctx, tx, memoryindex.ActionUpsert, kind, versionRef, owner, rootRef, versionRef,
	)
}

func enqueueMemoryRevision(
	ctx context.Context,
	tx *transaction,
	kind memoryindex.Kind,
	rootRef, oldVersionRef, newVersionRef string,
	owner ownerScope,
) error {
	if err := enqueueMemoryIndexOperation(
		ctx, tx, memoryindex.ActionDelete, kind, oldVersionRef, owner, rootRef, newVersionRef,
	); err != nil {
		return err
	}
	return enqueueMemoryIndexOperation(
		ctx, tx, memoryindex.ActionUpsert, kind, newVersionRef, owner, rootRef, newVersionRef,
	)
}

func versionRootRef(versionRef string) string {
	if separator := strings.LastIndex(versionRef, "@"); separator > 0 {
		return versionRef[:separator]
	}
	return versionRef
}

func projectionStreamLockKey(streamRef string) int64 {
	hash := hashFields("memory-index-stream-lock.v1", streamRef)
	return int64FromHash(hash)
}

func (store *Store) LeaseMemoryIndexOperation(ctx context.Context, leaseUntil time.Time) (memoryindex.Operation, bool, error) {
	if leaseUntil.IsZero() {
		return memoryindex.Operation{}, false, errors.New("MemoryIndex lease deadline is required")
	}
	tx, err := store.pool.BeginTx(ctx, txOptions{})
	if err != nil {
		return memoryindex.Operation{}, false, storageError("begin MemoryIndex operation lease", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var operation memoryindex.Operation
	err = tx.QueryRow(ctx, `
		SELECT operation_id, stream_ref, stream_sequence, operation_kind,
		       document_kind, document_ref, tenant_ref, agent_ref, relationship_ref
		FROM memory_index_operations AS operation
		WHERE operation.acknowledged_at IS NULL
		  AND operation.not_before <= CURRENT_TIMESTAMP(6)
		  AND (operation.lease_until IS NULL OR operation.lease_until <= CURRENT_TIMESTAMP(6))
		  AND NOT EXISTS (
			SELECT 1 FROM memory_index_operations AS earlier
			WHERE earlier.stream_ref = operation.stream_ref
			  AND earlier.stream_sequence < operation.stream_sequence
			  AND earlier.acknowledged_at IS NULL
		  )
		ORDER BY operation.created_at, operation.operation_id
		LIMIT 1
		FOR UPDATE SKIP LOCKED
	`).Scan(
		&operation.ID, &operation.StreamRef, &operation.Sequence, &operation.Action,
		&operation.Kind, &operation.Ref, &operation.Scope.TenantRef,
		&operation.Scope.AgentRef, &operation.Scope.RelationshipRef,
	)
	if errors.Is(err, errNoRows) {
		if err := tx.Commit(ctx); err != nil {
			return memoryindex.Operation{}, false, storageError("commit empty MemoryIndex lease", err)
		}
		return memoryindex.Operation{}, false, nil
	}
	if err != nil {
		return memoryindex.Operation{}, false, storageError("lease MemoryIndex operation", err)
	}
	leaseBytes := make([]byte, 16)
	if _, err := rand.Read(leaseBytes); err != nil {
		return memoryindex.Operation{}, false, fmt.Errorf("generate MemoryIndex lease token: %w", err)
	}
	operation.LeaseToken = hex.EncodeToString(leaseBytes)
	if _, err := tx.Exec(ctx, `
		UPDATE memory_index_operations
		SET lease_token = $2, lease_until = $3, updated_at = CURRENT_TIMESTAMP(6)
		WHERE operation_id = $1
	`, operation.ID, operation.LeaseToken, leaseUntil); err != nil {
		return memoryindex.Operation{}, false, storageError("freeze MemoryIndex operation lease", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return memoryindex.Operation{}, false, storageError("commit MemoryIndex operation lease", err)
	}
	return operation, true, nil
}

func (store *Store) AcknowledgeMemoryIndexOperation(ctx context.Context, operation memoryindex.Operation) error {
	tag, err := store.pool.Exec(ctx, `
		UPDATE memory_index_operations
		SET acknowledged_at = CURRENT_TIMESTAMP(6), lease_token = NULL, lease_until = NULL,
		    last_error = '', updated_at = CURRENT_TIMESTAMP(6)
		WHERE operation_id = $1 AND lease_token = $2 AND acknowledged_at IS NULL
	`, operation.ID, operation.LeaseToken)
	if err != nil {
		return storageError("acknowledge MemoryIndex operation", err)
	}
	if tag.RowsAffected() != 1 {
		return errors.New("MemoryIndex operation lease lost")
	}
	return nil
}

func (store *Store) RetryMemoryIndexOperation(ctx context.Context, operation memoryindex.Operation, notBefore time.Time, reason string) error {
	if notBefore.IsZero() {
		return errors.New("MemoryIndex retry deadline is required")
	}
	tag, err := store.pool.Exec(ctx, `
		UPDATE memory_index_operations
		SET attempt_count = attempt_count + 1, not_before = $3,
		    lease_token = NULL, lease_until = NULL, last_error = $4, updated_at = CURRENT_TIMESTAMP(6)
		WHERE operation_id = $1 AND lease_token = $2 AND acknowledged_at IS NULL
	`, operation.ID, operation.LeaseToken, notBefore, strings.TrimSpace(reason))
	if err != nil {
		return storageError("retry MemoryIndex operation", err)
	}
	if tag.RowsAffected() != 1 {
		return errors.New("MemoryIndex operation lease lost")
	}
	return nil
}

func (store *Store) MemoryIndexDocument(ctx context.Context, operation memoryindex.Operation) (memoryindex.Document, bool, error) {
	scope := operation.Scope
	document := memoryindex.Document{Kind: operation.Kind, Ref: operation.Ref, Scope: scope}
	switch operation.Kind {
	case memoryindex.KindEpisode:
		var episode episodeEvidence
		err := store.pool.QueryRow(ctx, `
			SELECT scope_kind, tenant_ref, agent_ref, relationship_ref,
			       session_ref, run_ref, source_group_ref, episode_ref
			FROM episodes
			WHERE episode_ref = $1 AND tenant_ref = $2 AND agent_ref = $3 AND relationship_ref = $4
		`, operation.Ref, scope.TenantRef, scope.AgentRef, scope.RelationshipRef).Scan(
			&episode.Owner.Kind, &episode.Owner.TenantRef, &episode.Owner.AgentRef,
			&episode.Owner.RelationshipRef, &episode.SessionRef, &episode.RunRef,
			&episode.GroupRef, &episode.Ref,
		)
		if errors.Is(err, errNoRows) {
			return memoryindex.Document{}, false, nil
		}
		if err != nil {
			return memoryindex.Document{}, false, storageError("rehydrate MemoryIndex Episode", err)
		}
		rows, err := store.pool.Query(ctx, `
			SELECT link.source_event_ref, link.role, source.actor_kind, source.actor_ref, source.source_text
			FROM episode_links AS link
			JOIN source_events AS source
			  ON source.tenant_ref = link.tenant_ref
			 AND source.scope_kind = link.scope_kind
			 AND source.agent_ref = link.agent_ref
			 AND source.relationship_ref = link.relationship_ref
			 AND source.session_ref = link.session_ref
			 AND source.source_ref = link.source_event_ref
			WHERE link.episode_ref = $1 AND link.tenant_ref = $2
			ORDER BY link.role, link.source_event_ref
		`, operation.Ref, scope.TenantRef)
		if err != nil {
			return memoryindex.Document{}, false, storageError("load MemoryIndex Episode sources", err)
		}
		for rows.Next() {
			var source sourceEvidence
			if err := rows.Scan(&source.Ref, &source.Role, &source.ActorKind, &source.ActorRef, &source.Text); err != nil {
				rows.Close()
				return memoryindex.Document{}, false, storageError("scan MemoryIndex Episode source", err)
			}
			episode.Sources = append(episode.Sources, source)
		}
		if err := rows.Err(); err != nil {
			rows.Close()
			return memoryindex.Document{}, false, storageError("iterate MemoryIndex Episode sources", err)
		}
		rows.Close()
		if len(episode.Sources) == 0 {
			return memoryindex.Document{}, false, nil
		}
		document.Text = renderConsolidationWindow([]episodeEvidence{episode}, nil, false)
	case memoryindex.KindRecollection:
		err := store.pool.QueryRow(ctx, `
			SELECT version.text
			FROM recollection_versions AS version
			JOIN recollections AS recollection
			  ON recollection.tenant_ref = version.tenant_ref
			 AND recollection.recollection_ref = version.recollection_ref
			WHERE version.recollection_version_ref = $1 AND version.status = 'active'
			  AND recollection.tenant_ref = $2 AND recollection.agent_ref = $3
			  AND recollection.relationship_ref = $4
		`, operation.Ref, scope.TenantRef, scope.AgentRef, scope.RelationshipRef).Scan(&document.Text)
		if errors.Is(err, errNoRows) {
			return memoryindex.Document{}, false, nil
		}
		if err != nil {
			return memoryindex.Document{}, false, storageError("rehydrate MemoryIndex Recollection", err)
		}
	case memoryindex.KindDisposition:
		err := store.pool.QueryRow(ctx, `
			SELECT version.tendency_text
			FROM seed_versions AS version
			JOIN disposition_seeds AS seed
			  ON seed.tenant_ref = version.tenant_ref AND seed.seed_ref = version.seed_ref
			WHERE version.seed_version_ref = $1 AND version.status = 'active'
			  AND seed.tenant_ref = $2 AND seed.agent_ref = $3 AND seed.relationship_ref = $4
		`, operation.Ref, scope.TenantRef, scope.AgentRef, scope.RelationshipRef).Scan(&document.Text)
		if errors.Is(err, errNoRows) {
			return memoryindex.Document{}, false, nil
		}
		if err != nil {
			return memoryindex.Document{}, false, storageError("rehydrate MemoryIndex Disposition", err)
		}
	default:
		return memoryindex.Document{}, false, nil
	}
	return document, true, nil
}

type memoryIndexDocumentRef struct {
	kind  memoryindex.Kind
	ref   string
	scope memoryindex.Scope
}

func (store *Store) EnumerateMemoryIndexDocuments(ctx context.Context, cursor string, limit int) ([]memoryindex.Document, string, error) {
	if limit <= 0 || limit > 1000 {
		return nil, "", errors.New("MemoryIndex enumeration limit must be between 1 and 1000")
	}
	afterKind, afterRef, err := parseMemoryIndexCursor(cursor)
	if err != nil {
		return nil, "", err
	}
	rows, err := store.pool.Query(ctx, `
		WITH document_refs AS (
			SELECT 0 AS kind_order, 'EPISODE' AS document_kind, episode_ref AS document_ref,
			       tenant_ref, agent_ref, relationship_ref
			FROM episodes
			UNION ALL
			SELECT 1, 'RECOLLECTION', version.recollection_version_ref,
			       recollection.tenant_ref, recollection.agent_ref, recollection.relationship_ref
			FROM recollection_versions AS version
			JOIN recollections AS recollection
			  ON recollection.tenant_ref = version.tenant_ref
			 AND recollection.recollection_ref = version.recollection_ref
			WHERE version.status = 'active'
			UNION ALL
			SELECT 2, 'DISPOSITION', version.seed_version_ref,
			       seed.tenant_ref, seed.agent_ref, seed.relationship_ref
			FROM seed_versions AS version
			JOIN disposition_seeds AS seed
			  ON seed.tenant_ref = version.tenant_ref AND seed.seed_ref = version.seed_ref
			WHERE version.status = 'active'
		)
		SELECT kind_order, document_kind, document_ref, tenant_ref, agent_ref, relationship_ref
		FROM document_refs
		WHERE kind_order > $1 OR (kind_order = $1 AND document_ref > $2)
		ORDER BY kind_order, document_ref
		LIMIT $3
	`, afterKind, afterRef, limit+1)
	if err != nil {
		return nil, "", storageError("enumerate MemoryIndex document refs", err)
	}
	defer rows.Close()
	refs := make([]memoryIndexDocumentRef, 0, limit+1)
	kindOrders := make([]int, 0, limit+1)
	for rows.Next() {
		var item memoryIndexDocumentRef
		var kindOrder int
		if err := rows.Scan(&kindOrder, &item.kind, &item.ref, &item.scope.TenantRef, &item.scope.AgentRef, &item.scope.RelationshipRef); err != nil {
			return nil, "", storageError("scan MemoryIndex document ref", err)
		}
		refs = append(refs, item)
		kindOrders = append(kindOrders, kindOrder)
	}
	if err := rows.Err(); err != nil {
		return nil, "", storageError("iterate MemoryIndex document refs", err)
	}
	hasMore := len(refs) > limit
	if hasMore {
		refs = refs[:limit]
		kindOrders = kindOrders[:limit]
	}
	documents := make([]memoryindex.Document, 0, len(refs))
	for _, item := range refs {
		document, found, err := store.MemoryIndexDocument(ctx, memoryindex.Operation{Kind: item.kind, Ref: item.ref, Scope: item.scope})
		if err != nil {
			return nil, "", err
		}
		if found {
			documents = append(documents, document)
		}
	}
	next := ""
	if hasMore && len(refs) > 0 {
		last := len(refs) - 1
		next = fmt.Sprintf("%d:%s", kindOrders[last], refs[last].ref)
	}
	return documents, next, nil
}

func parseMemoryIndexCursor(cursor string) (int, string, error) {
	if cursor == "" {
		return -1, "", nil
	}
	parts := strings.SplitN(cursor, ":", 2)
	if len(parts) != 2 || parts[1] == "" {
		return 0, "", errors.New("invalid MemoryIndex enumeration cursor")
	}
	kind, err := strconv.Atoi(parts[0])
	if err != nil || kind < 0 || kind > 2 {
		return 0, "", errors.New("invalid MemoryIndex enumeration cursor")
	}
	return kind, parts[1], nil
}
