package postgres

import (
	"context"
	"github.com/aig-dev/ChoraMem/internal/storage"
	"time"
)

func (store *Store) QueueDiagnostics(ctx context.Context, quietBefore time.Time, maxEpisodes int) (storage.QueueDiagnostics, error) {
	var result storage.QueueDiagnostics
	result.ProjectionEnabled = store.memoryIndex != nil
	var err error
	result.Consolidation, err = storage.ScanQueueStats(store.pool.QueryRow(ctx, storage.ConsolidationDiagnosticsSQL, quietBefore, maxEpisodes).Scan)
	if err != nil {
		return result, err
	}
	result.Projection, err = storage.ScanQueueStats(store.pool.QueryRow(ctx, storage.ProjectionDiagnosticsSQL).Scan)
	return result, err
}
