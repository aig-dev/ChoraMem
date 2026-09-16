package postgres_test

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/jackc/pgx/v5"
)

func TestAutomaticConsolidationJobSurvivesRestartAndLeasesExactlyOnce(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "queued-one", "session-one")

	harness.store.Close()
	harness.store = harness.openStore(t)
	second := harness.materializeEpisode(t, "queued-two", "session-two")

	now := time.Now()
	request := scheduler.LeaseRequest{
		QuietBefore: now.Add(time.Minute), LeaseUntil: now.Add(time.Minute),
		MaxEpisodes: 32,
	}
	job, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil {
		t.Fatalf("LeaseConsolidationJob: %v", err)
	}
	if !found {
		t.Fatal("durable collecting Job was not leased")
	}
	if !reflect.DeepEqual(job.EpisodeRefs, []string{first, second}) {
		t.Fatalf("Episode refs = %#v; want ordered cross-session refs", job.EpisodeRefs)
	}
	if job.Ref == "" || job.LeaseToken == "" {
		t.Fatalf("leased Job lacks stable identity: %#v", job)
	}
	if _, found, err := harness.store.LeaseConsolidationJob(context.Background(), request); err != nil || found {
		t.Fatalf("active lease second claim = (%v, %v); want false, nil", found, err)
	}

	if err := harness.store.RetryConsolidationJob(context.Background(), job.Ref, job.LeaseToken, now.Add(-time.Second)); err != nil {
		t.Fatalf("RetryConsolidationJob: %v", err)
	}
	retry, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil || !found {
		t.Fatalf("retry lease = (%#v, %v, %v)", retry, found, err)
	}
	if retry.Ref != job.Ref || retry.LeaseToken == job.LeaseToken {
		t.Fatalf("retry identity = %#v; want same Job and fresh lease", retry)
	}
	if err := harness.store.CompleteConsolidationJob(context.Background(), retry.Ref, retry.LeaseToken); err != nil {
		t.Fatalf("CompleteConsolidationJob: %v", err)
	}
	if _, found, err := harness.store.LeaseConsolidationJob(context.Background(), request); err != nil || found {
		t.Fatalf("completed Job was leased again = (%v, %v)", found, err)
	}
}

func TestAutomaticConsolidationWindowOverlapsPreviousBoundary(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "overlap-one", "session-one")
	second := harness.materializeEpisode(t, "overlap-two", "session-two")
	now := time.Now()
	request := scheduler.LeaseRequest{
		QuietBefore: now.Add(time.Minute), LeaseUntil: now.Add(time.Minute),
		MaxEpisodes: 32, Overlap: 2,
	}
	job, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil || !found {
		t.Fatalf("first lease = (%#v, %v, %v)", job, found, err)
	}

	third := harness.materializeEpisode(t, "overlap-three", "session-three")
	fourth := harness.materializeEpisode(t, "overlap-four", "session-four")
	if _, found, err := harness.store.LeaseConsolidationJob(context.Background(), scheduler.LeaseRequest{
		QuietBefore: time.Now().Add(time.Minute), LeaseUntil: time.Now().Add(time.Minute),
		MaxEpisodes: 32, Overlap: 2,
	}); err != nil || found {
		t.Fatalf("later exact-owner Job bypassed earlier lease = (%v, %v)", found, err)
	}
	if err := harness.store.CompleteConsolidationJob(context.Background(), job.Ref, job.LeaseToken); err != nil {
		t.Fatalf("complete first window: %v", err)
	}
	job, found, err = harness.store.LeaseConsolidationJob(context.Background(), scheduler.LeaseRequest{
		QuietBefore: time.Now().Add(time.Minute), LeaseUntil: time.Now().Add(time.Minute),
		MaxEpisodes: 32, Overlap: 2,
	})
	if err != nil || !found {
		t.Fatalf("second lease = (%#v, %v, %v)", job, found, err)
	}
	want := []string{first, second, third, fourth}
	if !reflect.DeepEqual(job.EpisodeRefs, want) {
		t.Fatalf("overlapped Episodes = %#v; want %#v", job.EpisodeRefs, want)
	}
}

