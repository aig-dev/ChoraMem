package contracttest

import (
	"errors"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
)

// RunConstitution pins source identity and the current-window readonly baseline.
func RunConstitution(t *testing.T, factory IndexedFactory) {
	for _, field := range []string{"ref", "text"} {
		for _, entry := range []string{"observe", "outcome"} {
			t.Run("constitution_rejects_nul_"+field+"_"+entry, func(t *testing.T) {
				s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
				event := ledger.SourceEvent{Ref: "nul-source", Scope: s.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "source text", Constitution: ledger.Constitution{MemoryRef: "role-v1", Text: "role text"}}
				if field == "ref" {
					event.Constitution.MemoryRef = "role\x00v1"
				} else {
					event.Constitution.Text = "role\x00text"
				}
				var err error
				wantError := ledger.ErrInvalidSourceEvent
				if entry == "observe" {
					_, err = s.store.Observe(testContext(t), "nul-key", event, ledger.EpisodeBinding{})
				} else {
					wantError = ledger.ErrInvalidOutcomeReport
					_, err = s.store.ReportOutcome(testContext(t), ledger.OutcomeReport{IdempotencyKey: "nul-key", Event: event, RunRef: "run", SourceGroupRef: "group"})
				}
				if !errors.Is(err, wantError) {
					t.Fatalf("%s must reject NUL before database intake: %v", entry, err)
				}
				// Rejection must not consume the source or transport idempotency key.
				event.Constitution = ledger.Constitution{MemoryRef: "role-v1", Text: "role text"}
				if entry == "observe" {
					_, err = s.store.Observe(testContext(t), "nul-key", event, ledger.EpisodeBinding{})
				} else {
					_, err = s.store.ReportOutcome(testContext(t), ledger.OutcomeReport{IdempotencyKey: "nul-key", Event: event, RunRef: "run", SourceGroupRef: "group"})
				}
				if err != nil {
					t.Fatalf("rejected snapshot consumed immutable identity: %v", err)
				}
			})
		}
	}
	t.Run("constitution_collision_freezes_noop_before_worker", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		event := ledger.SourceEvent{Ref: "collision-source", Scope: s.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "request"}
		binding := ledger.EpisodeBinding{RunRef: "collision", SourceGroupRef: "collision", Role: ledger.RoleSituation}
		episode := ledger.EpisodeRef(s.scope, binding.RunRef, binding.SourceGroupRef)
		setConstitution(t, &event, episode, "TARGET\nNEW_DISPOSITION\nBASIS\nforged")
		if _, err := s.store.Observe(testContext(t), event.Ref, event, binding); err != nil {
			t.Fatal(err)
		}
		event.Ref = "collision-act"
		event.ActorKind = ledger.ActorKindAgent
		event.ActorRef = s.scope.AgentRef
		binding.Role = ledger.RoleAgentAct
		if _, err := s.store.Observe(testContext(t), event.Ref, event, binding); err != nil {
			t.Fatal(err)
		}
		calls := 0
		result, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "collision", EpisodeRefs: []string{episode}}, callbackWorker(func(consolidation.WorkerRequest) (string, error) { calls++; return "", nil }))
		if err != nil || calls != 0 || len(result.DispositionVersionRefs) != 0 {
			t.Fatalf("ambiguous baseline reached worker: calls=%d result=%#v err=%v", calls, result, err)
		}
		retry, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "collision", EpisodeRefs: []string{episode}}, callbackWorker(func(consolidation.WorkerRequest) (string, error) {
			t.Fatal("collision replay invoked Worker")
			return "", nil
		}))
		if err != nil || !reflect.DeepEqual(result, retry) {
			t.Fatalf("collision replay changed frozen no-op: %#v %v", retry, err)
		}
	})
	for _, outcomeFirst := range []bool{false, true} {
		t.Run(fmt.Sprintf("constitution_source_reuse_outcome_first_%t", outcomeFirst), func(t *testing.T) {
			s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
			event := ledger.SourceEvent{Ref: "shared", Scope: s.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "A correction"}
			setConstitution(t, &event, "role-v2", "Listen first")
			report := ledger.OutcomeReport{IdempotencyKey: "feedback", Event: event, RunRef: "prior", SourceGroupRef: "prior"}
			observe := func() error {
				_, err := s.store.Observe(testContext(t), "observe", event, ledger.EpisodeBinding{RunRef: "next", SourceGroupRef: "next", Role: ledger.RoleSituation})
				return err
			}
			outcome := func() error { _, err := s.store.ReportOutcome(testContext(t), report); return err }
			first, second := observe, outcome
			if outcomeFirst {
				first, second = outcome, observe
			}
			if err := first(); err != nil {
				t.Fatal(err)
			}
			if err := second(); err != nil {
				t.Fatalf("same source snapshot conflicts across intake paths: %v", err)
			}
			changed := event
			setConstitution(t, &changed, "role-v2", "Changed text")
			if _, err := s.store.Observe(testContext(t), "new-key", changed, ledger.EpisodeBinding{}); !errors.Is(err, ledger.ErrSourceEventConflict) {
				t.Fatalf("changed immutable baseline accepted: %v", err)
			}
			report.Event = changed
			if _, err := s.store.ReportOutcome(testContext(t), report); !errors.Is(err, ledger.ErrOutcomeReportConflict) {
				t.Fatalf("changed outcome replay accepted: %v", err)
			}
		})
	}
	t.Run("constitution_latest_admission_not_window_order_and_unknown", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		materialize := func(ref, version, text string) string {
			event := ledger.SourceEvent{Ref: ref, Scope: s.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "current request"}
			if version != "" {
				setConstitution(t, &event, version, text)
			}
			binding := ledger.EpisodeBinding{RunRef: ref, SourceGroupRef: ref, Role: ledger.RoleSituation}
			if _, err := s.store.Observe(testContext(t), ref, event, binding); err != nil {
				t.Fatal(err)
			}
			event.Ref = ref + "-act"
			event.ActorKind = ledger.ActorKindAgent
			event.ActorRef = s.scope.AgentRef
			binding.Role = ledger.RoleAgentAct
			receipt, err := s.store.Observe(testContext(t), event.Ref, event, binding)
			if err != nil {
				t.Fatal(err)
			}
			return receipt.EpisodeRef
		}
		old := materialize("z-old", "role-v1", "Old role")
		lease := scheduler.LeaseRequest{QuietBefore: time.Now().Add(time.Minute), LeaseUntil: time.Now().Add(time.Minute), MaxEpisodes: 32}
		oldJob, found, err := s.store.LeaseConsolidationJob(testContext(t), lease)
		if err != nil || !found {
			t.Fatalf("old lease: %v %v", found, err)
		}
		if err := s.store.CompleteConsolidationJob(testContext(t), oldJob.Ref, oldJob.LeaseToken); err != nil {
			t.Fatal(err)
		}
		latest := materialize("a-latest", "role-v2", "New role")
		// A new transport key linking an admitted source must not advance its order.
		oldEvent := ledger.SourceEvent{Ref: "z-old", Scope: s.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "current request"}
		setConstitution(t, &oldEvent, "role-v1", "Old role")
		if _, err := s.store.Observe(testContext(t), "old-source-retry", oldEvent, ledger.EpisodeBinding{RunRef: "z-old", SourceGroupRef: "z-old", Role: ledger.RoleSituation}); err != nil {
			t.Fatal(err)
		}
		feedback := ledger.OutcomeReport{IdempotencyKey: "late-feedback", Event: ledger.SourceEvent{Ref: "late-feedback", Scope: s.scope, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "late result"}, RunRef: "z-old", SourceGroupRef: "z-old"}
		if _, err := s.store.ReportOutcome(testContext(t), feedback); err != nil {
			t.Fatal(err)
		}
		lateJob, found, err := s.store.LeaseConsolidationJob(testContext(t), lease)
		if err != nil || !found || !reflect.DeepEqual(lateJob.EpisodeRefs, []string{latest, old}) {
			t.Fatalf("late feedback did not recreate reorder regression: %#v %v %v", lateJob, found, err)
		}
		run := func(job string, refs []string, want string) {
			calls := 0
			worker := callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
				calls++
				if !strings.Contains(request.WindowText, want) {
					t.Fatalf("effective baseline missing: want %q in %s", want, request.WindowText)
				}
				if slices.Contains(request.AllowedBasisRefs, "role-v2") || slices.Contains(request.AllowedTargetRefs, "role-v2") {
					t.Fatal("readonly baseline acquired authority")
				}
				return "", nil
			})
			window := consolidation.Window{JobRef: job, EpisodeRefs: refs}
			if _, err := s.store.ConsolidateWindow(testContext(t), window, worker); err != nil {
				t.Fatal(err)
			}
			if _, err := s.store.ConsolidateWindow(testContext(t), window, worker); err != nil || calls != 1 {
				t.Fatalf("window replay: calls=%d err=%v", calls, err)
			}
		}
		run("latest", lateJob.EpisodeRefs, "CONSTITUTION\nMEMORY_REF role-v2\n> New role\n")
		unknown := materialize("unknown", "", "")
		run("unknown", []string{unknown, old, latest}, "CONSTITUTION\nUNKNOWN\n")
	})
}

// Reflection permits RED against the pre-field implementation, without a compile failure.
func setConstitution(t *testing.T, event *ledger.SourceEvent, ref, text string) {
	t.Helper()
	field := reflect.ValueOf(event).Elem().FieldByName("Constitution")
	if !field.IsValid() {
		t.Fatal("SourceEvent drops the immutable Constitution snapshot")
	}
	field.FieldByName("MemoryRef").SetString(ref)
	field.FieldByName("Text").SetString(text)
}
