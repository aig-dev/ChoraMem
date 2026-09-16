package memorygrpc

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/internal/core/fault"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestFourMethodsMapNeutralMemoryContract(t *testing.T) {
	core := &recordingCore{
		observeReceipt: ledger.ObserveReceipt{SourceEventRef: "source-1", EpisodeRef: "episode-1"},
		memoryContext: selection.MemoryContext{
			Ref: "context-1", RunRef: "run-1", Scope: relationshipScope(),
			Constitution: selection.Constitution{MemoryRef: "constitution-v3", Text: "Stay honest."},
			Recollections: []selection.Recollection{
				{MemoryRef: "recollection-self", Text: "I previously checked constraints.", Application: selection.ApplicationScopeSelf},
				{MemoryRef: "recollection-other", Text: "They prefer concise answers.", Application: selection.ApplicationScopeOther},
				{MemoryRef: "recollection-relation", Text: "We clarify decisions together.", Application: selection.ApplicationScopeRelation},
				{MemoryRef: "recollection-situation", Text: "This request has a strict boundary.", Application: selection.ApplicationScopeSituation},
			},
			Dispositions: []selection.Disposition{
				{MemoryRef: "disposition-self", Text: "Check authority first.", Application: selection.ApplicationScopeSelf},
				{MemoryRef: "disposition-relation", Text: "Ask when scope changes.", Application: selection.ApplicationScopeRelation},
			},
			EpisodeEvidence: []selection.EpisodeEvidence{{MemoryRef: "episode-1", Text: "SITUATION [user]\nfirst line\nsecond line\nAGENT_ACT [agent]\nanswer"}},
		},
		deliveryReceipt: ledger.MemoryDeliveryReceipt{Ref: "delivery-1"},
		outcomeReceipt:  ledger.OutcomeReceipt{OutcomeEventRef: "outcome-1", EpisodeRef: "episode-1"},
	}
	server := NewServer(core)
	ctx := auth.ContextWithIdentity(context.Background(), auth.Identity{TenantRef: "tenant-1"})

	observeResponse, err := server.ObserveSourceEvent(ctx, validObserveRequest())
	if err != nil {
		t.Fatalf("ObserveSourceEvent: %v", err)
	}
	if observeResponse.GetSourceEventRef() != "source-1" || observeResponse.GetEpisodeRef() != "episode-1" {
		t.Fatalf("observe response = %#v", observeResponse)
	}
	wantEvent := ledger.SourceEvent{
		Ref: "source-1", Scope: relationshipScope(), ActorKind: ledger.ActorKindAgent,
		ActorRef: "agent-1", Text: "I asked one clarifying question.",
	}
	if core.observeKey != "observe-1" || core.observeEvent != wantEvent || core.observeBinding != (ledger.EpisodeBinding{RunRef: "run-1", SourceGroupRef: "group-1", Role: ledger.RoleAgentAct}) {
		t.Fatalf("observe mapping = key=%q event=%#v binding=%#v", core.observeKey, core.observeEvent, core.observeBinding)
	}

	selectResponse, err := server.SelectMemory(ctx, validSelectRequest())
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	wantSelect := selection.SelectRequest{
		Scope: relationshipScope(), RunRef: "run-1",
		SituationSourceRefs:     []string{"situation-1", "situation-2"},
		Constitution:            selection.Constitution{MemoryRef: "constitution-v3", Text: "Stay honest."},
		EpisodeEvidenceMaxBytes: 4096,
	}
	if !reflect.DeepEqual(core.selectRequest, wantSelect) {
		t.Fatalf("select request = %#v, want %#v", core.selectRequest, wantSelect)
	}
	if selectResponse.GetContextRef() != "context-1" || selectResponse.GetConstitution().GetMemoryRef() != "constitution-v3" || len(selectResponse.GetRecollections()) != 4 || len(selectResponse.GetDispositions()) != 2 || len(selectResponse.GetEpisodeEvidence()) != 1 {
		t.Fatalf("MemoryContext = %#v", selectResponse)
	}
	if got := selectResponse.GetEpisodeEvidence()[0]; got.GetMemoryRef() != "episode-1" || got.GetText() != "SITUATION [user]\nfirst line\nsecond line\nAGENT_ACT [agent]\nanswer" {
		t.Fatalf("Episode evidence mapping = %#v", got)
	}
	wantApplications := []memoryv1.MemoryApplicationScope{
		memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SELF,
		memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_OTHER,
		memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_RELATION,
		memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SITUATION,
	}
	for index, want := range wantApplications {
		if got := selectResponse.GetRecollections()[index].GetApplicationScope(); got != want {
			t.Fatalf("recollection %d application = %v, want %v", index, got, want)
		}
	}

	deliveryResponse, err := server.RecordMemoryDelivery(ctx, validDeliveryRequest())
	if err != nil {
		t.Fatalf("RecordMemoryDelivery: %v", err)
	}
	if deliveryResponse.GetReceiptRef() != "delivery-1" {
		t.Fatalf("delivery response = %#v", deliveryResponse)
	}
	wantDelivery := ledger.MemoryDelivery{
		IdempotencyKey: "delivery-key", Scope: relationshipScope(), RunRef: "run-1",
		MemoryContextRef: "context-1", DeliveredMemoryRefs: []string{"constitution-v3", "disposition-relation"},
	}
	if !reflect.DeepEqual(core.delivery, wantDelivery) {
		t.Fatalf("delivery = %#v, want %#v", core.delivery, wantDelivery)
	}

	outcomeResponse, err := server.ReportOutcome(ctx, validOutcomeRequest())
	if err != nil {
		t.Fatalf("ReportOutcome: %v", err)
	}
	if outcomeResponse.GetOutcomeEventRef() != "outcome-1" || outcomeResponse.GetEpisodeRef() != "episode-1" {
		t.Fatalf("outcome response = %#v", outcomeResponse)
	}
	wantOutcome := ledger.OutcomeReport{
		IdempotencyKey: "outcome-key",
		Event:          ledger.SourceEvent{Ref: "outcome-source", Scope: relationshipScope(), ActorKind: ledger.ActorKindExternal, ActorRef: "provider-1", Text: "The result contradicted the action."},
		RunRef:         "run-1", SourceGroupRef: "group-1", DeliveryReceiptRefs: []string{"delivery-1"}, RelatedSourceEventRefs: []string{"situation-1", "act-1"},
	}
	if !reflect.DeepEqual(core.outcome, wantOutcome) {
		t.Fatalf("outcome = %#v, want %#v", core.outcome, wantOutcome)
	}
}

