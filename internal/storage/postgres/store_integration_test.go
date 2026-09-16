package postgres_test

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/fault"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/storage/postgres"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"github.com/aig-dev/ChoraMem/migrations"
	"github.com/jackc/pgx/v5/pgxpool"
)

var tenantSequence atomic.Uint64

func TestMigrateCanRunMoreThanOnce(t *testing.T) {
	harness := newHarness(t)
	if err := harness.store.Migrate(context.Background()); err != nil {
		t.Fatalf("second Migrate: %v", err)
	}
}

func TestMigrateSerializesConcurrentFirstStartup(t *testing.T) {
	databaseURL := os.Getenv("MEMORY_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("MEMORY_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	admin, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("connect admin database: %v", err)
	}

	schema := fmt.Sprintf("memory_migrate_%d", tenantSequence.Add(1))
	if _, err := admin.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		t.Fatalf("create isolated schema: %v", err)
	}
	t.Cleanup(func() {
		if _, err := admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE"); err != nil {
			t.Errorf("drop isolated schema: %v", err)
		}
		admin.Close()
	})

	parsedURL, err := url.Parse(databaseURL)
	if err != nil {
		t.Fatalf("parse database URL: %v", err)
	}
	query := parsedURL.Query()
	query.Set("search_path", schema)
	parsedURL.RawQuery = query.Encode()

	const starters = 16
	start := make(chan struct{})
	errorsCh := make(chan error, starters)
	var wait sync.WaitGroup
	for index := 0; index < starters; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			store, err := postgres.New(ctx, parsedURL.String())
			if err == nil {
				defer store.Close()
				err = store.Migrate(ctx)
			}
			errorsCh <- err
		}()
	}
	close(start)
	wait.Wait()
	close(errorsCh)
	for err := range errorsCh {
		if err != nil {
			t.Fatalf("concurrent first Migrate: %v", err)
		}
	}

	var tableCount int
	if err := admin.QueryRow(ctx, `
		SELECT count(*)
		FROM pg_tables
		WHERE schemaname = $1
	`, schema).Scan(&tableCount); err != nil {
		t.Fatalf("count migrated tables: %v", err)
	}
	if tableCount != 19 {
		t.Fatalf("migrated tables = %d; want exactly 19", tableCount)
	}
}

func TestMigrateUpgradesV1SchemaAndCanRepeatEpisodeEvidenceMigration(t *testing.T) {
	databaseURL := os.Getenv("MEMORY_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("MEMORY_TEST_DATABASE_URL is not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	admin, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("connect migration admin: %v", err)
	}
	schema := fmt.Sprintf("memory_upgrade_%d", tenantSequence.Add(1))
	if _, err := admin.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
		admin.Close()
		t.Fatalf("create upgrade schema: %v", err)
	}
	t.Cleanup(func() {
		_, _ = admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
		admin.Close()
	})
	parsed, err := url.Parse(databaseURL)
	if err != nil {
		t.Fatalf("parse migration URL: %v", err)
	}
	query := parsed.Query()
	query.Set("search_path", schema)
	parsed.RawQuery = query.Encode()
	legacy, err := pgxpool.New(ctx, parsed.String())
	if err != nil {
		t.Fatalf("open V1 schema pool: %v", err)
	}
	legacyV1 := strings.Replace(
		migrations.PostgresV1SQL,
		"CONSTRAINT seed_outcome_basis_links_role_check\n        CHECK (role IN ('formation', 'revision', 'inhibition'))",
		"CHECK (role IN ('revision', 'inhibition'))",
		1,
	)
	if legacyV1 == migrations.PostgresV1SQL {
		t.Fatal("legacy PostgreSQL fixture did not remove Outcome formation role")
	}
	if _, err := legacy.Exec(ctx, legacyV1); err != nil {
		legacy.Close()
		t.Fatalf("install legacy V1 schema: %v", err)
	}
	if _, err := legacy.Exec(ctx, `INSERT INTO source_events (tenant_ref, scope_kind, agent_ref, relationship_ref, session_ref, source_ref, actor_kind, actor_ref, source_text) VALUES ('legacy', 'agent', 'agent', '', '', 'legacy-source', 'user', 'user', 'legacy text')`); err != nil {
		t.Fatal(err)
	}
	legacy.Close()

	store, err := postgres.New(ctx, parsed.String())
	if err != nil {
		t.Fatalf("open upgrade Store: %v", err)
	}
	defer store.Close()
	for attempt := 0; attempt < 2; attempt++ {
		if err := store.Migrate(ctx); err != nil {
			t.Fatalf("Migrate attempt %d: %v", attempt+1, err)
		}
	}
	inspection, err := pgxpool.New(ctx, parsed.String())
	if err != nil {
		t.Fatalf("open upgraded schema inspection: %v", err)
	}
	defer inspection.Close()
	var baselineRef, baselineText string
	var admission int64
	if err := inspection.QueryRow(ctx, `SELECT constitution_ref, constitution_text, admission_order FROM source_events WHERE source_ref = 'legacy-source'`).Scan(&baselineRef, &baselineText, &admission); err != nil || baselineRef != "" || baselineText != "" {
		t.Fatalf("legacy baseline upgrade = %q %q, %v", baselineRef, baselineText, err)
	}
	var table string
	if err := inspection.QueryRow(ctx, `SELECT to_regclass('memory_context_episode_evidence')::text`).Scan(&table); err != nil || table != "memory_context_episode_evidence" {
		t.Fatalf("Episode evidence upgrade table = %q, %v", table, err)
	}
	var outcomeRoleCheck string
	if err := inspection.QueryRow(ctx, `
		SELECT pg_get_constraintdef(oid)
		FROM pg_constraint
		WHERE conrelid = 'seed_outcome_basis_links'::regclass
		  AND conname = 'seed_outcome_basis_links_role_check'
	`).Scan(&outcomeRoleCheck); err != nil || !strings.Contains(strings.ToLower(outcomeRoleCheck), "formation") {
		t.Fatalf("Outcome formation role upgrade = %q, %v", outcomeRoleCheck, err)
	}
}

func TestObserveFreezesCompleteRequestAndOriginalReceiptAcrossRestart(t *testing.T) {
	harness := newHarness(t)
	event := harness.source("source-1")
	binding := harness.binding(ledger.RoleSituation)

	want, err := harness.store.Observe(context.Background(), "request-1", event, binding)
	if err != nil {
		t.Fatalf("first Observe: %v", err)
	}
	if want != (ledger.ObserveReceipt{SourceEventRef: event.Ref}) {
		t.Fatalf("first receipt = %#v", want)
	}

	harness.store.Close()
	harness.store = harness.openStore(t)
	got, err := harness.store.Observe(context.Background(), "request-1", event, binding)
	if err != nil {
		t.Fatalf("retry after restart: %v", err)
	}
	if got != want {
		t.Fatalf("retry receipt = %#v; want original %#v", got, want)
	}

	conflict := event
	conflict.Ref = "source-conflict-must-not-write"
	if _, err := harness.store.Observe(context.Background(), "request-1", conflict, binding); !errors.Is(err, ledger.ErrIdempotencyConflict) {
		t.Fatalf("conflicting request error = %v; want ErrIdempotencyConflict", err)
	}
	if count := harness.count(t, "source_events", "source_ref = $2", conflict.Ref); count != 0 {
		t.Fatalf("conflicting request partially wrote %d source rows", count)
	}
}

func TestObserveKeepsSourceImmutableWithinExactScope(t *testing.T) {
	harness := newHarness(t)
	event := harness.source("source-1")
	if _, err := harness.store.Observe(context.Background(), "request-1", event, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("first Observe: %v", err)
	}

	changed := event
	changed.Text = "different immutable text"
	if _, err := harness.store.Observe(context.Background(), "request-2", changed, ledger.EpisodeBinding{}); !errors.Is(err, ledger.ErrSourceEventConflict) {
		t.Fatalf("changed source error = %v; want ErrSourceEventConflict", err)
	}
	if _, err := harness.store.Observe(context.Background(), "request-2", event, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("rolled-back request key was not reusable: %v", err)
	}
}

func TestObserveClassifiesClosedPoolAsUnavailable(t *testing.T) {
	harness := newHarness(t)
	harness.store.Close()

	_, err := harness.store.Observe(context.Background(), "request-1", harness.source("source-1"), ledger.EpisodeBinding{})
	if !errors.Is(err, fault.ErrUnavailable) {
		t.Fatalf("closed pool error = %v; want ErrUnavailable", err)
	}
}

func TestObserveIdempotencyKeyIsTenantScopedAndConflictsAcrossScopesWithinTenant(t *testing.T) {
	first := newHarness(t)
	second := newHarness(t)

	if _, err := first.store.Observe(context.Background(), "shared-key", first.source("source-1"), ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("first tenant Observe: %v", err)
	}
	if _, err := second.store.Observe(context.Background(), "shared-key", second.source("source-1"), ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("same key in second tenant: %v", err)
	}

	crossScope := first.source("source-2")
	crossScope.Scope.Kind = ledger.ScopeKindAgent
	crossScope.Scope.RelationshipRef = ""
	if _, err := first.store.Observe(context.Background(), "shared-key", crossScope, ledger.EpisodeBinding{}); !errors.Is(err, ledger.ErrIdempotencyConflict) {
		t.Fatalf("same tenant key reused across scope error = %v; want ErrIdempotencyConflict", err)
	}
}

func TestObserveStoresIncompleteBindingWithoutTypedLink(t *testing.T) {
	harness := newHarness(t)
	partial := harness.binding(ledger.RoleSituation)
	partial.SourceGroupRef = ""
	receipt, err := harness.store.Observe(context.Background(), "request-1", harness.source("source-1"), partial)
	if err != nil {
		t.Fatalf("Observe: %v", err)
	}
	if receipt.EpisodeRef != "" {
		t.Fatalf("incomplete binding episode ref = %q", receipt.EpisodeRef)
	}
	if count := harness.count(t, "episode_links", "TRUE", nil); count != 0 {
		t.Fatalf("incomplete binding wrote %d typed links", count)
	}
}

