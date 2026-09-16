// Package contracttest contains the database-neutral CoreStore release gate.
// Every relational adapter must run this exact suite against its real engine.
package contracttest

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/aig-dev/ChoraMem/internal/storage"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

var scenarioSequence atomic.Uint64

// Factory returns a Store connected to a fresh isolated database namespace.
// Run owns Close; the adapter wrapper owns namespace cleanup.
type Factory func(*testing.T) storage.CoreStore

// IndexedFactory returns a Store wired to the supplied disposable projection.
// The relational adapter must still rehydrate every returned ref canonically.
type IndexedFactory func(*testing.T, memoryindex.Index) storage.CoreStore

// Run executes the same observable causal contract for every adapter.
func Run(t *testing.T, factory Factory) {
	t.Helper()

	t.Run("migration_is_idempotent", func(t *testing.T) {
		store := migratedStore(t, factory)
		if err := store.Migrate(testContext(t)); err != nil {
			t.Fatalf("second Migrate: %v", err)
		}
	})

	t.Run("observe_is_atomic_idempotent_and_concurrent", func(t *testing.T) {
		store := migratedStore(t, factory)
		scenario := newScenario(store)
		event := scenario.source("concurrent-source", "same immutable input")

		const callers = 8
		start := make(chan struct{})
		receipts := make(chan ledger.ObserveReceipt, callers)
		errorsCh := make(chan error, callers)
		var wait sync.WaitGroup
		for index := 0; index < callers; index++ {
			wait.Add(1)
			go func() {
				defer wait.Done()
				<-start
				receipt, err := store.Observe(context.Background(), "concurrent-key", event, ledger.EpisodeBinding{})
				receipts <- receipt
				errorsCh <- err
			}()
		}
		close(start)
		wait.Wait()
		close(receipts)
		close(errorsCh)
		for err := range errorsCh {
			if err != nil {
				t.Fatalf("concurrent Observe: %v", err)
			}
		}
		for receipt := range receipts {
			if receipt != (ledger.ObserveReceipt{SourceEventRef: event.Ref}) {
				t.Fatalf("concurrent receipt = %#v", receipt)
			}
		}

		conflict := event
		conflict.Text = "different immutable input"
		if _, err := store.Observe(testContext(t), "concurrent-key", conflict, ledger.EpisodeBinding{}); !errors.Is(err, ledger.ErrIdempotencyConflict) {
			t.Fatalf("conflicting idempotency key error = %v; want ErrIdempotencyConflict", err)
		}
	})

	t.Run("invalid_worker_batch_commits_no_partial_memory", func(t *testing.T) {
		store := migratedStore(t, factory)
		scenario := newScenario(store)
		episode, _, _ := scenario.materializeEpisode(t, "atomic-basis", "只提交完整有效的后台语义批次")
		valid := taggedChange(
			consolidation.TargetNewRecollection,
			consolidation.ApplicationOther,
			consolidation.ChangeText,
			"只提交完整有效的后台语义批次",
			episode,
		)
		invalid := strings.Join([]string{
			"TARGET", "NOT_AN_ALLOWED_TARGET", "APPLICATION", "OTHER",
			"CHANGE", "TEXT invalid", "BASIS", episode,
		}, "\n")
		receipt, err := store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "atomic-batch-job", EpisodeRefs: []string{episode},
		}, textWorker(valid+"\n\n"+invalid))
		if err != nil {
			t.Fatalf("invalid atomic batch: %v", err)
		}
		if len(receipt.RecollectionVersionRefs) != 0 || len(receipt.DispositionVersionRefs) != 0 {
			t.Fatalf("invalid atomic batch committed partial memory: %#v", receipt)
		}
		contextValue := scenario.selectText(t, "atomic-select", "只提交完整有效的后台语义批次")
		if len(contextValue.Recollections) != 0 || len(contextValue.Dispositions) != 0 {
			t.Fatalf("invalid atomic batch changed future selection: %#v", contextValue)
		}
	})

	t.Run("recollection_is_formed_selected_and_projected", func(t *testing.T) {
		store := migratedStore(t, factory)
		scenario := newScenario(store)
		episode, _, _ := scenario.materializeEpisode(t, "recollection-basis", "用户喜欢在结论前看因果链")
		text := "用户喜欢在结论前看因果链"
		receipt, err := store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "recollection-job", EpisodeRefs: []string{episode},
		}, textWorker(taggedChange(
			consolidation.TargetNewRecollection,
			consolidation.ApplicationOther,
			consolidation.ChangeText,
			text,
			episode,
		)))
		if err != nil {
			t.Fatalf("form Recollection: %v", err)
		}
		if len(receipt.RecollectionVersionRefs) != 1 {
			t.Fatalf("Recollection receipt = %#v", receipt)
		}
		contextValue := scenario.selectText(t, "recollection-select", text)
		if len(contextValue.Recollections) != 1 || contextValue.Recollections[0].MemoryRef != receipt.RecollectionVersionRefs[0] {
			t.Fatalf("selected Recollections = %#v", contextValue.Recollections)
		}

		foundProjection := false
		seenOperations := make([]string, 0, 8)
		for attempt := 0; attempt < 8; attempt++ {
			operation, found, err := store.LeaseMemoryIndexOperation(testContext(t), time.Now().Add(time.Minute))
			if err != nil {
				t.Fatalf("lease MemoryIndex operation: %v", err)
			}
			if !found {
				break
			}
			document, exists, err := store.MemoryIndexDocument(testContext(t), operation)
			if err != nil {
				t.Fatalf("rehydrate MemoryIndex operation: %v", err)
			}
			if exists && document.Kind == memoryindex.KindRecollection && document.Ref == receipt.RecollectionVersionRefs[0] {
				foundProjection = true
			}
			seenOperations = append(seenOperations, fmt.Sprintf(
				"%s:%s:%s exists=%t", operation.Action, operation.Kind, operation.Ref, exists,
			))
			if err := store.AcknowledgeMemoryIndexOperation(testContext(t), operation); err != nil {
				t.Fatalf("acknowledge MemoryIndex operation: %v", err)
			}
		}
		if !foundProjection {
			t.Fatalf("Recollection never appeared in the durable MemoryIndex operation stream; saw %v", seenOperations)
		}
	})

	t.Run("scheduler_forms_seed_from_repeated_experience", func(t *testing.T) {
		store := migratedStore(t, factory)
		scenario := newScenario(store)
		first, _, _ := scenario.materializeEpisode(t, "scheduler-one", "先确认问题再给建议")
		second, _, _ := scenario.materializeEpisode(t, "scheduler-two", "先确认问题再给建议")
		time.Sleep(time.Millisecond)
		processor := scheduler.New(store, store, textWorker(taggedChange(
			consolidation.TargetNewDisposition,
			consolidation.ApplicationRelation,
			consolidation.ChangeText,
			"先确认问题再给建议",
			first,
			second,
		)), scheduler.Config{
			QuietPeriod: time.Nanosecond, LeaseDuration: time.Minute,
			WorkerTimeout: time.Minute, RetryDelay: time.Millisecond,
			PollInterval: time.Millisecond, MaxEpisodes: 32,
		})
		worked, err := processor.ProcessOne(testContext(t))
		if err != nil {
			t.Fatalf("ProcessOne: %v", err)
		}
		if !worked {
			t.Fatal("ProcessOne found no ready causal window")
		}
		contextValue := scenario.selectText(t, "scheduler-select", "先确认问题再给建议")
		if len(contextValue.Dispositions) != 1 {
			t.Fatalf("scheduled formation selected Dispositions = %#v", contextValue.Dispositions)
		}
	})

	t.Run("delivery_and_outcome_revise_future_selection", func(t *testing.T) {
		store := migratedStore(t, factory)
		scenario := newScenario(store)
		oldText := "先质疑我的架构"
		newText := "先承认我的担忧，再质疑我的架构"
		first, _, _ := scenario.materializeEpisode(t, "form-one", oldText)
		second, _, _ := scenario.materializeEpisode(t, "form-two", oldText)
		formed, err := store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "form-seed-job", EpisodeRefs: []string{first, second},
		}, textWorker(taggedChange(
			consolidation.TargetNewDisposition,
			consolidation.ApplicationRelation,
			consolidation.ChangeText,
			oldText,
			first,
			second,
		)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("form Seed = %#v, %v", formed, err)
		}
		oldVersion := formed.DispositionVersionRefs[0]

		run := scenario.activate(t, "revision", oldText, oldVersion)
		delivery, err := store.RecordMemoryDelivery(testContext(t), ledger.MemoryDelivery{
			IdempotencyKey: "revision-delivery",
			Scope:          run.scope, RunRef: run.runRef, MemoryContextRef: run.memoryContextRef,
			DeliveredMemoryRefs: []string{oldVersion},
		})
		if err != nil {
			t.Fatalf("RecordMemoryDelivery: %v", err)
		}
		episode, agentActRef := scenario.finishActivatedRun(t, run, "我会先承认担忧")
		outcome, err := store.ReportOutcome(testContext(t), ledger.OutcomeReport{
			IdempotencyKey: "revision-outcome",
			Event:          scenario.eventInScope("revision-outcome-source", run.scope, ledger.ActorKindUser, "这样更容易继续讨论"),
			RunRef:         run.runRef, SourceGroupRef: run.groupRef,
			DeliveryReceiptRefs:    []string{delivery.Ref},
			RelatedSourceEventRefs: []string{run.situationRef, agentActRef},
		})
		if err != nil || outcome.EpisodeRef != episode {
			t.Fatalf("ReportOutcome = %#v, %v; episode %s", outcome, err, episode)
		}

		revised, err := store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "revise-seed-job", EpisodeRefs: []string{episode},
			OutcomeEventRefs: []string{outcome.OutcomeEventRef},
		}, textWorker(taggedChange(
			oldVersion,
			consolidation.ApplicationRelation,
			consolidation.ChangeText,
			newText,
			episode,
			outcome.OutcomeEventRef,
		)))
		if err != nil || len(revised.DispositionVersionRefs) != 1 || revised.DispositionVersionRefs[0] == oldVersion {
			t.Fatalf("revise Seed = %#v, %v", revised, err)
		}
		contextValue := scenario.selectText(t, "revised-select", newText)
		if len(contextValue.Dispositions) != 1 || contextValue.Dispositions[0].MemoryRef != revised.DispositionVersionRefs[0] {
			t.Fatalf("future selection = %#v; want revised %s", contextValue.Dispositions, revised.DispositionVersionRefs[0])
		}
	})
}

