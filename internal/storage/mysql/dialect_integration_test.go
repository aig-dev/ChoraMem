package mysql_test

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	storemysql "github.com/aig-dev/ChoraMem/internal/storage/mysql"
	"github.com/aig-dev/ChoraMem/migrations"
	mysqldriver "github.com/go-sql-driver/mysql"
)

func TestMySQLV1MigrationSerializesAndCreatesExactlyTheCleanTables(t *testing.T) {
	baseURL := os.Getenv("MEMORY_TEST_MYSQL_DATABASE_URL")
	if baseURL == "" {
		t.Skip("MEMORY_TEST_MYSQL_DATABASE_URL is not set")
	}
	dsn := isolatedMySQLDSN(t, baseURL)

	const starters = 8
	start := make(chan struct{})
	errorsCh := make(chan error, starters)
	var wait sync.WaitGroup
	for index := 0; index < starters; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			store, err := storemysql.New(ctx, dsn)
			if err == nil {
				err = store.Migrate(ctx)
				store.Close()
			}
			errorsCh <- err
		}()
	}
	close(start)
	wait.Wait()
	close(errorsCh)
	for err := range errorsCh {
		if err != nil {
			t.Fatalf("concurrent first Migrate: %v", err)
		}
	}

	database, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatalf("open migrated MySQL database: %v", err)
	}
	defer database.Close()
	rows, err := database.Query(`
		SELECT table_name
		FROM information_schema.tables
		WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
		ORDER BY table_name
	`)
	if err != nil {
		t.Fatalf("list MySQL V1 tables: %v", err)
	}
	defer rows.Close()
	got := make([]string, 0, 18)
	for rows.Next() {
		var table string
		if err := rows.Scan(&table); err != nil {
			t.Fatalf("scan MySQL V1 table: %v", err)
		}
		got = append(got, table)
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("iterate MySQL V1 tables: %v", err)
	}
	want := []string{
		"consolidation_jobs", "consolidation_receipts", "disposition_seeds",
		"episode_links", "episodes", "memory_context_episode_evidence", "memory_context_items", "memory_contexts",
		"memory_delivery_receipts", "memory_index_operations", "outcome_events",
		"recollection_basis_links", "recollection_versions", "recollections",
		"request_receipts", "seed_basis_links", "seed_outcome_basis_links",
		"seed_versions", "source_events",
	}
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("MySQL V1 tables = %q; want %q", got, want)
	}
}

func TestMySQLMigrateUpgradesV1SchemaAndCanRepeatEpisodeEvidenceMigration(t *testing.T) {
	baseURL := os.Getenv("MEMORY_TEST_MYSQL_DATABASE_URL")
	if baseURL == "" {
		t.Skip("MEMORY_TEST_MYSQL_DATABASE_URL is not set")
	}
	dsn := isolatedMySQLDSN(t, baseURL)
	legacy, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatalf("open legacy MySQL schema: %v", err)
	}
	legacyV1 := strings.Replace(
		migrations.MySQLV1SQL,
		"CONSTRAINT seed_outcome_basis_links_role_check\n        CHECK (role IN ('formation', 'revision', 'inhibition'))",
		"CHECK (role IN ('revision', 'inhibition'))",
		1,
	)
	if legacyV1 == migrations.MySQLV1SQL {
		t.Fatal("legacy MySQL fixture did not remove Outcome formation role")
	}
	if _, err := legacy.Exec(legacyV1); err != nil {
		legacy.Close()
		t.Fatalf("install legacy MySQL V1 schema: %v", err)
	}
	if _, err := legacy.Exec(`INSERT INTO source_events (tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref, actor_kind, actor_ref, source_text) VALUES ('legacy', 'agent', 'agent', '', '', 'legacy-source', 'user', 'user', 'legacy text')`); err != nil {
		t.Fatal(err)
	}
	legacy.Close()

	store, err := storemysql.New(context.Background(), dsn)
	if err != nil {
		t.Fatalf("open MySQL upgrade Store: %v", err)
	}
	defer store.Close()
	for attempt := 0; attempt < 2; attempt++ {
		if err := store.Migrate(context.Background()); err != nil {
			t.Fatalf("Migrate attempt %d: %v", attempt+1, err)
		}
	}
	inspection, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatalf("open upgraded MySQL inspection: %v", err)
	}
	defer inspection.Close()
	var baselineRef, baselineText string
	var admission int64
	if err := inspection.QueryRow(`SELECT constitution_ref, constitution_text, admission_order FROM source_events WHERE source_ref = 'legacy-source'`).Scan(&baselineRef, &baselineText, &admission); err != nil || baselineRef != "" || baselineText != "" {
		t.Fatalf("legacy baseline upgrade = %q %q, %v", baselineRef, baselineText, err)
	}
	var count int
	if err := inspection.QueryRow(`
		SELECT count(*) FROM information_schema.tables
		WHERE table_schema = DATABASE() AND table_name = 'memory_context_episode_evidence'
	`).Scan(&count); err != nil || count != 1 {
		t.Fatalf("Episode evidence upgrade table count = %d, %v", count, err)
	}
	var outcomeRoleCheck string
	if err := inspection.QueryRow(`
		SELECT cc.check_clause
		FROM information_schema.table_constraints AS tc
		JOIN information_schema.check_constraints AS cc
		  ON cc.constraint_schema = tc.constraint_schema
		 AND cc.constraint_name = tc.constraint_name
		WHERE tc.constraint_schema = DATABASE()
		  AND tc.table_name = 'seed_outcome_basis_links'
		  AND tc.constraint_name = 'seed_outcome_basis_links_role_check'
	`).Scan(&outcomeRoleCheck); err != nil || !strings.Contains(strings.ToLower(outcomeRoleCheck), "formation") {
		t.Fatalf("Outcome formation role upgrade = %q, %v", outcomeRoleCheck, err)
	}
}