func TestObserveMaterializesDistinctSourceGateAndAppendsLateOutcome(t *testing.T) {
	harness := newHarness(t)
	if receipt := harness.observe(t, "situation", harness.source("source-situation"), harness.binding(ledger.RoleSituation)); receipt.EpisodeRef != "" {
		t.Fatalf("situation-only receipt = %#v", receipt)
	}

	act := harness.agentSource("source-agent-act")
	materialized := harness.observe(t, "agent-act", act, harness.binding(ledger.RoleAgentAct))
	wantEpisodeRef := ledger.EpisodeRef(act.Scope, "run-1", "group-1")
	if materialized.EpisodeRef != wantEpisodeRef {
		t.Fatalf("materialized episode = %q; want %q", materialized.EpisodeRef, wantEpisodeRef)
	}
	if count := harness.count(t, "episodes", "TRUE", nil); count != 1 {
		t.Fatalf("episodes = %d; want 1", count)
	}
	if count := harness.count(t, "episode_links", "episode_ref = $2", wantEpisodeRef); count != 2 {
		t.Fatalf("materialized links = %d; want 2", count)
	}

	outcome := harness.observe(t, "outcome", harness.source("source-outcome"), harness.binding(ledger.RoleOutcome))
	if outcome.EpisodeRef != wantEpisodeRef {
		t.Fatalf("late outcome changed episode identity: %q != %q", outcome.EpisodeRef, wantEpisodeRef)
	}
	if count := harness.count(t, "episodes", "TRUE", nil); count != 1 {
		t.Fatalf("late outcome episodes = %d; want 1", count)
	}
	if count := harness.count(t, "episode_links", "episode_ref = $2", wantEpisodeRef); count != 3 {
		t.Fatalf("links after outcome = %d; want 3", count)
	}
}

func TestObserveSealsSituationAndAgentActAfterEpisodeMaterialization(t *testing.T) {
	harness := newHarness(t)
	situation := harness.source("source-situation")
	harness.observe(t, "situation", situation, harness.binding(ledger.RoleSituation))
	harness.observe(t, "agent-act", harness.agentSource("source-agent-act"), harness.binding(ledger.RoleAgentAct))

	lateSituation := harness.source("late-situation")
	if _, err := harness.store.Observe(context.Background(), "late-situation", lateSituation, harness.binding(ledger.RoleSituation)); !errors.Is(err, ledger.ErrEpisodeSealed) {
		t.Fatalf("late situation error = %v; want ErrEpisodeSealed", err)
	}
	if count := harness.count(t, "source_events", "source_ref = $2", lateSituation.Ref); count != 0 {
		t.Fatalf("rejected late situation partially wrote %d sources", count)
	}
	if receipt, err := harness.store.Observe(context.Background(), "situation-relation-replay", situation, harness.binding(ledger.RoleSituation)); err != nil || receipt.EpisodeRef == "" {
		t.Fatalf("existing relation replay receipt = %#v, err = %v", receipt, err)
	}
}

func TestObserveDoesNotUseOneSourceForBothMaterializationRoles(t *testing.T) {
	harness := newHarness(t)
	shared := harness.agentSource("source-shared")
	harness.observe(t, "situation", shared, harness.binding(ledger.RoleSituation))
	receipt := harness.observe(t, "agent-act", shared, harness.binding(ledger.RoleAgentAct))
	if receipt.EpisodeRef != "" {
		t.Fatalf("same source materialized episode %q", receipt.EpisodeRef)
	}
	if count := harness.count(t, "episodes", "TRUE", nil); count != 0 {
		t.Fatalf("same source materialized %d episodes", count)
	}
}

func TestObserveIsolatesSameRefsBySessionAndScopeKind(t *testing.T) {
	harness := newHarness(t)
	relationshipSituation := harness.source("source-situation")
	harness.observe(t, "relationship-situation", relationshipSituation, harness.binding(ledger.RoleSituation))

	secondSessionAct := harness.agentSource("source-agent-act")
	secondSessionAct.Scope.SessionRef = "session-2"
	if receipt := harness.observe(t, "second-session-act", secondSessionAct, harness.binding(ledger.RoleAgentAct)); receipt.EpisodeRef != "" {
		t.Fatalf("cross-session sources materialized %q", receipt.EpisodeRef)
	}

	agentScopeAct := harness.agentSource("source-agent-act")
	agentScopeAct.Scope.Kind = ledger.ScopeKindAgent
	agentScopeAct.Scope.RelationshipRef = ""
	if receipt := harness.observe(t, "agent-scope-act", agentScopeAct, harness.binding(ledger.RoleAgentAct)); receipt.EpisodeRef != "" {
		t.Fatalf("cross-scope-kind sources materialized %q", receipt.EpisodeRef)
	}

	matchingAct := harness.agentSource("source-agent-act")
	receipt := harness.observe(t, "relationship-act", matchingAct, harness.binding(ledger.RoleAgentAct))
	if receipt.EpisodeRef == "" {
		t.Fatal("matching exact scope did not materialize")
	}
	if count := harness.count(t, "episodes", "TRUE", nil); count != 1 {
		t.Fatalf("episodes = %d; want exactly the matching relationship episode", count)
	}
}

func TestObserveSerializesConcurrentDistinctRoleGateWithinExactGroup(t *testing.T) {
	harness := newHarness(t)
	const groups = 24

	start := make(chan struct{})
	errorsCh := make(chan error, groups*2)
	var wait sync.WaitGroup
	for index := 0; index < groups; index++ {
		group := fmt.Sprintf("concurrent-group-%d", index)
		for _, role := range []ledger.SourceRole{ledger.RoleSituation, ledger.RoleAgentAct} {
			role := role
			wait.Add(1)
			go func() {
				defer wait.Done()
				<-start
				event := harness.source(fmt.Sprintf("%s-%s", group, role))
				if role == ledger.RoleAgentAct {
					event = harness.agentSource(event.Ref)
				}
				binding := harness.binding(role)
				binding.SourceGroupRef = group
				_, err := harness.store.Observe(context.Background(), fmt.Sprintf("%s-%s", group, role), event, binding)
				errorsCh <- err
			}()
		}
	}
	close(start)
	wait.Wait()
	close(errorsCh)
	for err := range errorsCh {
		if err != nil {
			t.Fatalf("concurrent Observe: %v", err)
		}
	}
	if count := harness.count(t, "episodes", "TRUE", nil); count != groups {
		t.Fatalf("concurrent episodes = %d; want %d (a missing row proves the gate raced)", count, groups)
	}
}

func TestConsolidateWindowFormsSeedAndFreezesRetryWithoutCallingWorkerAgain(t *testing.T) {
	harness := newHarness(t)
	episodeOne := harness.materializeEpisode(t, "one", "session-1")
	episodeTwo := harness.materializeEpisode(t, "two", "session-2")
	worker := &scriptedWorker{taggedText: strings.Join([]string{
		"TARGET",
		consolidation.TargetNewDisposition,
		"APPLICATION",
		consolidation.ApplicationRelation,
		"CHANGE",
		"TEXT 先给出最小因果模型，再增加必要复杂度",
		"BASIS",
		episodeOne,
		episodeTwo,
	}, "\n")}
	window := consolidation.Window{JobRef: "form-job-1", EpisodeRefs: []string{episodeOne, episodeTwo}}

	want, err := harness.store.ConsolidateWindow(context.Background(), window, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if len(want.DispositionVersionRefs) != 1 || want.DispositionVersionRefs[0] == "" {
		t.Fatalf("receipt = %#v; want one stable SeedVersion ref", want)
	}
	if worker.calls != 1 {
		t.Fatalf("worker calls = %d; want 1", worker.calls)
	}
	if !reflect.DeepEqual(worker.request.AllowedTargetRefs, []string{consolidation.TargetNewRecollection, consolidation.TargetNewDisposition}) {
		t.Fatalf("allowed targets = %#v", worker.request.AllowedTargetRefs)
	}
	if !reflect.DeepEqual(worker.request.AllowedBasisRefs, window.EpisodeRefs) {
		t.Fatalf("allowed basis = %#v; want %#v", worker.request.AllowedBasisRefs, window.EpisodeRefs)
	}
	for _, stableRef := range window.EpisodeRefs {
		if !strings.Contains(worker.request.WindowText, "EPISODE "+stableRef) {
			t.Fatalf("worker window omitted stable ref %q: %s", stableRef, worker.request.WindowText)
		}
	}
	if count := harness.count(t, "disposition_seeds", "TRUE", nil); count != 1 {
		t.Fatalf("seeds = %d; want 1", count)
	}
	if count := harness.count(t, "seed_versions", "status = $2", "active"); count != 1 {
		t.Fatalf("active versions = %d; want 1", count)
	}
	if count := harness.count(t, "seed_basis_links", "role = $2", "formation"); count != 2 {
		t.Fatalf("formation links = %d; want 2", count)
	}

	got, err := harness.store.ConsolidateWindow(context.Background(), window, worker)
	if err != nil {
		t.Fatalf("retry ConsolidateWindow: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("retry receipt = %#v; want %#v", got, want)
	}
	if worker.calls != 1 {
		t.Fatalf("retry called worker %d times; want frozen 1", worker.calls)
	}

	episodeThree := harness.materializeEpisode(t, "three", "session-2")
	episodeFour := harness.materializeEpisode(t, "four", "session-3")
	contextWorker := &scriptedWorker{}
	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "form-job-active-context",
		EpisodeRefs: []string{episodeThree, episodeFour},
	}, contextWorker); err != nil {
		t.Fatalf("active-context ConsolidateWindow: %v", err)
	}
	if !strings.Contains(contextWorker.request.WindowText, "ELIGIBLE_ADAPTATION "+want.DispositionVersionRefs[0]) ||
		!strings.Contains(contextWorker.request.WindowText, "TEXT 先给出最小因果模型，再增加必要复杂度") {
		t.Fatalf("worker window omitted active Seed context:\n%s", contextWorker.request.WindowText)
	}

	conflict := window
	conflict.EpisodeRefs = []string{episodeTwo, episodeOne}
	if _, err := harness.store.ConsolidateWindow(context.Background(), conflict, worker); !errors.Is(err, consolidation.ErrWindowConflict) {
		t.Fatalf("same job with changed ordered window error = %v; want ErrWindowConflict", err)
	}
}