// RunEpisodeEvidence executes the shared, real-database contract for bounded
// Episode evidence. The Index is intentionally untrusted and in-memory; every
// asserted fact and snapshot comes from the relational Store under test.
func RunEpisodeEvidence(t *testing.T, factory IndexedFactory) {
	t.Helper()

	t.Run("zero_budget_replays_legacy_context_and_nonzero_budget_changes_run_identity", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		episode, _, _ := scenario.materializeEpisodeWithTexts(t, "legacy-zero", "historical source", "historical act", scenario.scope)
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: episode}}
		query := scenario.source("legacy-zero-query", "find history")
		scenario.observe(t, "legacy-zero-query", query, ledger.EpisodeBinding{})
		request := selection.SelectRequest{
			Scope: scenario.scope, RunRef: scenario.prefix + "-legacy-zero-select", SituationSourceRefs: []string{query.Ref},
		}
		frozen, err := store.SelectMemory(testContext(t), request)
		if err != nil || len(frozen.EpisodeEvidence) != 0 {
			t.Fatalf("zero-budget SelectMemory = (%#v, %v); want legacy empty evidence lane", frozen, err)
		}
		replayed, err := store.SelectMemory(testContext(t), request)
		if err != nil || !reflect.DeepEqual(replayed, frozen) {
			t.Fatalf("zero-budget frozen replay = (%#v, %v); want %#v", replayed, err, frozen)
		}
		changed := request
		changed.EpisodeEvidenceMaxBytes = 1024
		if _, err := store.SelectMemory(testContext(t), changed); !errors.Is(err, selection.ErrRunConflict) {
			t.Fatalf("same run with evidence budget error = %v; want ErrRunConflict", err)
		}
		invalid := request
		invalid.RunRef += "-invalid"
		invalid.EpisodeEvidenceMaxBytes = selection.MaxEpisodeEvidenceBytes + 1
		if _, err := store.SelectMemory(testContext(t), invalid); !errors.Is(err, selection.ErrInvalidSelectRequest) {
			t.Fatalf("oversize evidence budget error = %v; want ErrInvalidSelectRequest", err)
		}
	})

	t.Run("episode_evidence_hash_domain_rejects_zero_budget_shape_collision_on_replay", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		episode, _, _ := scenario.materializeEpisodeWithTexts(t, "hash-domain-history", "historical source", "historical act", scenario.scope)
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: episode}}

		for position, event := range []ledger.SourceEvent{
			{Ref: "q", Scope: scenario.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: "query"},
			{Ref: "episode-evidence.v1", Scope: scenario.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: "marker-shaped query"},
			{Ref: "1024", Scope: scenario.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: "numeric-shaped query"},
		} {
			scenario.observe(t, fmt.Sprintf("hash-domain-source-%d", position), event, ledger.EpisodeBinding{})
		}

		enabled := selection.SelectRequest{
			Scope: scenario.scope, RunRef: scenario.prefix + "-hash-domain-enabled-first",
			SituationSourceRefs: []string{"q"}, EpisodeEvidenceMaxBytes: 1024,
		}
		contextValue, err := store.SelectMemory(testContext(t), enabled)
		if err != nil || len(contextValue.EpisodeEvidence) != 1 {
			t.Fatalf("enabled SelectMemory = (%#v, %v); want one Episode evidence item", contextValue, err)
		}
		disabled := enabled
		disabled.SituationSourceRefs = []string{"q", "episode-evidence.v1", "1024"}
		disabled.EpisodeEvidenceMaxBytes = 0
		if _, err := store.SelectMemory(testContext(t), disabled); !errors.Is(err, selection.ErrRunConflict) {
			t.Fatalf("enabled then colliding zero-budget replay error = %v; want ErrRunConflict", err)
		}

		disabled.RunRef = scenario.prefix + "-hash-domain-disabled-first"
		contextValue, err = store.SelectMemory(testContext(t), disabled)
		if err != nil || len(contextValue.EpisodeEvidence) != 0 {
			t.Fatalf("disabled SelectMemory = (%#v, %v); want legacy empty evidence lane", contextValue, err)
		}
		enabled.RunRef = disabled.RunRef
		if _, err := store.SelectMemory(testContext(t), enabled); !errors.Is(err, selection.ErrRunConflict) {
			t.Fatalf("zero-budget then colliding enabled replay error = %v; want ErrRunConflict", err)
		}
	})

	t.Run("episode_evidence_is_exact_owner_not_current_and_preserves_source_text", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		historical, _, _ := scenario.materializeEpisodeWithTexts(t, "evidence-history", "first line  \nsecond line\n", "answer\ncontinued  ", scenario.scope)

		siblingScope := scenario.scope
		siblingScope.RelationshipRef = "relationship-sibling"
		sibling, _, _ := scenario.materializeEpisodeWithTexts(t, "evidence-sibling", "must not leak", "sibling answer", siblingScope)

		agentScope := scenario.scope
		agentScope.Kind = ledger.ScopeKindAgent
		agentScope.RelationshipRef = ""
		agentScope.SessionRef = scenario.prefix + "-session-agent-shared-ref"
		sharedRef := scenario.prefix + "-evidence-current-situation"
		agentSituation := ledger.SourceEvent{Ref: sharedRef, Scope: agentScope, ActorKind: ledger.ActorKindUser, ActorRef: "user-1", Text: "agent baseline same bare ref"}
		scenario.observe(t, "agent-shared-ref-situation", agentSituation, ledger.EpisodeBinding{
			RunRef: scenario.prefix + "-run-agent-shared-ref", SourceGroupRef: scenario.prefix + "-group-agent-shared-ref", Role: ledger.RoleSituation,
		})
		agentAct := scenario.eventInScope("agent-shared-ref-act", agentScope, ledger.ActorKindAgent, "agent baseline act")
		agentEpisode := scenario.observe(t, "agent-shared-ref-act", agentAct, ledger.EpisodeBinding{
			RunRef: scenario.prefix + "-run-agent-shared-ref", SourceGroupRef: scenario.prefix + "-group-agent-shared-ref", Role: ledger.RoleAgentAct,
		}).EpisodeRef

		currentScope := scenario.scope
		currentScope.SessionRef = scenario.prefix + "-session-evidence-current"
		current, currentSituation, _ := scenario.materializeEpisodeWithTexts(t, "evidence-current", "current question", "current answer", currentScope)
		index.candidates = []memoryindex.Candidate{
			{Kind: memoryindex.KindEpisode, Ref: sibling},
			{Kind: memoryindex.KindEpisode, Ref: current},
			{Kind: memoryindex.KindEpisode, Ref: agentEpisode},
			{Kind: memoryindex.KindEpisode, Ref: historical},
			{Kind: memoryindex.KindEpisode, Ref: historical},
			{Kind: memoryindex.KindEpisode, Ref: "missing-episode"},
		}

		contextValue, err := store.SelectMemory(testContext(t), selection.SelectRequest{
			Scope: currentScope, RunRef: scenario.prefix + "-select-evidence-current",
			SituationSourceRefs: []string{currentSituation}, EpisodeEvidenceMaxBytes: 16 * 1024,
		})
		if err != nil {
			t.Fatalf("SelectMemory Episode evidence: %v", err)
		}
		agentText := "SITUATION [user]\nagent baseline same bare ref\nAGENT_ACT [agent]\nagent baseline act"
		wantText := "SITUATION [user]\nfirst line  \nsecond line\n\nAGENT_ACT [agent]\nanswer\ncontinued  "
		want := []selection.EpisodeEvidence{{MemoryRef: agentEpisode, Text: agentText}, {MemoryRef: historical, Text: wantText}}
		if !reflect.DeepEqual(contextValue.EpisodeEvidence, want) {
			t.Fatalf("Episode evidence = %#v; want exact isolated evidence %#v", contextValue.EpisodeEvidence, want)
		}
	})

	t.Run("episode_evidence_budget_skips_oversize_and_keeps_later_complete_entries", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		tooLarge, _, _ := scenario.materializeEpisodeWithTexts(t, "budget-large", strings.Repeat("L", 200), "large answer", scenario.scope)
		fits, _, _ := scenario.materializeEpisodeWithTexts(t, "budget-fits", "fit", "ok", scenario.scope)
		rest, _, _ := scenario.materializeEpisodeWithTexts(t, "budget-rest", "x", "y", scenario.scope)
		index.candidates = []memoryindex.Candidate{
			{Kind: memoryindex.KindEpisode, Ref: tooLarge},
			{Kind: memoryindex.KindEpisode, Ref: fits},
			{Kind: memoryindex.KindEpisode, Ref: rest},
		}
		fitText := "SITUATION [user]\nfit\nAGENT_ACT [agent]\nok"
		restText := "SITUATION [user]\nx\nAGENT_ACT [agent]\ny"
		current := scenario.source("budget-query", "find compact evidence")
		scenario.observe(t, "budget-query", current, ledger.EpisodeBinding{})

		contextValue, err := store.SelectMemory(testContext(t), selection.SelectRequest{
			Scope: scenario.scope, RunRef: scenario.prefix + "-budget-select", SituationSourceRefs: []string{current.Ref},
			EpisodeEvidenceMaxBytes: len([]byte(fitText)) + len([]byte(restText)),
		})
		if err != nil {
			t.Fatalf("SelectMemory bounded Episode evidence: %v", err)
		}
		want := []selection.EpisodeEvidence{{MemoryRef: fits, Text: fitText}, {MemoryRef: rest, Text: restText}}
		if !reflect.DeepEqual(contextValue.EpisodeEvidence, want) {
			t.Fatalf("bounded Episode evidence = %#v; want %#v", contextValue.EpisodeEvidence, want)
		}
	})

	t.Run("episode_evidence_uses_best_occurrence_across_query_lanes", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		first, _, _ := scenario.materializeEpisodeWithTexts(t, "lane-first", "first evidence", "first act", scenario.scope)
		better, _, _ := scenario.materializeEpisodeWithTexts(t, "lane-better", "better occurrence", "better act", scenario.scope)
		filler, _, _ := scenario.materializeEpisodeWithTexts(t, "lane-filler", "filler evidence", "filler act", scenario.scope)
		index.search = func(query memoryindex.Query) []memoryindex.Candidate {
			switch query.Text {
			case "query lane a":
				return []memoryindex.Candidate{
					{Kind: memoryindex.KindEpisode, Ref: first},
					{Kind: memoryindex.KindEpisode, Ref: filler},
					{Kind: memoryindex.KindEpisode, Ref: better},
				}
			case "query lane b":
				return []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: better}}
			default:
				return nil
			}
		}
		queryA := scenario.source("lane-query-a", "query lane a")
		queryB := scenario.source("lane-query-b", "query lane b")
		scenario.observe(t, "lane-query-a", queryA, ledger.EpisodeBinding{})
		scenario.observe(t, "lane-query-b", queryB, ledger.EpisodeBinding{})

		contextValue, err := store.SelectMemory(testContext(t), selection.SelectRequest{
			Scope: scenario.scope, RunRef: scenario.prefix + "-lane-select",
			SituationSourceRefs: []string{queryA.Ref, queryB.Ref}, EpisodeEvidenceMaxBytes: 16 * 1024,
		})
		if err != nil {
			t.Fatalf("SelectMemory multi-query evidence: %v", err)
		}
		got := make([]string, 0, len(contextValue.EpisodeEvidence))
		for _, item := range contextValue.EpisodeEvidence {
			got = append(got, item.MemoryRef)
		}
		want := []string{first, better, filler}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("multi-query Episode evidence order = %#v; want best cross-lane occurrence %#v", got, want)
		}
	})

	t.Run("episode_evidence_snapshot_does_not_change_after_new_outcome", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		snapshotScope := scenario.scope
		snapshotScope.SessionRef = scenario.prefix + "-session-snapshot"
		episode, situationRef, agentActRef := scenario.materializeEpisodeWithTexts(t, "snapshot", "remember this", "initial answer", snapshotScope)
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: episode}}
		query := scenario.source("snapshot-query", "find the episode")
		scenario.observe(t, "snapshot-query", query, ledger.EpisodeBinding{})
		request := selection.SelectRequest{
			Scope: scenario.scope, RunRef: scenario.prefix + "-snapshot-select", SituationSourceRefs: []string{query.Ref},
			EpisodeEvidenceMaxBytes: 16 * 1024,
		}
		frozen, err := store.SelectMemory(testContext(t), request)
		if err != nil || len(frozen.EpisodeEvidence) != 1 {
			t.Fatalf("freeze Episode evidence = (%#v, %v)", frozen, err)
		}
		outcome := scenario.eventInScope("snapshot-outcome", snapshotScope, ledger.ActorKindUser, "later outcome\nverbatim")
		if _, err := store.ReportOutcome(testContext(t), ledger.OutcomeReport{
			IdempotencyKey: scenario.prefix + "-snapshot-outcome", Event: outcome,
			RunRef: scenario.prefix + "-run-snapshot", SourceGroupRef: scenario.prefix + "-group-snapshot",
			RelatedSourceEventRefs: []string{situationRef, agentActRef},
		}); err != nil {
			t.Fatalf("append later Outcome: %v", err)
		}
		replayed, err := store.SelectMemory(testContext(t), request)
		if err != nil || !reflect.DeepEqual(replayed, frozen) {
			t.Fatalf("replayed frozen Context = (%#v, %v); want %#v", replayed, err, frozen)
		}

		freshRequest := request
		freshRequest.RunRef += "-fresh"
		fresh, err := store.SelectMemory(testContext(t), freshRequest)
		if err != nil || len(fresh.EpisodeEvidence) != 1 || !strings.Contains(fresh.EpisodeEvidence[0].Text, "OUTCOME [user]\nlater outcome\nverbatim") {
			t.Fatalf("fresh Context did not rehydrate appended Outcome = (%#v, %v)", fresh.EpisodeEvidence, err)
		}
	})

	t.Run("episode_evidence_rejects_cross_kind_reference_collision", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		episode, _, _ := scenario.materializeEpisodeWithTexts(t, "collision", "collision source", "collision answer", scenario.scope)
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: episode}}
		query := scenario.source("collision-query", "collision")
		scenario.observe(t, "collision-query", query, ledger.EpisodeBinding{})

		_, err := store.SelectMemory(testContext(t), selection.SelectRequest{
			Scope: scenario.scope, RunRef: scenario.prefix + "-collision-select", SituationSourceRefs: []string{query.Ref},
			Constitution: selection.Constitution{MemoryRef: episode, Text: "external baseline"}, EpisodeEvidenceMaxBytes: 16 * 1024,
		})
		if !errors.Is(err, selection.ErrAmbiguousMemoryRef) {
			t.Fatalf("cross-kind Episode evidence error = %v; want ErrAmbiguousMemoryRef", err)
		}
	})

	t.Run("episode_only_delivery_does_not_grant_selected_disposition_feedback_eligibility", func(t *testing.T) {
		index := &scriptedIndex{}
		store := migratedIndexedStore(t, factory, index)
		scenario := newScenario(store)
		first, _, _ := scenario.materializeEpisodeWithTexts(t, "evidence-seed-one", "seed formation one", "first act", scenario.scope)
		second, _, _ := scenario.materializeEpisodeWithTexts(t, "evidence-seed-two", "seed formation two", "second act", scenario.scope)
		formed, err := store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: scenario.prefix + "-evidence-seed-form", EpisodeRefs: []string{first, second},
		}, textWorker(taggedChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText,
			"a seed that must not be activated by evidence delivery", first, second,
		)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("form evidence delivery Seed = (%#v, %v)", formed, err)
		}
		seedRef := formed.DispositionVersionRefs[0]
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: first}}

		runScope := scenario.scope
		runScope.SessionRef = scenario.prefix + "-session-evidence-delivery"
		runRef := scenario.prefix + "-run-evidence-delivery"
		groupRef := scenario.prefix + "-group-evidence-delivery"
		situation := scenario.eventInScope("evidence-delivery-situation", runScope, ledger.ActorKindUser, "火山地质资料")
		scenario.observe(t, "evidence-delivery-situation", situation, ledger.EpisodeBinding{RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation})
		contextValue, err := store.SelectMemory(testContext(t), selection.SelectRequest{
			Scope: runScope, RunRef: runRef, SituationSourceRefs: []string{situation.Ref}, EpisodeEvidenceMaxBytes: 16 * 1024,
		})
		if err != nil || len(contextValue.EpisodeEvidence) != 1 || contextValue.EpisodeEvidence[0].MemoryRef != first {
			t.Fatalf("select support-linked evidence Context = (%#v, %v)", contextValue, err)
		}
		if len(contextValue.Dispositions) != 1 || contextValue.Dispositions[0].MemoryRef != seedRef {
			t.Fatalf("indexed support Episode did not independently select its linked Seed: %#v", contextValue.Dispositions)
		}
		delivery, err := store.RecordMemoryDelivery(testContext(t), ledger.MemoryDelivery{
			IdempotencyKey: scenario.prefix + "-evidence-only-delivery", Scope: runScope, RunRef: runRef,
			MemoryContextRef: contextValue.Ref, DeliveredMemoryRefs: []string{first},
		})
		if err != nil || delivery.Ref == "" {
			t.Fatalf("record evidence-only Delivery = (%#v, %v)", delivery, err)
		}
		agentAct := scenario.eventInScope("evidence-delivery-agent-act", runScope, ledger.ActorKindAgent, "current actual act")
		currentEpisode := scenario.observe(t, "evidence-delivery-agent-act", agentAct, ledger.EpisodeBinding{
			RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleAgentAct,
		}).EpisodeRef
		worker := &capturingWorker{text: taggedChange(seedRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", currentEpisode)}
		receipt, err := store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: scenario.prefix + "-evidence-only-feedback", EpisodeRefs: []string{currentEpisode},
		}, worker)
		if err != nil {
			t.Fatalf("consolidate after evidence-only Delivery: %v", err)
		}
		if len(receipt.DispositionVersionRefs) != 0 || strings.Contains(worker.request.WindowText, "ELIGIBLE_DISPOSITION "+seedRef) {
			t.Fatalf("Episode evidence Delivery granted Seed eligibility: receipt=%#v request=%s", receipt, worker.request.WindowText)
		}
	})
}

