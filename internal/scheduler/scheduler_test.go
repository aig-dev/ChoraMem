package scheduler

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
)

func TestProcessOneCommitsOneFrozenJob(t *testing.T) {
	queue := &fakeQueue{job: Job{
		Ref:              "job-1",
		EpisodeRefs:      []string{"episode-1", "episode-2"},
		OutcomeEventRefs: []string{"outcome-1"},
		LeaseToken:       "lease-1",
	}, found: true}
	engine := &fakeEngine{}
	worker := fakeWorker{}
	processor := New(queue, engine, worker, Config{
		QuietPeriod:   time.Minute,
		LeaseDuration: 2 * time.Minute,
		RetryDelay:    3 * time.Second,
		PollInterval:  time.Second,
		MaxEpisodes:   32,
		Overlap:       4,
	})
	processor.now = func() time.Time { return time.Unix(1000, 0) }

	worked, err := processor.ProcessOne(context.Background())
	if err != nil {
		t.Fatalf("ProcessOne: %v", err)
	}
	if !worked {
		t.Fatal("ProcessOne reported no work")
	}
	wantWindow := consolidation.Window{
		JobRef: "job-1", EpisodeRefs: []string{"episode-1", "episode-2"},
		OutcomeEventRefs: []string{"outcome-1"},
	}
	if !reflect.DeepEqual(engine.window, wantWindow) {
		t.Fatalf("engine window = %#v; want %#v", engine.window, wantWindow)
	}
	if queue.completedRef != "job-1" || queue.completedToken != "lease-1" {
		t.Fatalf("complete = (%q, %q)", queue.completedRef, queue.completedToken)
	}
	if queue.retryRef != "" {
		t.Fatalf("successful job was retried: %q", queue.retryRef)
	}
	wantLease := LeaseRequest{
		QuietBefore: time.Unix(940, 0),
		LeaseUntil:  time.Unix(1120, 0),
		MaxEpisodes: 32,
		Overlap:     4,
	}
	if queue.leaseRequest != wantLease {
		t.Fatalf("lease request = %#v; want %#v", queue.leaseRequest, wantLease)
	}
}

func TestProcessOneAcknowledgesCommittedMemoryReceiptWithoutProjectionDependency(t *testing.T) {
	queue := &fakeQueue{
		job: Job{Ref: "job-1", EpisodeRefs: []string{"e1"}, LeaseToken: "lease-1"}, found: true,
	}
	engine := &fakeEngine{receipt: consolidation.Receipt{
		RecollectionVersionRefs: []string{"recollection-version-1"},
		DispositionVersionRefs:  []string{"disposition-version-1"},
	}}
	processor := New(queue, engine, fakeWorker{}, DefaultConfig())

	worked, err := processor.ProcessOne(context.Background())
	if err != nil || !worked {
		t.Fatalf("ProcessOne = (%v, %v); want successful acknowledgement", worked, err)
	}
	if queue.completedRef != "job-1" || queue.retryRef != "" {
		t.Fatalf("complete=%q retry=%q; committed causal job must ack directly", queue.completedRef, queue.retryRef)
	}
}

func TestProcessOneReleasesFailedWorkerJobForRetry(t *testing.T) {
	wantErr := errors.New("worker unavailable")
	queue := &fakeQueue{job: Job{Ref: "job-1", EpisodeRefs: []string{"e1", "e2"}, LeaseToken: "lease-1"}, found: true}
	engine := &fakeEngine{err: wantErr}
	processor := New(queue, engine, fakeWorker{}, Config{
		QuietPeriod: time.Minute, LeaseDuration: time.Minute, RetryDelay: 5 * time.Second,
		PollInterval: time.Second, MaxEpisodes: 32,
	})
	processor.now = func() time.Time { return time.Unix(1000, 0) }

	worked, err := processor.ProcessOne(context.Background())
	if !worked || !errors.Is(err, wantErr) {
		t.Fatalf("ProcessOne = (%v, %v); want worked + worker error", worked, err)
	}
	if queue.completedRef != "" {
		t.Fatalf("failed job was completed: %q", queue.completedRef)
	}
	if queue.retryRef != "job-1" || queue.retryToken != "lease-1" || queue.retryAt != time.Unix(1005, 0) {
		t.Fatalf("retry = (%q, %q, %v)", queue.retryRef, queue.retryToken, queue.retryAt)
	}
}

func TestProcessOneSchedulesRetryFromFailureTime(t *testing.T) {
	queue := &fakeQueue{job: Job{Ref: "job-1", EpisodeRefs: []string{"e1", "e2"}, LeaseToken: "lease-1"}, found: true}
	processor := New(queue, &fakeEngine{err: errors.New("slow worker failed")}, fakeWorker{}, Config{
		QuietPeriod: time.Minute, LeaseDuration: time.Minute, RetryDelay: 5 * time.Second,
		PollInterval: time.Second, MaxEpisodes: 32,
	})
	times := []time.Time{time.Unix(1000, 0), time.Unix(1040, 0)}
	processor.now = func() time.Time {
		current := times[0]
		times = times[1:]
		return current
	}

	worked, err := processor.ProcessOne(context.Background())
	if !worked || err == nil {
		t.Fatalf("ProcessOne = (%v, %v); want failed work", worked, err)
	}
	if queue.retryAt != time.Unix(1045, 0) {
		t.Fatalf("retry at = %v; want failure time + delay", queue.retryAt)
	}
}

func TestProcessOneDoesNothingWhenQueueIsEmpty(t *testing.T) {
	queue := &fakeQueue{}
	engine := &fakeEngine{}
	processor := New(queue, engine, fakeWorker{}, DefaultConfig())

	worked, err := processor.ProcessOne(context.Background())
	if err != nil || worked {
		t.Fatalf("ProcessOne = (%v, %v); want false, nil", worked, err)
	}
	if engine.calls != 0 {
		t.Fatalf("engine calls = %d; want 0", engine.calls)
	}
}

type fakeQueue struct {
	job                          Job
	found                        bool
	leaseRequest                 LeaseRequest
	completedRef, completedToken string
	retryRef, retryToken         string
	retryAt                      time.Time
	events                       *[]string
}

func (queue *fakeQueue) LeaseConsolidationJob(_ context.Context, request LeaseRequest) (Job, bool, error) {
	queue.leaseRequest = request
	return queue.job, queue.found, nil
}

func (queue *fakeQueue) CompleteConsolidationJob(_ context.Context, ref, token string) error {
	queue.completedRef, queue.completedToken = ref, token
	if queue.events != nil {
		*queue.events = append(*queue.events, "complete")
	}
	return nil
}

func (queue *fakeQueue) RetryConsolidationJob(_ context.Context, ref, token string, notBefore time.Time) error {
	queue.retryRef, queue.retryToken, queue.retryAt = ref, token, notBefore
	return nil
}

type fakeEngine struct {
	window  consolidation.Window
	err     error
	calls   int
	receipt consolidation.Receipt
}

func (engine *fakeEngine) ConsolidateWindow(_ context.Context, window consolidation.Window, _ consolidation.Worker) (consolidation.Receipt, error) {
	engine.calls++
	engine.window = window
	return engine.receipt, engine.err
}

type fakeWorker struct{}

func (fakeWorker) ProcessConsolidationWindow(context.Context, consolidation.WorkerRequest) (string, error) {
	return "", nil
}