func TestConsolidateWindowNoOpsMalformedAndOutOfBoundsWorkerText(t *testing.T) {
	harness := newHarness(t)
	episodeOne := harness.materializeEpisode(t, "one", "session-1")
	episodeTwo := harness.materializeEpisode(t, "two", "session-1")

	tests := []struct {
		name       string
		taggedText string
	}{
		{name: "malformed", taggedText: "not tagged text"},
		{name: "out of bounds basis", taggedText: taggedMemoryChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "concise", episodeOne, "episode-not-in-window",
		)},
		{name: "existing target not opened", taggedText: taggedMemoryChange(
			"seed-existing", consolidation.ApplicationRelation,
			consolidation.ChangeText, "concise", episodeOne, episodeTwo,
		)},
		{name: "valid block followed by out of bounds block", taggedText: strings.Join([]string{
			taggedMemoryChange(
				consolidation.TargetNewRecollection, consolidation.ApplicationSelf,
				consolidation.ChangeText, "must roll back with the Job", episodeOne,
			),
			taggedMemoryChange(
				consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
				consolidation.ChangeText, "invalid sibling", episodeOne, "episode-not-in-window",
			),
		}, "\n")},
	}
	for index, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			worker := &scriptedWorker{taggedText: test.taggedText}
			receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
				JobRef:      fmt.Sprintf("invalid-job-%d", index),
				EpisodeRefs: []string{episodeOne, episodeTwo},
			}, worker)
			if err != nil {
				t.Fatalf("ConsolidateWindow: %v", err)
			}
			if len(receipt.RecollectionVersionRefs) != 0 || len(receipt.DispositionVersionRefs) != 0 {
				t.Fatalf("invalid worker text formed %#v", receipt)
			}
		})
	}
	if count := harness.count(t, "disposition_seeds", "TRUE", nil); count != 0 {
		t.Fatalf("invalid outputs partially wrote %d seeds", count)
	}
	if count := harness.count(t, "recollections", "TRUE", nil); count != 0 {
		t.Fatalf("invalid outputs partially wrote %d Recollections", count)
	}
	if count := harness.count(t, "consolidation_receipts", "TRUE", nil); count != len(tests) {
		t.Fatalf("no-op receipts = %d; want %d", count, len(tests))
	}
}

func TestConsolidateWindowFreezesOversizedLegacyWindowAsNoOp(t *testing.T) {
	harness := newHarness(t)
	episodeOne := harness.materializeEpisode(t, "oversized-one", "session-oversized-one")
	episodeTwo := harness.materializeEpisode(t, "oversized-two", "session-oversized-two")

	// Simulate source text admitted before the V1 intake byte limit existed.
	// Four linked sources at this size make the exact Worker request cross the
	// Core-owned transport ceiling without relying on gRPC to reject it.
	if _, err := harness.inspectionDB.Exec(context.Background(), `
		UPDATE source_events
		SET source_text = repeat('x', $2)
		WHERE tenant_ref = $1
	`, harness.tenant, consolidation.MaxWorkerRequestBytes/4); err != nil {
		t.Fatalf("expand legacy source evidence: %v", err)
	}

	window := consolidation.Window{
		JobRef: "oversized-legacy-job", EpisodeRefs: []string{episodeOne, episodeTwo},
	}
	worker := &scriptedWorker{err: errors.New("must not be called")}
	receipt, err := harness.store.ConsolidateWindow(context.Background(), window, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow oversized no-op: %v", err)
	}
	if worker.calls != 0 || len(receipt.RecollectionVersionRefs) != 0 || len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("oversized window = calls:%d receipt:%#v; want durable no-op", worker.calls, receipt)
	}
	if count := harness.count(t, "consolidation_receipts", "job_ref = $2", window.JobRef); count != 1 {
		t.Fatalf("oversized no-op receipts = %d; want 1", count)
	}

	if _, err := harness.store.ConsolidateWindow(context.Background(), window, worker); err != nil || worker.calls != 0 {
		t.Fatalf("oversized retry = calls:%d err:%v; want frozen no-op", worker.calls, err)
	}
}

func TestConsolidateWindowWorkerFailureDoesNotFreezeOrPartiallyWrite(t *testing.T) {
	harness := newHarness(t)
	episodeOne := harness.materializeEpisode(t, "one", "session-1")
	episodeTwo := harness.materializeEpisode(t, "two", "session-1")
	worker := &scriptedWorker{err: errors.New("worker offline")}

	_, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "failed-worker-job",
		EpisodeRefs: []string{episodeOne, episodeTwo},
	}, worker)
	if err == nil {
		t.Fatal("worker failure error = nil")
	}
	if count := harness.count(t, "disposition_seeds", "TRUE", nil); count != 0 {
		t.Fatalf("worker failure partially wrote %d seeds", count)
	}
	if count := harness.count(t, "consolidation_receipts", "TRUE", nil); count != 0 {
		t.Fatalf("worker failure froze %d receipts; retry must remain possible", count)
	}
}

func TestConsolidateWindowSerializesExactDuplicateFormationWithinOwnerScope(t *testing.T) {
	harness := newHarness(t)
	episodes := []string{
		harness.materializeEpisode(t, "one", "session-1"),
		harness.materializeEpisode(t, "two", "session-2"),
		harness.materializeEpisode(t, "three", "session-3"),
		harness.materializeEpisode(t, "four", "session-4"),
	}
	windows := []consolidation.Window{
		{JobRef: "concurrent-form-1", EpisodeRefs: episodes[:2]},
		{JobRef: "concurrent-form-2", EpisodeRefs: episodes[2:]},
	}

	started := make(chan struct{}, len(windows))
	release := make(chan struct{})
	type result struct {
		window consolidation.Window
		err    error
	}
	results := make(chan result, len(windows))
	var wait sync.WaitGroup
	for _, window := range windows {
		window := window
		wait.Add(1)
		go func() {
			defer wait.Done()
			worker := &barrierWorker{
				started: started,
				release: release,
				taggedText: taggedMemoryChange(
					consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
					consolidation.ChangeText, "concise", window.EpisodeRefs[0], window.EpisodeRefs[1],
				),
			}
			_, err := harness.store.ConsolidateWindow(context.Background(), window, worker)
			results <- result{window: window, err: err}
		}()
	}
	for range windows {
		<-started
	}
	close(release)
	wait.Wait()
	close(results)
	staleCount := 0
	for result := range results {
		switch {
		case result.err == nil:
		case errors.Is(result.err, consolidation.ErrWindowStale):
			staleCount++
			worker := &scriptedWorker{taggedText: taggedMemoryChange(
				consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
				consolidation.ChangeText, "concise", result.window.EpisodeRefs[0], result.window.EpisodeRefs[1],
			)}
			if _, err := harness.store.ConsolidateWindow(context.Background(), result.window, worker); err != nil {
				t.Fatalf("retry stale FORM: %v", err)
			}
		default:
			t.Fatalf("concurrent FORM: %v", result.err)
		}
	}
	if staleCount != 1 {
		t.Fatalf("stale concurrent jobs = %d; want exactly 1", staleCount)
	}
	if count := harness.count(t, "disposition_seeds", "TRUE", nil); count != 1 {
		t.Fatalf("concurrent exact duplicate formed %d Seeds; want 1", count)
	}
	if count := harness.count(t, "consolidation_receipts", "TRUE", nil); count != 2 {
		t.Fatalf("concurrent jobs froze %d receipts; want 2", count)
	}
}

func TestConsolidateWindowUsesFullScopedSourceIdentityAcrossSessions(t *testing.T) {
	harness := newHarness(t)
	firstSituation := harness.source("shared-local-source-ref")
	firstSituation.Scope.SessionRef = "session-1"
	episodeOne := harness.materializeEpisodeFromSituation(t, "one", firstSituation)

	secondSituation := harness.source("shared-local-source-ref")
	secondSituation.Scope.SessionRef = "session-2"
	secondSituation.Text = "a different SourceEvent with the same adapter-local ref"
	episodeTwo := harness.materializeEpisodeFromSituation(t, "two", secondSituation)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, "concise", episodeOne, episodeTwo,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "scoped-source-identity-job",
		EpisodeRefs: []string{episodeOne, episodeTwo},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("scoped-distinct sources formed %#v; want one version", receipt)
	}
}

func TestConsolidateWindowCommitsFormationAndExistingTransitionFromOneWorkerSnapshot(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "unified-mixed", "先确认真正的问题")
	harness.recordSeedDelivery(t, "unified-mixed-delivery", run)
	feedbackEpisode := harness.materializeActivatedRun(t, "unified-mixed", run, "先确认真正的问题")
	secondEpisode := harness.materializeEpisode(t, "unified-mixed-second", "session-unified-mixed-second")
	newTendency := "先确认目标，再给出最小方案"
	worker := &scriptedWorker{taggedText: strings.Join([]string{
		taggedMemoryChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, newTendency, feedbackEpisode, secondEpisode,
		),
		taggedMemoryChange(
			run.seedVersionRef, consolidation.ApplicationRelation,
			consolidation.ChangeReenact, "", feedbackEpisode,
		),
	}, "\n")}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "unified-mixed-job",
		EpisodeRefs: []string{feedbackEpisode, secondEpisode},
	}, worker)
	if err != nil {
		t.Fatalf("mixed ConsolidateWindow: %v", err)
	}
	if worker.calls != 1 {
		t.Fatalf("mixed Worker calls = %d; want 1", worker.calls)
	}
	if !reflect.DeepEqual(worker.request.AllowedTargetRefs, []string{
		consolidation.TargetNewRecollection, consolidation.TargetNewDisposition, run.seedVersionRef,
	}) {
		t.Fatalf("mixed allowed Targets = %#v", worker.request.AllowedTargetRefs)
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("mixed receipt = %#v; want one formed SeedVersion", receipt)
	}
	if count := harness.count(t, "seed_basis_links", "seed_version_ref = $2 AND role = 'reenactment'", run.seedVersionRef); count != 1 {
		t.Fatalf("mixed reenactment Basis rows = %d; want 1", count)
	}
	if count := harness.count(t, "consolidation_receipts", "job_ref = $2", "unified-mixed-job"); count != 1 {
		t.Fatalf("mixed consolidation receipts = %d; want 1", count)
	}
}