func TestTenantMismatchRejectsEveryMethodBeforeCore(t *testing.T) {
	core := &recordingCore{}
	server := NewServer(core)
	ctx := auth.ContextWithIdentity(context.Background(), auth.Identity{TenantRef: "tenant-other"})

	tests := []struct {
		name string
		call func() error
	}{
		{name: "observe", call: func() error { _, err := server.ObserveSourceEvent(ctx, validObserveRequest()); return err }},
		{name: "select", call: func() error { _, err := server.SelectMemory(ctx, validSelectRequest()); return err }},
		{name: "delivery", call: func() error { _, err := server.RecordMemoryDelivery(ctx, validDeliveryRequest()); return err }},
		{name: "outcome", call: func() error { _, err := server.ReportOutcome(ctx, validOutcomeRequest()); return err }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			before := core.calls
			if code := status.Code(test.call()); code != codes.PermissionDenied {
				t.Fatalf("status = %v, want PermissionDenied", code)
			}
			if core.calls != before {
				t.Fatalf("Core calls changed from %d to %d", before, core.calls)
			}
		})
	}
}

func TestTransportMapsStableCoreErrors(t *testing.T) {
	tests := []struct {
		name string
		err  error
		call func(*Server, context.Context) error
		want codes.Code
	}{
		{name: "invalid select", err: selection.ErrInvalidSelectRequest, call: callSelect, want: codes.InvalidArgument},
		{name: "ambiguous memory", err: selection.ErrAmbiguousMemoryRef, call: callSelect, want: codes.FailedPrecondition},
		{name: "run conflict", err: selection.ErrRunConflict, call: callSelect, want: codes.AlreadyExists},
		{name: "invalid delivery", err: ledger.ErrInvalidMemoryDelivery, call: callDelivery, want: codes.InvalidArgument},
		{name: "delivery conflict", err: ledger.ErrMemoryDeliveryConflict, call: callDelivery, want: codes.AlreadyExists},
		{name: "invalid outcome", err: ledger.ErrInvalidOutcomeReport, call: callOutcome, want: codes.InvalidArgument},
		{name: "outcome conflict", err: ledger.ErrOutcomeReportConflict, call: callOutcome, want: codes.AlreadyExists},
		{name: "unavailable", err: fmt.Errorf("wrapped: %w", fault.ErrUnavailable), call: callObserve, want: codes.Unavailable},
		{name: "aborted", err: fmt.Errorf("wrapped: %w", fault.ErrAborted), call: callObserve, want: codes.Aborted},
		{name: "canceled", err: context.Canceled, call: callObserve, want: codes.Canceled},
		{name: "deadline", err: context.DeadlineExceeded, call: callObserve, want: codes.DeadlineExceeded},
		{name: "unknown", err: errors.New("database detail"), call: callObserve, want: codes.Internal},
	}
	ctx := auth.ContextWithIdentity(context.Background(), auth.Identity{TenantRef: "tenant-1"})
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			core := &recordingCore{err: test.err}
			if got := status.Code(test.call(NewServer(core), ctx)); got != test.want {
				t.Fatalf("status = %v, want %v", got, test.want)
			}
		})
	}
}

