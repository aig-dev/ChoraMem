package postgres_test

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"sort"
	"strings"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
)

func TestMigrateInstallsNeutralMemoryContextTables(t *testing.T) {
	harness := newHarness(t)
	for _, table := range []string{"memory_contexts", "memory_context_items", "memory_delivery_receipts"} {
		var exists bool
		if err := harness.inspectionDB.QueryRow(context.Background(), `
			SELECT to_regclass('public.' || $1) IS NOT NULL
		`, table).Scan(&exists); err != nil {
			t.Fatalf("inspect %s migration: %v", table, err)
		}
		if !exists {
			t.Fatalf("migration 008 did not install %s", table)
		}
	}
	rows, err := harness.inspectionDB.Query(context.Background(), `
		SELECT column_name
		FROM information_schema.columns
		WHERE table_schema = 'public' AND table_name = 'memory_context_items'
		ORDER BY ordinal_position
	`)
	if err != nil {
		t.Fatalf("inspect memory_context_items columns: %v", err)
	}
	defer rows.Close()
	var columns []string
	for rows.Next() {
		var column string
		if err := rows.Scan(&column); err != nil {
			t.Fatalf("scan memory_context_items column: %v", err)
		}
		columns = append(columns, column)
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("iterate memory_context_items columns: %v", err)
	}
	wantColumns := []string{
		"tenant_ref", "context_ref", "item_kind", "memory_ref", "item_order",
		"query_source_ref", "matched_memory_ref", "evidence_episode_ref", "evidence_source_ref",
	}
	if !reflect.DeepEqual(columns, wantColumns) {
		t.Fatalf("memory_context_items columns = %#v; want exact ref/path-only schema %#v", columns, wantColumns)
	}
}