func TestAutomaticConsolidationLeaseSplitsAtMaxAndKeepsOutcomesWithEpisode(t *testing.T) {
	harness := newHarness(t)
	episodes := []string{
		harness.materializeEpisode(t, "bounded-one", "session-bounded-one"),
		harness.materializeEpisode(t, "bounded-two", "session-bounded-two"),
		harness.materializeEpisode(t, "bounded-three", "session-bounded-three"),
		harness.materializeEpisode(t, "bounded-four", "session-bounded-four"),
		harness.materializeEpisode(t, "bounded-five", "session-bounded-five"),
	}
	firstOutcome := harness.reportUnattributedEpisodeOutcome(t, "bounded-one", "session-bounded-one")
	remainderOutcome := harness.reportUnattributedEpisodeOutcome(t, "bounded-four", "session-bounded-four")
	now := time.Now()
	request := scheduler.LeaseRequest{
		QuietBefore: now.Add(time.Minute), LeaseUntil: now.Add(time.Minute), MaxEpisodes: 3,
	}

	first, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil || !found {
		t.Fatalf("first bounded lease = (%#v, %v, %v)", first, found, err)
	}
	if !reflect.DeepEqual(first.EpisodeRefs, episodes[:3]) {
		t.Fatalf("first bounded Episodes = %#v; want %#v", first.EpisodeRefs, episodes[:3])
	}
	if !reflect.DeepEqual(first.OutcomeEventRefs, []string{firstOutcome}) {
		t.Fatalf("first bounded Outcomes = %#v; want first Episode Outcome", first.OutcomeEventRefs)
	}
	if err := harness.store.CompleteConsolidationJob(context.Background(), first.Ref, first.LeaseToken); err != nil {
		t.Fatalf("complete first bounded window: %v", err)
	}

	second, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil || !found {
		t.Fatalf("remainder lease = (%#v, %v, %v)", second, found, err)
	}
	if !reflect.DeepEqual(second.EpisodeRefs, episodes[3:]) {
		t.Fatalf("remainder Episodes = %#v; want %#v", second.EpisodeRefs, episodes[3:])
	}
	if !reflect.DeepEqual(second.OutcomeEventRefs, []string{remainderOutcome}) {
		t.Fatalf("remainder Outcomes = %#v; want fourth Episode Outcome", second.OutcomeEventRefs)
	}
}

func TestAutomaticConsolidationSplitEventuallyLeasesOddTailWithOverlap(t *testing.T) {
	harness := newHarness(t)
	previous := []string{
		harness.materializeEpisode(t, "odd-tail-previous-one", "session-odd-tail-previous-one"),
		harness.materializeEpisode(t, "odd-tail-previous-two", "session-odd-tail-previous-two"),
	}
	now := time.Now()
	request := scheduler.LeaseRequest{
		QuietBefore: now.Add(time.Minute), LeaseUntil: now.Add(time.Minute), MaxEpisodes: 3, Overlap: 2,
	}
	priorJob, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil || !found || !reflect.DeepEqual(priorJob.EpisodeRefs, previous) {
		t.Fatalf("previous lease = (%#v, %v, %v); want %#v", priorJob, found, err, previous)
	}
	if err := harness.store.CompleteConsolidationJob(context.Background(), priorJob.Ref, priorJob.LeaseToken); err != nil {
		t.Fatalf("complete previous window: %v", err)
	}

	current := []string{
		harness.materializeEpisode(t, "odd-tail-current-one", "session-odd-tail-current-one"),
		harness.materializeEpisode(t, "odd-tail-current-two", "session-odd-tail-current-two"),
		harness.materializeEpisode(t, "odd-tail-current-three", "session-odd-tail-current-three"),
		harness.materializeEpisode(t, "odd-tail-current-four", "session-odd-tail-current-four"),
	}
	windows := [][]string{
		{previous[0], previous[1], current[0]},
		{previous[1], current[0], current[1]},
		{current[0], current[1], current[2]},
		{current[1], current[2], current[3]},
	}
	for index, want := range windows {
		job, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
		if err != nil || !found {
			t.Fatalf("odd-tail lease %d = (%#v, %v, %v)", index+1, job, found, err)
		}
		if !reflect.DeepEqual(job.EpisodeRefs, want) {
			t.Fatalf("odd-tail window %d = %#v; want %#v", index+1, job.EpisodeRefs, want)
		}
		if len(job.EpisodeRefs) > request.MaxEpisodes {
			t.Fatalf("odd-tail window %d exceeded MaxEpisodes: %#v", index+1, job.EpisodeRefs)
		}
		if err := harness.store.CompleteConsolidationJob(context.Background(), job.Ref, job.LeaseToken); err != nil {
			t.Fatalf("complete odd-tail window %d: %v", index+1, err)
		}
	}
	if count := harness.count(t, "consolidation_jobs", "completed_at IS NULL", nil); count != 0 {
		t.Fatalf("odd-tail unfinished Jobs = %d; want 0", count)
	}
}

