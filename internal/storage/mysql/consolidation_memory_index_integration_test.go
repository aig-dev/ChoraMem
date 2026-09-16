package mysql_test

import (
	"context"
	"database/sql"
	"os"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	storemysql "github.com/aig-dev/ChoraMem/internal/storage/mysql"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

func TestMySQLConsolidationUsesIndexedEpisodeAsCrossWindowFormationEvidence(t *testing.T) {
	baseURL := testMySQLDatabaseURL(t)
	dsn := isolatedMySQLDSN(t, baseURL)
	index := &mysqlConsolidationIndex{}
	store, err := storemysql.New(context.Background(), dsn, storemysql.WithMemoryIndex(index))
	if err != nil {
		t.Fatalf("open MySQL Store: %v", err)
	}
	t.Cleanup(store.Close)
	if err := store.Migrate(context.Background()); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	oldEpisode := materializeMySQLConsolidationEpisode(t, store, "old", "旧对话里，用户要求先给结论")
	currentText := "用户再次要求先给结论，再解释细节"
	index.search = func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" && query.Text == currentText {
			return []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: oldEpisode}}, nil
		}
		return nil, nil
	}
	currentEpisode := materializeMySQLConsolidationEpisode(t, store, "current", currentText)
	worker := &mysqlConsolidationWorker{taggedText: mysqlTaggedMemoryChange(
		consolidation.TargetNewDisposition,
		consolidation.ApplicationRelation,
		consolidation.ChangeText,
		"面对明确请求时，我会先给结论",
		oldEpisode,
		currentEpisode,
	)}

	receipt, err := store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "mysql-index-cross-window-form", EpisodeRefs: []string{currentEpisode},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if !strings.Contains(worker.request.WindowText, "RELATED_EPISODE "+oldEpisode+"\n") {
		t.Fatalf("indexed prior Episode missing from MySQL Worker evidence:\n%s", worker.request.WindowText)
	}
	for _, ref := range []string{oldEpisode, currentEpisode} {
		if !slices.Contains(worker.request.AllowedBasisRefs, ref) {
			t.Fatalf("Episode %s missing from allowed Basis %#v", ref, worker.request.AllowedBasisRefs)
		}
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("cross-window formation receipt = %#v; want one Disposition", receipt)
	}

	database, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatalf("open MySQL inspection database: %v", err)
	}
	defer database.Close()
	var basisCount int
	if err := database.QueryRow(`
		SELECT count(*) FROM seed_basis_links
		WHERE tenant_ref = ? AND seed_version_ref = ?
		  AND JSON_CONTAINS(JSON_ARRAY(?, ?), JSON_QUOTE(CONVERT(episode_ref USING utf8mb4)))
		  AND role = 'formation'
	`, "tenant-index-consolidation", receipt.DispositionVersionRefs[0], oldEpisode, currentEpisode).Scan(&basisCount); err != nil {
		t.Fatalf("count cross-window formation Basis: %v", err)
	}
	if basisCount != 2 {
		t.Fatalf("cross-window formation Basis rows = %d; want 2", basisCount)
	}

	formedDisposition := receipt.DispositionVersionRefs[0]
	nextText := "再次出现相似请求，但没有投递链"
	index.search = func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text == nextText {
			return []memoryindex.Candidate{{Kind: memoryindex.KindDisposition, Ref: formedDisposition}}, nil
		}
		return nil, nil
	}
	nextEpisode := materializeMySQLConsolidationEpisode(t, store, "next", nextText)
	nextWorker := &mysqlConsolidationWorker{taggedText: mysqlTaggedMemoryChange(
		formedDisposition,
		consolidation.ApplicationRelation,
		consolidation.ChangeReenact,
		"",
		nextEpisode,
	)}
	nextReceipt, err := store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "mysql-index-disposition-is-hint", EpisodeRefs: []string{nextEpisode},
	}, nextWorker)
	if err != nil {
		t.Fatalf("consolidate indexed Disposition hint: %v", err)
	}
	if !strings.Contains(nextWorker.request.WindowText, "ELIGIBLE_ADAPTATION "+formedDisposition+"\n") {
		t.Fatalf("indexed Disposition missing from MySQL direct candidates:\n%s", nextWorker.request.WindowText)
	}
	if !slices.Contains(nextWorker.request.AllowedTargetRefs, formedDisposition) ||
		strings.Contains(nextWorker.request.WindowText, "ELIGIBLE_DISPOSITION "+formedDisposition+"\n") || len(nextReceipt.DispositionVersionRefs) != 0 {
		t.Fatalf("indexed Disposition must have direct-only eligibility: request=%#v receipt=%#v", nextWorker.request, nextReceipt)
	}
}