func TestMySQLGeneratedGuardsMatchPostgresPartialUniqueContracts(t *testing.T) {
	baseURL := os.Getenv("MEMORY_TEST_MYSQL_DATABASE_URL")
	if baseURL == "" {
		t.Skip("MEMORY_TEST_MYSQL_DATABASE_URL is not set")
	}
	dsn := isolatedMySQLDSN(t, baseURL)
	store, err := storemysql.New(context.Background(), dsn)
	if err != nil {
		t.Fatalf("open MySQL Store: %v", err)
	}
	if err := store.Migrate(context.Background()); err != nil {
		store.Close()
		t.Fatalf("Migrate: %v", err)
	}
	store.Close()
	database, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatalf("open MySQL inspection database: %v", err)
	}
	defer database.Close()

	insertJob := `INSERT INTO consolidation_jobs (
		job_ref, tenant_ref, scope_kind, agent_ref, relationship_ref, episode_refs, outcome_event_refs
	) VALUES (?, 'tenant', 'relationship', 'agent', 'relationship', JSON_ARRAY('episode'), JSON_ARRAY())`
	if _, err := database.Exec(insertJob, "job-1"); err != nil {
		t.Fatalf("insert first collecting Job: %v", err)
	}
	if _, err := database.Exec(insertJob, "job-2"); !isMySQLDuplicate(err) {
		t.Fatalf("second collecting Job error = %v; want duplicate guard", err)
	}
	if _, err := database.Exec(`UPDATE consolidation_jobs SET window_hash = ? WHERE job_ref = 'job-1'`, make([]byte, 32)); err != nil {
		t.Fatalf("freeze first collecting Job: %v", err)
	}
	if _, err := database.Exec(insertJob, "job-2"); err != nil {
		t.Fatalf("insert next collecting Job after freeze: %v", err)
	}

	if _, err := database.Exec(`INSERT INTO recollections (
		tenant_ref, scope_kind, agent_ref, relationship_ref, recollection_ref
	) VALUES ('tenant', 'relationship', 'agent', 'relationship', 'recollection')`); err != nil {
		t.Fatalf("insert Recollection: %v", err)
	}
	insertVersion := `INSERT INTO recollection_versions (
		tenant_ref, recollection_ref, recollection_version_ref, version_number,
		text, application_scope, status, origin_job_ref
	) VALUES ('tenant', 'recollection', ?, ?, ?, 'other', 'active', ?)`
	if _, err := database.Exec(insertVersion, "recollection@1", 1, "first", "job-1"); err != nil {
		t.Fatalf("insert first active RecollectionVersion: %v", err)
	}
	if _, err := database.Exec(insertVersion, "recollection@2", 2, "second", "job-2"); !isMySQLDuplicate(err) {
		t.Fatalf("second active RecollectionVersion error = %v; want duplicate guard", err)
	}
	if _, err := database.Exec(`UPDATE recollection_versions SET status = 'superseded' WHERE recollection_version_ref = 'recollection@1'`); err != nil {
		t.Fatalf("supersede first RecollectionVersion: %v", err)
	}
	if _, err := database.Exec(insertVersion, "recollection@2", 2, "second", "job-2"); err != nil {
		t.Fatalf("insert replacement active RecollectionVersion: %v", err)
	}
}

func TestMySQLAdapterIgnoresClientFoundRowsToPreserveIdempotency(t *testing.T) {
	baseURL := os.Getenv("MEMORY_TEST_MYSQL_DATABASE_URL")
	if baseURL == "" {
		t.Skip("MEMORY_TEST_MYSQL_DATABASE_URL is not set")
	}
	dsn := isolatedMySQLDSN(t, baseURL)
	configuration, err := mysqldriver.ParseDSN(dsn)
	if err != nil {
		t.Fatalf("parse isolated MySQL DSN: %v", err)
	}
	configuration.ClientFoundRows = true
	store, err := storemysql.New(context.Background(), configuration.FormatDSN())
	if err != nil {
		t.Fatalf("open MySQL Store: %v", err)
	}
	t.Cleanup(store.Close)
	if err := store.Migrate(context.Background()); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	scope := ledger.Scope{
		Kind: ledger.ScopeKindAgent, TenantRef: "tenant-found-rows", AgentRef: "agent-found-rows",
	}
	first := ledger.SourceEvent{
		Scope: scope, Ref: "source-first", ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "first",
	}
	second := ledger.SourceEvent{
		Scope: scope, Ref: "source-second", ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "second",
	}
	if _, err := store.Observe(context.Background(), "same-request", first, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("first Observe: %v", err)
	}
	if _, err := store.Observe(context.Background(), "same-request", second, ledger.EpisodeBinding{}); !errors.Is(err, ledger.ErrIdempotencyConflict) {
		t.Fatalf("conflicting Observe error = %v; want ErrIdempotencyConflict", err)
	}
}

func isMySQLDuplicate(err error) bool {
	var mysqlError *mysqldriver.MySQLError
	return errors.As(err, &mysqlError) && mysqlError.Number == 1062
}
