package postgres_test

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

func TestEpisodeProjectionEnqueuesMaterializationAndLaterSourceChangeExactlyOnce(t *testing.T) {
	harness := newHarness(t)
	situation := harness.source("projection-situation")
	binding := ledger.EpisodeBinding{RunRef: "projection-run", SourceGroupRef: "projection-group", Role: ledger.RoleSituation}
	harness.observe(t, "projection-situation", situation, binding)
	act := harness.agentSource("projection-act")
	binding.Role = ledger.RoleAgentAct
	episodeRef := harness.observe(t, "projection-act", act, binding).EpisodeRef

	operations := loadProjectionOperations(t, harness)
	if len(operations) != 1 || operations[0].action != memoryindex.ActionUpsert ||
		operations[0].kind != memoryindex.KindEpisode || operations[0].ref != episodeRef || operations[0].sequence != 1 {
		t.Fatalf("materialization operations = %#v", operations)
	}
	if _, err := harness.store.Observe(context.Background(), "projection-act", act, binding); err != nil {
		t.Fatalf("idempotent Observe: %v", err)
	}
	if got := len(loadProjectionOperations(t, harness)); got != 1 {
		t.Fatalf("idempotent replay operations = %d; want 1", got)
	}

	outcome := harness.source("projection-outcome")
	binding.Role = ledger.RoleOutcome
	harness.observe(t, "projection-outcome", outcome, binding)
	operations = loadProjectionOperations(t, harness)
	if len(operations) != 2 || operations[1].ref != episodeRef || operations[1].sequence != 2 {
		t.Fatalf("late source operations = %#v", operations)
	}
}

func TestEpisodeProjectionIdentityIncludesTypedLinkRole(t *testing.T) {
	harness := newHarness(t)
	binding := ledger.EpisodeBinding{RunRef: "typed-link-run", SourceGroupRef: "typed-link-group", Role: ledger.RoleSituation}
	harness.observe(t, "typed-link-situation", harness.source("typed-link-situation"), binding)
	shared := harness.agentSource("typed-link-shared-source")
	binding.Role = ledger.RoleAgentAct
	episodeRef := harness.observe(t, "typed-link-agent-act", shared, binding).EpisodeRef
	if episodeRef == "" {
		t.Fatal("Agent act did not materialize Episode")
	}

	index := &projectionRecordingIndex{}
	projector := memoryindex.NewProjector(harness.store, harness.store, index, memoryindex.ProjectorConfig{})
	if worked, err := projector.ProcessOne(context.Background()); err != nil || !worked {
		t.Fatalf("project materialized Episode = (%v, %v)", worked, err)
	}
	if len(index.documents) != 1 || strings.Contains(index.documents[0].Text, "OUTCOME\nSOURCE "+shared.Ref) {
		t.Fatalf("initial Episode projection = %#v", index.documents)
	}

	binding.Role = ledger.RoleOutcome
	if receipt := harness.observe(t, "typed-link-outcome", shared, binding); receipt.EpisodeRef != episodeRef {
		t.Fatalf("same source Outcome Episode = %q; want %q", receipt.EpisodeRef, episodeRef)
	}
	operations := loadProjectionOperations(t, harness)
	if len(operations) != 2 || operations[0].stream != operations[1].stream ||
		operations[0].sequence != 1 || operations[1].sequence != 2 ||
		operations[1].action != memoryindex.ActionUpsert || operations[1].ref != episodeRef {
		t.Fatalf("typed-link projection operations = %#v", operations)
	}

	if worked, err := projector.ProcessOne(context.Background()); err != nil || !worked {
		t.Fatalf("project changed Episode = (%v, %v)", worked, err)
	}
	if len(index.documents) != 2 || !strings.Contains(index.documents[1].Text, "OUTCOME\nSOURCE "+shared.Ref) {
		t.Fatalf("changed Episode projection = %#v", index.documents)
	}
	if _, err := harness.store.Observe(context.Background(), "typed-link-outcome", shared, binding); err != nil {
		t.Fatalf("exact typed-link replay: %v", err)
	}
	if _, err := harness.store.Observe(context.Background(), "typed-link-outcome-replayed-key", shared, binding); err != nil {
		t.Fatalf("typed-link replay with fresh request key: %v", err)
	}
	if got := len(loadProjectionOperations(t, harness)); got != 2 {
		t.Fatalf("typed-link replay projection operations = %d; want 2", got)
	}
}