func TestMySQLConsolidationBuildsFocusedFormationGroupFromCompleteCausalPairs(t *testing.T) {
	baseURL := testMySQLDatabaseURL(t)
	dsn := isolatedMySQLDSN(t, baseURL)
	index := &mysqlConsolidationIndex{}
	store, err := storemysql.New(context.Background(), dsn, storemysql.WithMemoryIndex(index))
	if err != nil {
		t.Fatalf("open MySQL Store: %v", err)
	}
	t.Cleanup(store.Close)
	if err := store.Migrate(context.Background()); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	oldOne := materializeMySQLConsolidationEpisode(t, store, "causal-old-one", "I freeze when several choices all feel urgent")
	oldOneOutcome := reportMySQLFormationOutcome(t, store, "causal-old-one", "One reversible option helped me choose", ledger.ActorKindUser)
	oldTwo := materializeMySQLConsolidationEpisode(t, store, "causal-old-two", "I stalled again while comparing too many options")
	oldTwoOutcome := reportMySQLFormationOutcome(t, store, "causal-old-two", "A single reversible option got me moving", ledger.ActorKindExternal)
	missingOutcome := materializeMySQLConsolidationEpisode(t, store, "causal-missing-outcome", "structurally complete but no result")
	distractor := materializeMySQLConsolidationEpisode(t, store, "causal-current-noise", "I bought a desk lamp")
	distractorOutcome := reportMySQLFormationOutcome(t, store, "causal-current-noise", "The room is brighter", ledger.ActorKindUser)
	currentText := "I am frozen because five options all seem urgent"
	current := materializeMySQLConsolidationEpisode(t, store, "causal-current", currentText)
	currentOutcomeText := "Choosing one reversible option helped me begin"
	currentOutcome := reportMySQLFormationOutcome(t, store, "causal-current", currentOutcomeText, ledger.ActorKindUser)

	index.search = func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text == currentText {
			return []memoryindex.Candidate{
				{Kind: memoryindex.KindEpisode, Ref: current},
				{Kind: memoryindex.KindEpisode, Ref: oldTwo},
				{Kind: memoryindex.KindEpisode, Ref: oldOne},
				{Kind: memoryindex.KindEpisode, Ref: distractor},
			}, nil
		}
		if strings.Contains(query.Text, currentText) &&
			strings.Contains(query.Text, "agent response causal-current") &&
			strings.Contains(query.Text, currentOutcomeText) {
			return []memoryindex.Candidate{
				{Kind: memoryindex.KindEpisode, Ref: current},
				{Kind: memoryindex.KindEpisode, Ref: oldOne},
				{Kind: memoryindex.KindEpisode, Ref: distractor},
				{Kind: memoryindex.KindEpisode, Ref: oldTwo},
				{Kind: memoryindex.KindEpisode, Ref: missingOutcome},
			}, nil
		}
		return nil, nil
	}
	worker := &mysqlConsolidationWorker{taggedText: mysqlTaggedMemoryChange(
		consolidation.TargetNewDisposition,
		consolidation.ApplicationRelation,
		consolidation.ChangeText,
		"When the user freezes among choices, the Agent offers one reversible option",
		current, currentOutcome, oldOne, oldOneOutcome, oldTwo, oldTwoOutcome,
	)}

	receipt, err := store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:           "mysql-causal-group-job",
		EpisodeRefs:      []string{distractor, oldOne, current},
		OutcomeEventRefs: []string{distractorOutcome, currentOutcome},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("formation receipt = %#v; want one Disposition", receipt)
	}
	wantBasis := []string{current, currentOutcome, oldOne, oldOneOutcome, oldTwo, oldTwoOutcome}
	if got, ok := consolidation.DispositionFormationBasis(worker.request.Evidence); !ok || !reflect.DeepEqual(got, wantBasis) {
		t.Fatalf("focused Basis = %#v, %v; want %#v, true", got, ok, wantBasis)
	}
	roles := make(map[string]string)
	for _, episode := range worker.request.Evidence.Episodes {
		roles[episode.EpisodeRef] = episode.FormationRole
	}
	if roles[current] != consolidation.EvidenceFormationAnchor ||
		roles[oldOne] != consolidation.EvidenceFormationCandidate ||
		roles[oldTwo] != consolidation.EvidenceFormationCandidate ||
		roles[distractor] != "" || roles[missingOutcome] != "" {
		t.Fatalf("formation roles = %#v", roles)
	}
}

