package postgres

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/jackc/pgx/v5"
)

// RecordMemoryDelivery freezes only the stable memories a Harness can
// prove were exposed. It does not claim model use or behavioral causation.
func (store *Store) RecordMemoryDelivery(ctx context.Context, delivery ledger.MemoryDelivery) (ledger.MemoryDeliveryReceipt, error) {
	if err := ledger.ValidateMemoryDelivery(delivery); err != nil {
		return ledger.MemoryDeliveryReceipt{}, err
	}
	memoryRefs := sortedRefs(delivery.DeliveredMemoryRefs)
	requestHash := memoryDeliveryHash(delivery, memoryRefs)
	receiptRef := memoryDeliveryReceiptRef(delivery.Scope.TenantRef, delivery.IdempotencyKey)

	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return ledger.MemoryDeliveryReceipt{}, storageError("begin Memory delivery", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", feedbackRequestLockKey("delivery", delivery.Scope.TenantRef, delivery.IdempotencyKey)); err != nil {
		return ledger.MemoryDeliveryReceipt{}, storageError("lock Memory delivery request", err)
	}
	if receipt, found, err := loadMemoryDeliveryReceipt(ctx, tx, delivery.Scope.TenantRef, delivery.IdempotencyKey, requestHash); err != nil {
		return ledger.MemoryDeliveryReceipt{}, err
	} else if found {
		if err := tx.Commit(ctx); err != nil {
			return ledger.MemoryDeliveryReceipt{}, storageError("commit frozen Memory delivery read", err)
		}
		return receipt, nil
	}
	if err := lockFeedbackRun(ctx, tx, delivery.Scope, delivery.RunRef); err != nil {
		return ledger.MemoryDeliveryReceipt{}, err
	}
	if err := rejectRetroactiveMemoryDelivery(ctx, tx, delivery); err != nil {
		return ledger.MemoryDeliveryReceipt{}, err
	}

	allowed, err := loadDeliverableMemoryRefs(ctx, tx, delivery)
	if err != nil {
		return ledger.MemoryDeliveryReceipt{}, err
	}
	for _, ref := range memoryRefs {
		if _, exists := allowed[ref]; !exists {
			return ledger.MemoryDeliveryReceipt{}, fmt.Errorf("%w: memory %s is not in MemoryContext %s", ledger.ErrInvalidMemoryDelivery, ref, delivery.MemoryContextRef)
		}
	}

	receipt := ledger.MemoryDeliveryReceipt{Ref: receiptRef}
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_delivery_receipts (
			tenant_ref, receipt_ref, idempotency_key, request_hash,
			scope_kind, agent_ref, relationship_ref, session_ref,
			run_ref, context_ref, delivered_memory_refs
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
	`,
		delivery.Scope.TenantRef,
		receipt.Ref,
		delivery.IdempotencyKey,
		requestHash[:],
		delivery.Scope.Kind,
		delivery.Scope.AgentRef,
		delivery.Scope.RelationshipRef,
		delivery.Scope.SessionRef,
		delivery.RunRef,
		delivery.MemoryContextRef,
		memoryRefs,
	); err != nil {
		return ledger.MemoryDeliveryReceipt{}, storageError("freeze Memory delivery", err)
	}
	if err := enqueueRunEpisodes(ctx, tx, delivery.Scope, delivery.RunRef); err != nil {
		return ledger.MemoryDeliveryReceipt{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return ledger.MemoryDeliveryReceipt{}, storageError("commit Memory delivery", err)
	}
	return receipt, nil
}

// ReportOutcome admits an immutable result SourceEvent, attaches its outcome
// relation, and freezes optional delivery attribution in one transaction.
func (store *Store) ReportOutcome(ctx context.Context, report ledger.OutcomeReport) (ledger.OutcomeReceipt, error) {
	if err := ledger.ValidateOutcomeReport(report); err != nil {
		return ledger.OutcomeReceipt{}, err
	}
	deliveryRefs := sortedRefs(report.DeliveryReceiptRefs)
	relatedRefs := sortedRefs(report.RelatedSourceEventRefs)
	requestHash := outcomeReportHash(report, deliveryRefs, relatedRefs)
	outcomeRef := outcomeEventRef(report)

	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return ledger.OutcomeReceipt{}, storageError("begin Outcome report", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", feedbackRequestLockKey("outcome", report.Event.Scope.TenantRef, report.IdempotencyKey)); err != nil {
		return ledger.OutcomeReceipt{}, storageError("lock Outcome request", err)
	}
	if receipt, found, err := loadOutcomeReceipt(ctx, tx, report, requestHash); err != nil {
		return ledger.OutcomeReceipt{}, err
	} else if found {
		if err := tx.Commit(ctx); err != nil {
			return ledger.OutcomeReceipt{}, storageError("commit frozen Outcome read", err)
		}
		return receipt, nil
	}

	if err := validateOutcomeBindings(ctx, tx, report, deliveryRefs, relatedRefs); err != nil {
		return ledger.OutcomeReceipt{}, err
	}
	if err := admitSource(ctx, tx, report.Event); err != nil {
		return ledger.OutcomeReceipt{}, err
	}
	episodeRef, episodeChanged, err := appendLinkAndMaterialize(ctx, tx, report.Event, ledger.EpisodeBinding{
		RunRef: report.RunRef, SourceGroupRef: report.SourceGroupRef, Role: ledger.RoleOutcome,
	})
	if err != nil {
		return ledger.OutcomeReceipt{}, err
	}
	receipt := ledger.OutcomeReceipt{OutcomeEventRef: outcomeRef, EpisodeRef: episodeRef}
	if _, err := tx.Exec(ctx, `
		INSERT INTO outcome_events (
			tenant_ref, outcome_event_ref, idempotency_key, request_hash,
			scope_kind, agent_ref, relationship_ref, session_ref,
			run_ref, source_group_ref, source_event_ref, episode_ref,
			delivery_receipt_refs, related_source_event_refs
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
	`,
		report.Event.Scope.TenantRef,
		outcomeRef,
		report.IdempotencyKey,
		requestHash[:],
		report.Event.Scope.Kind,
		report.Event.Scope.AgentRef,
		report.Event.Scope.RelationshipRef,
		report.Event.Scope.SessionRef,
		report.RunRef,
		report.SourceGroupRef,
		report.Event.Ref,
		episodeRef,
		deliveryRefs,
		relatedRefs,
	); err != nil {
		return ledger.OutcomeReceipt{}, storageError("freeze Outcome event", err)
	}
	if err := enqueueEpisodeState(ctx, tx, report.Event.Scope, episodeRef); err != nil {
		return ledger.OutcomeReceipt{}, err
	}
	if episodeChanged && episodeRef != "" {
		if err := enqueueEpisodeProjection(
			ctx, tx, report.Event.Scope, episodeRef, ledger.RoleOutcome, report.Event.Ref,
		); err != nil {
			return ledger.OutcomeReceipt{}, err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return ledger.OutcomeReceipt{}, storageError("commit Outcome report", err)
	}
	return receipt, nil
}

func loadMemoryDeliveryReceipt(ctx context.Context, tx pgx.Tx, tenantRef, idempotencyKey string, requestHash [sha256.Size]byte) (ledger.MemoryDeliveryReceipt, bool, error) {
	var receipt ledger.MemoryDeliveryReceipt
	var storedHash []byte
	err := tx.QueryRow(ctx, `
		SELECT receipt_ref, request_hash
		FROM memory_delivery_receipts
		WHERE tenant_ref = $1 AND idempotency_key = $2
	`, tenantRef, idempotencyKey).Scan(&receipt.Ref, &storedHash)
	if errors.Is(err, pgx.ErrNoRows) {
		return ledger.MemoryDeliveryReceipt{}, false, nil
	}
	if err != nil {
		return ledger.MemoryDeliveryReceipt{}, false, storageError("load Memory delivery receipt", err)
	}
	if !bytes.Equal(storedHash, requestHash[:]) {
		return ledger.MemoryDeliveryReceipt{}, false, fmt.Errorf("%w: %s", ledger.ErrMemoryDeliveryConflict, idempotencyKey)
	}
	return receipt, true, nil
}

func loadDeliverableMemoryRefs(ctx context.Context, tx pgx.Tx, delivery ledger.MemoryDelivery) (map[string]string, error) {
	var constitutionRef string
	err := tx.QueryRow(ctx, `
		SELECT constitution_ref
		FROM memory_contexts
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND run_ref = $6
		  AND context_ref = $7
	`,
		delivery.Scope.TenantRef,
		delivery.Scope.Kind,
		delivery.Scope.AgentRef,
		delivery.Scope.RelationshipRef,
		delivery.Scope.SessionRef,
		delivery.RunRef,
		delivery.MemoryContextRef,
	).Scan(&constitutionRef)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, fmt.Errorf("%w: unknown exact MemoryContext", ledger.ErrInvalidMemoryDelivery)
	}
	if err != nil {
		return nil, storageError("load delivered MemoryContext", err)
	}
	allowed := make(map[string]string)
	if constitutionRef != "" {
		allowed[constitutionRef] = "constitution"
	}
	rows, err := tx.Query(ctx, `
		SELECT item_kind, memory_ref
		FROM memory_context_items
		WHERE tenant_ref = $1 AND context_ref = $2
	`, delivery.Scope.TenantRef, delivery.MemoryContextRef)
	if err != nil {
		return nil, storageError("load deliverable memories", err)
	}
	defer rows.Close()
	for rows.Next() {
		var kind, ref string
		if err := rows.Scan(&kind, &ref); err != nil {
			return nil, storageError("scan deliverable memory", err)
		}
		if previous, exists := allowed[ref]; exists && previous != kind {
			return nil, fmt.Errorf("%w: memory %s is both %s and %s", ledger.ErrInvalidMemoryDelivery, ref, previous, kind)
		}
		allowed[ref] = kind
	}
	if err := rows.Err(); err != nil {
		return nil, storageError("iterate deliverable memories", err)
	}
	rows.Close()
	evidenceRows, err := tx.Query(ctx, `
		SELECT episode_ref
		FROM memory_context_episode_evidence
		WHERE tenant_ref = $1 AND context_ref = $2
	`, delivery.Scope.TenantRef, delivery.MemoryContextRef)
	if err != nil {
		return nil, storageError("load deliverable Episode evidence", err)
	}
	defer evidenceRows.Close()
	for evidenceRows.Next() {
		var ref string
		if err := evidenceRows.Scan(&ref); err != nil {
			return nil, storageError("scan deliverable Episode evidence", err)
		}
		if previous, exists := allowed[ref]; exists && previous != "episode_evidence" {
			return nil, fmt.Errorf("%w: memory %s is both %s and episode_evidence", ledger.ErrInvalidMemoryDelivery, ref, previous)
		}
		allowed[ref] = "episode_evidence"
	}
	if err := evidenceRows.Err(); err != nil {
		return nil, storageError("iterate deliverable Episode evidence", err)
	}
	return allowed, nil
}

func loadOutcomeReceipt(ctx context.Context, tx pgx.Tx, report ledger.OutcomeReport, requestHash [sha256.Size]byte) (ledger.OutcomeReceipt, bool, error) {
	var receipt ledger.OutcomeReceipt
	var storedHash []byte
	err := tx.QueryRow(ctx, `
		SELECT outcome_event_ref, episode_ref, request_hash
		FROM outcome_events
		WHERE tenant_ref = $1 AND idempotency_key = $2
	`, report.Event.Scope.TenantRef, report.IdempotencyKey).Scan(
		&receipt.OutcomeEventRef, &receipt.EpisodeRef, &storedHash,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return ledger.OutcomeReceipt{}, false, nil
	}
	if err != nil {
		return ledger.OutcomeReceipt{}, false, storageError("load Outcome receipt", err)
	}
	if !bytes.Equal(storedHash, requestHash[:]) {
		return ledger.OutcomeReceipt{}, false, fmt.Errorf("%w: %s", ledger.ErrOutcomeReportConflict, report.IdempotencyKey)
	}
	return receipt, true, nil
}

func validateOutcomeBindings(ctx context.Context, tx pgx.Tx, report ledger.OutcomeReport, deliveryRefs, relatedRefs []string) error {
	for _, ref := range deliveryRefs {
		var exists bool
		if err := tx.QueryRow(ctx, `
			SELECT EXISTS (
				SELECT 1 FROM memory_delivery_receipts
				WHERE tenant_ref = $1
				  AND scope_kind = $2
				  AND agent_ref = $3
				  AND relationship_ref = $4
				  AND session_ref = $5
				  AND run_ref = $6
				  AND receipt_ref = $7
			)
		`,
			report.Event.Scope.TenantRef,
			report.Event.Scope.Kind,
			report.Event.Scope.AgentRef,
			report.Event.Scope.RelationshipRef,
			report.Event.Scope.SessionRef,
			report.RunRef,
			ref,
		).Scan(&exists); err != nil {
			return storageError("validate Outcome delivery binding", err)
		}
		if !exists {
			return fmt.Errorf("%w: unknown exact delivery receipt %s", ledger.ErrInvalidOutcomeReport, ref)
		}
	}
	for _, ref := range relatedRefs {
		var exists bool
		if err := tx.QueryRow(ctx, `
			SELECT EXISTS (
				SELECT 1 FROM episode_links
				WHERE tenant_ref = $1
				  AND scope_kind = $2
				  AND agent_ref = $3
				  AND relationship_ref = $4
				  AND session_ref = $5
				  AND run_ref = $6
				  AND source_group_ref = $7
				  AND source_event_ref = $8
			)
		`,
			report.Event.Scope.TenantRef,
			report.Event.Scope.Kind,
			report.Event.Scope.AgentRef,
			report.Event.Scope.RelationshipRef,
			report.Event.Scope.SessionRef,
			report.RunRef,
			report.SourceGroupRef,
			ref,
		).Scan(&exists); err != nil {
			return storageError("validate Outcome related source", err)
		}
		if !exists {
			return fmt.Errorf("%w: unrelated source %s", ledger.ErrInvalidOutcomeReport, ref)
		}
	}
	if len(deliveryRefs) > 0 {
		var hasAgentAct bool
		if err := tx.QueryRow(ctx, `
			SELECT EXISTS (
				SELECT 1 FROM episode_links
				WHERE tenant_ref = $1
				  AND scope_kind = $2
				  AND agent_ref = $3
				  AND relationship_ref = $4
				  AND session_ref = $5
				  AND run_ref = $6
				  AND source_group_ref = $7
				  AND role = 'agent_act'
				  AND source_event_ref = ANY($8)
			)
		`,
			report.Event.Scope.TenantRef,
			report.Event.Scope.Kind,
			report.Event.Scope.AgentRef,
			report.Event.Scope.RelationshipRef,
			report.Event.Scope.SessionRef,
			report.RunRef,
			report.SourceGroupRef,
			relatedRefs,
		).Scan(&hasAgentAct); err != nil {
			return storageError("validate attributed Outcome AgentAct", err)
		}
		if !hasAgentAct {
			return fmt.Errorf("%w: delivery-attributed Outcome requires an exact related AgentAct", ledger.ErrInvalidOutcomeReport)
		}
	}
	return nil
}

func rejectRetroactiveMemoryDelivery(ctx context.Context, tx pgx.Tx, delivery ledger.MemoryDelivery) error {
	var agentActExists bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1 FROM episode_links
			WHERE tenant_ref = $1
			  AND scope_kind = $2
			  AND agent_ref = $3
			  AND relationship_ref = $4
			  AND session_ref = $5
			  AND run_ref = $6
			  AND role = 'agent_act'
		)
	`,
		delivery.Scope.TenantRef,
		delivery.Scope.Kind,
		delivery.Scope.AgentRef,
		delivery.Scope.RelationshipRef,
		delivery.Scope.SessionRef,
		delivery.RunRef,
	).Scan(&agentActExists); err != nil {
		return storageError("validate Memory delivery causal order", err)
	}
	if agentActExists {
		return fmt.Errorf("%w: Memory delivery must be recorded before AgentAct", ledger.ErrInvalidMemoryDelivery)
	}
	return nil
}

func lockFeedbackRun(ctx context.Context, tx pgx.Tx, scope ledger.Scope, runRef string) error {
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", feedbackRunLockKey(scope, runRef)); err != nil {
		return storageError("lock Memory feedback run", err)
	}
	return nil
}

