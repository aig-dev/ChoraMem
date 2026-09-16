package postgres

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"

	"github.com/aig-dev/ChoraMem/internal/core/fault"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	corestorage "github.com/aig-dev/ChoraMem/internal/storage"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"github.com/aig-dev/ChoraMem/migrations"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Store is the PostgreSQL authority for deterministic causal intake.
type Store struct {
	pool            *pgxpool.Pool
	memoryIndex     memoryindex.Index
	selectionRanker selection.Ranker
	selectionPolicy selection.Policy
}

var _ corestorage.CoreStore = (*Store)(nil)

type Option func(*Store)

// WithMemoryIndex enables an optional semantic candidate projection. The Store
// always rehydrates returned refs from PostgreSQL before activation.
func WithMemoryIndex(index memoryindex.Index) Option {
	return func(store *Store) {
		store.memoryIndex = index
	}
}

// New opens and verifies a PostgreSQL-backed Store.
func New(ctx context.Context, databaseURL string, options ...Option) (*Store, error) {
	if databaseURL == "" {
		return nil, errors.New("postgres database URL is required")
	}
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, fmt.Errorf("open postgres pool: %w", err)
	}
	store := &Store{
		pool:            pool,
		selectionRanker: selection.DefaultRanker(),
		selectionPolicy: selection.DefaultPolicy(),
	}
	for _, option := range options {
		if option != nil {
			option(store)
		}
	}
	if err := store.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return store, nil
}

// Close releases the Store connection pool.
func (store *Store) Close() {
	store.pool.Close()
}

// Ping verifies that PostgreSQL can serve a request.
func (store *Store) Ping(ctx context.Context) error {
	if err := store.pool.Ping(ctx); err != nil {
		return storageError("ping postgres", err)
	}
	return nil
}

