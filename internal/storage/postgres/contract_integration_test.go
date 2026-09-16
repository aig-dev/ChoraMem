package postgres_test

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/storage"
	"github.com/aig-dev/ChoraMem/internal/storage/contracttest"
	"github.com/aig-dev/ChoraMem/internal/storage/postgres"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var contractSchemaSequence atomic.Uint64

func TestCoreStoreContract(t *testing.T) {
	databaseURL := os.Getenv("MEMORY_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("MEMORY_TEST_DATABASE_URL is not set")
	}
	contracttest.Run(t, postgresContractFactory(databaseURL))
	contracttest.RunAdaptiveSeeds(t, postgresIndexedContractFactory(databaseURL))
	contracttest.RunConstitution(t, postgresIndexedContractFactory(databaseURL))
	contracttest.RunLiveConstitutionHarnesses(t, postgresIndexedContractFactory(databaseURL))
	contracttest.RunLiveAdaptiveWorker(t, postgresIndexedContractFactory(databaseURL), func(t *testing.T, scope ledger.Scope) map[string]int {
		admin, err := pgxpool.New(context.Background(), databaseURL)
		if err != nil {
			t.Fatal(err)
		}
		defer admin.Close()
		rows, err := admin.Query(context.Background(), "SELECT table_schema FROM information_schema.tables WHERE table_name='seed_versions' AND table_schema LIKE 'memory_contract_%'")
		if err != nil {
			t.Fatal(err)
		}
		var schemas []string
		for rows.Next() {
			var schema string
			if err := rows.Scan(&schema); err != nil {
				t.Fatal(err)
			}
			schemas = append(schemas, schema)
		}
		rows.Close()
		if err := rows.Err(); err != nil {
			t.Fatal(err)
		}
		counts := map[string]int{}
		for _, table := range []string{"disposition_seeds", "seed_versions", "seed_basis_links", "seed_outcome_basis_links", "memory_delivery_receipts", "outcome_events"} {
			for _, schema := range schemas {
				var count int
				if err := admin.QueryRow(context.Background(), "SELECT COUNT(*) FROM "+pgx.Identifier{schema, table}.Sanitize()+" WHERE tenant_ref=$1", scope.TenantRef).Scan(&count); err != nil {
					t.Fatal(err)
				}
				counts[table] += count
			}
		}
		return counts
	})
	contracttest.RunEpisodeEvidence(t, postgresIndexedContractFactory(databaseURL))
}

func postgresIndexedContractFactory(databaseURL string) contracttest.IndexedFactory {
	return func(t *testing.T, index memoryindex.Index) storage.CoreStore {
		t.Helper()
		return newPostgresContractStore(t, databaseURL, postgres.WithMemoryIndex(index))
	}
}

func newPostgresContractStore(t *testing.T, databaseURL string, options ...postgres.Option) storage.CoreStore {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	admin, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("connect PostgreSQL contract admin: %v", err)
	}
	schema := fmt.Sprintf("memory_contract_%d", contractSchemaSequence.Add(1))
	if _, err := admin.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		admin.Close()
		t.Fatalf("create PostgreSQL contract schema: %v", err)
	}
	parsed, err := url.Parse(databaseURL)
	if err != nil {
		admin.Close()
		t.Fatalf("parse PostgreSQL contract URL: %v", err)
	}
	query := parsed.Query()
	query.Set("search_path", schema)
	parsed.RawQuery = query.Encode()
	store, err := postgres.New(ctx, parsed.String(), options...)
	if err != nil {
		_, _ = admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
		admin.Close()
		t.Fatalf("open PostgreSQL contract Store: %v", err)
	}
	t.Cleanup(func() {
		_, dropErr := admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
		if dropErr != nil {
			t.Errorf("drop PostgreSQL contract schema: %v", dropErr)
		}
		admin.Close()
	})
	probe, err := pgxpool.New(ctx, parsed.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(probe.Close)
	return &adaptiveProbeStore{CoreStore: store, database: probe}
}

func postgresContractFactory(databaseURL string) contracttest.Factory {
	return func(t *testing.T) storage.CoreStore {
		t.Helper()
		return newPostgresContractStore(t, databaseURL)
	}
}