func memoryDeliveryHash(delivery ledger.MemoryDelivery, memoryRefs []string) [sha256.Size]byte {
	fields := []string{
		"memory-delivery.v2",
		string(delivery.Scope.Kind), delivery.Scope.TenantRef, delivery.Scope.AgentRef,
		delivery.Scope.RelationshipRef, delivery.Scope.SessionRef,
		delivery.RunRef, delivery.MemoryContextRef,
	}
	fields = append(fields, memoryRefs...)
	return hashFields(fields...)
}

func outcomeReportHash(report ledger.OutcomeReport, deliveryRefs, relatedRefs []string) [sha256.Size]byte {
	fields := []string{
		"outcome-report.v1",
		string(report.Event.Scope.Kind), report.Event.Scope.TenantRef, report.Event.Scope.AgentRef,
		report.Event.Scope.RelationshipRef, report.Event.Scope.SessionRef,
		report.RunRef, report.SourceGroupRef,
		report.Event.Ref, string(report.Event.ActorKind), report.Event.ActorRef, report.Event.Text,
	}
	fields = append(fields, deliveryRefs...)
	fields = append(fields, "related-sources")
	fields = append(fields, relatedRefs...)
	if report.Event.Constitution != (ledger.Constitution{}) {
		fields[0] = "outcome-report.v2"
		fields = append(fields, "constitution.v1", report.Event.Constitution.MemoryRef, report.Event.Constitution.Text)
	}
	return hashFields(fields...)
}