func TestAutomaticConsolidationMaxPlusOneLeasesSingleTailFromFirstWindowOverlap(t *testing.T) {
	harness := newHarness(t)
	episodes := []string{
		harness.materializeEpisode(t, "single-tail-one", "session-single-tail-one"),
		harness.materializeEpisode(t, "single-tail-two", "session-single-tail-two"),
		harness.materializeEpisode(t, "single-tail-three", "session-single-tail-three"),
		harness.materializeEpisode(t, "single-tail-four", "session-single-tail-four"),
	}
	firstWindowOutcome := harness.reportUnattributedEpisodeOutcome(t, "single-tail-two", "session-single-tail-two")
	tailOutcome := harness.reportUnattributedEpisodeOutcome(t, "single-tail-four", "session-single-tail-four")
	request := scheduler.LeaseRequest{
		QuietBefore: time.Now().Add(time.Minute), LeaseUntil: time.Now().Add(time.Minute),
		MaxEpisodes: 3, Overlap: 2,
	}

	first, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	if err != nil || !found || !reflect.DeepEqual(first.EpisodeRefs, episodes[:3]) {
		t.Fatalf("first Max+1 lease = (%#v, %v, %v); want %#v", first, found, err, episodes[:3])
	}
	if !reflect.DeepEqual(first.OutcomeEventRefs, []string{firstWindowOutcome}) {
		t.Fatalf("first Max+1 Outcomes = %#v; want only first-window Outcome", first.OutcomeEventRefs)
	}
	if err := harness.store.CompleteConsolidationJob(context.Background(), first.Ref, first.LeaseToken); err != nil {
		t.Fatalf("complete first Max+1 window: %v", err)
	}

	tail, found, err := harness.store.LeaseConsolidationJob(context.Background(), request)
	wantTail := episodes[1:]
	if err != nil || !found || !reflect.DeepEqual(tail.EpisodeRefs, wantTail) {
		t.Fatalf("single-tail lease = (%#v, %v, %v); want %#v", tail, found, err, wantTail)
	}
	if !reflect.DeepEqual(tail.OutcomeEventRefs, []string{firstWindowOutcome, tailOutcome}) {
		t.Fatalf("single-tail Outcomes = %#v; want overlap and tail Outcomes", tail.OutcomeEventRefs)
	}
	if err := harness.store.CompleteConsolidationJob(context.Background(), tail.Ref, tail.LeaseToken); err != nil {
		t.Fatalf("complete single-tail window: %v", err)
	}
	if count := harness.count(t, "consolidation_jobs", "completed_at IS NULL", nil); count != 0 {
		t.Fatalf("single-tail unfinished Jobs = %d; want 0", count)
	}
}

