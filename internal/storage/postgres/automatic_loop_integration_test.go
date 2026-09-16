package postgres_test

import (
	"context"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
)

func TestAutomaticSchedulerClosesTheSeedEvolutionLoop(t *testing.T) {
	harness := newHarness(t)
	oldTendency := "先确认真正的问题"

	// G1: two ordinary interaction Episodes create the first Seed without a
	// Harness consolidation call.
	first := harness.materializeEpisode(t, "automatic-form-one", "session-form-one")
	second := harness.materializeEpisode(t, "automatic-form-two", "session-form-two")
	processAutomaticJob(t, harness, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, oldTendency, first, second,
	)})
	formed := harness.assembleCurrentText(t, "automatic-formed-select", oldTendency)
	if len(formed.Dispositions) != 1 {
		t.Fatalf("automatic FORM next Select = %#v; want one Seed", formed.Dispositions)
	}
	seedVersionRef := formed.Dispositions[0].MemoryRef

	// G2: exact Activation + Delivery + actual Agent act reenacts the Seed and
	// expands its future semantic surface.
	feedbackSituation := oldTendency + "：用户提到蓝色鲸鱼"
	novelSurface := "用户提到蓝色鲸鱼"
	if before := harness.assembleCurrentText(t, "automatic-reenact-before", novelSurface); len(before.Dispositions) != 0 {
		t.Fatalf("novel surface activated before reenactment: %#v", before.Dispositions)
	}
	reenactRun := prepareAutomaticRun(t, harness, "automatic-reenact", seedVersionRef, feedbackSituation)
	harness.recordSeedDelivery(t, "automatic-reenact-delivery", reenactRun)
	reenactEpisode := harness.materializeActivatedRun(t, "automatic-reenact", reenactRun, oldTendency)
	harness.materializeEpisode(t, "automatic-reenact-filler", "session-reenact-filler")
	processAutomaticJob(t, harness, &scriptedWorker{taggedText: taggedMemoryChange(
		seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", reenactEpisode,
	)})
	if after := harness.assembleCurrentText(t, "automatic-reenact-after", novelSurface); len(after.Dispositions) != 1 || after.Dispositions[0].MemoryRef != seedVersionRef {
		t.Fatalf("automatic REENACT did not alter next Select: %#v", after.Dispositions)
	}

	// G2 revision: a source-bound Outcome replaces the active semantic text;
	// the old version cannot remain active.
	newTendency := "继续质疑我的架构，但先承认我的担忧"
	reviseRun := prepareAutomaticRun(t, harness, "automatic-revise", seedVersionRef, oldTendency)
	reviseDelivery := harness.recordSeedDelivery(t, "automatic-revise-delivery", reviseRun)
	reviseEpisode := harness.materializeActivatedRun(t, "automatic-revise", reviseRun, oldTendency)
	reviseOutcome := harness.reportRunOutcome(
		t, "automatic-revise", reviseRun, reviseEpisode, reviseDelivery.Ref, newTendency,
	)
	harness.materializeEpisode(t, "automatic-revise-filler", "session-revise-filler")
	processAutomaticJob(t, harness, &scriptedWorker{taggedText: taggedMemoryChange(
		seedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeText, newTendency,
		reviseEpisode, reviseOutcome.OutcomeEventRef,
	)})
	revised := harness.assembleCurrentText(t, "automatic-revised-select", newTendency)
	if len(revised.Dispositions) != 1 || revised.Dispositions[0].MemoryRef == seedVersionRef {
		t.Fatalf("automatic REVISE next Select = %#v; want replacement version", revised.Dispositions)
	}
	revisedVersionRef := revised.Dispositions[0].MemoryRef

	// G3: inhibition is contextual. The exception stops activation only for a
	// matching future situation, while the normal tendency remains selectable.
	exception := "我现在只需要情绪支持，不要" + newTendency
	inhibitRun := prepareAutomaticRun(t, harness, "automatic-inhibit", revisedVersionRef, exception)
	inhibitDelivery := harness.recordSeedDelivery(t, "automatic-inhibit-delivery", inhibitRun)
	inhibitEpisode := harness.materializeActivatedRun(t, "automatic-inhibit", inhibitRun, "我会先提供情绪支持")
	inhibitOutcome := harness.reportRunOutcome(
		t, "automatic-inhibit", inhibitRun, inhibitEpisode, inhibitDelivery.Ref, "此刻不适合架构式质疑",
	)
	harness.materializeEpisode(t, "automatic-inhibit-filler", "session-inhibit-filler")
	processAutomaticJob(t, harness, &scriptedWorker{taggedText: taggedMemoryChange(
		revisedVersionRef, consolidation.ApplicationRelation, consolidation.ChangeInhibit, "",
		inhibitEpisode, inhibitOutcome.OutcomeEventRef,
	)})
	if got := harness.assembleCurrentText(t, "automatic-inhibit-exception", exception); len(got.Dispositions) != 0 {
		t.Fatalf("automatic INHIBIT did not suppress matching situation: %#v", got.Dispositions)
	}
	if got := harness.assembleCurrentText(t, "automatic-inhibit-normal", newTendency); len(got.Dispositions) != 1 || got.Dispositions[0].MemoryRef != revisedVersionRef {
		t.Fatalf("automatic INHIBIT suppressed normal situation: %#v", got.Dispositions)
	}

	if count := harness.count(t, "consolidation_jobs", "completed_at IS NOT NULL", nil); count != 4 {
		t.Fatalf("completed automatic Jobs = %d; want FORM + REENACT + REVISE + INHIBIT", count)
	}
	if count := harness.count(t, "consolidation_jobs", "completed_at IS NULL", nil); count != 0 {
		t.Fatalf("unfinished automatic Jobs = %d; want 0", count)
	}
}

func prepareAutomaticRun(
	t *testing.T,
	harness *testHarness,
	suffix string,
	seedVersionRef string,
	situationText string,
) activatedRun {
	t.Helper()
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
		t.Fatalf("automatic SelectMemory: %v", err)
	}
	found := false
	for _, item := range contextValue.Dispositions {
		found = found || item.MemoryRef == seedVersionRef
	}
	if !found {
		t.Fatalf("automatic run Context = %#v; want %s", contextValue.Dispositions, seedVersionRef)
	}
	return activatedRun{
		scope: situation.Scope, runRef: runRef, groupRef: groupRef,
		situationRef: situation.Ref, seedVersionRef: seedVersionRef, context: contextValue,
	}
}

func processAutomaticJob(t *testing.T, harness *testHarness, worker consolidation.Worker) {
	t.Helper()
	// PostgreSQL and Go clocks share the host but may differ in timestamp
	// precision; this makes the one-nanosecond quiet gate deterministic.
	time.Sleep(time.Millisecond)
	processor := scheduler.New(harness.store, harness.store, worker, scheduler.Config{
		QuietPeriod: time.Nanosecond, LeaseDuration: time.Minute,
		WorkerTimeout: time.Minute, RetryDelay: time.Millisecond,
		PollInterval: time.Millisecond, MaxEpisodes: 32,
	})
	worked, err := processor.ProcessOne(context.Background())
	if err != nil {
		t.Fatalf("automatic ProcessOne: %v", err)
	}
	if !worked {
		t.Fatal("automatic ProcessOne found no ready Job")
	}
}