func TestMemoryIndexLeaseCannotOvertakeEarlierUnfinishedSameStream(t *testing.T) {
	harness := newHarness(t)
	for sequence, id := range []string{"op-first", "op-second"} {
		if _, err := harness.inspectionDB.Exec(context.Background(), `
			INSERT INTO memory_index_operations (
				operation_id, stream_ref, stream_sequence, operation_kind,
				document_kind, document_ref, tenant_ref, agent_ref
			) VALUES ($1, 'stream-one', $2, 'UPSERT', 'EPISODE', $3, $4, 'agent-1')
		`, id, sequence+1, "episode-"+id, harness.tenant); err != nil {
			t.Fatalf("insert operation: %v", err)
		}
	}

	first, found, err := harness.store.LeaseMemoryIndexOperation(context.Background(), time.Now().Add(time.Minute))
	if err != nil || !found || first.ID != "op-first" {
		t.Fatalf("first lease = (%#v, %v, %v)", first, found, err)
	}
	if second, found, err := harness.store.LeaseMemoryIndexOperation(context.Background(), time.Now().Add(time.Minute)); err != nil || found {
		t.Fatalf("overtaking lease = (%#v, %v, %v); want empty", second, found, err)
	}
	if err := harness.store.AcknowledgeMemoryIndexOperation(context.Background(), first); err != nil {
		t.Fatalf("ack first: %v", err)
	}
	second, found, err := harness.store.LeaseMemoryIndexOperation(context.Background(), time.Now().Add(time.Minute))
	if err != nil || !found || second.ID != "op-second" {
		t.Fatalf("second lease after ack = (%#v, %v, %v)", second, found, err)
	}
}

func TestCanonicalMemoryIndexEnumerationIsStablePaginatedAndRebuildOnlyResetsProvider(t *testing.T) {
	harness := newHarness(t)
	episode := harness.materializeEpisode(t, "enumerate", "session-enumerate")
	disposition := harness.formSeed(t, "enumerate", "index this disposition", "relationship-1")
	recollectionReceipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "enumerate-recollection", EpisodeRefs: []string{episode},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationSituation,
		consolidation.ChangeText, "index this recollection", episode,
	)})
	if err != nil || len(recollectionReceipt.RecollectionVersionRefs) != 1 {
		t.Fatalf("form Recollection = (%#v, %v)", recollectionReceipt, err)
	}
	recollection := recollectionReceipt.RecollectionVersionRefs[0]

	var documents []memoryindex.Document
	cursor := ""
	for {
		page, next, err := harness.store.EnumerateMemoryIndexDocuments(context.Background(), cursor, 1)
		if err != nil {
			t.Fatalf("enumerate page: %v", err)
		}
		if len(page) > 1 {
			t.Fatalf("page exceeded limit: %#v", page)
		}
		documents = append(documents, page...)
		if next == "" {
			break
		}
		if next == cursor {
			t.Fatalf("cursor did not advance: %q", cursor)
		}
		cursor = next
	}
	refs := make(map[string]memoryindex.Document, len(documents))
	for _, document := range documents {
		refs[document.Ref] = document
	}
	for _, ref := range []string{episode, disposition, recollection} {
		if _, exists := refs[ref]; !exists {
			t.Fatalf("enumeration omitted %s: %#v", ref, documents)
		}
	}
	if episodeDocument := refs[episode]; episodeDocument.Kind != memoryindex.KindEpisode ||
		!strings.Contains(episodeDocument.Text, "SITUATION") || !strings.Contains(episodeDocument.Text, "AGENT_ACT") {
		t.Fatalf("Episode document = %#v", episodeDocument)
	}
	again, found, err := harness.store.MemoryIndexDocument(context.Background(), memoryindex.Operation{
		Kind: memoryindex.KindEpisode, Ref: episode, Scope: refs[episode].Scope,
	})
	if err != nil || !found || again != refs[episode] {
		t.Fatalf("deterministic Episode rehydrate = (%#v, %v, %v); want %#v", again, found, err, refs[episode])
	}
	wrongOwner := refs[episode].Scope
	wrongOwner.TenantRef = "sibling-tenant"
	if document, found, err := harness.store.MemoryIndexDocument(context.Background(), memoryindex.Operation{
		Kind: memoryindex.KindEpisode, Ref: episode, Scope: wrongOwner,
	}); err != nil || found {
		t.Fatalf("wrong-owner Episode rehydrate = (%#v, %v, %v); want discarded", document, found, err)
	}

	canonicalBefore := []int{
		harness.count(t, "episodes", "TRUE", nil),
		harness.count(t, "recollection_versions", "TRUE", nil),
		harness.count(t, "seed_versions", "TRUE", nil),
	}
	index := &projectionRecordingIndex{}
	projector := memoryindex.NewProjector(harness.store, harness.store, index, memoryindex.ProjectorConfig{PageSize: 1})
	if err := projector.Rebuild(context.Background()); err != nil {
		t.Fatalf("Rebuild: %v", err)
	}
	if index.resets != 1 || len(index.documents) != len(documents) {
		t.Fatalf("rebuilt provider resets=%d documents=%#v", index.resets, index.documents)
	}
	canonicalAfter := []int{
		harness.count(t, "episodes", "TRUE", nil),
		harness.count(t, "recollection_versions", "TRUE", nil),
		harness.count(t, "seed_versions", "TRUE", nil),
	}
	if !reflect.DeepEqual(canonicalAfter, canonicalBefore) {
		t.Fatalf("rebuild mutated canonical tables: before=%v after=%v", canonicalBefore, canonicalAfter)
	}
}