func TestMemoryContextItemsRejectCrossKindReferenceCollision(t *testing.T) {
	harness := newHarness(t)
	evidence := harness.source("cross-kind-item-constraint")
	evidence.Scope.SessionRef = "session-cross-kind-item-constraint"
	episodeRef := harness.materializeEpisodeFromSituation(t, "cross-kind-item-constraint", evidence)
	ctx := context.Background()
	tx, err := harness.inspectionDB.Begin(ctx)
	if err != nil {
		t.Fatalf("begin cross-kind item constraint fixture: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	contextRef := "context-cross-kind-item-" + harness.tenant
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_contexts (
			tenant_ref, context_ref, scope_kind, agent_ref, relationship_ref,
			session_ref, run_ref, request_hash, policy_ref
		) VALUES ($1, $2, 'relationship', 'agent-1', 'relationship-1', $3, $4, $5, 'test-policy')
	`, harness.tenant, contextRef, evidence.Scope.SessionRef, "run-cross-kind-item", make([]byte, 32)); err != nil {
		t.Fatalf("insert cross-kind constraint MemoryContext: %v", err)
	}
	itemRef := "same-item-ref-" + harness.tenant
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_context_items (
			tenant_ref, context_ref, item_kind, memory_ref, item_order,
			query_source_ref, matched_memory_ref, evidence_episode_ref, evidence_source_ref
		) VALUES ($1, $2, 'recollection', $3, 0, $4, $3, $5, $4)
	`, harness.tenant, contextRef, itemRef, evidence.Ref, episodeRef); err != nil {
		t.Fatalf("insert first cross-kind item: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_context_items (
			tenant_ref, context_ref, item_kind, memory_ref, item_order,
			query_source_ref, matched_memory_ref, evidence_episode_ref, evidence_source_ref
		) VALUES ($1, $2, 'disposition', $3, 1, $4, $3, $5, $4)
	`, harness.tenant, contextRef, itemRef, evidence.Ref, episodeRef); err == nil {
		t.Fatal("cross-kind duplicate memory_ref insert succeeded; want database uniqueness rejection")
	}
}

func TestSelectMemorySeparatesKindsScopesAndFreezesExactOldVersions(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "memory-context-first", "session-memory-context-first")
	second := harness.materializeEpisode(t, "memory-context-second", "session-memory-context-second")
	worker := &scriptedWorker{taggedText: strings.Join([]string{
		taggedMemoryChange(consolidation.TargetNewRecollection, consolidation.ApplicationSelf, consolidation.ChangeText, "shared cue self recollection", first),
		taggedMemoryChange(consolidation.TargetNewRecollection, consolidation.ApplicationOther, consolidation.ChangeText, "shared cue other recollection", first),
		taggedMemoryChange(consolidation.TargetNewRecollection, consolidation.ApplicationRelation, consolidation.ChangeText, "shared cue relation recollection", first),
		taggedMemoryChange(consolidation.TargetNewRecollection, consolidation.ApplicationSituation, consolidation.ChangeText, "shared cue situation recollection", first),
		taggedMemoryChange(consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText, "shared cue exact relationship disposition", first, second),
	}, "\n")}
	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "memory-context-kinds-job", EpisodeRefs: []string{first, second},
	}, worker)
	if err != nil {
		t.Fatalf("form relationship memories: %v", err)
	}
	if len(receipt.RecollectionVersionRefs) != 4 || len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("formation receipt = %#v", receipt)
	}
	relationshipDisposition := receipt.DispositionVersionRefs[0]

	agentScope := harness.source("agent-baseline-scope").Scope
	agentScope.Kind = ledger.ScopeKindAgent
	agentScope.RelationshipRef = ""
	agentDisposition := harness.formSeedInScope(t, "memory-context-agent-baseline", "shared cue agent baseline disposition", agentScope)
	siblingDisposition := harness.formSeed(t, "memory-context-sibling", "shared cue sibling disposition", "relationship-2")

	situation := harness.source("memory-context-current")
	situation.Scope.SessionRef = "session-memory-context-current"
	situation.Text = "shared cue"
	runRef, groupRef := "run-memory-context-current", "group-memory-context-current"
	harness.observe(t, "memory-context-current", situation, ledger.EpisodeBinding{
		RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation,
	})
	request := selection.SelectRequest{
		Scope: situation.Scope, RunRef: runRef, SituationSourceRefs: []string{situation.Ref},
		Constitution: selection.Constitution{MemoryRef: "constitution-v1", Text: "stay curious"},
	}

	want, err := harness.store.SelectMemory(context.Background(), request)
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	applications := make([]selection.ApplicationScope, 0, len(want.Recollections))
	for _, recollection := range want.Recollections {
		applications = append(applications, recollection.Application)
	}
	sort.Slice(applications, func(i, j int) bool { return applications[i] < applications[j] })
	if !reflect.DeepEqual(applications, []selection.ApplicationScope{
		selection.ApplicationScopeOther,
		selection.ApplicationScopeRelation,
		selection.ApplicationScopeSelf,
		selection.ApplicationScopeSituation,
	}) {
		t.Fatalf("Recollection applications = %#v", applications)
	}
	dispositions := make(map[string]selection.ApplicationScope, len(want.Dispositions))
	for _, disposition := range want.Dispositions {
		dispositions[disposition.MemoryRef] = disposition.Application
	}
	if !reflect.DeepEqual(dispositions, map[string]selection.ApplicationScope{
		agentDisposition:        selection.ApplicationScopeSelf,
		relationshipDisposition: selection.ApplicationScopeRelation,
	}) {
		t.Fatalf("Disposition selection = %#v; sibling %s must be absent", dispositions, siblingDisposition)
	}

	deliveredRefs := []string{want.Constitution.MemoryRef, want.Recollections[0].MemoryRef, agentDisposition, relationshipDisposition}
	delivery, err := harness.store.RecordMemoryDelivery(context.Background(), ledger.MemoryDelivery{
		IdempotencyKey: "memory-context-delivery", Scope: situation.Scope, RunRef: runRef,
		MemoryContextRef: want.Ref, DeliveredMemoryRefs: deliveredRefs,
	})
	if err != nil || delivery.Ref == "" {
		t.Fatalf("RecordMemoryDelivery = (%#v, %v)", delivery, err)
	}
	if count := harness.count(t, "memory_delivery_receipts", "receipt_ref = $2", delivery.Ref); count != 1 {
		t.Fatalf("memory delivery receipts = %d; want 1", count)
	}
	supersedeSelectedVersions(t, harness, want.Recollections[0].MemoryRef, relationshipDisposition)
	got, err := harness.store.SelectMemory(context.Background(), request)
	if err != nil {
		t.Fatalf("frozen SelectMemory retry: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("frozen retry = %#v; want exact old refs/text %#v", got, want)
	}
	changed := request
	changed.Constitution.Text = "changed baseline"
	if _, err := harness.store.SelectMemory(context.Background(), changed); !errors.Is(err, selection.ErrRunConflict) {
		t.Fatalf("changed same-run request error = %v; want ErrRunConflict", err)
	}
}

func TestSelectMemoryRejectsConstitutionDispositionReferenceCollisionBeforeFreeze(t *testing.T) {
	harness := newHarness(t)
	dispositionRef := harness.formSeed(t, "ambiguous-constitution-disposition", "ambiguous memory signal", "relationship-1")
	current := harness.source("ambiguous-constitution-current")
	current.Scope.SessionRef = "session-ambiguous-constitution"
	current.Text = "ambiguous memory signal"
	harness.observe(t, "ambiguous-constitution-current", current, ledger.EpisodeBinding{})
	runRef := "run-ambiguous-constitution"

	_, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: runRef, SituationSourceRefs: []string{current.Ref},
		Constitution: selection.Constitution{MemoryRef: dispositionRef, Text: "external baseline"},
	})
	if !errors.Is(err, selection.ErrAmbiguousMemoryRef) {
		t.Fatalf("SelectMemory collision error = %v; want ErrAmbiguousMemoryRef", err)
	}
	if count := harness.count(t, "memory_contexts", "run_ref = $2", runRef); count != 0 {
		t.Fatalf("ambiguous selection froze %d MemoryContexts; want 0", count)
	}
	if count := harness.count(t, "memory_context_items", "TRUE", nil); count != 0 {
		t.Fatalf("ambiguous selection froze %d MemoryContext items; want 0", count)
	}
}

func TestSelectMemoryRejectsRecollectionDispositionReferenceCollisionBeforeFreeze(t *testing.T) {
	harness := newHarness(t)
	basisSituation := harness.source("ambiguous-kinds-basis")
	basisSituation.Scope.SessionRef = "session-ambiguous-kinds-basis"
	basisSituation.Text = "ambiguous kinds signal"
	basisEpisode := harness.materializeEpisodeFromSituation(t, "ambiguous-kinds-basis", basisSituation)
	ambiguousRef := "ambiguous-kinds-" + harness.tenant + "@1"
	insertAmbiguousMemoryKindFixtures(t, harness, ambiguousRef, basisEpisode)

	current := harness.source("ambiguous-kinds-current")
	current.Scope.SessionRef = "session-ambiguous-kinds-current"
	current.Text = "ambiguous kinds signal"
	harness.observe(t, "ambiguous-kinds-current", current, ledger.EpisodeBinding{})
	runRef := "run-ambiguous-kinds"
	_, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: runRef, SituationSourceRefs: []string{current.Ref},
	})
	if !errors.Is(err, selection.ErrAmbiguousMemoryRef) {
		t.Fatalf("SelectMemory cross-kind collision error = %v; want ErrAmbiguousMemoryRef", err)
	}
	if count := harness.count(t, "memory_contexts", "run_ref = $2", runRef); count != 0 {
		t.Fatalf("cross-kind ambiguous selection froze %d MemoryContexts; want 0", count)
	}
}

func TestRecordMemoryDeliveryRejectsAmbiguousConstitutionDispositionReference(t *testing.T) {
	harness := newHarness(t)
	dispositionRef := harness.formSeed(t, "ambiguous-delivery-disposition", "ambiguous delivery signal", "relationship-1")
	evidence := harness.source("ambiguous-delivery-evidence")
	evidence.Scope.SessionRef = "session-ambiguous-delivery-evidence"
	evidence.Text = "ambiguous delivery signal"
	episodeRef := harness.materializeEpisodeFromSituation(t, "ambiguous-delivery-evidence", evidence)
	contextRef := "context-ambiguous-delivery-" + harness.tenant
	runRef := "run-ambiguous-delivery"
	sessionRef := "session-ambiguous-delivery"
	ctx := context.Background()
	tx, err := harness.inspectionDB.Begin(ctx)
	if err != nil {
		t.Fatalf("begin ambiguous delivery fixture: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_contexts (
			tenant_ref, context_ref, scope_kind, agent_ref, relationship_ref,
			session_ref, run_ref, request_hash, policy_ref,
			constitution_ref, constitution_text
		) VALUES ($1, $2, 'relationship', 'agent-1', 'relationship-1', $3, $4, $5, 'test-policy', $6, 'external baseline')
	`, harness.tenant, contextRef, sessionRef, runRef, make([]byte, 32), dispositionRef); err != nil {
		t.Fatalf("insert ambiguous delivery MemoryContext: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO memory_context_items (
			tenant_ref, context_ref, item_kind, memory_ref, item_order,
			query_source_ref, matched_memory_ref, evidence_episode_ref, evidence_source_ref
		) VALUES ($1, $2, 'disposition', $3, 0, $4, $3, $5, $4)
	`, harness.tenant, contextRef, dispositionRef, evidence.Ref, episodeRef); err != nil {
		t.Fatalf("insert ambiguous delivery MemoryContext item: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("commit ambiguous delivery fixture: %v", err)
	}

	_, err = harness.store.RecordMemoryDelivery(ctx, ledger.MemoryDelivery{
		IdempotencyKey: "ambiguous-delivery", Scope: ledger.Scope{
			Kind: ledger.ScopeKindRelationship, TenantRef: harness.tenant,
			AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: sessionRef,
		},
		RunRef: runRef, MemoryContextRef: contextRef, DeliveredMemoryRefs: []string{dispositionRef},
	})
	if !errors.Is(err, ledger.ErrInvalidMemoryDelivery) {
		t.Fatalf("RecordMemoryDelivery ambiguous ref error = %v; want ErrInvalidMemoryDelivery", err)
	}
	if count := harness.count(t, "memory_delivery_receipts", "context_ref = $2", contextRef); count != 0 {
		t.Fatalf("ambiguous delivery wrote %d receipts; want 0", count)
	}
	if count := harness.count(t, "outcome_events", "run_ref = $2", runRef); count != 0 {
		t.Fatalf("ambiguous delivery granted %d Outcome feedback rows; want 0", count)
	}
}

func TestSelectMemoryOwnerBoundPreservesExactRelationshipLane(t *testing.T) {
	harness := newHarness(t)
	agentScope := harness.source("owner-bound-agent").Scope
	agentScope.Kind = ledger.ScopeKindAgent
	agentScope.RelationshipRef = ""
	agentSituation := harness.source("owner-bound-agent-situation")
	agentSituation.Scope = agentScope
	agentSituation.Scope.SessionRef = "session-owner-bound-agent"
	agentSituation.Text = "baseline filler"
	agentEpisode := harness.materializeEpisodeFromSituation(t, "owner-bound-agent", agentSituation)
	insertDispositionFixtures(t, harness, agentScope, agentEpisode, 41, "a-baseline", "baseline filler")

	exact := harness.formSeed(t, "owner-bound-exact", "exact relationship signal", "relationship-1")
	current := harness.source("owner-bound-current")
	current.Scope.SessionRef = "session-owner-bound-current"
	current.Text = "exact relationship signal"
	harness.observe(t, "owner-bound-current", current, ledger.EpisodeBinding{})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-owner-bound-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory owner-bound union: %v", err)
	}
	if !containsDisposition(contextValue.Dispositions, exact) {
		t.Fatalf("exact relationship disposition %s was erased by capped agent baseline: %#v", exact, contextValue.Dispositions)
	}
}

func TestSelectMemoryRelationshipTieKeepsExactOwnerInsideDispositionBudget(t *testing.T) {
	harness := newHarness(t)
	agentScope := harness.source("owner-tie-agent").Scope
	agentScope.Kind = ledger.ScopeKindAgent
	agentScope.RelationshipRef = ""
	agentSituation := harness.source("owner-tie-agent-situation")
	agentSituation.Scope = agentScope
	agentSituation.Scope.SessionRef = "session-owner-tie-agent"
	agentSituation.Text = "equal owner signal"
	agentEpisode := harness.materializeEpisodeFromSituation(t, "owner-tie-agent", agentSituation)
	insertDispositionFixtures(t, harness, agentScope, agentEpisode, 8, "tie-agent", "equal owner signal")

	exact := harness.formSeed(t, "owner-tie-exact", "equal owner signal", "relationship-1")
	current := harness.source("owner-tie-current")
	current.Scope.SessionRef = "session-owner-tie-current"
	current.Text = "equal owner signal"
	harness.observe(t, "owner-tie-current", current, ledger.EpisodeBinding{})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-owner-tie-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory equal owner tie: %v", err)
	}
	if len(contextValue.Dispositions) != 8 {
		t.Fatalf("Disposition budget selected %d items; want 8", len(contextValue.Dispositions))
	}
	if contextValue.Dispositions[0].MemoryRef != exact {
		t.Fatalf("equal-score first Disposition = %s; want exact relationship %s", contextValue.Dispositions[0].MemoryRef, exact)
	}
}

func TestSelectMemoryStrongerAgentDispositionStillPrecedesExactRelationship(t *testing.T) {
	harness := newHarness(t)
	agentScope := harness.source("stronger-agent-scope").Scope
	agentScope.Kind = ledger.ScopeKindAgent
	agentScope.RelationshipRef = ""
	agent := harness.formSeedInScope(t, "stronger-agent", "stronger agent memory", agentScope)
	exact := harness.formSeed(t, "weaker-exact", "stronger agent", "relationship-1")

	current := harness.source("stronger-agent-current")
	current.Scope.SessionRef = "session-stronger-agent-current"
	current.Text = "stronger agent memory"
	harness.observe(t, "stronger-agent-current", current, ledger.EpisodeBinding{})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-stronger-agent-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory stronger agent: %v", err)
	}
	if len(contextValue.Dispositions) < 2 {
		t.Fatalf("Disposition selection = %#v; want agent %s and exact %s", contextValue.Dispositions, agent, exact)
	}
	if contextValue.Dispositions[0].MemoryRef != agent || !containsDisposition(contextValue.Dispositions, exact) {
		t.Fatalf("Disposition relevance order = %#v; want stronger agent %s before exact %s", contextValue.Dispositions, agent, exact)
	}
}

func TestSelectMemoryRejectsCrossOwnerRecollectionBasis(t *testing.T) {
	harness := newHarness(t)
	siblingSituation := harness.source("cross-owner-sibling")
	siblingSituation.Scope.RelationshipRef = "relationship-2"
	siblingSituation.Scope.SessionRef = "session-cross-owner-sibling"
	siblingSituation.Text = "cross owner leak signal"
	siblingEpisode := harness.materializeEpisodeFromSituation(t, "cross-owner-sibling", siblingSituation)
	recollectionRef := "cross-owner-recollection-" + harness.tenant
	versionRef := recollectionRef + "@1"
	ctx := context.Background()
	tx, err := harness.inspectionDB.Begin(ctx)
	if err != nil {
		t.Fatalf("begin corrupt Basis fixture: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollections (tenant_ref, scope_kind, agent_ref, relationship_ref, recollection_ref)
		VALUES ($1, 'relationship', 'agent-1', 'relationship-1', $2)
	`, harness.tenant, recollectionRef); err != nil {
		t.Fatalf("insert corrupt cross-owner Recollection: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		) VALUES ($1, $2, $3, 1, 'cross owner leak signal', 'relation', 'active', 'cross-owner-job')
	`, harness.tenant, recollectionRef, versionRef); err != nil {
		t.Fatalf("insert corrupt cross-owner RecollectionVersion: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_basis_links (tenant_ref, recollection_version_ref, episode_ref, role)
		VALUES ($1, $2, $3, 'formation')
	`, harness.tenant, versionRef, siblingEpisode); err != nil {
		t.Fatalf("insert corrupt cross-owner Recollection Basis: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("commit corrupt cross-owner fixture: %v", err)
	}
	current := harness.source("cross-owner-current")
	current.Scope.SessionRef = "session-cross-owner-current"
	current.Text = "cross owner leak signal"
	harness.observe(t, "cross-owner-current", current, ledger.EpisodeBinding{})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-cross-owner-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory corrupt Basis: %v", err)
	}
	for _, recollection := range contextValue.Recollections {
		if recollection.MemoryRef == versionRef {
			t.Fatalf("cross-owner Basis leaked Recollection into MemoryContext: %#v", contextValue.Recollections)
		}
	}
}

func TestDeliveredRecollectionDoesNotGainDispositionFeedbackAuthority(t *testing.T) {
	harness := newHarness(t)
	basis := harness.materializeEpisode(t, "recollection-delivery-basis", "session-recollection-delivery-basis")
	formed, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-delivery-form", EpisodeRefs: []string{basis},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection,
		consolidation.ApplicationRelation,
		consolidation.ChangeText,
		"remember the exact clarification",
		basis,
	)})
	if err != nil || len(formed.RecollectionVersionRefs) != 1 {
		t.Fatalf("form delivered Recollection = (%#v, %v)", formed, err)
	}
	recollectionRef := formed.RecollectionVersionRefs[0]
	situation := harness.source("recollection-delivery-situation")
	situation.Scope.SessionRef = "session-recollection-delivery"
	situation.Text = "remember the exact clarification"
	run := activatedRun{
		scope: situation.Scope, runRef: "run-recollection-delivery", groupRef: "group-recollection-delivery",
		situationRef: situation.Ref,
	}
	harness.observe(t, "recollection-delivery-situation", situation, ledger.EpisodeBinding{
		RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleSituation,
	})
	run.context, err = harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: run.scope, RunRef: run.runRef, SituationSourceRefs: []string{run.situationRef},
	})
	if err != nil || len(run.context.Recollections) != 1 || run.context.Recollections[0].MemoryRef != recollectionRef {
		t.Fatalf("select delivered Recollection = (%#v, %v)", run.context, err)
	}
	delivery, err := harness.store.RecordMemoryDelivery(context.Background(), ledger.MemoryDelivery{
		IdempotencyKey: "recollection-only-delivery", Scope: run.scope, RunRef: run.runRef,
		MemoryContextRef: run.context.Ref, DeliveredMemoryRefs: []string{recollectionRef},
	})
	if err != nil {
		t.Fatalf("deliver Recollection: %v", err)
	}
	if delivery.Ref == "" {
		t.Fatal("deliver Recollection returned empty receipt")
	}
	episode := harness.materializeActivatedRun(t, "recollection-delivery", run, "acted after reading recollection")
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		recollectionRef,
		consolidation.ApplicationRelation,
		consolidation.ChangeReenact,
		"",
		episode,
	)}
	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-delivery-feedback", EpisodeRefs: []string{episode},
	}, worker)
	if err != nil {
		t.Fatalf("consolidate delivered Recollection: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 0 || strings.Contains(worker.request.WindowText, "ELIGIBLE_DISPOSITION "+recollectionRef) {
		t.Fatalf("delivered Recollection gained Disposition authority: receipt=%#v request=%s", receipt, worker.request.WindowText)
	}
	if !strings.Contains(worker.request.WindowText, "ELIGIBLE_RECOLLECTION "+recollectionRef) {
		t.Fatalf("Recollection lost its direct reconsolidation lane: %s", worker.request.WindowText)
	}
}

