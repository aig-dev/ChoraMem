package memoryindex_test

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/memoryindex"
)

func TestProjectorAcknowledgesStaleUpsertWithoutResurrection(t *testing.T) {
	queue := &fakeQueue{operations: []memoryindex.Operation{{
		ID: "op-1", StreamRef: "memory-1", Sequence: 1,
		Action: memoryindex.ActionUpsert, Kind: memoryindex.KindRecollection,
		Ref: "memory-1@1", Scope: memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent"},
	}}}
	index := &fakeIndex{}
	projector := memoryindex.NewProjector(queue, fakeSource{}, index, memoryindex.ProjectorConfig{})

	worked, err := projector.ProcessOne(context.Background())
	if err != nil || !worked {
		t.Fatalf("ProcessOne = (%v, %v); want stale acknowledgement", worked, err)
	}
	if !reflect.DeepEqual(queue.acknowledged, []string{"op-1"}) || len(index.upserts) != 0 {
		t.Fatalf("stale operation ack=%v upserts=%v", queue.acknowledged, index.upserts)
	}
}

func TestProjectorRejectsCanonicalDocumentOutsideFrozenOperation(t *testing.T) {
	operation := memoryindex.Operation{
		ID: "op-1", StreamRef: "memory-1", Sequence: 1, Action: memoryindex.ActionUpsert,
		Kind: memoryindex.KindRecollection, Ref: "memory-1@1",
		Scope: memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent"},
	}
	valid := memoryindex.Document{Kind: operation.Kind, Ref: operation.Ref, Scope: operation.Scope, Text: "private canonical text"}
	tests := []struct {
		name     string
		document memoryindex.Document
	}{
		{name: "kind", document: func() memoryindex.Document { value := valid; value.Kind = memoryindex.KindDisposition; return value }()},
		{name: "ref", document: func() memoryindex.Document { value := valid; value.Ref = "sibling@1"; return value }()},
		{name: "scope", document: func() memoryindex.Document { value := valid; value.Scope.TenantRef = "sibling"; return value }()},
		{name: "blank text", document: func() memoryindex.Document { value := valid; value.Text = "   "; return value }()},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			queue := &fakeQueue{operations: []memoryindex.Operation{operation}}
			index := &fakeIndex{}
			projector := memoryindex.NewProjector(
				queue, fakeSource{documents: map[string]memoryindex.Document{operation.Ref: test.document}},
				index, memoryindex.ProjectorConfig{},
			)
			if worked, err := projector.ProcessOne(context.Background()); err == nil || !worked {
				t.Fatalf("mismatched source ProcessOne = (%v, %v); want retryable isolation failure", worked, err)
			}
			if len(index.upserts) != 0 || !reflect.DeepEqual(queue.retried, []string{"op-1"}) {
				t.Fatalf("mismatched source upserts=%v retry=%v", index.upserts, queue.retried)
			}
		})
	}
}

func TestProjectorRetriesProviderFailureThenSucceeds(t *testing.T) {
	document := memoryindex.Document{
		Kind: memoryindex.KindDisposition, Ref: "memory-1@1",
		Scope: memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent"}, Text: "canonical",
	}
	queue := &fakeQueue{operations: []memoryindex.Operation{{
		ID: "op-1", StreamRef: "memory-1", Sequence: 1, Action: memoryindex.ActionUpsert,
		Kind: document.Kind, Ref: document.Ref, Scope: document.Scope,
	}}}
	index := &fakeIndex{upsertErrors: []error{errors.New("offline"), nil}}
	projector := memoryindex.NewProjector(queue, fakeSource{documents: map[string]memoryindex.Document{document.Ref: document}}, index, memoryindex.ProjectorConfig{})

	if worked, err := projector.ProcessOne(context.Background()); err == nil || !worked {
		t.Fatalf("offline ProcessOne = (%v, %v); want retryable failure", worked, err)
	}
	if !reflect.DeepEqual(queue.retried, []string{"op-1"}) || len(queue.acknowledged) != 0 {
		t.Fatalf("offline queue ack=%v retry=%v", queue.acknowledged, queue.retried)
	}
	if worked, err := projector.ProcessOne(context.Background()); err != nil || !worked {
		t.Fatalf("retry ProcessOne = (%v, %v)", worked, err)
	}
	if !reflect.DeepEqual(queue.acknowledged, []string{"op-1"}) || len(index.upserts) != 2 {
		t.Fatalf("retry queue ack=%v upserts=%v", queue.acknowledged, index.upserts)
	}
}