func TestInvalidProtocolNeverCallsCore(t *testing.T) {
	core := &recordingCore{}
	server := NewServer(core)
	ctx := auth.ContextWithIdentity(context.Background(), auth.Identity{TenantRef: "tenant-1"})
	request := validSelectRequest()
	request.Scope.Kind = memoryv1.MemoryScopeKind(99)
	_, err := server.SelectMemory(ctx, request)
	if status.Code(err) != codes.InvalidArgument {
		t.Fatalf("status = %v, want InvalidArgument", status.Code(err))
	}
	if core.calls != 0 {
		t.Fatalf("Core calls = %d, want 0", core.calls)
	}
}

func callObserve(server *Server, ctx context.Context) error {
	_, err := server.ObserveSourceEvent(ctx, validObserveRequest())
	return err
}
func callSelect(server *Server, ctx context.Context) error {
	_, err := server.SelectMemory(ctx, validSelectRequest())
	return err
}
func callDelivery(server *Server, ctx context.Context) error {
	_, err := server.RecordMemoryDelivery(ctx, validDeliveryRequest())
	return err
}
func callOutcome(server *Server, ctx context.Context) error {
	_, err := server.ReportOutcome(ctx, validOutcomeRequest())
	return err
}

func validObserveRequest() *memoryv1.ObserveSourceEventRequest {
	return &memoryv1.ObserveSourceEventRequest{
		IdempotencyKey: "observe-1",
		SourceEvent:    &memoryv1.SourceEvent{Scope: relationshipProtoScope(), SourceRef: "source-1", ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_AGENT, ActorRef: "agent-1", Text: "I asked one clarifying question."},
		EpisodeBinding: &memoryv1.EpisodeBinding{RunRef: "run-1", SourceGroupRef: "group-1", Role: memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_AGENT_ACT},
	}
}

func validSelectRequest() *memoryv1.SelectMemoryRequest {
	return &memoryv1.SelectMemoryRequest{
		Scope: relationshipProtoScope(), RunRef: "run-1", SituationSourceEventRefs: []string{"situation-1", "situation-2"},
		Constitution:            &memoryv1.Constitution{MemoryRef: "constitution-v3", Text: "Stay honest."},
		EpisodeEvidenceMaxBytes: 4096,
	}
}

func validDeliveryRequest() *memoryv1.MemoryDeliveryReceipt {
	return &memoryv1.MemoryDeliveryReceipt{
		IdempotencyKey: "delivery-key", Scope: relationshipProtoScope(), RunRef: "run-1", MemoryContextRef: "context-1",
		DeliveredMemoryRefs: []string{"constitution-v3", "disposition-relation"},
	}
}

func validOutcomeRequest() *memoryv1.ReportOutcomeRequest {
	return &memoryv1.ReportOutcomeRequest{
		IdempotencyKey: "outcome-key", Scope: relationshipProtoScope(), RunRef: "run-1", SourceGroupRef: "group-1",
		Text: "The result contradicted the action.", DeliveryReceiptRefs: []string{"delivery-1"}, RelatedSourceEventRefs: []string{"situation-1", "act-1"},
		SourceRef: "outcome-source", ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_EXTERNAL, ActorRef: "provider-1",
	}
}

func relationshipProtoScope() *memoryv1.MemoryScope {
	return &memoryv1.MemoryScope{Kind: memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_RELATIONSHIP, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-1"}
}

func relationshipScope() ledger.Scope {
	return ledger.Scope{Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-1"}
}

type recordingCore struct {
	calls           int
	observeKey      string
	observeEvent    ledger.SourceEvent
	observeBinding  ledger.EpisodeBinding
	observeReceipt  ledger.ObserveReceipt
	selectRequest   selection.SelectRequest
	memoryContext   selection.MemoryContext
	delivery        ledger.MemoryDelivery
	deliveryReceipt ledger.MemoryDeliveryReceipt
	outcome         ledger.OutcomeReport
	outcomeReceipt  ledger.OutcomeReceipt
	err             error
}

func (core *recordingCore) Observe(_ context.Context, key string, event ledger.SourceEvent, binding ledger.EpisodeBinding) (ledger.ObserveReceipt, error) {
	core.calls++
	core.observeKey, core.observeEvent, core.observeBinding = key, event, binding
	return core.observeReceipt, core.err
}

func (core *recordingCore) SelectMemory(_ context.Context, request selection.SelectRequest) (selection.MemoryContext, error) {
	core.calls++
	core.selectRequest = request
	return core.memoryContext, core.err
}

func (core *recordingCore) RecordMemoryDelivery(_ context.Context, delivery ledger.MemoryDelivery) (ledger.MemoryDeliveryReceipt, error) {
	core.calls++
	core.delivery = delivery
	return core.deliveryReceipt, core.err
}

func (core *recordingCore) ReportOutcome(_ context.Context, outcome ledger.OutcomeReport) (ledger.OutcomeReceipt, error) {
	core.calls++
	core.outcome = outcome
	return core.outcomeReceipt, core.err
}
