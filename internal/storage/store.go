// Package storage freezes the process-local relational authority required by
// Memory Core. Transport, inference, and semantic indexes depend on narrower
// ports; memoryd alone needs the complete store during composition.
package storage

import (
	"context"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

// CoreStore is the smallest complete authority implemented by every
// relational adapter. It contains only operations that change or serve the
// causal lifecycle, scheduler, or disposable MemoryIndex projection.
type CoreStore interface {
	Observe(context.Context, string, ledger.SourceEvent, ledger.EpisodeBinding) (ledger.ObserveReceipt, error)
	SelectMemory(context.Context, selection.SelectRequest) (selection.MemoryContext, error)
	RecordMemoryDelivery(context.Context, ledger.MemoryDelivery) (ledger.MemoryDeliveryReceipt, error)
	ReportOutcome(context.Context, ledger.OutcomeReport) (ledger.OutcomeReceipt, error)

	LeaseConsolidationJob(context.Context, scheduler.LeaseRequest) (scheduler.Job, bool, error)
	CompleteConsolidationJob(context.Context, string, string) error
	RetryConsolidationJob(context.Context, string, string, time.Time) error
	ConsolidateWindow(context.Context, consolidation.Window, consolidation.Worker) (consolidation.Receipt, error)

	LeaseMemoryIndexOperation(context.Context, time.Time) (memoryindex.Operation, bool, error)
	AcknowledgeMemoryIndexOperation(context.Context, memoryindex.Operation) error
	RetryMemoryIndexOperation(context.Context, memoryindex.Operation, time.Time, string) error
	MemoryIndexDocument(context.Context, memoryindex.Operation) (memoryindex.Document, bool, error)
	EnumerateMemoryIndexDocuments(context.Context, string, int) ([]memoryindex.Document, string, error)

	Ping(context.Context) error
	Migrate(context.Context) error
	Close()
}
