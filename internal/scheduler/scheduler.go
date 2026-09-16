// Package scheduler turns durable intake evidence into frozen consolidation
// windows. It owns retry mechanics only; semantic eligibility and every Seed
// transition remain inside the consolidation engine.
package scheduler

import (
	"context"
	"errors"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
)

// ErrLeaseLost means another process no longer recognizes the caller's lease.
var ErrLeaseLost = errors.New("consolidation Job lease lost")

// Job is one immutable leased window. Stable refs remain Core-owned and are
// never generated or rewritten by the inference Worker.
type Job struct {
	Ref              string
	EpisodeRefs      []string
	OutcomeEventRefs []string
	LeaseToken       string
}

// LeaseRequest carries only runtime timing and bounded-window policy.
type LeaseRequest struct {
	QuietBefore time.Time
	LeaseUntil  time.Time
	MaxEpisodes int
	Overlap     int
}

// Queue is the minimal durable work boundary implemented by PostgreSQL.
type Queue interface {
	LeaseConsolidationJob(context.Context, LeaseRequest) (Job, bool, error)
	CompleteConsolidationJob(context.Context, string, string) error
	RetryConsolidationJob(context.Context, string, string, time.Time) error
}

// Engine is the single semantic commit entry. Scheduler never branches on
// FORM, REENACT, INHIBIT, or REVISE.
type Engine interface {
	ConsolidateWindow(context.Context, consolidation.Window, consolidation.Worker) (consolidation.Receipt, error)
}

// Config controls batching and retry frequency, not learning meaning.
type Config struct {
	QuietPeriod   time.Duration
	LeaseDuration time.Duration
	WorkerTimeout time.Duration
	RetryDelay    time.Duration
	PollInterval  time.Duration
	MaxEpisodes   int
	Overlap       int
	OnError       func(error)
}

// DefaultConfig waits for a short quiet period, while a full window is ready
// immediately. One complete Episode is enough after the quiet period.
func DefaultConfig() Config {
	return Config{
		QuietPeriod:   2 * time.Minute,
		LeaseDuration: 5 * time.Minute,
		WorkerTimeout: 2 * time.Minute,
		RetryDelay:    15 * time.Second,
		PollInterval:  time.Second,
		MaxEpisodes:   32,
		Overlap:       4,
	}
}

// Processor leases, consolidates, and acknowledges one durable Job at a time.
type Processor struct {
	queue  Queue
	engine Engine
	worker consolidation.Worker
	config Config
	now    func() time.Time
}

// New constructs a background processor. Non-positive timing values fall
// back to safe defaults so environment parsing cannot create a busy loop.
func New(queue Queue, engine Engine, worker consolidation.Worker, config Config) *Processor {
	defaults := DefaultConfig()
	if config.QuietPeriod <= 0 {
		config.QuietPeriod = defaults.QuietPeriod
	}
	if config.LeaseDuration <= 0 {
		config.LeaseDuration = defaults.LeaseDuration
	}
	if config.WorkerTimeout <= 0 {
		config.WorkerTimeout = defaults.WorkerTimeout
	}
	if config.RetryDelay <= 0 {
		config.RetryDelay = defaults.RetryDelay
	}
	if config.PollInterval <= 0 {
		config.PollInterval = defaults.PollInterval
	}
	if config.MaxEpisodes < 2 {
		config.MaxEpisodes = defaults.MaxEpisodes
	}
	if config.Overlap < 0 {
		config.Overlap = 0
	}
	if config.Overlap >= config.MaxEpisodes {
		config.Overlap = config.MaxEpisodes - 1
	}
	return &Processor{queue: queue, engine: engine, worker: worker, config: config, now: time.Now}
}

// ProcessOne runs exactly one frozen Job. A failed semantic call releases the
// same window for retry; a commit followed by a lost ack is safe because the
// engine freezes its own receipt by Job ref and window hash.
func (processor *Processor) ProcessOne(ctx context.Context) (bool, error) {
	now := processor.now()
	job, found, err := processor.queue.LeaseConsolidationJob(ctx, LeaseRequest{
		QuietBefore: now.Add(-processor.config.QuietPeriod),
		LeaseUntil:  now.Add(processor.config.LeaseDuration),
		MaxEpisodes: processor.config.MaxEpisodes,
		Overlap:     processor.config.Overlap,
	})
	if err != nil || !found {
		return false, err
	}

	jobContext, cancel := context.WithTimeout(ctx, processor.config.WorkerTimeout)
	defer cancel()
	_, err = processor.engine.ConsolidateWindow(jobContext, consolidation.Window{
		JobRef:           job.Ref,
		EpisodeRefs:      append([]string(nil), job.EpisodeRefs...),
		OutcomeEventRefs: append([]string(nil), job.OutcomeEventRefs...),
	}, processor.worker)
	if err != nil {
		return processor.retryJob(ctx, job, err)
	}
	if err := processor.queue.CompleteConsolidationJob(ctx, job.Ref, job.LeaseToken); err != nil {
		return true, err
	}
	return true, nil
}

func (processor *Processor) retryJob(ctx context.Context, job Job, cause error) (bool, error) {
	if ctx.Err() != nil {
		return true, cause
	}
	retryErr := processor.queue.RetryConsolidationJob(
		ctx, job.Ref, job.LeaseToken, processor.now().Add(processor.config.RetryDelay),
	)
	return true, errors.Join(cause, retryErr)
}

// Run drains available Jobs and then polls. Individual failures are reported
// and retried without taking down public intake.
func (processor *Processor) Run(ctx context.Context) error {
	for {
		for {
			worked, err := processor.ProcessOne(ctx)
			if ctx.Err() != nil {
				return nil
			}
			if err != nil {
				if processor.config.OnError != nil {
					processor.config.OnError(err)
				}
				break
			}
			if !worked {
				break
			}
		}

		timer := time.NewTimer(processor.config.PollInterval)
		select {
		case <-ctx.Done():
			if !timer.Stop() {
				<-timer.C
			}
			return nil
		case <-timer.C:
		}
	}
}