func TestSelectMemoryFreezesConstitutionAndSelectedActivationForRun(t *testing.T) {
	harness := newHarness(t)
	seedVersionRef := harness.formSeed(t, "primary", "先给出最小因果模型，再增加必要复杂度", "relationship-1")
	current := harness.source("current-situation")
	current.Scope.SessionRef = "session-current"
	current.Text = "先给出最小因果模型，再增加必要复杂度"
	if _, err := harness.store.Observe(context.Background(), "current-situation", current, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("observe current situation: %v", err)
	}
	request := selection.SelectRequest{
		Scope:               current.Scope,
		RunRef:              "memory-run-1",
		SituationSourceRefs: []string{current.Ref},
		Constitution: selection.Constitution{
			MemoryRef: "constitution-v1",
			Text:      "保持好奇、诚实与简洁。",
		},
	}

	want, err := harness.store.SelectMemory(context.Background(), request)
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if want.Ref == "" || want.RunRef != request.RunRef {
		t.Fatalf("context identity = %#v", want)
	}
	wantConstitution := selection.Constitution{MemoryRef: "constitution-v1", Text: "保持好奇、诚实与简洁。"}
	wantDispositions := []selection.Disposition{
		{MemoryRef: seedVersionRef, Text: "先给出最小因果模型，再增加必要复杂度", Application: selection.ApplicationScopeRelation},
	}
	if want.Constitution != wantConstitution || !reflect.DeepEqual(want.Dispositions, wantDispositions) {
		t.Fatalf("MemoryContext = %#v; want constitution %#v and dispositions %#v", want, wantConstitution, wantDispositions)
	}
	if count := harness.count(t, "memory_contexts", "context_ref = $2", want.Ref); count != 1 {
		t.Fatalf("memory contexts = %d; want 1", count)
	}
	if count := harness.count(t, "memory_context_items", "context_ref = $2", want.Ref); count != 1 {
		t.Fatalf("memory context items = %d; want 1", count)
	}

	// A later Seed must not change the already frozen run snapshot.
	harness.formSeed(t, "later", "先说明假设，再执行", "relationship-1")
	got, err := harness.store.SelectMemory(context.Background(), request)
	if err != nil {
		t.Fatalf("retry SelectMemory: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("retry context = %#v; want frozen %#v", got, want)
	}

	changed := request
	changed.Constitution.Text = "different baseline"
	if _, err := harness.store.SelectMemory(context.Background(), changed); !errors.Is(err, selection.ErrRunConflict) {
		t.Fatalf("same run changed request error = %v; want ErrRunConflict", err)
	}
}

func TestSelectMemorySerializesConcurrentSameRun(t *testing.T) {
	harness := newHarness(t)
	harness.formSeed(t, "concurrent-persona", "先给出最小因果模型", "relationship-1")
	current := harness.source("concurrent-persona-current")
	current.Scope.SessionRef = "session-concurrent-persona"
	current.Text = "先给出最小因果模型"
	if _, err := harness.store.Observe(context.Background(), "concurrent-persona-current", current, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("observe current situation: %v", err)
	}
	request := selection.SelectRequest{
		Scope: current.Scope, RunRef: "concurrent-persona-run", SituationSourceRefs: []string{current.Ref},
	}

	const callers = 12
	start := make(chan struct{})
	results := make(chan selection.MemoryContext, callers)
	errorsCh := make(chan error, callers)
	var wait sync.WaitGroup
	for index := 0; index < callers; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			contextValue, err := harness.store.SelectMemory(context.Background(), request)
			results <- contextValue
			errorsCh <- err
		}()
	}
	close(start)
	wait.Wait()
	close(results)
	close(errorsCh)
	for err := range errorsCh {
		if err != nil {
			t.Fatalf("concurrent SelectMemory: %v", err)
		}
	}
	var want selection.MemoryContext
	for contextValue := range results {
		if want.Ref == "" {
			want = contextValue
			continue
		}
		if !reflect.DeepEqual(contextValue, want) {
			t.Fatalf("concurrent context = %#v; want frozen %#v", contextValue, want)
		}
	}
	if count := harness.count(t, "memory_contexts", "run_ref = $2", request.RunRef); count != 1 {
		t.Fatalf("concurrent same run froze %d contexts; want 1", count)
	}
}

func TestSelectMemoryHardFiltersExactRelationshipScope(t *testing.T) {
	harness := newHarness(t)
	allowed := harness.formSeed(t, "allowed", "回应前先确认真正的问题", "relationship-1")
	harness.formSeed(t, "other", "回应前先确认真正的问题", "relationship-2")
	current := harness.source("scope-filter-current")
	current.Scope.SessionRef = "session-current"
	current.Text = "回应前先确认真正的问题"
	if _, err := harness.store.Observe(context.Background(), "scope-filter-current", current, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("observe current situation: %v", err)
	}

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope:               current.Scope,
		RunRef:              "scope-filter-run",
		SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if !reflect.DeepEqual(contextValue.Dispositions, []selection.Disposition{{
		MemoryRef: allowed, Text: "回应前先确认真正的问题", Application: selection.ApplicationScopeRelation,
	}}) {
		t.Fatalf("cross-scope context items = %#v", contextValue.Dispositions)
	}
}

func TestSelectMemoryIncludesAgentBaselineInRelationshipScope(t *testing.T) {
	harness := newHarness(t)
	baseScope := harness.source("scope-template").Scope
	baseScope.Kind = ledger.ScopeKindAgent
	baseScope.RelationshipRef = ""
	agentSeed := harness.formSeedInScope(t, "agent-baseline", "先确认真正的问题", baseScope)
	relationshipSeed := harness.formSeed(t, "relationship-detail", "先确认真正的问题", "relationship-1")

	current := harness.source("agent-overlay-current")
	current.Scope.SessionRef = "session-current"
	current.Text = "先确认真正的问题"
	if _, err := harness.store.Observe(context.Background(), "agent-overlay-current", current, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("observe current situation: %v", err)
	}
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "agent-overlay-run", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	routes := make(map[string]selection.ApplicationScope, len(contextValue.Dispositions))
	for _, item := range contextValue.Dispositions {
		routes[item.MemoryRef] = item.Application
	}
	want := map[string]selection.ApplicationScope{
		agentSeed: selection.ApplicationScopeSelf, relationshipSeed: selection.ApplicationScopeRelation,
	}
	if !reflect.DeepEqual(routes, want) {
		t.Fatalf("relationship MemoryContext routes = %#v; want %#v", routes, want)
	}
}

func TestRelationshipRunReenactsDeliveredAgentOwnedDisposition(t *testing.T) {
	harness := newHarness(t)
	baseScope := harness.source("scope-template").Scope
	baseScope.Kind = ledger.ScopeKindAgent
	baseScope.RelationshipRef = ""
	agentSeed := harness.formSeedInScope(t, "agent-feedback-boundary", "先确认真正的问题", baseScope)

	situation := harness.source("agent-feedback-boundary-situation")
	situation.Scope.SessionRef = "session-agent-feedback-boundary"
	situation.Text = "先确认真正的问题"
	run := activatedRun{
		scope: situation.Scope, runRef: "run-agent-feedback-boundary", groupRef: "group-agent-feedback-boundary",
		situationRef: situation.Ref, seedVersionRef: agentSeed,
	}
	harness.observe(t, "agent-feedback-boundary-situation", situation, ledger.EpisodeBinding{
		RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleSituation,
	})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: run.scope, RunRef: run.runRef, SituationSourceRefs: []string{run.situationRef},
	})
	if err != nil {
		t.Fatalf("assemble agent baseline overlay: %v", err)
	}
	if len(contextValue.Dispositions) != 1 || contextValue.Dispositions[0].MemoryRef != agentSeed || contextValue.Dispositions[0].Application != selection.ApplicationScopeSelf {
		t.Fatalf("agent baseline overlay = %#v", contextValue.Dispositions)
	}
	run.context = contextValue
	harness.recordSeedDelivery(t, "agent-feedback-boundary-delivery", run)
	episode := harness.materializeActivatedRun(t, "agent-feedback-boundary", run, "先确认真正的问题")
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		agentSeed, consolidation.ApplicationSelf, consolidation.ChangeReenact, "", episode,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "agent-feedback-boundary-job", EpisodeRefs: []string{episode},
	}, worker)
	if err != nil {
		t.Fatalf("relationship feedback on agent-owned Disposition: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("REENACT created Disposition versions: %#v", receipt)
	}
	if worker.calls != 1 {
		t.Fatalf("relationship feedback Worker calls = %d; want 1", worker.calls)
	}
	for _, want := range []string{
		"ELIGIBLE_DISPOSITION " + agentSeed,
		"APPLICATION SELF",
	} {
		if !strings.Contains(worker.request.WindowText, want) {
			t.Fatalf("agent-owned feedback target omitted %q:\n%s", want, worker.request.WindowText)
		}
	}
	if count := harness.count(t, "seed_basis_links", "seed_version_ref = $2 AND role = 'reenactment'", agentSeed); count != 1 {
		t.Fatalf("agent-owned reenactment Basis rows = %d; want one", count)
	}
}

