package postgres_test

import (
	"context"
	"testing"
	"time"
)

func TestQueueDiagnosticsRespectsOwnerAndStreamPredecessors(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	_, err := h.inspectionDB.Exec(ctx, `INSERT INTO consolidation_jobs
 (job_ref,tenant_ref,scope_kind,agent_ref,episode_refs,window_hash,created_at,updated_at,not_before,attempts)
 VALUES ($1,$3,'agent','chain',ARRAY['episode'],decode(repeat('00',32),'hex'),now()-interval '20 minutes',now()-interval '20 minutes',now()+interval '10 minutes',1),
 ($2,$3,'agent','chain',ARRAY['episode'],NULL,now()-interval '20 minutes',now()-interval '20 minutes',now()-interval '10 minutes',0)`, h.tenant+"head", h.tenant+"tail", h.tenant)
	if err != nil {
		t.Fatal(err)
	}
	_, err = h.inspectionDB.Exec(ctx, `INSERT INTO memory_index_operations
 (operation_id,stream_ref,stream_sequence,operation_kind,document_kind,document_ref,tenant_ref,agent_ref,created_at,not_before)
 VALUES ($1,$3,1,'UPSERT','EPISODE','episode',$3,'chain',now()-interval '20 minutes',now()+interval '10 minutes'),
 ($2,$3,2,'UPSERT','EPISODE','episode',$3,'chain',now()-interval '20 minutes',now()-interval '10 minutes')`, h.tenant+"ophead", h.tenant+"optail", h.tenant)
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := h.store.QueueDiagnostics(ctx, time.Now().UTC().Add(-2*time.Minute), 32)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Consolidation.Pending != 2 || snapshot.Projection.Pending != 2 || snapshot.Consolidation.Due != 0 || snapshot.Projection.Due != 0 || snapshot.Consolidation.OldestDueAgeSeconds != 0 || snapshot.Projection.OldestDueAgeSeconds != 0 {
		t.Fatalf("future predecessor should block later runnable-age rows: %+v", snapshot)
	}
	_, err = h.inspectionDB.Exec(ctx, `UPDATE consolidation_jobs SET not_before=now()-interval '1 minute',lease_token='lease',lease_until=now()+interval '5 minutes' WHERE job_ref=$1`, h.tenant+"head")
	if err != nil {
		t.Fatal(err)
	}
	_, err = h.inspectionDB.Exec(ctx, `UPDATE memory_index_operations SET not_before=now()-interval '1 minute',lease_token='lease',lease_until=now()+interval '5 minutes' WHERE operation_id=$1`, h.tenant+"ophead")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err = h.store.QueueDiagnostics(ctx, time.Now().UTC().Add(-2*time.Minute), 32)
	if err != nil || snapshot.Consolidation.Due != 0 || snapshot.Projection.Due != 0 {
		t.Fatalf("active predecessor should block tail: %+v, %v", snapshot, err)
	}
	_, err = h.inspectionDB.Exec(ctx, `UPDATE consolidation_jobs SET completed_at=now() WHERE job_ref=$1`, h.tenant+"head")
	if err != nil {
		t.Fatal(err)
	}
	_, err = h.inspectionDB.Exec(ctx, `UPDATE memory_index_operations SET acknowledged_at=now() WHERE operation_id=$1`, h.tenant+"ophead")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err = h.store.QueueDiagnostics(ctx, time.Now().UTC().Add(-2*time.Minute), 32)
	if err != nil || snapshot.Consolidation.Due != 1 || snapshot.Projection.Due != 1 {
		t.Fatalf("completed predecessor should release tail: %+v, %v", snapshot, err)
	}
}