func TestIntakeJoinsRemainderCreatedWhileCollectingRowIsBeingFrozen(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "freeze-race-one", "session-freeze-race-one")
	second := harness.materializeEpisode(t, "freeze-race-two", "session-freeze-race-two")
	thirdSituation := harness.source("source-freeze-race-three-situation")
	thirdSituation.Scope.SessionRef = "session-freeze-race-three"
	binding := ledger.EpisodeBinding{
		RunRef: "run-freeze-race-three", SourceGroupRef: "group-freeze-race-three", Role: ledger.RoleSituation,
	}
	harness.observe(t, "freeze-race-three-situation", thirdSituation, binding)

	tx, err := harness.inspectionDB.BeginTx(context.Background(), pgx.TxOptions{})
	if err != nil {
		t.Fatalf("begin manual freeze: %v", err)
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	var collectingRef string
	if err := tx.QueryRow(context.Background(), `
		SELECT job_ref
		FROM consolidation_jobs
		WHERE tenant_ref = $1 AND window_hash IS NULL AND completed_at IS NULL
		FOR UPDATE
	`, harness.tenant).Scan(&collectingRef); err != nil {
		t.Fatalf("lock collecting row: %v", err)
	}

	type observeResult struct {
		episodeRef string
		err        error
	}
	result := make(chan observeResult, 1)
	go func() {
		agentAct := harness.agentSource("source-freeze-race-three-agent-act")
		agentAct.Scope = thirdSituation.Scope
		binding.Role = ledger.RoleAgentAct
		receipt, err := harness.store.Observe(context.Background(), "freeze-race-three-agent-act", agentAct, binding)
		result <- observeResult{episodeRef: receipt.EpisodeRef, err: err}
	}()

	deadline := time.Now().Add(2 * time.Second)
	for {
		var blocked bool
		if err := harness.inspectionDB.QueryRow(context.Background(), `
			SELECT EXISTS (
				SELECT 1 FROM pg_stat_activity
				WHERE datname = current_database()
				  AND wait_event_type = 'Lock'
				  AND query LIKE '%FROM consolidation_jobs%'
				  AND query LIKE '%FOR UPDATE%'
			)
		`).Scan(&blocked); err != nil {
			t.Fatalf("inspect blocked intake: %v", err)
		}
		if blocked {
			break
		}
		if time.Now().After(deadline) {
			_ = tx.Rollback(context.Background())
			<-result
			t.Fatal("intake never blocked on collecting row")
		}
		time.Sleep(10 * time.Millisecond)
	}

	windowHash := make([]byte, 32)
	if _, err := tx.Exec(context.Background(), `
		UPDATE consolidation_jobs
		SET episode_refs = $2, window_hash = $3
		WHERE job_ref = $1
	`, collectingRef, []string{first}, windowHash); err != nil {
		t.Fatalf("freeze original collecting row: %v", err)
	}
	if _, err := tx.Exec(context.Background(), `
		INSERT INTO consolidation_jobs (
			job_ref, tenant_ref, scope_kind, agent_ref, relationship_ref,
			episode_refs, outcome_event_refs
		)
		SELECT $2, tenant_ref, scope_kind, agent_ref, relationship_ref, $3, '{}'
		FROM consolidation_jobs WHERE job_ref = $1
	`, collectingRef, collectingRef+"-remainder", []string{second}); err != nil {
		t.Fatalf("create manual remainder: %v", err)
	}
	if err := tx.Commit(context.Background()); err != nil {
		t.Fatalf("commit manual freeze: %v", err)
	}

	observed := <-result
	if observed.err != nil {
		t.Fatalf("concurrent intake: %v", observed.err)
	}
	if observed.episodeRef == "" {
		t.Fatal("concurrent intake did not materialize third Episode")
	}
	var collectingEpisodes []string
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT episode_refs
		FROM consolidation_jobs
		WHERE tenant_ref = $1 AND window_hash IS NULL AND completed_at IS NULL
	`, harness.tenant).Scan(&collectingEpisodes); err != nil {
		t.Fatalf("load joined remainder: %v", err)
	}
	if want := []string{second, observed.episodeRef}; !reflect.DeepEqual(collectingEpisodes, want) {
		t.Fatalf("joined remainder Episodes = %#v; want %#v", collectingEpisodes, want)
	}
}

func (harness *testHarness) reportUnattributedEpisodeOutcome(t *testing.T, suffix, sessionRef string) string {
	t.Helper()
	event := harness.source("outcome-" + suffix)
	event.Scope.SessionRef = sessionRef
	event.Text = "outcome for " + suffix
	receipt, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "outcome-" + suffix,
		Event:          event,
		RunRef:         "run-" + suffix,
		SourceGroupRef: "group-" + suffix,
	})
	if err != nil {
		t.Fatalf("ReportOutcome(%s): %v", suffix, err)
	}
	if receipt.EpisodeRef == "" {
		t.Fatalf("ReportOutcome(%s) did not bind an Episode", suffix)
	}
	return receipt.OutcomeEventRef
}