type scenario struct {
	store  storage.CoreStore
	scope  ledger.Scope
	prefix string
}

type activatedRun struct {
	scope            ledger.Scope
	runRef           string
	groupRef         string
	situationRef     string
	memoryContextRef string
}

func newScenario(store storage.CoreStore) *scenario {
	sequence := scenarioSequence.Add(1)
	prefix := fmt.Sprintf("contract-%d-%d", time.Now().UnixNano(), sequence)
	return &scenario{
		store:  store,
		prefix: prefix,
		scope: ledger.Scope{
			Kind: ledger.ScopeKindRelationship, TenantRef: prefix,
			AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-1",
		},
	}
}

func (scenario *scenario) source(ref, text string) ledger.SourceEvent {
	return scenario.eventInScope(ref, scenario.scope, ledger.ActorKindUser, text)
}

func (scenario *scenario) eventInScope(ref string, scope ledger.Scope, kind ledger.ActorKind, text string) ledger.SourceEvent {
	actorRef := "user-1"
	if kind == ledger.ActorKindAgent {
		actorRef = scope.AgentRef
	}
	return ledger.SourceEvent{Ref: scenario.prefix + "-" + ref, Scope: scope, ActorKind: kind, ActorRef: actorRef, Text: text}
}

func (scenario *scenario) materializeEpisode(t *testing.T, suffix, situationText string) (string, string, string) {
	t.Helper()
	scope := scenario.scope
	scope.SessionRef = scenario.prefix + "-session-" + suffix
	runRef := scenario.prefix + "-run-" + suffix
	groupRef := scenario.prefix + "-group-" + suffix
	situation := scenario.eventInScope(suffix+"-situation", scope, ledger.ActorKindUser, situationText)
	scenario.observe(t, suffix+"-situation", situation, ledger.EpisodeBinding{
		RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation,
	})
	agentAct := scenario.eventInScope(suffix+"-agent-act", scope, ledger.ActorKindAgent, "agent response: "+situationText)
	receipt := scenario.observe(t, suffix+"-agent-act", agentAct, ledger.EpisodeBinding{
		RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleAgentAct,
	})
	if receipt.EpisodeRef == "" {
		t.Fatalf("%s did not materialize an Episode", suffix)
	}
	return receipt.EpisodeRef, situation.Ref, agentAct.Ref
}