func TestProjectorDeleteUsesFrozenOperationAndRebuildUsesStablePages(t *testing.T) {
	scope := memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent", RelationshipRef: "relation"}
	queue := &fakeQueue{operations: []memoryindex.Operation{{
		ID: "delete-1", StreamRef: "memory-1", Sequence: 1,
		Action: memoryindex.ActionDelete, Kind: memoryindex.KindRecollection, Ref: "memory-1@1", Scope: scope,
	}}}
	documents := []memoryindex.Document{
		{Kind: memoryindex.KindEpisode, Ref: "episode-1", Scope: scope, Text: "episode"},
		{Kind: memoryindex.KindRecollection, Ref: "memory-1@2", Scope: scope, Text: "active"},
	}
	index := &fakeIndex{}
	projector := memoryindex.NewProjector(queue, fakeSource{pages: [][]memoryindex.Document{{documents[0]}, {documents[1]}}}, index, memoryindex.ProjectorConfig{PageSize: 1})

	if worked, err := projector.ProcessOne(context.Background()); err != nil || !worked {
		t.Fatalf("delete ProcessOne = (%v, %v)", worked, err)
	}
	if !reflect.DeepEqual(index.deletes, []deleteCall{{scope: scope, candidates: []memoryindex.Candidate{{Kind: memoryindex.KindRecollection, Ref: "memory-1@1"}}}}) {
		t.Fatalf("deletes = %#v", index.deletes)
	}
	if err := projector.Rebuild(context.Background()); err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	if index.resets != 1 || !reflect.DeepEqual(index.upserts[len(index.upserts)-2:], [][]memoryindex.Document{{documents[0]}, {documents[1]}}) {
		t.Fatalf("rebuild resets=%d upserts=%#v", index.resets, index.upserts)
	}
}

func TestProjectorRejectsInvalidFrozenDeleteBeforeProviderCall(t *testing.T) {
	valid := memoryindex.Operation{
		ID: "delete-1", StreamRef: "memory-1", Sequence: 1, Action: memoryindex.ActionDelete,
		Kind: memoryindex.KindDisposition, Ref: "memory-1@1",
		Scope: memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent"},
	}
	tests := []struct {
		name      string
		operation memoryindex.Operation
	}{
		{name: "kind", operation: func() memoryindex.Operation { value := valid; value.Kind = "UNKNOWN"; return value }()},
		{name: "ref", operation: func() memoryindex.Operation { value := valid; value.Ref = "bad ref"; return value }()},
		{name: "tenant", operation: func() memoryindex.Operation { value := valid; value.Scope.TenantRef = ""; return value }()},
		{name: "agent", operation: func() memoryindex.Operation { value := valid; value.Scope.AgentRef = "bad agent"; return value }()},
		{name: "relationship", operation: func() memoryindex.Operation {
			value := valid
			value.Scope.RelationshipRef = "bad relation"
			return value
		}()},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			queue := &fakeQueue{operations: []memoryindex.Operation{test.operation}}
			index := &fakeIndex{}
			projector := memoryindex.NewProjector(queue, fakeSource{}, index, memoryindex.ProjectorConfig{})
			if worked, err := projector.ProcessOne(context.Background()); err == nil || !worked {
				t.Fatalf("invalid DELETE ProcessOne = (%v, %v); want retryable validation error", worked, err)
			}
			if len(index.deletes) != 0 {
				t.Fatalf("invalid DELETE reached Provider: %#v", index.deletes)
			}
		})
	}
}

func TestProjectorRebuildRejectsInvalidCanonicalPageBeforeProviderWrite(t *testing.T) {
	valid := memoryindex.Document{Kind: memoryindex.KindRecollection, Ref: "memory-1@1", Scope: memoryindex.Scope{TenantRef: "tenant", AgentRef: "agent"}, Text: "canonical"}
	tests := []struct {
		name     string
		document memoryindex.Document
	}{
		{name: "kind", document: func() memoryindex.Document { value := valid; value.Kind = "UNKNOWN"; return value }()},
		{name: "ref", document: func() memoryindex.Document { value := valid; value.Ref = "bad ref"; return value }()},
		{name: "scope", document: func() memoryindex.Document { value := valid; value.Scope.AgentRef = ""; return value }()},
		{name: "text", document: func() memoryindex.Document { value := valid; value.Text = "   "; return value }()},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			index := &fakeIndex{}
			projector := memoryindex.NewProjector(&fakeQueue{}, fakeSource{pages: [][]memoryindex.Document{{test.document}}}, index, memoryindex.ProjectorConfig{})
			if err := projector.Rebuild(context.Background()); err == nil {
				t.Fatal("Rebuild accepted invalid canonical document")
			}
			if len(index.upserts) != 0 {
				t.Fatalf("invalid rebuild page reached Provider: %#v", index.upserts)
			}
		})
	}
}

