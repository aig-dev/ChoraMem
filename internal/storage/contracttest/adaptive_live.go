package contracttest

import (
	"context"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	inferencev1 "github.com/aig-dev/ChoraMem/gen/memory/inference/v1"
	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/inference/grpcworker"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// RunLiveAdaptiveWorker crosses the production inference transport, Python
// Worker and configured live model. It never supplies deterministic model text.
// The ordinary contract factories own disposable migrated databases.
// Its explicit 330-second test envelope does not validate memoryd's stock
// two-minute scheduler deadline.
func RunLiveAdaptiveWorker(t *testing.T, factory IndexedFactory, audit func(*testing.T, ledger.Scope) map[string]int) {
	endpoint := os.Getenv("MEMORY_TEST_ADAPTIVE_WORKER_ADDR")
	if endpoint == "" {
		return
	}
	t.Run("adaptive_live_model_lifecycle", func(t *testing.T) {
		connection, err := grpc.NewClient(endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = connection.Close() })
		remote := grpcworker.New(inferencev1.NewInferenceWorkerClient(connection))
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		calls := 0
		baselines := []ledger.Constitution{
			{MemoryRef: "live-role@1", Text: "在用户压力大时，保持耐心陪伴，尊重表达节奏。"},
			{MemoryRef: "live-role@2", Text: "先帮助用户澄清担忧，再尊重其表达节奏。"},
			{},
		}
		worker := callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
			baseline := baselines[calls]
			calls++
			wantBaseline := "CONSTITUTION\nUNKNOWN\n\n"
			if baseline.MemoryRef != "" {
				wantBaseline = "CONSTITUTION\nMEMORY_REF " + baseline.MemoryRef + "\n> " + baseline.Text + "\nEND_CONSTITUTION"
			}
			if !strings.HasPrefix(request.WindowText, wantBaseline) {
				t.Fatalf("immutable source baseline did not reach inference: want %q got %s", wantBaseline, request.WindowText)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 330*time.Second)
			defer cancel()
			output, err := remote.ProcessConsolidationWindow(ctx, request)
			t.Logf("live request=%s\n%s\nraw tagged output:\n%s", request.JobRef, request.WindowText, output)
			if err == nil {
				_, err = consolidation.ParseTaggedText(output)
			}
			return output, err
		})
		consolidate := func(job, episode string) consolidation.Receipt {
			ctx, cancel := context.WithTimeout(context.Background(), 330*time.Second)
			defer cancel()
			r, err := s.store.ConsolidateWindow(ctx, consolidation.Window{JobRef: job, EpisodeRefs: []string{episode}}, worker)
			if err != nil {
				t.Fatal(err)
			}
			t.Logf("committed receipt: %#v", r)
			return r
		}
		var replaySources []func()
		materialize := func(suffix, situationText, agentText string, baseline ledger.Constitution) string {
			scope := s.scope
			scope.SessionRef = s.prefix + "-session-" + suffix
			binding := ledger.EpisodeBinding{RunRef: s.prefix + "-run-" + suffix, SourceGroupRef: s.prefix + "-group-" + suffix, Role: ledger.RoleSituation}
			event := s.eventInScope(suffix+"-situation", scope, ledger.ActorKindUser, situationText)
			event.Constitution = baseline
			original := s.observe(t, suffix+"-situation", event, binding)
			for _, conflicting := range liveConstitutionConflicts(baseline) {
				changed := event
				changed.Constitution = conflicting
				if _, err := s.store.Observe(testContext(t), "conflict-"+suffix, changed, binding); !errors.Is(err, ledger.ErrSourceEventConflict) {
					t.Fatalf("immutable source baseline conflict accepted: %v", err)
				}
			}
			for _, replay := range replaySources {
				replay()
			}
			situationBinding := binding
			replaySources = append(replaySources, func() {
				replayed := s.observe(t, suffix+"-situation", event, situationBinding)
				if !reflect.DeepEqual(original, replayed) {
					t.Fatal("immutable source replay changed receipt")
				}
			})
			act := s.eventInScope(suffix+"-agent-act", scope, ledger.ActorKindAgent, agentText)
			binding.Role = ledger.RoleAgentAct
			r := s.observe(t, suffix+"-agent-act", act, binding)
			if r.EpisodeRef == "" {
				t.Fatal("missing complete live Episode")
			}
			return r.EpisodeRef
		}
		first := materialize("birth", "以后我压力大时先听我把话说完，别急着给方案", "明天可能下雨。", baselines[0])
		formed := consolidate("live-birth", first)
		if len(formed.DispositionVersionRefs) != 1 || len(formed.RecollectionVersionRefs) != 0 {
			t.Fatalf("direct birth: %#v", formed)
		}
		old := formed.DispositionVersionRefs[0]
		before := s.selectText(t, "first-select", "我压力大，想把话说完")
		if len(before.Dispositions) != 1 || before.Dispositions[0].MemoryRef != old {
			t.Fatalf("first Select: %#v", before)
		}
		t.Logf("first selected text: %s", before.Dispositions[0].Text)
		correction := materialize("correction", "以后我压力大时改一下：先确认我最担心什么，再听我说完，最后问我要不要建议。", "收到。", baselines[1])
		revised := consolidate("live-correction", correction)
		want := strings.TrimSuffix(old, "@1") + "@2"
		if !reflect.DeepEqual(revised.DispositionVersionRefs, []string{want}) || len(revised.RecollectionVersionRefs) != 0 {
			t.Fatalf("same Seed correction: %#v", revised)
		}
		after := s.selectText(t, "second-select", "我压力大，最担心什么，想把话说完")
		if len(after.Dispositions) != 1 || after.Dispositions[0].MemoryRef != want || after.Dispositions[0].Text == before.Dispositions[0].Text {
			t.Fatalf("second Select: %#v", after)
		}
		t.Logf("second selected text: %s", after.Dispositions[0].Text)
		beforePraise := audit(t, s.scope)
		for table, want := range map[string]int{"disposition_seeds": 1, "seed_versions": 2, "seed_basis_links": 2} {
			if beforePraise[table] != want {
				t.Fatalf("live formation/correction SQL %s=%d; want %d", table, beforePraise[table], want)
			}
		}
		// No Delivery or Outcome was fabricated. Direct eligibility exists, but
		// this real USER statement reports only an effect and cannot bypass it.
		praise := materialize("no-chain", "刚才那个回答很棒", "谢谢。", baselines[2])
		unchanged := consolidate("live-no-chain", praise)
		if len(unchanged.DispositionVersionRefs) != 0 {
			t.Fatalf("unchained effect wrote memory: %#v", unchanged)
		}
		// A source-faithful Recollection of the user's reaction is not a claim
		// that this Seed caused a successful effect. Seed Basis must not change.
		afterPraise := audit(t, s.scope)
		if !reflect.DeepEqual(beforePraise, afterPraise) {
			t.Fatalf("unchained praise changed causal Seed state: before=%v after=%v", beforePraise, afterPraise)
		}
		for _, table := range []string{"memory_delivery_receipts", "outcome_events", "seed_outcome_basis_links"} {
			if afterPraise[table] != 0 {
				t.Fatalf("fabricated %s: %v", table, afterPraise)
			}
		}
		t.Logf("causal SQL counts unchanged across unchained USER praise: %v", afterPraise)
		final := s.selectText(t, "final-select", "我压力大，最担心什么，想把话说完")
		if len(final.Dispositions) != 1 || final.Dispositions[0].MemoryRef != want {
			t.Fatalf("no-chain changed selection: %#v", final)
		}
		retry := consolidate("live-correction", correction)
		if !reflect.DeepEqual(retry, revised) || calls != 3 {
			t.Fatalf("idempotent retry called model: calls=%d receipt=%#v", calls, retry)
		}
	})
}

func liveConstitutionConflicts(baseline ledger.Constitution) []ledger.Constitution {
	if baseline == (ledger.Constitution{}) {
		return []ledger.Constitution{{MemoryRef: "conflicting-role", Text: "conflicting baseline"}}
	}
	changedRef, changedText := baseline, baseline
	changedRef.MemoryRef = "conflicting-role"
	changedText.Text = "conflicting baseline"
	return []ledger.Constitution{changedRef, changedText}
}