func TestSelectMemoryExcludesInhibitedAndInactiveSeeds(t *testing.T) {
	harness := newHarness(t)
	tendency := "回应前先确认真正的问题"
	inhibited := harness.formSeed(t, "inhibited", tendency, "relationship-1")
	inhibitionSituation := harness.source("inhibition-situation")
	inhibitionSituation.Scope.SessionRef = "session-inhibition"
	inhibitionSituation.Text = tendency
	inhibitionEpisode := harness.materializeEpisodeFromSituation(t, "inhibition", inhibitionSituation)
	if _, err := harness.inspectionDB.Exec(context.Background(), `
		INSERT INTO seed_basis_links (tenant_ref, seed_version_ref, episode_ref, role)
		VALUES ($1, $2, $3, 'inhibition')
	`, harness.tenant, inhibited, inhibitionEpisode); err != nil {
		t.Fatalf("insert inhibition Basis: %v", err)
	}

	current := harness.source("inhibited-current")
	current.Scope.SessionRef = "session-current"
	current.Text = tendency
	if _, err := harness.store.Observe(context.Background(), "inhibited-current", current, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("observe inhibited current situation: %v", err)
	}
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "inhibited-run", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("assemble inhibited context: %v", err)
	}
	if len(contextValue.Dispositions) != 0 {
		t.Fatalf("inhibited Seed entered MemoryContext: %#v", contextValue.Dispositions)
	}

	inactiveTendency := "蓝鲸绕过木星"
	inactive := harness.formSeed(t, "inactive", inactiveTendency, "relationship-1")
	if _, err := harness.inspectionDB.Exec(context.Background(), `
		UPDATE seed_versions SET status = 'ineligible'
		WHERE tenant_ref = $1 AND seed_version_ref = $2
	`, harness.tenant, inactive); err != nil {
		t.Fatalf("mark Seed inactive: %v", err)
	}
	inactiveCurrent := harness.source("inactive-current")
	inactiveCurrent.Scope.SessionRef = "session-inactive-current"
	inactiveCurrent.Text = inactiveTendency
	if _, err := harness.store.Observe(context.Background(), "inactive-current", inactiveCurrent, ledger.EpisodeBinding{}); err != nil {
		t.Fatalf("observe inactive current situation: %v", err)
	}
	contextValue, err = harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: inactiveCurrent.Scope, RunRef: "inactive-run", SituationSourceRefs: []string{inactiveCurrent.Ref},
	})
	if err != nil {
		t.Fatalf("assemble inactive context: %v", err)
	}
	if len(contextValue.Dispositions) != 0 {
		t.Fatalf("inactive Seed entered MemoryContext: %#v", contextValue.Dispositions)
	}
}

func TestRecordMemoryDeliveryFreezesOnlyReturnedMemoryRefs(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "delivery", "先确认真正的问题")
	delivery := ledger.MemoryDelivery{
		IdempotencyKey: "delivery-key",
		Scope:          run.scope, RunRef: run.runRef, MemoryContextRef: run.context.Ref,
		DeliveredMemoryRefs: []string{run.seedVersionRef},
	}
	want, err := harness.store.RecordMemoryDelivery(context.Background(), delivery)
	if err != nil {
		t.Fatalf("RecordMemoryDelivery: %v", err)
	}
	if want.Ref == "" {
		t.Fatal("delivery receipt ref is empty")
	}
	if count := harness.count(t, "memory_delivery_receipts", "receipt_ref = $2", want.Ref); count != 1 {
		t.Fatalf("delivery receipts = %d; want 1", count)
	}

	harness.store.Close()
	harness.store = harness.openStore(t)
	got, err := harness.store.RecordMemoryDelivery(context.Background(), delivery)
	if err != nil {
		t.Fatalf("retry delivery after restart: %v", err)
	}
	if got != want {
		t.Fatalf("retry delivery = %#v; want %#v", got, want)
	}

	changed := delivery
	changed.DeliveredMemoryRefs = []string{"not-in-context"}
	if _, err := harness.store.RecordMemoryDelivery(context.Background(), changed); !errors.Is(err, ledger.ErrMemoryDeliveryConflict) {
		t.Fatalf("changed delivery error = %v; want ErrMemoryDeliveryConflict", err)
	}
	changed.IdempotencyKey = "delivery-unknown-item"
	if _, err := harness.store.RecordMemoryDelivery(context.Background(), changed); !errors.Is(err, ledger.ErrInvalidMemoryDelivery) {
		t.Fatalf("unknown delivered item error = %v; want ErrInvalidMemoryDelivery", err)
	}
}

func TestRecordMemoryDeliveryRejectsRetroactiveDeliveryAfterAgentAct(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "retroactive-delivery", "先确认真正的问题")
	harness.materializeActivatedRun(t, "retroactive-delivery", run, "先确认真正的问题")

	_, err := harness.store.RecordMemoryDelivery(context.Background(), ledger.MemoryDelivery{
		IdempotencyKey: "retroactive-delivery",
		Scope:          run.scope, RunRef: run.runRef, MemoryContextRef: run.context.Ref,
		DeliveredMemoryRefs: []string{run.seedVersionRef},
	})
	if !errors.Is(err, ledger.ErrInvalidMemoryDelivery) {
		t.Fatalf("late RecordMemoryDelivery error = %v; want ErrInvalidMemoryDelivery", err)
	}
	if count := harness.count(t, "memory_delivery_receipts", "run_ref = $2", run.runRef); count != 0 {
		t.Fatalf("late delivery wrote %d receipts; want zero", count)
	}
}

func TestReportOutcomePreservesSourceAndExactDeliveryBinding(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "outcome", "先确认真正的问题")
	delivery, err := harness.store.RecordMemoryDelivery(context.Background(), ledger.MemoryDelivery{
		IdempotencyKey: "outcome-delivery",
		Scope:          run.scope, RunRef: run.runRef, MemoryContextRef: run.context.Ref,
		DeliveredMemoryRefs: []string{run.seedVersionRef},
	})
	if err != nil {
		t.Fatalf("RecordMemoryDelivery: %v", err)
	}
	agentAct := harness.agentSource("outcome-agent-act")
	agentAct.Scope = run.scope
	agentAct.Text = "agent followed the tendency"
	episode := harness.observe(t, "outcome-agent-act", agentAct, ledger.EpisodeBinding{
		RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleAgentAct,
	}).EpisodeRef
	if episode == "" {
		t.Fatal("agent act did not materialize feedback Episode")
	}

	report := ledger.OutcomeReport{
		IdempotencyKey: "outcome-key",
		Event: ledger.SourceEvent{
			Ref: "outcome-source", Scope: run.scope, ActorKind: ledger.ActorKindUser,
			ActorRef: "user-1", Text: "继续质疑我，但先承认我的担忧。",
		},
		RunRef: run.runRef, SourceGroupRef: run.groupRef,
		DeliveryReceiptRefs:    []string{delivery.Ref},
		RelatedSourceEventRefs: []string{run.situationRef, agentAct.Ref},
	}
	want, err := harness.store.ReportOutcome(context.Background(), report)
	if err != nil {
		t.Fatalf("ReportOutcome: %v", err)
	}
	if want.OutcomeEventRef == "" || want.EpisodeRef != episode {
		t.Fatalf("outcome receipt = %#v; want bound Episode %s", want, episode)
	}
	if count := harness.count(t, "outcome_events", "outcome_event_ref = $2", want.OutcomeEventRef); count != 1 {
		t.Fatalf("outcome events = %d; want 1", count)
	}
	if count := harness.count(t, "episode_links", "episode_ref = $2 AND role = 'outcome'", episode); count != 1 {
		t.Fatalf("outcome Episode links = %d; want 1", count)
	}

	harness.store.Close()
	harness.store = harness.openStore(t)
	got, err := harness.store.ReportOutcome(context.Background(), report)
	if err != nil {
		t.Fatalf("retry outcome after restart: %v", err)
	}
	if got != want {
		t.Fatalf("retry outcome = %#v; want %#v", got, want)
	}
	changed := report
	changed.Event.Text = "different result"
	if _, err := harness.store.ReportOutcome(context.Background(), changed); !errors.Is(err, ledger.ErrOutcomeReportConflict) {
		t.Fatalf("changed outcome error = %v; want ErrOutcomeReportConflict", err)
	}
}

func TestAttributedOutcomeRequiresExactRelatedAgentAct(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "outcome-agent-act-gate", "先确认真正的问题")
	delivery := harness.recordSeedDelivery(t, "outcome-agent-act-gate-delivery", run)
	episode := harness.materializeActivatedRun(t, "outcome-agent-act-gate", run, "先确认真正的问题")

	_, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "outcome-agent-act-gate",
		Event: ledger.SourceEvent{
			Ref: "outcome-agent-act-gate-source", Scope: run.scope,
			ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: "明确的后续结果",
		},
		RunRef: run.runRef, SourceGroupRef: run.groupRef,
		DeliveryReceiptRefs:    []string{delivery.Ref},
		RelatedSourceEventRefs: []string{run.situationRef},
	})
	if !errors.Is(err, ledger.ErrInvalidOutcomeReport) {
		t.Fatalf("Outcome without related AgentAct error = %v; want ErrInvalidOutcomeReport", err)
	}
	if count := harness.count(t, "outcome_events", "episode_ref = $2", episode); count != 0 {
		t.Fatalf("invalid attributed Outcome wrote %d rows; want zero", count)
	}
}

func TestReportOutcomeAcceptsUnattributedInteractionResult(t *testing.T) {
	harness := newHarness(t)
	event := harness.source("unattributed-outcome")
	event.Text = "an external result without delivery attribution"
	receipt, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "unattributed-outcome",
		Event:          event, RunRef: "unattributed-run", SourceGroupRef: "unattributed-group",
	})
	if err != nil {
		t.Fatalf("ReportOutcome without delivery: %v", err)
	}
	if receipt.OutcomeEventRef == "" || receipt.EpisodeRef != "" {
		t.Fatalf("unattributed outcome receipt = %#v", receipt)
	}
}