// Migrate creates the canonical Memory Core schemas. Every statement is
// idempotent so startup may safely call this more than once.
func (store *Store) Migrate(ctx context.Context) error {
	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return storageError("begin causal intake migration", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	migrationHash := hashFields("memory-core-migration.v1")
	migrationLockKey := int64(binary.BigEndian.Uint64(migrationHash[:8]))
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", migrationLockKey); err != nil {
		return storageError("lock causal intake migration", err)
	}
	if _, err := tx.Exec(ctx, migrations.PostgresV1SQL); err != nil {
		return storageError("migrate PostgreSQL V1 schema", err)
	}
	if _, err := tx.Exec(ctx, migrations.PostgresEpisodeEvidenceSQL); err != nil {
		return storageError("migrate PostgreSQL Episode evidence schema", err)
	}
	if _, err := tx.Exec(ctx, migrations.PostgresSourceConstitutionSQL); err != nil {
		return storageError("migrate source Constitution snapshot", err)
	}
	if _, err := tx.Exec(ctx, migrations.PostgresDispositionFormationOutcomeSQL); err != nil {
		return storageError("migrate Disposition formation Outcome Basis", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return storageError("commit causal intake migration", err)
	}
	return nil
}

// Observe atomically freezes transport idempotency, immutable source input,
// one optional typed link, and a deterministically materialized Episode.
func (store *Store) Observe(ctx context.Context, idempotencyKey string, event ledger.SourceEvent, binding ledger.EpisodeBinding) (ledger.ObserveReceipt, error) {
	if !ledger.ValidStableRef(idempotencyKey) {
		return ledger.ObserveReceipt{}, errors.New("idempotency key is required")
	}
	if err := ledger.Validate(event, binding); err != nil {
		return ledger.ObserveReceipt{}, err
	}

	tx, err := store.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return ledger.ObserveReceipt{}, storageError("begin causal intake", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	receipt, claimed, err := claimRequest(ctx, tx, idempotencyKey, event, binding)
	if err != nil {
		return ledger.ObserveReceipt{}, err
	}
	if !claimed {
		if err := tx.Commit(ctx); err != nil {
			return ledger.ObserveReceipt{}, storageError("commit idempotent receipt read", err)
		}
		return receipt, nil
	}
	if binding.Role == ledger.RoleAgentAct {
		if err := lockFeedbackRun(ctx, tx, event.Scope, binding.RunRef); err != nil {
			return ledger.ObserveReceipt{}, err
		}
	}

	if err := admitSource(ctx, tx, event); err != nil {
		return ledger.ObserveReceipt{}, err
	}
	if completeBinding(binding) {
		var changed bool
		receipt.EpisodeRef, changed, err = appendLinkAndMaterialize(ctx, tx, event, binding)
		if err != nil {
			return ledger.ObserveReceipt{}, err
		}
		if changed && receipt.EpisodeRef != "" {
			if err := enqueueEpisodeState(ctx, tx, event.Scope, receipt.EpisodeRef); err != nil {
				return ledger.ObserveReceipt{}, err
			}
			if err := enqueueEpisodeProjection(
				ctx, tx, event.Scope, receipt.EpisodeRef, binding.Role, event.Ref,
			); err != nil {
				return ledger.ObserveReceipt{}, err
			}
		}
	}
	if _, err := tx.Exec(ctx, `
		UPDATE request_receipts
		SET episode_ref = $3
		WHERE tenant_ref = $1 AND idempotency_key = $2
	`, event.Scope.TenantRef, idempotencyKey, receipt.EpisodeRef); err != nil {
		return ledger.ObserveReceipt{}, storageError("freeze observe receipt", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return ledger.ObserveReceipt{}, storageError("commit causal intake", err)
	}
	return receipt, nil
}

func claimRequest(ctx context.Context, tx pgx.Tx, idempotencyKey string, event ledger.SourceEvent, binding ledger.EpisodeBinding) (ledger.ObserveReceipt, bool, error) {
	hash := requestHash(event, binding)
	receipt := ledger.ObserveReceipt{SourceEventRef: event.Ref}
	tag, err := tx.Exec(ctx, `
		INSERT INTO request_receipts (
			tenant_ref, idempotency_key, request_hash, source_event_ref, episode_ref
		) VALUES ($1, $2, $3, $4, '')
		ON CONFLICT (tenant_ref, idempotency_key) DO NOTHING
	`, event.Scope.TenantRef, idempotencyKey, hash[:], event.Ref)
	if err != nil {
		return ledger.ObserveReceipt{}, false, storageError("claim idempotency key", err)
	}
	if tag.RowsAffected() == 1 {
		return receipt, true, nil
	}

	var storedHash []byte
	if err := tx.QueryRow(ctx, `
		SELECT request_hash, source_event_ref, episode_ref
		FROM request_receipts
		WHERE tenant_ref = $1 AND idempotency_key = $2
	`, event.Scope.TenantRef, idempotencyKey).Scan(&storedHash, &receipt.SourceEventRef, &receipt.EpisodeRef); err != nil {
		return ledger.ObserveReceipt{}, false, storageError("load idempotent receipt", err)
	}
	if !bytes.Equal(storedHash, hash[:]) {
		return ledger.ObserveReceipt{}, false, fmt.Errorf("%w: %s", ledger.ErrIdempotencyConflict, idempotencyKey)
	}
	return receipt, false, nil
}

func admitSource(ctx context.Context, tx pgx.Tx, event ledger.SourceEvent) error {
	tag, err := tx.Exec(ctx, `
		INSERT INTO source_events (
			tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
			source_ref, actor_kind, actor_ref, source_text, constitution_ref, constitution_text
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
		ON CONFLICT (
			tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref
		) DO NOTHING
	`,
		event.Scope.TenantRef,
		event.Scope.Kind,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		event.Ref,
		event.ActorKind,
		event.ActorRef,
		event.Text,
		event.Constitution.MemoryRef,
		event.Constitution.Text,
	)
	if err != nil {
		return storageError("admit source event", err)
	}
	if tag.RowsAffected() == 1 {
		return nil
	}

	var actorKind ledger.ActorKind
	var actorRef, text string
	var constitution ledger.Constitution
	if err := tx.QueryRow(ctx, `
		SELECT actor_kind, actor_ref, source_text, constitution_ref, constitution_text
		FROM source_events
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND source_ref = $6
	`,
		event.Scope.TenantRef,
		event.Scope.Kind,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		event.Ref,
	).Scan(&actorKind, &actorRef, &text, &constitution.MemoryRef, &constitution.Text); err != nil {
		return storageError("load immutable source event", err)
	}
	if actorKind != event.ActorKind || actorRef != event.ActorRef || text != event.Text || constitution != event.Constitution {
		return fmt.Errorf("%w: %s", ledger.ErrSourceEventConflict, event.Ref)
	}
	return nil
}

func appendLinkAndMaterialize(ctx context.Context, tx pgx.Tx, event ledger.SourceEvent, binding ledger.EpisodeBinding) (string, bool, error) {
	lockKey := groupLockKey(event.Scope, binding.RunRef, binding.SourceGroupRef)
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", lockKey); err != nil {
		return "", false, storageError("lock exact episode group", err)
	}

	episodeRef, err := findEpisode(ctx, tx, event.Scope, binding)
	if err != nil {
		return "", false, err
	}
	if episodeRef != "" {
		exists, err := episodeLinkExists(ctx, tx, event.Scope, binding, event.Ref)
		if err != nil {
			return "", false, err
		}
		if exists {
			return episodeRef, false, nil
		}
		if binding.Role != ledger.RoleOutcome {
			return "", false, ledger.ErrEpisodeSealed
		}
	}
	tag, err := tx.Exec(ctx, `
		INSERT INTO episode_links (
			tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
			run_ref, source_group_ref, source_event_ref, role, episode_ref
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		ON CONFLICT (
			tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
			run_ref, source_group_ref, source_event_ref, role
		) DO NOTHING
	`,
		event.Scope.TenantRef,
		event.Scope.Kind,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
		event.Ref,
		binding.Role,
		episodeRef,
	)
	if err != nil {
		return "", false, storageError("append typed episode link", err)
	}
	linkChanged := tag.RowsAffected() == 1
	if episodeRef != "" {
		return episodeRef, linkChanged, nil
	}

	materialized, err := distinctRoleGate(ctx, tx, event.Scope, binding)
	if err != nil {
		return "", false, err
	}
	if !materialized {
		return "", linkChanged, nil
	}

	episodeRef = ledger.EpisodeRef(event.Scope, binding.RunRef, binding.SourceGroupRef)
	if _, err := tx.Exec(ctx, `
		INSERT INTO episodes (
			tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
			run_ref, source_group_ref, episode_ref
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
		ON CONFLICT (
			tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref,
			run_ref, source_group_ref
		) DO NOTHING
	`,
		event.Scope.TenantRef,
		event.Scope.Kind,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
		episodeRef,
	); err != nil {
		return "", false, storageError("materialize episode", err)
	}
	if _, err := tx.Exec(ctx, `
		UPDATE episode_links
		SET episode_ref = $8
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND run_ref = $6
		  AND source_group_ref = $7
	`,
		event.Scope.TenantRef,
		event.Scope.Kind,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
		episodeRef,
	); err != nil {
		return "", false, storageError("bind materialized episode links", err)
	}
	if _, err := tx.Exec(ctx, `
		UPDATE outcome_events
		SET episode_ref = $8
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND run_ref = $6
		  AND source_group_ref = $7
		  AND episode_ref = ''
	`,
		event.Scope.TenantRef,
		event.Scope.Kind,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
		episodeRef,
	); err != nil {
		return "", false, storageError("bind materialized Outcome events", err)
	}
	return episodeRef, true, nil
}

func findEpisode(ctx context.Context, tx pgx.Tx, scope ledger.Scope, binding ledger.EpisodeBinding) (string, error) {
	var episodeRef string
	err := tx.QueryRow(ctx, `
		SELECT episode_ref
		FROM episodes
		WHERE tenant_ref = $1
		  AND scope_kind = $2
		  AND agent_ref = $3
		  AND relationship_ref = $4
		  AND session_ref = $5
		  AND run_ref = $6
		  AND source_group_ref = $7
	`,
		scope.TenantRef,
		scope.Kind,
		scope.AgentRef,
		scope.RelationshipRef,
		scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
	).Scan(&episodeRef)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", storageError("find materialized episode", err)
	}
	return episodeRef, nil
}

func episodeLinkExists(ctx context.Context, tx pgx.Tx, scope ledger.Scope, binding ledger.EpisodeBinding, sourceRef string) (bool, error) {
	var exists bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM episode_links
			WHERE tenant_ref = $1
			  AND scope_kind = $2
			  AND agent_ref = $3
			  AND relationship_ref = $4
			  AND session_ref = $5
			  AND run_ref = $6
			  AND source_group_ref = $7
			  AND source_event_ref = $8
			  AND role = $9
		)
	`,
		scope.TenantRef,
		scope.Kind,
		scope.AgentRef,
		scope.RelationshipRef,
		scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
		sourceRef,
		binding.Role,
	).Scan(&exists); err != nil {
		return false, storageError("check existing Episode link", err)
	}
	return exists, nil
}

func distinctRoleGate(ctx context.Context, tx pgx.Tx, scope ledger.Scope, binding ledger.EpisodeBinding) (bool, error) {
	var materialized bool
	if err := tx.QueryRow(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM episode_links AS situation
			JOIN episode_links AS agent_act
			  ON agent_act.tenant_ref = situation.tenant_ref
			 AND agent_act.scope_kind = situation.scope_kind
			 AND agent_act.agent_ref = situation.agent_ref
			 AND agent_act.relationship_ref = situation.relationship_ref
			 AND agent_act.session_ref = situation.session_ref
			 AND agent_act.run_ref = situation.run_ref
			 AND agent_act.source_group_ref = situation.source_group_ref
			WHERE situation.tenant_ref = $1
			  AND situation.scope_kind = $2
			  AND situation.agent_ref = $3
			  AND situation.relationship_ref = $4
			  AND situation.session_ref = $5
			  AND situation.run_ref = $6
			  AND situation.source_group_ref = $7
			  AND situation.role = 'situation'
			  AND agent_act.role = 'agent_act'
			  AND situation.source_event_ref <> agent_act.source_event_ref
		)
	`,
		scope.TenantRef,
		scope.Kind,
		scope.AgentRef,
		scope.RelationshipRef,
		scope.SessionRef,
		binding.RunRef,
		binding.SourceGroupRef,
	).Scan(&materialized); err != nil {
		return false, storageError("evaluate episode materialization gate", err)
	}
	return materialized, nil
}

