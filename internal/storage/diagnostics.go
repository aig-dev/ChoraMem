package storage

import (
	"database/sql"
	"time"
)

// QueueStats is an operator snapshot, never memory evidence or authority.
type QueueStats struct {
	Pending             int64   `json:"pending"`
	Due                 int64   `json:"due"`
	InFlight            int64   `json:"in_flight"`
	MaxRetries          int64   `json:"max_retries"`
	OldestDueAgeSeconds float64 `json:"oldest_due_age_seconds"`
}

type QueueDiagnostics struct {
	WorkerReady       bool       `json:"worker_ready"`
	ProjectionEnabled bool       `json:"projection_enabled"`
	Consolidation     QueueStats `json:"consolidation"`
	Projection        QueueStats `json:"projection"`
}

// These aggregate-only queries work with both relational adapters. Attempts
// increments on consolidation lease; the first attempt is not a retry.
const ConsolidationDiagnosticsSQL = `WITH pending AS (
 SELECT *, (not_before <= CURRENT_TIMESTAMP AND
  (window_hash IS NOT NULL OR updated_at <= $1 OR cardinality(episode_refs) >= $2)) AS time_ready
 FROM consolidation_jobs WHERE completed_at IS NULL
 ) SELECT COUNT(*),
 COALESCE(SUM(CASE WHEN time_ready AND (lease_until IS NULL OR lease_until <= CURRENT_TIMESTAMP) THEN 1 ELSE 0 END), 0),
 COALESCE(SUM(CASE WHEN lease_until > CURRENT_TIMESTAMP THEN 1 ELSE 0 END), 0),
 COALESCE(MAX(CASE WHEN lease_until > CURRENT_TIMESTAMP AND attempts > 0 THEN attempts - 1 ELSE attempts END), 0),
 MIN(CASE WHEN time_ready AND (lease_until IS NULL OR lease_until <= CURRENT_TIMESTAMP) THEN created_at END)
 FROM pending`

const ProjectionDiagnosticsSQL = `SELECT COUNT(*),
 COALESCE(SUM(CASE WHEN not_before <= CURRENT_TIMESTAMP AND (lease_until IS NULL OR lease_until <= CURRENT_TIMESTAMP) THEN 1 ELSE 0 END), 0),
 COALESCE(SUM(CASE WHEN lease_until > CURRENT_TIMESTAMP THEN 1 ELSE 0 END), 0),
 COALESCE(MAX(attempt_count), 0),
 MIN(CASE WHEN not_before <= CURRENT_TIMESTAMP AND (lease_until IS NULL OR lease_until <= CURRENT_TIMESTAMP) THEN created_at END)
 FROM memory_index_operations WHERE acknowledged_at IS NULL`

func ScanQueueStats(scan func(...any) error) (QueueStats, error) {
	var stats QueueStats
	var oldest sql.NullTime
	if err := scan(&stats.Pending, &stats.Due, &stats.InFlight, &stats.MaxRetries, &oldest); err != nil {
		return QueueStats{}, err
	}
	if oldest.Valid {
		stats.OldestDueAgeSeconds = max(0, time.Since(oldest.Time).Seconds())
	}
	return stats, nil
}
