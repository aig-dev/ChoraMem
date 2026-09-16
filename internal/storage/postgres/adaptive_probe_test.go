package postgres_test

import (
	"context"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/storage"
	"github.com/jackc/pgx/v5/pgxpool"
)

type adaptiveProbeStore struct {
	storage.CoreStore
	database *pgxpool.Pool
}

func (store *adaptiveProbeStore) AdaptiveJobUpdatedAt(t *testing.T) time.Time {
	t.Helper()
	var updatedAt time.Time
	if err := store.database.QueryRow(context.Background(), "SELECT updated_at FROM consolidation_jobs WHERE window_hash IS NULL AND completed_at IS NULL").Scan(&updatedAt); err != nil {
		t.Fatal(err)
	}
	return updatedAt
}

func (store *adaptiveProbeStore) AdaptiveCounts(t *testing.T, version, basis, text string) map[string]int {
	t.Helper()
	counts := make(map[string]int)
	for _, table := range []string{"disposition_seeds", "seed_versions", "memory_contexts", "memory_context_items", "memory_delivery_receipts", "outcome_events", "seed_outcome_basis_links"} {
		var count int
		if err := store.database.QueryRow(context.Background(), "SELECT count(*) FROM "+table).Scan(&count); err != nil {
			t.Fatal(err)
		}
		counts[table] = count
	}
	for key, query := range map[string]string{
		"feedback_basis": "SELECT count(*) FROM seed_basis_links WHERE role IN ('reenactment', 'inhibition')",
		"superseded":     "SELECT count(*) FROM seed_versions WHERE status = 'superseded'",
	} {
		var count int
		if err := store.database.QueryRow(context.Background(), query).Scan(&count); err != nil {
			t.Fatal(err)
		}
		counts[key] = count
	}
	var active, current int
	if err := store.database.QueryRow(context.Background(), "SELECT count(*) FROM seed_versions WHERE seed_version_ref = $1 AND tendency_text = $2 AND status = 'active'", version, text).Scan(&active); err != nil {
		t.Fatal(err)
	}
	if err := store.database.QueryRow(context.Background(), "SELECT count(*) FROM seed_basis_links WHERE seed_version_ref = $1 AND episode_ref = $2 AND role = 'revision'", version, basis).Scan(&current); err != nil {
		t.Fatal(err)
	}
	counts["active_target"], counts["current_revision_basis"] = active, current
	return counts
}

func (store *adaptiveProbeStore) AdaptiveFormationOutcomeCount(t *testing.T, version, outcome string) int {
	t.Helper()
	var count int
	if err := store.database.QueryRow(
		context.Background(),
		"SELECT count(*) FROM seed_outcome_basis_links WHERE seed_version_ref = $1 AND outcome_event_ref = $2 AND role = 'formation'",
		version,
		outcome,
	).Scan(&count); err != nil {
		t.Fatal(err)
	}
	return count
}
