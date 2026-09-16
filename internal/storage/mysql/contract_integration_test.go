package mysql_test

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/storage"
	"github.com/aig-dev/ChoraMem/internal/storage/contracttest"
	storemysql "github.com/aig-dev/ChoraMem/internal/storage/mysql"
	"github.com/aig-dev/ChoraMem/memoryindex"
	mysqldriver "github.com/go-sql-driver/mysql"
)

var contractDatabaseSequence atomic.Uint64

func TestCoreStoreContract(t *testing.T) {
	databaseURL := os.Getenv("MEMORY_TEST_MYSQL_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("MEMORY_TEST_MYSQL_DATABASE_URL is not set")
	}
	contracttest.Run(t, mysqlContractFactory(databaseURL))
	contracttest.RunAdaptiveSeeds(t, mysqlIndexedContractFactory(databaseURL))
	contracttest.RunConstitution(t, mysqlIndexedContractFactory(databaseURL))
	contracttest.RunLiveConstitutionHarnesses(t, mysqlIndexedContractFactory(databaseURL))
	contracttest.RunLiveAdaptiveWorker(t, mysqlIndexedContractFactory(databaseURL), func(t *testing.T, scope ledger.Scope) map[string]int {
		admin, err := sql.Open("mysql", databaseURL)
		if err != nil {
			t.Fatal(err)
		}
		defer admin.Close()
		rows, err := admin.Query("SELECT table_schema FROM information_schema.tables WHERE table_name='seed_versions' AND table_schema LIKE 'memory_contract_%'")
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
				if err := admin.QueryRow("SELECT COUNT(*) FROM `"+schema+"`.`"+table+"` WHERE tenant_ref=?", scope.TenantRef).Scan(&count); err != nil {
					t.Fatal(err)
				}
				counts[table] += count
			}
		}
		return counts
	})
	contracttest.RunEpisodeEvidence(t, mysqlIndexedContractFactory(databaseURL))
}

func mysqlIndexedContractFactory(databaseURL string) contracttest.IndexedFactory {
	return func(t *testing.T, index memoryindex.Index) storage.CoreStore {
		t.Helper()
		dsn := isolatedMySQLDSN(t, databaseURL)
		store, err := storemysql.New(context.Background(), dsn, storemysql.WithMemoryIndex(index))
		if err != nil {
			t.Fatalf("open MySQL contract Store: %v", err)
		}
		probe, err := sql.Open("mysql", dsn)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = probe.Close() })
		return &adaptiveProbeStore{CoreStore: store, database: probe}
	}
}

func mysqlContractFactory(databaseURL string) contracttest.Factory {
	return func(t *testing.T) storage.CoreStore {
		t.Helper()
		dsn := isolatedMySQLDSN(t, databaseURL)
		store, err := storemysql.New(context.Background(), dsn)
		if err != nil {
			t.Fatalf("open MySQL contract Store: %v", err)
		}
		return store
	}
}

func isolatedMySQLDSN(t *testing.T, databaseURL string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	config, err := mysqldriver.ParseDSN(databaseURL)
	if err != nil {
		t.Fatalf("parse MySQL test DSN: %v", err)
	}
	config.ParseTime = true
	config.MultiStatements = true
	config.Loc = time.UTC
	adminConfig := config.Clone()
	adminConfig.DBName = ""
	admin, err := sql.Open("mysql", adminConfig.FormatDSN())
	if err != nil {
		t.Fatalf("open MySQL test admin: %v", err)
	}
	if err := admin.PingContext(ctx); err != nil {
		admin.Close()
		t.Fatalf("ping MySQL test admin: %v", err)
	}
	database := fmt.Sprintf("memory_contract_%d", contractDatabaseSequence.Add(1))
	if _, err := admin.ExecContext(ctx, "CREATE DATABASE `"+database+"` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"); err != nil {
		admin.Close()
		t.Fatalf("create MySQL test database: %v", err)
	}
	config.DBName = database
	t.Cleanup(func() {
		_, dropErr := admin.ExecContext(context.Background(), "DROP DATABASE `"+database+"`")
		if dropErr != nil {
			t.Errorf("drop MySQL test database: %v", dropErr)
		}
		admin.Close()
	})
	return config.FormatDSN()
}
