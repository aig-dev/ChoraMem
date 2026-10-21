package postgres_test

import (
	"context"
	"testing"
	"time"
)

func TestQueueDiagnosticsSeparatesQuietPeriodLeaseRetriesAndCompletion(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	_, err := h.inspectionDB.Exec(ctx, `INSERT INTO consolidation_jobs
 (job_ref,tenant_ref,scope_kind,agent_ref,episode_refs,created_at,not_before,lease_token,lease_until,attempts)
 VALUES ($1,$2,'agent','agent',ARRAY['episode'],now()-interval '20 minutes',now()-interval '10 minutes','lease',now()+interval '5 minutes',1)`, h.tenant+"active", h.tenant)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := h.store.QueueDiagnostics(ctx, time.Now().UTC().Add(-2*time.Minute), 32)
	if err != nil {
		t.Fatal(err)
	}

	if snapshot.Consolidation.MaxRetries != 0 {
		t.Fatal("first active attempt counted as failed")
	}
	_, err = h.inspectionDB.Exec(ctx, `INSERT INTO consolidation_jobs
 (job_ref,tenant_ref,scope_kind,agent_ref,episode_refs,created_at,not_before,attempts,completed_at)
 VALUES ($1,$3,'agent','quiet',ARRAY['episode'],now()-interval '20 minutes',now()-interval '10 minutes',0,NULL),
 ($2,$3,'agent','retry',ARRAY['episode'],now()-interval '10 minutes',now()-interval '5 seconds',3,NULL),
 ($4,$3,'agent','done',ARRAY['episode'],now()-interval '20 minutes',now()-interval '10 minutes',9,now()),
 ($5,$3,'agent','future',ARRAY['episode'],now()-interval '20 minutes',now()+interval '10 minutes',3,NULL)`, h.tenant+"quiet", h.tenant+"retry", h.tenant, h.tenant+"done", h.tenant+"future")
	if err != nil {
		t.Fatal(err)
	}
	_, err = h.inspectionDB.Exec(ctx, `INSERT INTO memory_index_operations
 (operation_id,stream_ref,stream_sequence,operation_kind,document_kind,document_ref,tenant_ref,agent_ref,attempt_count,created_at,not_before)
 VALUES ($1,$1,1,'UPSERT','EPISODE','episode',$2,'agent',2,now()-interval '12 minutes',now()-interval '10 seconds')`, h.tenant+"projection", h.tenant)
	if err != nil {
		t.Fatal(err)
	}
	_, err = h.inspectionDB.Exec(ctx, `UPDATE consolidation_jobs SET window_hash = decode(repeat('00',32),'hex') WHERE job_ref IN ($1,$2)`, h.tenant+"retry", h.tenant+"future")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err = h.store.QueueDiagnostics(ctx, time.Now().UTC().Add(-2*time.Minute), 32)
	if err != nil {
		t.Fatal(err)
	}
	q := snapshot.Consolidation
	if q.Pending != 4 || q.Due != 1 || q.InFlight != 1 || q.MaxRetries != 3 || q.OldestDueAgeSeconds < 590 || q.OldestDueAgeSeconds > 610 {
		t.Fatalf("consolidation=%+v", q)
	}
	if snapshot.Projection.Pending != 1 || snapshot.Projection.MaxRetries != 2 || snapshot.ProjectionEnabled {
		t.Fatalf("projection=%+v", snapshot)
	}
	_, err = h.inspectionDB.Exec(ctx, `UPDATE consolidation_jobs SET episode_refs = array_fill('episode'::text,ARRAY[32]) WHERE job_ref = $1`, h.tenant+"quiet")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err = h.store.QueueDiagnostics(ctx, time.Now().UTC().Add(-2*time.Minute), 32)
	if err != nil || snapshot.Consolidation.Due != 2 {
		t.Fatalf("full window should bypass quiet period: %+v, %v", snapshot, err)
	}
}