func outcomeEventRef(report ledger.OutcomeReport) string {
	hash := hashFields(
		"outcome-event-identity.v1",
		string(report.Event.Scope.Kind), report.Event.Scope.TenantRef, report.Event.Scope.AgentRef,
		report.Event.Scope.RelationshipRef, report.Event.Scope.SessionRef,
		report.RunRef, report.SourceGroupRef, report.Event.Ref, report.IdempotencyKey,
	)
	return "outcome_" + hex.EncodeToString(hash[:16])
}

func memoryDeliveryReceiptRef(tenantRef, idempotencyKey string) string {
	hash := hashFields("memory-delivery-receipt.v2", tenantRef, idempotencyKey)
	return "delivery_" + hex.EncodeToString(hash[:16])
}

func feedbackRequestLockKey(kind, tenantRef, idempotencyKey string) int64 {
	hash := hashFields("feedback-request-lock.v1", kind, tenantRef, idempotencyKey)
	return int64(binary.BigEndian.Uint64(hash[:8]))
}

func feedbackRunLockKey(scope ledger.Scope, runRef string) int64 {
	hash := hashFields(
		"feedback-run-lock.v1", string(scope.Kind), scope.TenantRef, scope.AgentRef,
		scope.RelationshipRef, scope.SessionRef, runRef,
	)
	return int64(binary.BigEndian.Uint64(hash[:8]))
}

func sortedRefs(refs []string) []string {
	result := make([]string, len(refs))
	copy(result, refs)
	sort.Strings(result)
	return result
}