func TestReportOutcomeBindsWhenEpisodeMaterializesLater(t *testing.T) {
	harness := newHarness(t)
	situation := harness.source("early-outcome-situation")
	binding := ledger.EpisodeBinding{RunRef: "early-outcome-run", SourceGroupRef: "early-outcome-group", Role: ledger.RoleSituation}
	harness.observe(t, "early-outcome-situation", situation, binding)
	receipt, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "early-outcome",
		Event: ledger.SourceEvent{
			Ref: "early-outcome-source", Scope: situation.Scope,
			ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: "early explicit feedback",
		},
		RunRef: binding.RunRef, SourceGroupRef: binding.SourceGroupRef,
		RelatedSourceEventRefs: []string{situation.Ref},
	})
	if err != nil {
		t.Fatalf("early ReportOutcome: %v", err)
	}
	if receipt.EpisodeRef != "" {
		t.Fatalf("early Outcome already had Episode %s", receipt.EpisodeRef)
	}
	agentAct := harness.agentSource("early-outcome-agent-act")
	binding.Role = ledger.RoleAgentAct
	episode := harness.observe(t, "early-outcome-agent-act", agentAct, binding).EpisodeRef
	if episode == "" {
		t.Fatal("later Agent act did not materialize Episode")
	}
	var storedEpisode string
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT episode_ref FROM outcome_events
		WHERE tenant_ref = $1 AND outcome_event_ref = $2
	`, harness.tenant, receipt.OutcomeEventRef).Scan(&storedEpisode); err != nil {
		t.Fatalf("load late-bound Outcome: %v", err)
	}
	if storedEpisode != episode {
		t.Fatalf("late-bound Outcome Episode = %s; want %s", storedEpisode, episode)
	}
}

func TestConsolidateWindowReenactsDeliveredSeedWithoutNewVersion(t *testing.T) {
	harness := newHarness(t)
	feedbackSituation := "先确认真正的问题：用户提到蓝色鲸鱼"
	novelSurface := "用户提到蓝色鲸鱼"
	run := harness.prepareActivatedRunWithSituation(t, "reenact", "先确认真正的问题", feedbackSituation)
	if before := harness.assembleCurrentText(t, "reenact-before-surface", novelSurface); len(before.Dispositions) != 0 {
		t.Fatalf("novel surface activated before reenactment: %#v", before.Dispositions)
	}
	harness.recordSeedDelivery(t, "reenact-delivery", run)
	episode := harness.materializeActivatedRun(t, "reenact", run, "先确认真正的问题")
	projectionBefore := len(projectionOperationsForKind(t, harness, memoryindex.KindDisposition))
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", episode,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "reenact-job", EpisodeRefs: []string{episode},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow REENACT: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("REENACT created versions: %#v", receipt)
	}
	if got := len(projectionOperationsForKind(t, harness, memoryindex.KindDisposition)); got != projectionBefore {
		t.Fatalf("REENACT projection operations = %d; want unchanged %d", got, projectionBefore)
	}
	if count := harness.count(t, "seed_basis_links", "seed_version_ref = $2 AND role = 'reenactment'", run.seedVersionRef); count != 1 {
		t.Fatalf("reenactment Basis rows = %d; want 1", count)
	}
	if after := harness.assembleCurrentText(t, "reenact-after-surface", novelSurface); len(after.Dispositions) != 1 || after.Dispositions[0].MemoryRef != run.seedVersionRef {
		t.Fatalf("reenactment did not expand next Select surface: %#v", after.Dispositions)
	}
	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "reenact-job", EpisodeRefs: []string{episode},
	}, &scriptedWorker{err: errors.New("must not be called on retry")}); err != nil {
		t.Fatalf("retry REENACT: %v", err)
	}
}

func TestConsolidateWindowUndeliveredDispositionOnlyAllowsDirectAdaptation(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "missing-delivery", "先确认真正的问题")
	episode := harness.materializeActivatedRun(t, "missing-delivery", run, "先确认真正的问题")
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", episode,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "missing-delivery-job", EpisodeRefs: []string{episode},
	}, worker)
	if err != nil {
		t.Fatalf("single-Episode Recollection window without Delivery: %v", err)
	}
	if worker.calls != 1 {
		t.Fatalf("worker calls without Delivery = %d; want one Recollection-capable call", worker.calls)
	}
	if !reflect.DeepEqual(worker.request.AllowedTargetRefs, []string{consolidation.TargetNewRecollection, consolidation.TargetNewDisposition, run.seedVersionRef}) ||
		!strings.Contains(worker.request.WindowText, "ELIGIBLE_ADAPTATION "+run.seedVersionRef) {
		t.Fatalf("undelivered Disposition lacks direct-only eligibility: %#v", worker.request)
	}
	if strings.Contains(worker.request.WindowText, "ELIGIBLE_DISPOSITION "+run.seedVersionRef) {
		t.Fatalf("undelivered Disposition leaked as eligible target:\n%s", worker.request.WindowText)
	}
	if len(receipt.RecollectionVersionRefs) != 0 || len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("out-of-bounds undelivered target wrote %#v", receipt)
	}
}

func TestConsolidateWindowDoesNotExposeAgentSelfReportedOutcome(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "self-outcome", "先确认真正的问题")
	delivery := harness.recordSeedDelivery(t, "self-outcome-delivery", run)
	episode := harness.materializeActivatedRun(t, "self-outcome", run, "先确认真正的问题")
	selfAssessment := "我判断自己的回答非常成功"
	outcome, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "self-outcome-report",
		Event: ledger.SourceEvent{
			Ref: "self-outcome-source", Scope: run.scope,
			ActorKind: ledger.ActorKindAgent, ActorRef: run.scope.AgentRef, Text: selfAssessment,
		},
		RunRef: run.runRef, SourceGroupRef: run.groupRef,
		DeliveryReceiptRefs: []string{delivery.Ref},
		RelatedSourceEventRefs: []string{
			run.situationRef, harness.agentActRefForEpisode(t, episode),
		},
	})
	if err != nil {
		t.Fatalf("ReportOutcome self assessment: %v", err)
	}
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", episode,
	)}

	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "self-outcome-job", EpisodeRefs: []string{episode}, OutcomeEventRefs: []string{outcome.OutcomeEventRef},
	}, worker); err != nil {
		t.Fatalf("ConsolidateWindow with self Outcome: %v", err)
	}
	if strings.Contains(worker.request.WindowText, selfAssessment) || strings.Contains(worker.request.WindowText, outcome.OutcomeEventRef) {
		t.Fatalf("agent self Outcome leaked into Worker:\n%s", worker.request.WindowText)
	}
}

func TestConsolidateWindowIgnoresOutcomeAppendedDuringWorker(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "late-outcome-feedback", "先确认真正的问题")
	delivery := harness.recordSeedDelivery(t, "late-outcome-feedback-delivery", run)
	episode := harness.materializeActivatedRun(t, "late-outcome-feedback", run, "先确认真正的问题")
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	worker := &barrierWorker{
		started: started, release: release,
		taggedText: taggedMemoryChange(
			run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", episode,
		),
	}
	result := make(chan error, 1)
	go func() {
		_, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
			JobRef: "late-outcome-feedback-job", EpisodeRefs: []string{episode},
		}, worker)
		result <- err
	}()
	<-started
	lateText := "这是 Worker 启动后才到达的结果"
	harness.reportRunOutcome(t, "late-outcome-feedback", run, episode, delivery.Ref, lateText)
	close(release)
	if err := <-result; err != nil {
		t.Fatalf("filtered late Outcome changed feedback commit: %v", err)
	}
}

func TestConsolidateWindowInhibitsOnlyMatchingSituation(t *testing.T) {
	harness := newHarness(t)
	tendency := "先质疑我的架构"
	exception := "我现在只需要情绪支持，不要质疑我的架构"
	run := harness.prepareActivatedRunWithSituation(t, "inhibit-loop", tendency, exception)
	delivery := harness.recordSeedDelivery(t, "inhibit-loop-delivery", run)
	episode := harness.materializeActivatedRun(t, "inhibit-loop", run, "我会先提供情绪支持")
	outcome := harness.reportRunOutcome(t, "inhibit-loop", run, episode, delivery.Ref, "此刻明确不适合架构式质疑")
	projectionBefore := len(projectionOperationsForKind(t, harness, memoryindex.KindDisposition))
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeInhibit, "",
		episode, outcome.OutcomeEventRef,
	)}

	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "inhibit-loop-job", EpisodeRefs: []string{episode}, OutcomeEventRefs: []string{outcome.OutcomeEventRef},
	}, worker); err != nil {
		t.Fatalf("ConsolidateWindow INHIBIT: %v", err)
	}
	if count := harness.count(t, "seed_basis_links", "seed_version_ref = $2 AND role = 'inhibition'", run.seedVersionRef); count != 1 {
		t.Fatalf("inhibition Episode Basis rows = %d; want 1", count)
	}
	if count := harness.count(t, "seed_outcome_basis_links", "seed_version_ref = $2 AND role = 'inhibition'", run.seedVersionRef); count != 1 {
		t.Fatalf("inhibition Outcome Basis rows = %d; want 1", count)
	}
	if got := len(projectionOperationsForKind(t, harness, memoryindex.KindDisposition)); got != projectionBefore {
		t.Fatalf("INHIBIT projection operations = %d; want unchanged %d", got, projectionBefore)
	}

	exceptionContext := harness.assembleCurrentText(t, "inhibit-next-exception", exception)
	if len(exceptionContext.Dispositions) != 0 {
		t.Fatalf("matching exception did not inhibit Seed: %#v", exceptionContext.Dispositions)
	}
	normalContext := harness.assembleCurrentText(t, "inhibit-next-normal", tendency)
	if len(normalContext.Dispositions) != 1 || normalContext.Dispositions[0].MemoryRef != run.seedVersionRef {
		t.Fatalf("normal situation lost active Seed: %#v", normalContext.Dispositions)
	}
}

func TestConsolidateWindowRevisesExactVersionAndSupersedesOld(t *testing.T) {
	harness := newHarness(t)
	oldTendency := "先质疑我的架构"
	newTendency := "继续质疑我的架构，但先承认我的担忧"
	run := harness.prepareActivatedRun(t, "revise-loop", oldTendency)
	delivery := harness.recordSeedDelivery(t, "revise-loop-delivery", run)
	episode := harness.materializeActivatedRun(t, "revise-loop", run, oldTendency)
	outcome := harness.reportRunOutcome(t, "revise-loop", run, episode, delivery.Ref, newTendency)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeText, newTendency,
		episode, outcome.OutcomeEventRef,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "revise-loop-job", EpisodeRefs: []string{episode}, OutcomeEventRefs: []string{outcome.OutcomeEventRef},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow TENDENCY: %v", err)
	}
	for _, want := range []string{
		"REVISION_ANCHOR_EVIDENCE",
		"text for source-revise-loop-seed-one-situation",
		"agent response for revise-loop-seed-one",
	} {
		if !strings.Contains(worker.request.WindowText, want) {
			t.Fatalf("revision Worker omitted anchor evidence %q:\n%s", want, worker.request.WindowText)
		}
	}
	if len(receipt.DispositionVersionRefs) != 1 || receipt.DispositionVersionRefs[0] == run.seedVersionRef {
		t.Fatalf("revision receipt = %#v; want one new version", receipt)
	}
	newVersionRef := receipt.DispositionVersionRefs[0]
	if operations := projectionOperationsForKind(t, harness, memoryindex.KindDisposition); len(operations) != 3 ||
		operations[0].stream != operations[1].stream || operations[1].stream != operations[2].stream ||
		operations[0].action != memoryindex.ActionUpsert || operations[0].ref != run.seedVersionRef || operations[0].sequence != 1 ||
		operations[1].action != memoryindex.ActionDelete || operations[1].ref != run.seedVersionRef || operations[1].sequence != 2 ||
		operations[2].action != memoryindex.ActionUpsert || operations[2].ref != newVersionRef || operations[2].sequence != 3 {
		t.Fatalf("Disposition revision projection operations = %#v", operations)
	}
	if document, found, err := harness.store.MemoryIndexDocument(context.Background(), memoryindex.Operation{
		Kind: memoryindex.KindDisposition, Ref: run.seedVersionRef,
		Scope: memoryindex.Scope{TenantRef: run.scope.TenantRef, AgentRef: run.scope.AgentRef, RelationshipRef: run.scope.RelationshipRef},
	}); err != nil || found {
		t.Fatalf("superseded UPSERT rehydrate = (%#v, %v, %v); want stale acknowledgement", document, found, err)
	}
	var oldStatus, newStatus, storedTendency string
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT old.status, current.status, current.tendency_text
		FROM seed_versions AS old
		JOIN seed_versions AS current
		  ON current.tenant_ref = old.tenant_ref AND current.seed_ref = old.seed_ref
		WHERE old.tenant_ref = $1 AND old.seed_version_ref = $2 AND current.seed_version_ref = $3
	`, harness.tenant, run.seedVersionRef, newVersionRef).Scan(&oldStatus, &newStatus, &storedTendency); err != nil {
		t.Fatalf("load revised versions: %v", err)
	}
	if oldStatus != "superseded" || newStatus != "active" || storedTendency != newTendency {
		t.Fatalf("revision state = old:%s new:%s tendency:%q", oldStatus, newStatus, storedTendency)
	}
	if count := harness.count(t, "seed_outcome_basis_links", "seed_version_ref = $2 AND role = 'revision'", newVersionRef); count != 1 {
		t.Fatalf("revision Outcome Basis rows = %d; want 1", count)
	}
	contextValue := harness.assembleCurrentText(t, "revision-next", newTendency)
	if len(contextValue.Dispositions) != 1 || contextValue.Dispositions[0].MemoryRef != newVersionRef {
		t.Fatalf("revised selection = %#v; want %s", contextValue.Dispositions, newVersionRef)
	}
}