func (scenario *scenario) reportEpisodeOutcome(
	t *testing.T,
	outcomeSuffix, episodeSuffix, situationRef, agentActRef string,
	actorKind ledger.ActorKind,
	text string,
) string {
	t.Helper()
	scope := scenario.scope
	scope.SessionRef = scenario.prefix + "-session-" + episodeSuffix
	event := scenario.eventInScope(outcomeSuffix+"-outcome", scope, actorKind, text)
	if actorKind == ledger.ActorKindExternal {
		event.ActorRef = "observer-1"
	}
	receipt, err := scenario.store.ReportOutcome(testContext(t), ledger.OutcomeReport{
		IdempotencyKey:         scenario.prefix + "-" + outcomeSuffix + "-outcome-report",
		Event:                  event,
		RunRef:                 scenario.prefix + "-run-" + episodeSuffix,
		SourceGroupRef:         scenario.prefix + "-group-" + episodeSuffix,
		RelatedSourceEventRefs: []string{situationRef, agentActRef},
	})
	if err != nil {
		t.Fatalf("ReportOutcome(%s): %v", outcomeSuffix, err)
	}
	if receipt.OutcomeEventRef == "" {
		t.Fatalf("ReportOutcome(%s) returned no ref", outcomeSuffix)
	}
	return receipt.OutcomeEventRef
}