func completeBinding(binding ledger.EpisodeBinding) bool {
	return binding.RunRef != "" && binding.SourceGroupRef != ""
}

func requestHash(event ledger.SourceEvent, binding ledger.EpisodeBinding) [sha256.Size]byte {
	fields := []string{
		"observe-request.v1",
		event.Ref,
		string(event.Scope.Kind),
		event.Scope.TenantRef,
		event.Scope.AgentRef,
		event.Scope.RelationshipRef,
		event.Scope.SessionRef,
		string(event.ActorKind),
		event.ActorRef,
		event.Text,
		binding.RunRef,
		binding.SourceGroupRef,
		string(binding.Role),
	}
	if event.Constitution != (ledger.Constitution{}) {
		fields[0] = "observe-request.v2"
		fields = append(fields, "constitution.v1", event.Constitution.MemoryRef, event.Constitution.Text)
	}
	return hashFields(fields...)
}

func groupLockKey(scope ledger.Scope, runRef, sourceGroupRef string) int64 {
	hash := hashFields(
		"episode-group-lock.v1",
		string(scope.Kind),
		scope.TenantRef,
		scope.AgentRef,
		scope.RelationshipRef,
		scope.SessionRef,
		runRef,
		sourceGroupRef,
	)
	return int64(binary.BigEndian.Uint64(hash[:8]))
}

func hashFields(fields ...string) [sha256.Size]byte {
	hash := sha256.New()
	var length [8]byte
	for _, field := range fields {
		binary.BigEndian.PutUint64(length[:], uint64(len(field)))
		_, _ = hash.Write(length[:])
		_, _ = hash.Write([]byte(field))
	}
	var result [sha256.Size]byte
	copy(result[:], hash.Sum(nil))
	return result
}

func storageError(operation string, err error) error {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return fmt.Errorf("%s: %w", operation, err)
	}

	var postgresError *pgconn.PgError
	if errors.As(err, &postgresError) {
		switch postgresError.Code {
		case "40001", "40P01":
			return fmt.Errorf("%s: %w: %w", operation, fault.ErrAborted, err)
		default:
			return fmt.Errorf("%s: %w", operation, err)
		}
	}

	// Driver/network failures have either not reached PostgreSQL or may have
	// lost the commit response. Observe is idempotent, so callers should retry
	// the complete request with the same key.
	return fmt.Errorf("%s: %w: %w", operation, fault.ErrUnavailable, err)
}