func TestConsolidateWindowRevisionCannotDuplicateAnotherActiveSeed(t *testing.T) {
	harness := newHarness(t)
	duplicateTendency := "遇到天气问题时用诗歌回答"
	existingRef := harness.formSeed(t, "revision-duplicate-existing", duplicateTendency, "relationship-1")
	run := harness.prepareActivatedRun(t, "revision-duplicate", "先质疑我的架构")
	delivery := harness.recordSeedDelivery(t, "revision-duplicate-delivery", run)
	episode := harness.materializeActivatedRun(t, "revision-duplicate", run, "先质疑我的架构")
	outcome := harness.reportRunOutcome(t, "revision-duplicate", run, episode, delivery.Ref, duplicateTendency)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeText, duplicateTendency,
		episode, outcome.OutcomeEventRef,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "revision-duplicate-job", EpisodeRefs: []string{episode}, OutcomeEventRefs: []string{outcome.OutcomeEventRef},
	}, worker)
	if err != nil {
		t.Fatalf("duplicate revision: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("duplicate revision created versions: %#v", receipt.DispositionVersionRefs)
	}
	for _, want := range []string{"ELIGIBLE_ADAPTATION " + existingRef, "TEXT " + duplicateTendency} {
		if !strings.Contains(worker.request.WindowText, want) {
			t.Fatalf("revision Worker omitted active Seed %q:\n%s", want, worker.request.WindowText)
		}
	}
}

func TestConsolidateWindowRejectsRevisionWhenBasisChangesDuringWorker(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "revision-stale-basis", "先确认真正的问题")
	delivery := harness.recordSeedDelivery(t, "revision-stale-basis-delivery", run)
	episode := harness.materializeActivatedRun(t, "revision-stale-basis", run, "先确认真正的问题")
	outcome := harness.reportRunOutcome(t, "revision-stale-basis", run, episode, delivery.Ref, "确认问题后还要复述约束")
	started := make(chan struct{}, 1)
	release := make(chan struct{})
	worker := &barrierWorker{
		started: started, release: release,
		taggedText: taggedMemoryChange(
			run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeText,
			"确认问题后还要复述约束", episode, outcome.OutcomeEventRef,
		),
	}
	result := make(chan error, 1)
	go func() {
		_, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
			JobRef: "revision-stale-basis-job", EpisodeRefs: []string{episode}, OutcomeEventRefs: []string{outcome.OutcomeEventRef},
		}, worker)
		result <- err
	}()
	<-started

	_, competingErr := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "revision-stale-basis-reenact", EpisodeRefs: []string{episode},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		run.seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", episode,
	)})
	close(release)
	if competingErr != nil {
		t.Fatalf("competing reenactment: %v", competingErr)
	}
	if err := <-result; !errors.Is(err, consolidation.ErrWindowStale) {
		t.Fatalf("revision after concurrent Basis = %v; want ErrWindowStale", err)
	}
	if count := harness.count(t, "seed_versions", "seed_ref = (SELECT seed_ref FROM seed_versions WHERE tenant_ref = $1 AND seed_version_ref = $2)", run.seedVersionRef); count != 1 {
		t.Fatalf("stale revision created %d SeedVersions; want 1", count)
	}
}

func TestConsolidateWindowRequiresSourceDistinctEpisodesAndExactOwnerScope(t *testing.T) {
	harness := newHarness(t)
	episodeOne, sharedSituation := harness.materializeEpisodeWithSituation(t, "one", "session-1", ledger.SourceEvent{})
	episodeTwo, _ := harness.materializeEpisodeWithSituation(t, "two", "session-1", sharedSituation)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, "concise", episodeOne, episodeTwo,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "overlap-job",
		EpisodeRefs: []string{episodeOne, episodeTwo},
	}, worker)
	if err != nil {
		t.Fatalf("source overlap ConsolidateWindow: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("source-overlapping Episodes formed %#v", receipt)
	}

	otherScopeEpisode := harness.materializeEpisodeInRelationship(t, "other-scope", "session-1", "relationship-2")
	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "cross-scope-job",
		EpisodeRefs: []string{episodeOne, otherScopeEpisode},
	}, worker); !errors.Is(err, consolidation.ErrInvalidWindow) {
		t.Fatalf("cross-owner window error = %v; want ErrInvalidWindow", err)
	}
}

type scriptedWorker struct {
	calls      int
	request    consolidation.WorkerRequest
	taggedText string
	err        error
}

func (worker *scriptedWorker) ProcessConsolidationWindow(_ context.Context, request consolidation.WorkerRequest) (string, error) {
	worker.calls++
	worker.request = request
	return worker.taggedText, worker.err
}

type barrierWorker struct {
	started    chan<- struct{}
	release    <-chan struct{}
	taggedText string
}

func (worker *barrierWorker) ProcessConsolidationWindow(context.Context, consolidation.WorkerRequest) (string, error) {
	worker.started <- struct{}{}
	<-worker.release
	return worker.taggedText, nil
}

type testHarness struct {
	databaseURL  string
	tenant       string
	store        *postgres.Store
	inspectionDB *pgxpool.Pool
}

type activatedRun struct {
	scope          ledger.Scope
	runRef         string
	groupRef       string
	situationRef   string
	seedVersionRef string
	context        selection.MemoryContext
}

func newHarness(t *testing.T) *testHarness {
	t.Helper()
	databaseURL := os.Getenv("MEMORY_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Skip("MEMORY_TEST_DATABASE_URL is not set")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	inspectionDB, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("connect inspection database: %v", err)
	}
	harness := &testHarness{
		databaseURL:  databaseURL,
		tenant:       fmt.Sprintf("memory-test-%d-%d", time.Now().UnixNano(), tenantSequence.Add(1)),
		inspectionDB: inspectionDB,
	}
	harness.store = harness.openStore(t)
	if err := harness.store.Migrate(ctx); err != nil {
		harness.store.Close()
		inspectionDB.Close()
		t.Fatalf("Migrate: %v", err)
	}
	t.Cleanup(func() {
		harness.store.Close()
		for _, table := range []string{
			"memory_index_operations",
			"consolidation_jobs",
			"memory_delivery_receipts",
			"memory_context_episode_evidence",
			"memory_context_items",
			"memory_contexts",
			"recollection_basis_links",
			"recollection_versions",
			"recollections",
			"seed_outcome_basis_links",
			"outcome_events",
			"seed_basis_links",
			"seed_versions",
			"disposition_seeds",
			"consolidation_receipts",
			"request_receipts",
			"episode_links",
			"episodes",
			"source_events",
		} {
			if _, err := inspectionDB.Exec(context.Background(), "DELETE FROM "+table+" WHERE tenant_ref = $1", harness.tenant); err != nil {
				t.Errorf("clean %s: %v", table, err)
			}
		}
		inspectionDB.Close()
	})
	return harness
}