func TestProjectorSerializesRebuildAgainstProcessOne(t *testing.T) {
	leaseStarted := make(chan struct{}, 1)
	queue := &fakeQueue{leaseStarted: leaseStarted}
	resetStarted := make(chan struct{})
	releaseReset := make(chan struct{})
	index := &fakeIndex{resetStarted: resetStarted, releaseReset: releaseReset}
	projector := memoryindex.NewProjector(queue, fakeSource{}, index, memoryindex.ProjectorConfig{})

	rebuildDone := make(chan error, 1)
	go func() { rebuildDone <- projector.Rebuild(context.Background()) }()
	<-resetStarted
	processDone := make(chan error, 1)
	go func() { _, err := projector.ProcessOne(context.Background()); processDone <- err }()
	select {
	case <-leaseStarted:
		t.Fatal("ProcessOne leased while Rebuild held projector serialization")
	case <-time.After(20 * time.Millisecond):
	}
	close(releaseReset)
	if err := <-rebuildDone; err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	if err := <-processDone; err != nil {
		t.Fatalf("ProcessOne: %v", err)
	}
	select {
	case <-leaseStarted:
	default:
		t.Fatal("ProcessOne did not resume after Rebuild")
	}
}

type fakeQueue struct {
	mu           sync.Mutex
	operations   []memoryindex.Operation
	next         int
	acknowledged []string
	retried      []string
	leaseStarted chan struct{}
}

func (queue *fakeQueue) LeaseMemoryIndexOperation(context.Context, time.Time) (memoryindex.Operation, bool, error) {
	queue.mu.Lock()
	defer queue.mu.Unlock()
	if queue.leaseStarted != nil {
		select {
		case queue.leaseStarted <- struct{}{}:
		default:
		}
	}
	if queue.next >= len(queue.operations) {
		return memoryindex.Operation{}, false, nil
	}
	operation := queue.operations[queue.next]
	queue.next++
	operation.LeaseToken = "lease"
	return operation, true, nil
}
func (queue *fakeQueue) AcknowledgeMemoryIndexOperation(_ context.Context, operation memoryindex.Operation) error {
	queue.acknowledged = append(queue.acknowledged, operation.ID)
	return nil
}
func (queue *fakeQueue) RetryMemoryIndexOperation(_ context.Context, operation memoryindex.Operation, _ time.Time, _ string) error {
	queue.retried = append(queue.retried, operation.ID)
	queue.next--
	return nil
}

type fakeSource struct {
	documents map[string]memoryindex.Document
	pages     [][]memoryindex.Document
}

func (source fakeSource) MemoryIndexDocument(_ context.Context, operation memoryindex.Operation) (memoryindex.Document, bool, error) {
	document, found := source.documents[operation.Ref]
	return document, found, nil
}
func (source fakeSource) EnumerateMemoryIndexDocuments(_ context.Context, cursor string, _ int) ([]memoryindex.Document, string, error) {
	page := 0
	if cursor != "" {
		page = int(cursor[0] - '0')
	}
	if page >= len(source.pages) {
		return nil, "", nil
	}
	next := ""
	if page+1 < len(source.pages) {
		next = string(rune('0' + page + 1))
	}
	return source.pages[page], next, nil
}

type deleteCall struct {
	scope      memoryindex.Scope
	candidates []memoryindex.Candidate
}

type fakeIndex struct {
	upserts      [][]memoryindex.Document
	deletes      []deleteCall
	resets       int
	upsertErrors []error
	resetStarted chan struct{}
	releaseReset chan struct{}
}

func (*fakeIndex) Search(context.Context, memoryindex.Query) ([]memoryindex.Candidate, error) {
	return nil, nil
}
func (index *fakeIndex) Upsert(_ context.Context, documents []memoryindex.Document) error {
	index.upserts = append(index.upserts, append([]memoryindex.Document(nil), documents...))
	if len(index.upsertErrors) == 0 {
		return nil
	}
	err := index.upsertErrors[0]
	index.upsertErrors = index.upsertErrors[1:]
	return err
}
func (index *fakeIndex) Delete(_ context.Context, scope memoryindex.Scope, candidates []memoryindex.Candidate) error {
	index.deletes = append(index.deletes, deleteCall{scope: scope, candidates: append([]memoryindex.Candidate(nil), candidates...)})
	return nil
}
func (index *fakeIndex) Reset(context.Context) error {
	if index.resetStarted != nil {
		close(index.resetStarted)
	}
	if index.releaseReset != nil {
		<-index.releaseReset
	}
	index.resets++
	return nil
}