func testMySQLDatabaseURL(t *testing.T) string {
	t.Helper()
	baseURL := os.Getenv("MEMORY_TEST_MYSQL_DATABASE_URL")
	if baseURL == "" {
		t.Skip("MEMORY_TEST_MYSQL_DATABASE_URL is not set")
	}
	return baseURL
}

func materializeMySQLConsolidationEpisode(
	t *testing.T,
	store *storemysql.Store,
	suffix string,
	situationText string,
) string {
	t.Helper()
	scope := ledger.Scope{
		Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-index-consolidation",
		AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-" + suffix,
	}
	binding := ledger.EpisodeBinding{
		RunRef: "run-" + suffix, SourceGroupRef: "group-" + suffix, Role: ledger.RoleSituation,
	}
	if _, err := store.Observe(context.Background(), "observe-"+suffix+"-situation", ledger.SourceEvent{
		Scope: scope, Ref: "source-" + suffix + "-situation", ActorKind: ledger.ActorKindUser,
		ActorRef: "user-1", Text: situationText,
	}, binding); err != nil {
		t.Fatalf("observe Situation: %v", err)
	}
	binding.Role = ledger.RoleAgentAct
	receipt, err := store.Observe(context.Background(), "observe-"+suffix+"-agent-act", ledger.SourceEvent{
		Scope: scope, Ref: "source-" + suffix + "-agent-act", ActorKind: ledger.ActorKindAgent,
		ActorRef: "agent-1", Text: "agent response " + suffix,
	}, binding)
	if err != nil {
		t.Fatalf("observe AgentAct: %v", err)
	}
	if receipt.EpisodeRef == "" {
		t.Fatal("Episode was not materialized")
	}
	return receipt.EpisodeRef
}

func reportMySQLFormationOutcome(
	t *testing.T,
	store *storemysql.Store,
	suffix string,
	text string,
	actorKind ledger.ActorKind,
) string {
	t.Helper()
	scope := ledger.Scope{
		Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-index-consolidation",
		AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-" + suffix,
	}
	actorRef := "user-1"
	if actorKind == ledger.ActorKindAgent {
		actorRef = scope.AgentRef
	} else if actorKind == ledger.ActorKindExternal {
		actorRef = "observer-1"
	}
	receipt, err := store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "report-" + suffix,
		Event: ledger.SourceEvent{
			Ref: "source-" + suffix + "-outcome", Scope: scope,
			ActorKind: actorKind, ActorRef: actorRef, Text: text,
		},
		RunRef: "run-" + suffix, SourceGroupRef: "group-" + suffix,
		RelatedSourceEventRefs: []string{"source-" + suffix + "-situation", "source-" + suffix + "-agent-act"},
	})
	if err != nil {
		t.Fatalf("ReportOutcome(%s): %v", suffix, err)
	}
	if receipt.EpisodeRef == "" {
		t.Fatalf("ReportOutcome(%s) did not bind an Episode", suffix)
	}
	return receipt.OutcomeEventRef
}

type mysqlConsolidationIndex struct {
	search func(memoryindex.Query) ([]memoryindex.Candidate, error)
}

func (index *mysqlConsolidationIndex) Search(_ context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	if index.search == nil {
		return nil, nil
	}
	return index.search(query)
}

func (*mysqlConsolidationIndex) Upsert(context.Context, []memoryindex.Document) error { return nil }
func (*mysqlConsolidationIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (*mysqlConsolidationIndex) Reset(context.Context) error { return nil }

type mysqlConsolidationWorker struct {
	taggedText string
	request    consolidation.WorkerRequest
}

func (worker *mysqlConsolidationWorker) ProcessConsolidationWindow(
	_ context.Context,
	request consolidation.WorkerRequest,
) (string, error) {
	worker.request = request
	return worker.taggedText, nil
}

func mysqlTaggedMemoryChange(target, application, operation, text string, basis ...string) string {
	var result strings.Builder
	result.WriteString("TARGET\n" + target + "\nAPPLICATION\n" + application + "\nCHANGE\n" + operation + "\n")
	if operation == consolidation.ChangeText {
		result.WriteString(text + "\n")
	}
	result.WriteString("BASIS\n")
	result.WriteString(strings.Join(basis, "\n"))
	result.WriteByte('\n')
	return result.String()
}