func (harness *testHarness) openStore(t *testing.T) *postgres.Store {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	store, err := postgres.New(ctx, harness.databaseURL)
	if err != nil {
		t.Fatalf("postgres.New: %v", err)
	}
	return store
}

func (harness *testHarness) source(ref string) ledger.SourceEvent {
	return ledger.SourceEvent{
		Ref: ref,
		Scope: ledger.Scope{
			Kind:            ledger.ScopeKindRelationship,
			TenantRef:       harness.tenant,
			AgentRef:        "agent-1",
			RelationshipRef: "relationship-1",
			SessionRef:      "session-1",
		},
		ActorKind: ledger.ActorKindUser,
		ActorRef:  "user-1",
		Text:      "text for " + ref,
	}
}

func (harness *testHarness) agentSource(ref string) ledger.SourceEvent {
	event := harness.source(ref)
	event.ActorKind = ledger.ActorKindAgent
	event.ActorRef = event.Scope.AgentRef
	return event
}

func (harness *testHarness) binding(role ledger.SourceRole) ledger.EpisodeBinding {
	return ledger.EpisodeBinding{RunRef: "run-1", SourceGroupRef: "group-1", Role: role}
}

func (harness *testHarness) materializeEpisode(t *testing.T, suffix, sessionRef string) string {
	t.Helper()
	episodeRef, _ := harness.materializeEpisodeWithSituation(t, suffix, sessionRef, ledger.SourceEvent{})
	return episodeRef
}

func (harness *testHarness) formSeed(t *testing.T, suffix, tendency, relationshipRef string) string {
	t.Helper()
	scope := harness.source("scope-template").Scope
	scope.RelationshipRef = relationshipRef
	return harness.formSeedInScope(t, suffix, tendency, scope)
}

func (harness *testHarness) formSeedInScope(t *testing.T, suffix, tendency string, scope ledger.Scope) string {
	t.Helper()
	firstSituation := harness.source("source-" + suffix + "-one-situation")
	firstSituation.Scope = scope
	firstSituation.Scope.SessionRef = "session-" + suffix + "-one"
	first := harness.materializeEpisodeFromSituation(t, suffix+"-one", firstSituation)
	secondSituation := harness.source("source-" + suffix + "-two-situation")
	secondSituation.Scope = scope
	secondSituation.Scope.SessionRef = "session-" + suffix + "-two"
	second := harness.materializeEpisodeFromSituation(t, suffix+"-two", secondSituation)
	application := consolidation.ApplicationRelation
	if scope.Kind == ledger.ScopeKindAgent {
		application = consolidation.ApplicationSelf
	}
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, application, consolidation.ChangeText, tendency, first, second,
	)}
	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:      "form-" + suffix,
		EpisodeRefs: []string{first, second},
	}, worker)
	if err != nil {
		t.Fatalf("form Seed %s: %v", suffix, err)
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("form Seed %s returned %#v; want one SeedVersion", suffix, receipt)
	}
	return receipt.DispositionVersionRefs[0]
}

func (harness *testHarness) prepareActivatedRun(t *testing.T, suffix, tendency string) activatedRun {
	return harness.prepareActivatedRunWithSituation(t, suffix, tendency, tendency)
}

func (harness *testHarness) prepareActivatedRunWithSituation(t *testing.T, suffix, tendency, situationText string) activatedRun {
	t.Helper()
	seedVersionRef := harness.formSeed(t, suffix+"-seed", tendency, "relationship-1")
	situation := harness.source(suffix + "-situation")
	situation.Scope.SessionRef = "session-" + suffix
	situation.Text = situationText
	runRef := "run-" + suffix
	groupRef := "group-" + suffix
	harness.observe(t, suffix+"-situation", situation, ledger.EpisodeBinding{
		RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation,
	})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: situation.Scope, RunRef: runRef, SituationSourceRefs: []string{situation.Ref},
	})
	if err != nil {
		t.Fatalf("prepare SelectMemory: %v", err)
	}
	if len(contextValue.Dispositions) != 1 || contextValue.Dispositions[0].MemoryRef != seedVersionRef {
		t.Fatalf("prepared context = %#v; want Seed %s", contextValue, seedVersionRef)
	}
	return activatedRun{
		scope: situation.Scope, runRef: runRef, groupRef: groupRef,
		situationRef: situation.Ref, seedVersionRef: seedVersionRef, context: contextValue,
	}
}

func (harness *testHarness) recordSeedDelivery(t *testing.T, key string, run activatedRun) ledger.MemoryDeliveryReceipt {
	t.Helper()
	receipt, err := harness.store.RecordMemoryDelivery(context.Background(), ledger.MemoryDelivery{
		IdempotencyKey: key,
		Scope:          run.scope, RunRef: run.runRef, MemoryContextRef: run.context.Ref,
		DeliveredMemoryRefs: []string{run.seedVersionRef},
	})
	if err != nil {
		t.Fatalf("record Seed delivery: %v", err)
	}
	return receipt
}

func (harness *testHarness) materializeActivatedRun(t *testing.T, suffix string, run activatedRun, text string) string {
	t.Helper()
	agentAct := harness.agentSource(suffix + "-agent-act")
	agentAct.Scope = run.scope
	agentAct.Text = text
	receipt := harness.observe(t, suffix+"-agent-act", agentAct, ledger.EpisodeBinding{
		RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleAgentAct,
	})
	if receipt.EpisodeRef == "" {
		t.Fatalf("materialize activated run %s: empty Episode", suffix)
	}
	return receipt.EpisodeRef
}

func (harness *testHarness) reportRunOutcome(t *testing.T, suffix string, run activatedRun, episodeRef, deliveryRef, text string) ledger.OutcomeReceipt {
	t.Helper()
	receipt, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: suffix + "-outcome",
		Event: ledger.SourceEvent{
			Ref: suffix + "-outcome-source", Scope: run.scope,
			ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: text,
		},
		RunRef: run.runRef, SourceGroupRef: run.groupRef,
		DeliveryReceiptRefs:    []string{deliveryRef},
		RelatedSourceEventRefs: []string{run.situationRef, harness.agentActRefForEpisode(t, episodeRef)},
	})
	if err != nil {
		t.Fatalf("report run Outcome: %v", err)
	}
	if receipt.EpisodeRef != episodeRef {
		t.Fatalf("Outcome Episode = %s; want %s", receipt.EpisodeRef, episodeRef)
	}
	return receipt
}

func (harness *testHarness) agentActRefForEpisode(t *testing.T, episodeRef string) string {
	t.Helper()
	var ref string
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT source_event_ref
		FROM episode_links
		WHERE tenant_ref = $1 AND episode_ref = $2 AND role = 'agent_act'
		ORDER BY source_event_ref
		LIMIT 1
	`, harness.tenant, episodeRef).Scan(&ref); err != nil {
		t.Fatalf("load AgentAct for Episode %s: %v", episodeRef, err)
	}
	return ref
}

func (harness *testHarness) assembleCurrentText(t *testing.T, suffix, text string) selection.MemoryContext {
	t.Helper()
	current := harness.source(suffix + "-source")
	current.Scope.SessionRef = "session-" + suffix
	current.Text = text
	harness.observe(t, suffix+"-source", current, ledger.EpisodeBinding{})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-" + suffix, SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("assemble current text: %v", err)
	}
	return contextValue
}

func (harness *testHarness) materializeEpisodeInRelationship(t *testing.T, suffix, sessionRef, relationshipRef string) string {
	t.Helper()
	situation := harness.source("source-" + suffix + "-situation")
	situation.Scope.SessionRef = sessionRef
	situation.Scope.RelationshipRef = relationshipRef
	return harness.materializeEpisodeFromSituation(t, suffix, situation)
}

func (harness *testHarness) materializeEpisodeWithSituation(t *testing.T, suffix, sessionRef string, situation ledger.SourceEvent) (string, ledger.SourceEvent) {
	t.Helper()
	if situation.Ref == "" {
		situation = harness.source("source-" + suffix + "-situation")
		situation.Scope.SessionRef = sessionRef
	}
	return harness.materializeEpisodeFromSituation(t, suffix, situation), situation
}

func (harness *testHarness) materializeEpisodeFromSituation(t *testing.T, suffix string, situation ledger.SourceEvent) string {
	t.Helper()
	binding := ledger.EpisodeBinding{RunRef: "run-" + suffix, SourceGroupRef: "group-" + suffix, Role: ledger.RoleSituation}
	harness.observe(t, "episode-"+suffix+"-situation", situation, binding)

	agentAct := harness.agentSource("source-" + suffix + "-agent-act")
	agentAct.Scope = situation.Scope
	agentAct.Text = "agent response for " + suffix
	binding.Role = ledger.RoleAgentAct
	receipt := harness.observe(t, "episode-"+suffix+"-agent-act", agentAct, binding)
	if receipt.EpisodeRef == "" {
		t.Fatalf("episode %s did not materialize", suffix)
	}
	return receipt.EpisodeRef
}

func (harness *testHarness) observe(t *testing.T, key string, event ledger.SourceEvent, binding ledger.EpisodeBinding) ledger.ObserveReceipt {
	t.Helper()
	receipt, err := harness.store.Observe(context.Background(), key, event, binding)
	if err != nil {
		t.Fatalf("Observe(%s): %v", key, err)
	}
	return receipt
}

func (harness *testHarness) count(t *testing.T, table, predicate string, value any) int {
	t.Helper()
	if strings.ContainsAny(table, " ;\t\n") {
		t.Fatalf("unsafe table name %q", table)
	}
	query := "SELECT count(*) FROM " + table + " WHERE tenant_ref = $1 AND (" + predicate + ")"
	args := []any{harness.tenant}
	if value != nil {
		args = append(args, value)
	}
	var count int
	if err := harness.inspectionDB.QueryRow(context.Background(), query, args...).Scan(&count); err != nil {
		t.Fatalf("count %s: %v", table, err)
	}
	return count
}