func TestOfflineMemoryIndexRetriesOnlyProjectionAndLaterSucceeds(t *testing.T) {
	harness := newHarness(t)
	episode := harness.materializeEpisode(t, "offline-projection", "session-offline-projection")
	if count := harness.count(t, "episodes", "episode_ref = $2", episode); count != 1 {
		t.Fatalf("canonical Episode commit = %d; want 1 before Provider execution", count)
	}
	index := &projectionRecordingIndex{upsertErrors: []error{errors.New("Provider offline"), nil}}
	projector := memoryindex.NewProjector(harness.store, harness.store, index, memoryindex.ProjectorConfig{
		LeaseDuration: time.Minute, RetryDelay: time.Nanosecond,
	})
	if worked, err := projector.ProcessOne(context.Background()); err == nil || !worked {
		t.Fatalf("offline projection = (%v, %v); want retryable failure", worked, err)
	}
	var attempts int
	var acknowledged bool
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT attempt_count, acknowledged_at IS NOT NULL
		FROM memory_index_operations WHERE tenant_ref = $1
	`, harness.tenant).Scan(&attempts, &acknowledged); err != nil {
		t.Fatalf("inspect retryable projection: %v", err)
	}
	if attempts != 1 || acknowledged || len(index.documents) != 0 {
		t.Fatalf("offline operation attempts=%d acknowledged=%v documents=%v", attempts, acknowledged, index.documents)
	}
	deadline := time.Now().Add(250 * time.Millisecond)
	for {
		worked, err := projector.ProcessOne(context.Background())
		if err != nil {
			t.Fatalf("projection retry: %v", err)
		}
		if worked {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("projection retry did not become ready")
		}
		time.Sleep(time.Millisecond)
	}
	if len(index.documents) != 1 || index.documents[0].Ref != episode {
		t.Fatalf("projection retry documents = %#v", index.documents)
	}
	if worked, err := projector.ProcessOne(context.Background()); err != nil || worked {
		t.Fatalf("acknowledged replay = (%v, %v); want no duplicate work", worked, err)
	}
}

type projectionRecordingIndex struct {
	resets       int
	documents    []memoryindex.Document
	upsertErrors []error
}

func (*projectionRecordingIndex) Search(context.Context, memoryindex.Query) ([]memoryindex.Candidate, error) {
	return nil, nil
}
func (index *projectionRecordingIndex) Upsert(_ context.Context, documents []memoryindex.Document) error {
	if len(index.upsertErrors) > 0 {
		err := index.upsertErrors[0]
		index.upsertErrors = index.upsertErrors[1:]
		if err != nil {
			return err
		}
	}
	index.documents = append(index.documents, documents...)
	return nil
}
func (*projectionRecordingIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (index *projectionRecordingIndex) Reset(context.Context) error {
	index.resets++
	index.documents = nil
	return nil
}

type projectionOperation struct {
	id       string
	stream   string
	action   memoryindex.Action
	kind     memoryindex.Kind
	ref      string
	sequence int64
}

func loadProjectionOperations(t *testing.T, harness *testHarness) []projectionOperation {
	t.Helper()
	rows, err := harness.inspectionDB.Query(context.Background(), `
		SELECT operation_id, stream_ref, operation_kind, document_kind, document_ref, stream_sequence
		FROM memory_index_operations
		WHERE tenant_ref = $1
		ORDER BY stream_ref, stream_sequence, created_at, operation_id
	`, harness.tenant)
	if err != nil {
		t.Fatalf("load projection operations: %v", err)
	}
	defer rows.Close()
	var result []projectionOperation
	for rows.Next() {
		var operation projectionOperation
		if err := rows.Scan(&operation.id, &operation.stream, &operation.action, &operation.kind, &operation.ref, &operation.sequence); err != nil {
			t.Fatalf("scan projection operation: %v", err)
		}
		result = append(result, operation)
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("iterate projection operations: %v", err)
	}
	return result
}

func projectionOperationsForKind(t *testing.T, harness *testHarness, kind memoryindex.Kind) []projectionOperation {
	t.Helper()
	var result []projectionOperation
	for _, operation := range loadProjectionOperations(t, harness) {
		if operation.kind == kind {
			result = append(result, operation)
		}
	}
	return result
}