func supersedeSelectedVersions(t *testing.T, harness *testHarness, recollectionVersionRef, dispositionVersionRef string) {
	t.Helper()
	ctx := context.Background()
	tx, err := harness.inspectionDB.Begin(ctx)
	if err != nil {
		t.Fatalf("begin version supersession fixture: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `
		UPDATE recollection_versions SET status = 'superseded'
		WHERE tenant_ref = $1 AND recollection_version_ref = $2
	`, harness.tenant, recollectionVersionRef); err != nil {
		t.Fatalf("supersede RecollectionVersion: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		)
		SELECT tenant_ref, recollection_ref, recollection_version_ref || '-next', version_number + 1,
		       text || ' revised', application_scope, 'active', 'frozen-retry-recollection'
		FROM recollection_versions WHERE tenant_ref = $1 AND recollection_version_ref = $2
	`, harness.tenant, recollectionVersionRef); err != nil {
		t.Fatalf("insert revised RecollectionVersion: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		UPDATE seed_versions SET status = 'superseded'
		WHERE tenant_ref = $1 AND seed_version_ref = $2
	`, harness.tenant, dispositionVersionRef); err != nil {
		t.Fatalf("supersede DispositionVersion: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO seed_versions (
			tenant_ref, seed_ref, seed_version_ref, version_number,
			tendency_text, status, origin_job_ref
		)
		SELECT tenant_ref, seed_ref, seed_version_ref || '-next', version_number + 1,
		       tendency_text || ' revised', 'active', 'frozen-retry-disposition'
		FROM seed_versions WHERE tenant_ref = $1 AND seed_version_ref = $2
	`, harness.tenant, dispositionVersionRef); err != nil {
		t.Fatalf("insert revised DispositionVersion: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("commit version supersession fixture: %v", err)
	}
}

func insertDispositionFixtures(t *testing.T, harness *testHarness, scope ledger.Scope, episodeRef string, count int, prefix, text string) {
	t.Helper()
	for index := 0; index < count; index++ {
		memoryRef := fmt.Sprintf("%s-%03d-%s", prefix, index, harness.tenant)
		ctx := context.Background()
		tx, err := harness.inspectionDB.Begin(ctx)
		if err != nil {
			t.Fatalf("begin bounded Disposition fixture %d: %v", index, err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO disposition_seeds (tenant_ref, scope_kind, agent_ref, relationship_ref, seed_ref)
			VALUES ($1, $2, $3, $4, $5)
		`, harness.tenant, scope.Kind, scope.AgentRef, scope.RelationshipRef, memoryRef); err != nil {
			_ = tx.Rollback(ctx)
			t.Fatalf("insert bounded Disposition %d: %v", index, err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO seed_versions (
				tenant_ref, seed_ref, seed_version_ref, version_number,
				tendency_text, status, origin_job_ref
			) VALUES ($1, $2, $3, 1, $4, 'active', 'owner-bound-fixture')
		`, harness.tenant, memoryRef, memoryRef+"@1", text); err != nil {
			_ = tx.Rollback(ctx)
			t.Fatalf("insert bounded DispositionVersion %d: %v", index, err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO seed_basis_links (tenant_ref, seed_version_ref, episode_ref, role)
			VALUES ($1, $2, $3, 'formation')
		`, harness.tenant, memoryRef+"@1", episodeRef); err != nil {
			_ = tx.Rollback(ctx)
			t.Fatalf("insert bounded Disposition Basis %d: %v", index, err)
		}
		if err := tx.Commit(ctx); err != nil {
			t.Fatalf("commit bounded Disposition fixture %d: %v", index, err)
		}
	}
}

func insertAmbiguousMemoryKindFixtures(t *testing.T, harness *testHarness, versionRef, episodeRef string) {
	t.Helper()
	ctx := context.Background()
	tx, err := harness.inspectionDB.Begin(ctx)
	if err != nil {
		t.Fatalf("begin ambiguous memory-kind fixture: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	recollectionRef := "ambiguous-recollection-" + harness.tenant
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollections (tenant_ref, scope_kind, agent_ref, relationship_ref, recollection_ref)
		VALUES ($1, 'relationship', 'agent-1', 'relationship-1', $2)
	`, harness.tenant, recollectionRef); err != nil {
		t.Fatalf("insert ambiguous Recollection: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		) VALUES ($1, $2, $3, 1, 'ambiguous kinds signal', 'relation', 'active', 'ambiguous-kinds-recollection')
	`, harness.tenant, recollectionRef, versionRef); err != nil {
		t.Fatalf("insert ambiguous RecollectionVersion: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO recollection_basis_links (tenant_ref, recollection_version_ref, episode_ref, role)
		VALUES ($1, $2, $3, 'formation')
	`, harness.tenant, versionRef, episodeRef); err != nil {
		t.Fatalf("insert ambiguous Recollection Basis: %v", err)
	}
	dispositionRef := "ambiguous-disposition-" + harness.tenant
	if _, err := tx.Exec(ctx, `
		INSERT INTO disposition_seeds (tenant_ref, scope_kind, agent_ref, relationship_ref, seed_ref)
		VALUES ($1, 'relationship', 'agent-1', 'relationship-1', $2)
	`, harness.tenant, dispositionRef); err != nil {
		t.Fatalf("insert ambiguous Disposition: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO seed_versions (
			tenant_ref, seed_ref, seed_version_ref, version_number,
			tendency_text, status, origin_job_ref
		) VALUES ($1, $2, $3, 1, 'ambiguous kinds signal', 'active', 'ambiguous-kinds-disposition')
	`, harness.tenant, dispositionRef, versionRef); err != nil {
		t.Fatalf("insert ambiguous DispositionVersion: %v", err)
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO seed_basis_links (tenant_ref, seed_version_ref, episode_ref, role)
		VALUES ($1, $2, $3, 'formation')
	`, harness.tenant, versionRef, episodeRef); err != nil {
		t.Fatalf("insert ambiguous Disposition Basis: %v", err)
	}
	if err := tx.Commit(ctx); err != nil {
		t.Fatalf("commit ambiguous memory-kind fixture: %v", err)
	}
}

func containsDisposition(dispositions []selection.Disposition, ref string) bool {
	for _, disposition := range dispositions {
		if disposition.MemoryRef == ref {
			return true
		}
	}
	return false
}
