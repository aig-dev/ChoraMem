package memoryindex

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode"
)

// Action identifies the only disposable-index mutations carried by the queue.
type Action string

const (
	ActionUpsert Action = "UPSERT"
	ActionDelete Action = "DELETE"
)

// Operation is one frozen, ordered projection mutation. It intentionally has
// no document text: UPSERT text must be rehydrated from canonical Memory.
type Operation struct {
	ID         string
	StreamRef  string
	Sequence   int64
	Action     Action
	Kind       Kind
	Ref        string
	Scope      Scope
	LeaseToken string
}

// OperationQueue owns durable leasing, retry, and acknowledgement.
type OperationQueue interface {
	LeaseMemoryIndexOperation(context.Context, time.Time) (Operation, bool, error)
	AcknowledgeMemoryIndexOperation(context.Context, Operation) error
	RetryMemoryIndexOperation(context.Context, Operation, time.Time, string) error
}

// CanonicalSource rehydrates one queued operation and enumerates the complete
// disposable projection through an opaque stable cursor.
type CanonicalSource interface {
	MemoryIndexDocument(context.Context, Operation) (Document, bool, error)
	EnumerateMemoryIndexDocuments(context.Context, string, int) ([]Document, string, error)
}

type ProjectorConfig struct {
	LeaseDuration time.Duration
	RetryDelay    time.Duration
	PollInterval  time.Duration
	PageSize      int
	OnError       func(error)
}

func DefaultProjectorConfig() ProjectorConfig {
	return ProjectorConfig{
		LeaseDuration: 30 * time.Second,
		RetryDelay:    5 * time.Second,
		PollInterval:  time.Second,
		PageSize:      128,
	}
}

// Projector independently reconciles canonical Memory into a disposable
// semantic index. ProcessOne and Rebuild serialize within this process.
type Projector struct {
	queue  OperationQueue
	source CanonicalSource
	index  Index
	config ProjectorConfig
	mu     sync.Mutex
}

func NewProjector(queue OperationQueue, source CanonicalSource, index Index, config ProjectorConfig) *Projector {
	defaults := DefaultProjectorConfig()
	if config.LeaseDuration <= 0 {
		config.LeaseDuration = defaults.LeaseDuration
	}
	if config.RetryDelay <= 0 {
		config.RetryDelay = defaults.RetryDelay
	}
	if config.PollInterval <= 0 {
		config.PollInterval = defaults.PollInterval
	}
	if config.PageSize <= 0 {
		config.PageSize = defaults.PageSize
	}
	return &Projector{queue: queue, source: source, index: index, config: config}
}

func (projector *Projector) ProcessOne(ctx context.Context) (bool, error) {
	if projector == nil || projector.queue == nil || projector.source == nil || projector.index == nil {
		return false, errors.New("MemoryIndex projector dependencies are required")
	}
	projector.mu.Lock()
	defer projector.mu.Unlock()

	operation, found, err := projector.queue.LeaseMemoryIndexOperation(ctx, time.Now().Add(projector.config.LeaseDuration))
	if err != nil || !found {
		return false, err
	}
	if err := projector.apply(ctx, operation); err != nil {
		retryErr := projector.queue.RetryMemoryIndexOperation(
			ctx, operation, time.Now().Add(projector.config.RetryDelay), err.Error(),
		)
		if retryErr != nil {
			return true, errors.Join(err, retryErr)
		}
		return true, err
	}
	if err := projector.queue.AcknowledgeMemoryIndexOperation(ctx, operation); err != nil {
		return true, err
	}
	return true, nil
}

func (projector *Projector) apply(ctx context.Context, operation Operation) error {
	switch operation.Action {
	case ActionUpsert:
		document, found, err := projector.source.MemoryIndexDocument(ctx, operation)
		if err != nil || !found {
			return err
		}
		if document.Kind != operation.Kind || document.Ref != operation.Ref ||
			document.Scope != operation.Scope || !validDocument(document) {
			return errors.New("canonical MemoryIndex document does not match frozen operation")
		}
		return projector.index.Upsert(ctx, []Document{document})
	case ActionDelete:
		if !validOperationIdentity(operation) {
			return errors.New("frozen MemoryIndex DELETE operation is invalid")
		}
		return projector.index.Delete(ctx, operation.Scope, []Candidate{{Kind: operation.Kind, Ref: operation.Ref}})
	default:
		return fmt.Errorf("unknown MemoryIndex operation %q", operation.Action)
	}
}

// Rebuild resets only the disposable provider, then copies stable canonical
// pages. Durable queued deltas remain untouched and reconcile after this lock.
func (projector *Projector) Rebuild(ctx context.Context) error {
	if projector == nil || projector.source == nil || projector.index == nil {
		return errors.New("MemoryIndex projector dependencies are required")
	}
	projector.mu.Lock()
	defer projector.mu.Unlock()

	if err := projector.index.Reset(ctx); err != nil {
		return fmt.Errorf("reset MemoryIndex: %w", err)
	}
	cursor := ""
	for {
		documents, next, err := projector.source.EnumerateMemoryIndexDocuments(ctx, cursor, projector.config.PageSize)
		if err != nil {
			return fmt.Errorf("enumerate canonical MemoryIndex documents: %w", err)
		}
		if len(documents) > 0 {
			for _, document := range documents {
				if !validDocument(document) {
					return errors.New("canonical MemoryIndex enumeration returned an invalid document")
				}
			}
			if err := projector.index.Upsert(ctx, documents); err != nil {
				return fmt.Errorf("rebuild MemoryIndex page: %w", err)
			}
		}
		if next == "" {
			return nil
		}
		if next == cursor {
			return errors.New("canonical MemoryIndex enumeration did not advance")
		}
		cursor = next
	}
}

func validOperationIdentity(operation Operation) bool {
	validKind := operation.Kind == KindEpisode || operation.Kind == KindRecollection || operation.Kind == KindDisposition
	return validKind && validProjectionRef(operation.Ref, false) &&
		validProjectionRef(operation.Scope.TenantRef, false) && validProjectionRef(operation.Scope.AgentRef, false) &&
		validProjectionRef(operation.Scope.RelationshipRef, true)
}

func validDocument(document Document) bool {
	validKind := document.Kind == KindEpisode || document.Kind == KindRecollection || document.Kind == KindDisposition
	return validKind && validProjectionRef(document.Ref, false) &&
		validProjectionRef(document.Scope.TenantRef, false) && validProjectionRef(document.Scope.AgentRef, false) &&
		validProjectionRef(document.Scope.RelationshipRef, true) && strings.TrimSpace(document.Text) != ""
}

func validProjectionRef(ref string, emptyAllowed bool) bool {
	return (emptyAllowed || ref != "") && strings.IndexFunc(ref, unicode.IsSpace) == -1
}

func (projector *Projector) Run(ctx context.Context) error {
	ticker := time.NewTicker(projector.config.PollInterval)
	defer ticker.Stop()
	for {
		worked, err := projector.ProcessOne(ctx)
		if err != nil && !errors.Is(err, context.Canceled) && projector.config.OnError != nil {
			projector.config.OnError(err)
		}
		if worked {
			continue
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}
