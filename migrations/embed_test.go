package migrations

import (
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

var v1TableNames = []string{
	"consolidation_jobs",
	"consolidation_receipts",
	"disposition_seeds",
	"episode_links",
	"episodes",
	"memory_context_items",
	"memory_contexts",
	"memory_delivery_receipts",
	"memory_index_operations",
	"outcome_events",
	"recollection_basis_links",
	"recollection_versions",
	"recollections",
	"request_receipts",
	"seed_basis_links",
	"seed_outcome_basis_links",
	"seed_versions",
	"source_events",
}

func TestV1SchemasDefineExactlyTheActiveCausalTables(t *testing.T) {
	t.Parallel()

	for name, schema := range map[string]string{
		"postgres": PostgresV1SQL,
		"mysql":    MySQLV1SQL,
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			got := schemaTableNames(schema)
			if !reflect.DeepEqual(got, v1TableNames) {
				t.Fatalf("tables = %q; want %q", got, v1TableNames)
			}
			for _, legacy := range []string{
				"persona_contexts",
				"seed_activations",
				"context_delivery_receipts",
			} {
				if strings.Contains(strings.ToLower(schema), legacy) {
					t.Fatalf("clean V1 schema still contains legacy table %q", legacy)
				}
			}
		})
	}
}

func TestPostgresV1SchemaRetainsCanonicalContracts(t *testing.T) {
	t.Parallel()

	for _, fragment := range []string{
		"request_hash bytea NOT NULL",
		"created_at timestamptz NOT NULL DEFAULT now()",
		"delivered_memory_refs text[] NOT NULL",
		"CREATE UNIQUE INDEX IF NOT EXISTS recollection_versions_one_active_uq",
		"WHERE status = 'active'",
	} {
		if !strings.Contains(PostgresV1SQL, fragment) {
			t.Fatalf("PostgresV1SQL is missing %q", fragment)
		}
	}
}

func TestMySQLV1SchemaUsesMySQL8StoragePrimitives(t *testing.T) {
	t.Parallel()

	for _, fragment := range []string{
		"ENGINE=InnoDB",
		"DATETIME(6)",
		"BINARY(32)",
		"JSON NOT NULL",
		"GENERATED ALWAYS AS",
	} {
		if !strings.Contains(MySQLV1SQL, fragment) {
			t.Fatalf("MySQLV1SQL is missing %q", fragment)
		}
	}
}

func TestSeedOutcomeBasisSchemasAllowFormationProvenance(t *testing.T) {
	t.Parallel()
	for name, schema := range map[string]string{
		"postgres": PostgresV1SQL + PostgresDispositionFormationOutcomeSQL,
		"mysql":    MySQLV1SQL + MySQLDispositionFormationOutcomeSQL,
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			if !strings.Contains(schema, "'formation', 'revision', 'inhibition'") {
				t.Fatal("Seed Outcome Basis schema does not allow formation provenance")
			}
		})
	}
}

func TestEpisodeEvidenceMigrationsAreAdditiveSingleTableSchemas(t *testing.T) {
	t.Parallel()
	for name, schema := range map[string]string{
		"postgres": PostgresEpisodeEvidenceSQL,
		"mysql":    MySQLEpisodeEvidenceSQL,
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			want := []string{"memory_context_episode_evidence"}
			if got := schemaTableNames(schema); !reflect.DeepEqual(got, want) {
				t.Fatalf("Episode evidence migration tables = %q; want %q", got, want)
			}
			if strings.Contains(strings.ToLower(schema), "alter table") || strings.Contains(strings.ToLower(schema), "drop table") {
				t.Fatal("Episode evidence migration must only add its projection table")
			}
		})
	}
}

func schemaTableNames(schema string) []string {
	re := regexp.MustCompile(`(?i)CREATE TABLE(?: IF NOT EXISTS)?\s+([a-z_]+)`)
	matches := re.FindAllStringSubmatch(schema, -1)
	names := make([]string, 0, len(matches))
	for _, match := range matches {
		names = append(names, strings.ToLower(match[1]))
	}
	sort.Strings(names)
	return names
}