func (scenario *scenario) materializeEpisodeWithTexts(t *testing.T, suffix, situationText, agentText string, scope ledger.Scope) (string, string, string) {
	t.Helper()
	scope.SessionRef = scenario.prefix + "-session-" + suffix
	runRef := scenario.prefix + "-run-" + suffix
	groupRef := scenario.prefix + "-group-" + suffix
	situation := scenario.eventInScope(suffix+"-situation", scope, ledger.ActorKindUser, situationText)
	scenario.observe(t, suffix+"-situation", situation, ledger.EpisodeBinding{RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation})
	agentAct := scenario.eventInScope(suffix+"-agent-act", scope, ledger.ActorKindAgent, agentText)
	receipt := scenario.observe(t, suffix+"-agent-act", agentAct, ledger.EpisodeBinding{RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleAgentAct})
	if receipt.EpisodeRef == "" {
		t.Fatalf("%s did not materialize an Episode", suffix)
	}
	return receipt.EpisodeRef, situation.Ref, agentAct.Ref
}

func (scenario *scenario) selectText(t *testing.T, suffix, text string) selection.MemoryContext {
	t.Helper()
	scope := scenario.scope
	scope.SessionRef = scenario.prefix + "-session-" + suffix
	event := scenario.eventInScope(suffix+"-source", scope, ledger.ActorKindUser, text)
	scenario.observe(t, suffix+"-source", event, ledger.EpisodeBinding{})
	contextValue, err := scenario.store.SelectMemory(testContext(t), selection.SelectRequest{
		Scope: scope, RunRef: scenario.prefix + "-run-" + suffix, SituationSourceRefs: []string{event.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory(%s): %v", suffix, err)
	}
	return contextValue
}

func (scenario *scenario) activate(t *testing.T, suffix, text, versionRef string) activatedRun {
	t.Helper()
	scope := scenario.scope
	scope.SessionRef = scenario.prefix + "-session-" + suffix
	runRef := scenario.prefix + "-run-" + suffix
	groupRef := scenario.prefix + "-group-" + suffix
	situation := scenario.eventInScope(suffix+"-situation", scope, ledger.ActorKindUser, text)
	scenario.observe(t, suffix+"-situation", situation, ledger.EpisodeBinding{
		RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation,
	})
	contextValue, err := scenario.store.SelectMemory(testContext(t), selection.SelectRequest{
		Scope: scope, RunRef: runRef, SituationSourceRefs: []string{situation.Ref},
	})
	if err != nil {
		t.Fatalf("activate SelectMemory: %v", err)
	}
	found := false
	for _, disposition := range contextValue.Dispositions {
		found = found || disposition.MemoryRef == versionRef
	}
	if !found {
		t.Fatalf("activation context = %#v; want %s", contextValue.Dispositions, versionRef)
	}
	return activatedRun{scope: scope, runRef: runRef, groupRef: groupRef, situationRef: situation.Ref, memoryContextRef: contextValue.Ref}
}

func (scenario *scenario) finishActivatedRun(t *testing.T, run activatedRun, text string) (string, string) {
	t.Helper()
	agentAct := scenario.eventInScope("revision-agent-act", run.scope, ledger.ActorKindAgent, text)
	receipt := scenario.observe(t, "revision-agent-act", agentAct, ledger.EpisodeBinding{
		RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleAgentAct,
	})
	if receipt.EpisodeRef == "" {
		t.Fatal("activated run did not materialize an Episode")
	}
	return receipt.EpisodeRef, agentAct.Ref
}

func (scenario *scenario) observe(t *testing.T, key string, event ledger.SourceEvent, binding ledger.EpisodeBinding) ledger.ObserveReceipt {
	t.Helper()
	receipt, err := scenario.store.Observe(testContext(t), scenario.prefix+"-"+key, event, binding)
	if err != nil {
		t.Fatalf("Observe(%s): %v", key, err)
	}
	return receipt
}

func migratedStore(t *testing.T, factory Factory) storage.CoreStore {
	t.Helper()
	store := factory(t)
	t.Cleanup(store.Close)
	if err := store.Migrate(testContext(t)); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	return store
}

func migratedIndexedStore(t *testing.T, factory IndexedFactory, index memoryindex.Index) storage.CoreStore {
	t.Helper()
	store := factory(t, index)
	t.Cleanup(store.Close)
	if err := store.Migrate(testContext(t)); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	return store
}

func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	return ctx
}

type textWorker string

func (worker textWorker) ProcessConsolidationWindow(context.Context, consolidation.WorkerRequest) (string, error) {
	return string(worker), nil
}

type capturingWorker struct {
	text    string
	request consolidation.WorkerRequest
}

func (worker *capturingWorker) ProcessConsolidationWindow(_ context.Context, request consolidation.WorkerRequest) (string, error) {
	worker.request = request
	return worker.text, nil
}

type scriptedIndex struct {
	candidates []memoryindex.Candidate
	search     func(memoryindex.Query) []memoryindex.Candidate
}

func (index *scriptedIndex) Search(_ context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	if index.search != nil {
		return append([]memoryindex.Candidate(nil), index.search(query)...), nil
	}
	return append([]memoryindex.Candidate(nil), index.candidates...), nil
}

func (*scriptedIndex) Upsert(context.Context, []memoryindex.Document) error { return nil }
func (*scriptedIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (*scriptedIndex) Reset(context.Context) error { return nil }

func taggedChange(target, application, operation, text string, basis ...string) string {
	change := operation
	if operation == consolidation.ChangeText {
		change += " " + text
	}
	lines := []string{"TARGET", target, "APPLICATION", application, "CHANGE", change, "BASIS"}
	lines = append(lines, basis...)
	return strings.Join(lines, "\n")
}
