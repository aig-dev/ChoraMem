package memorygrpc

import (
	"context"
	"errors"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/internal/core/fault"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/transport/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type Core interface {
	Observe(context.Context, string, ledger.SourceEvent, ledger.EpisodeBinding) (ledger.ObserveReceipt, error)
	SelectMemory(context.Context, selection.SelectRequest) (selection.MemoryContext, error)
	RecordMemoryDelivery(context.Context, ledger.MemoryDelivery) (ledger.MemoryDeliveryReceipt, error)
	ReportOutcome(context.Context, ledger.OutcomeReport) (ledger.OutcomeReceipt, error)
}

type Server struct {
	memoryv1.UnimplementedMemoryCoreServer
	core Core
}

func NewServer(core Core) *Server {
	return &Server{core: core}
}

func (server *Server) ObserveSourceEvent(ctx context.Context, request *memoryv1.ObserveSourceEventRequest) (*memoryv1.SourceEventReceipt, error) {
	key, event, binding, err := observeCommand(ctx, request)
	if err != nil {
		return nil, err
	}
	if server.core == nil {
		return nil, status.Error(codes.Internal, "memory core is unavailable")
	}
	receipt, err := server.core.Observe(ctx, key, event, binding)
	if err != nil {
		return nil, coreStatus("observe", err)
	}
	return &memoryv1.SourceEventReceipt{SourceEventRef: receipt.SourceEventRef, EpisodeRef: receipt.EpisodeRef}, nil
}

func (server *Server) SelectMemory(ctx context.Context, request *memoryv1.SelectMemoryRequest) (*memoryv1.MemoryContext, error) {
	command, err := selectCommand(ctx, request)
	if err != nil {
		return nil, err
	}
	if server.core == nil {
		return nil, status.Error(codes.Internal, "memory core is unavailable")
	}
	contextValue, err := server.core.SelectMemory(ctx, command)
	if err != nil {
		return nil, coreStatus("select", err)
	}
	response, err := mapMemoryContext(contextValue)
	if err != nil {
		return nil, status.Error(codes.Internal, "memory core returned an invalid MemoryContext")
	}
	return response, nil
}

func (server *Server) RecordMemoryDelivery(ctx context.Context, request *memoryv1.MemoryDeliveryReceipt) (*memoryv1.ReceiptAck, error) {
	delivery, err := deliveryCommand(ctx, request)
	if err != nil {
		return nil, err
	}
	if server.core == nil {
		return nil, status.Error(codes.Internal, "memory core is unavailable")
	}
	receipt, err := server.core.RecordMemoryDelivery(ctx, delivery)
	if err != nil {
		return nil, coreStatus("delivery", err)
	}
	return &memoryv1.ReceiptAck{ReceiptRef: receipt.Ref}, nil
}

func (server *Server) ReportOutcome(ctx context.Context, request *memoryv1.ReportOutcomeRequest) (*memoryv1.OutcomeReceipt, error) {
	report, err := outcomeCommand(ctx, request)
	if err != nil {
		return nil, err
	}
	if server.core == nil {
		return nil, status.Error(codes.Internal, "memory core is unavailable")
	}
	receipt, err := server.core.ReportOutcome(ctx, report)
	if err != nil {
		return nil, coreStatus("outcome", err)
	}
	return &memoryv1.OutcomeReceipt{OutcomeEventRef: receipt.OutcomeEventRef, EpisodeRef: receipt.EpisodeRef}, nil
}

func observeCommand(ctx context.Context, request *memoryv1.ObserveSourceEventRequest) (string, ledger.SourceEvent, ledger.EpisodeBinding, error) {
	if request == nil || request.GetSourceEvent() == nil || request.GetSourceEvent().GetScope() == nil {
		return "", ledger.SourceEvent{}, ledger.EpisodeBinding{}, invalidArgument("source_event.scope is required")
	}
	scope, err := authorizedScope(ctx, request.GetSourceEvent().GetScope())
	if err != nil {
		return "", ledger.SourceEvent{}, ledger.EpisodeBinding{}, err
	}
	if request.GetIdempotencyKey() == "" {
		return "", ledger.SourceEvent{}, ledger.EpisodeBinding{}, invalidArgument("idempotency_key is required")
	}
	actorKind, err := mapActorKind(request.GetSourceEvent().GetActorKind())
	if err != nil {
		return "", ledger.SourceEvent{}, ledger.EpisodeBinding{}, invalidArgument(err.Error())
	}
	binding, err := mapEpisodeBinding(request.GetEpisodeBinding())
	if err != nil {
		return "", ledger.SourceEvent{}, ledger.EpisodeBinding{}, invalidArgument(err.Error())
	}
	source := request.GetSourceEvent()
	return request.GetIdempotencyKey(), ledger.SourceEvent{
		Ref: source.GetSourceRef(), Scope: scope, ActorKind: actorKind, ActorRef: source.GetActorRef(), Text: source.GetText(),
		Constitution: mapConstitution(source.GetConstitution()),
	}, binding, nil
}

func selectCommand(ctx context.Context, request *memoryv1.SelectMemoryRequest) (selection.SelectRequest, error) {
	if request == nil || request.GetScope() == nil {
		return selection.SelectRequest{}, invalidArgument("scope is required")
	}
	scope, err := authorizedScope(ctx, request.GetScope())
	if err != nil {
		return selection.SelectRequest{}, err
	}
	command := selection.SelectRequest{
		Scope: scope, RunRef: request.GetRunRef(), SituationSourceRefs: append([]string(nil), request.GetSituationSourceEventRefs()...),
		EpisodeEvidenceMaxBytes: int(request.GetEpisodeEvidenceMaxBytes()),
	}
	if constitution := request.GetConstitution(); constitution != nil {
		command.Constitution = selection.Constitution{MemoryRef: constitution.GetMemoryRef(), Text: constitution.GetText()}
	}
	return command, nil
}

func deliveryCommand(ctx context.Context, request *memoryv1.MemoryDeliveryReceipt) (ledger.MemoryDelivery, error) {
	if request == nil || request.GetScope() == nil {
		return ledger.MemoryDelivery{}, invalidArgument("scope is required")
	}
	scope, err := authorizedScope(ctx, request.GetScope())
	if err != nil {
		return ledger.MemoryDelivery{}, err
	}
	return ledger.MemoryDelivery{
		IdempotencyKey: request.GetIdempotencyKey(), Scope: scope, RunRef: request.GetRunRef(),
		MemoryContextRef: request.GetMemoryContextRef(), DeliveredMemoryRefs: append([]string(nil), request.GetDeliveredMemoryRefs()...),
	}, nil
}

func outcomeCommand(ctx context.Context, request *memoryv1.ReportOutcomeRequest) (ledger.OutcomeReport, error) {
	if request == nil || request.GetScope() == nil {
		return ledger.OutcomeReport{}, invalidArgument("scope is required")
	}
	scope, err := authorizedScope(ctx, request.GetScope())
	if err != nil {
		return ledger.OutcomeReport{}, err
	}
	actorKind, err := mapActorKind(request.GetActorKind())
	if err != nil {
		return ledger.OutcomeReport{}, invalidArgument(err.Error())
	}
	return ledger.OutcomeReport{
		IdempotencyKey: request.GetIdempotencyKey(),
		Event:          ledger.SourceEvent{Ref: request.GetSourceRef(), Scope: scope, ActorKind: actorKind, ActorRef: request.GetActorRef(), Text: request.GetText(), Constitution: mapConstitution(request.GetConstitution())},
		RunRef:         request.GetRunRef(), SourceGroupRef: request.GetSourceGroupRef(),
		DeliveryReceiptRefs:    append([]string(nil), request.GetDeliveryReceiptRefs()...),
		RelatedSourceEventRefs: append([]string(nil), request.GetRelatedSourceEventRefs()...),
	}, nil
}

func mapConstitution(value *memoryv1.Constitution) ledger.Constitution {
	return ledger.Constitution{MemoryRef: value.GetMemoryRef(), Text: value.GetText()}
}

func authorizedScope(ctx context.Context, value *memoryv1.MemoryScope) (ledger.Scope, error) {
	scope, err := mapScope(value)
	if err != nil {
		return ledger.Scope{}, invalidArgument(err.Error())
	}
	if err := auth.AuthorizeTenant(ctx, scope.TenantRef); err != nil {
		switch {
		case errors.Is(err, auth.ErrUnauthenticated):
			return ledger.Scope{}, status.Error(codes.Unauthenticated, "valid bearer authentication is required")
		default:
			return ledger.Scope{}, status.Error(codes.PermissionDenied, "authenticated tenant does not match request tenant")
		}
	}
	return scope, nil
}

func mapMemoryContext(value selection.MemoryContext) (*memoryv1.MemoryContext, error) {
	scope, err := mapProtoScope(value.Scope)
	if err != nil {
		return nil, err
	}
	response := &memoryv1.MemoryContext{ContextRef: value.Ref, RunRef: value.RunRef, Scope: scope}
	if value.Constitution.MemoryRef != "" || value.Constitution.Text != "" {
		response.Constitution = &memoryv1.Constitution{MemoryRef: value.Constitution.MemoryRef, Text: value.Constitution.Text}
	}
	response.Recollections = make([]*memoryv1.Recollection, 0, len(value.Recollections))
	for _, item := range value.Recollections {
		application, err := mapApplicationScope(item.Application)
		if err != nil {
			return nil, err
		}
		response.Recollections = append(response.Recollections, &memoryv1.Recollection{MemoryRef: item.MemoryRef, Text: item.Text, ApplicationScope: application})
	}
	response.Dispositions = make([]*memoryv1.Disposition, 0, len(value.Dispositions))
	for _, item := range value.Dispositions {
		application, err := mapApplicationScope(item.Application)
		if err != nil {
			return nil, err
		}
		response.Dispositions = append(response.Dispositions, &memoryv1.Disposition{MemoryRef: item.MemoryRef, Text: item.Text, ApplicationScope: application})
	}
	response.EpisodeEvidence = make([]*memoryv1.EpisodeEvidence, 0, len(value.EpisodeEvidence))
	for _, item := range value.EpisodeEvidence {
		response.EpisodeEvidence = append(response.EpisodeEvidence, &memoryv1.EpisodeEvidence{MemoryRef: item.MemoryRef, Text: item.Text})
	}
	return response, nil
}

func mapApplicationScope(value selection.ApplicationScope) (memoryv1.MemoryApplicationScope, error) {
	switch value {
	case selection.ApplicationScopeSelf:
		return memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SELF, nil
	case selection.ApplicationScopeOther:
		return memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_OTHER, nil
	case selection.ApplicationScopeRelation:
		return memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_RELATION, nil
	case selection.ApplicationScopeSituation:
		return memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SITUATION, nil
	default:
		return memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_UNSPECIFIED, errors.New("known application scope is required")
	}
}

func mapScope(value *memoryv1.MemoryScope) (ledger.Scope, error) {
	if value == nil {
		return ledger.Scope{}, errors.New("scope is required")
	}
	var kind ledger.ScopeKind
	switch value.GetKind() {
	case memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_AGENT:
		kind = ledger.ScopeKindAgent
	case memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_RELATIONSHIP:
		kind = ledger.ScopeKindRelationship
	default:
		return ledger.Scope{}, errors.New("known scope kind is required")
	}
	return ledger.Scope{Kind: kind, TenantRef: value.GetTenantRef(), AgentRef: value.GetAgentRef(), RelationshipRef: value.GetRelationshipRef(), SessionRef: value.GetSessionRef()}, nil
}

func mapProtoScope(value ledger.Scope) (*memoryv1.MemoryScope, error) {
	var kind memoryv1.MemoryScopeKind
	switch value.Kind {
	case ledger.ScopeKindAgent:
		kind = memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_AGENT
	case ledger.ScopeKindRelationship:
		kind = memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_RELATIONSHIP
	default:
		return nil, errors.New("known scope kind is required")
	}
	return &memoryv1.MemoryScope{Kind: kind, TenantRef: value.TenantRef, AgentRef: value.AgentRef, RelationshipRef: value.RelationshipRef, SessionRef: value.SessionRef}, nil
}

func mapActorKind(value memoryv1.SourceActorKind) (ledger.ActorKind, error) {
	switch value {
	case memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_USER:
		return ledger.ActorKindUser, nil
	case memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_AGENT:
		return ledger.ActorKindAgent, nil
	case memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_SYSTEM:
		return ledger.ActorKindSystem, nil
	case memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_TOOL:
		return ledger.ActorKindTool, nil
	case memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_EXTERNAL:
		return ledger.ActorKindExternal, nil
	default:
		return "", errors.New("known source actor kind is required")
	}
}

func mapEpisodeBinding(value *memoryv1.EpisodeBinding) (ledger.EpisodeBinding, error) {
	if value == nil {
		return ledger.EpisodeBinding{}, nil
	}
	var role ledger.SourceRole
	switch value.GetRole() {
	case memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_UNSPECIFIED:
	case memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_SITUATION:
		role = ledger.RoleSituation
	case memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_AGENT_ACT:
		role = ledger.RoleAgentAct
	case memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_OUTCOME:
		role = ledger.RoleOutcome
	default:
		return ledger.EpisodeBinding{}, errors.New("known episode source role is required")
	}
	return ledger.EpisodeBinding{RunRef: value.GetRunRef(), SourceGroupRef: value.GetSourceGroupRef(), Role: role}, nil
}

func coreStatus(operation string, err error) error {
	switch {
	case errors.Is(err, ledger.ErrInvalidSourceEvent),
		errors.Is(err, ledger.ErrInvalidEpisodeBinding),
		errors.Is(err, selection.ErrInvalidSelectRequest),
		errors.Is(err, ledger.ErrInvalidMemoryDelivery),
		errors.Is(err, ledger.ErrInvalidOutcomeReport):
		return status.Error(codes.InvalidArgument, err.Error())
	case errors.Is(err, ledger.ErrIdempotencyConflict),
		errors.Is(err, selection.ErrRunConflict),
		errors.Is(err, ledger.ErrMemoryDeliveryConflict),
		errors.Is(err, ledger.ErrOutcomeReportConflict):
		return status.Error(codes.AlreadyExists, err.Error())
	case errors.Is(err, ledger.ErrSourceEventConflict),
		errors.Is(err, ledger.ErrEpisodeSealed),
		errors.Is(err, selection.ErrAmbiguousMemoryRef):
		return status.Error(codes.FailedPrecondition, err.Error())
	case errors.Is(err, fault.ErrUnavailable):
		return status.Error(codes.Unavailable, "memory core is temporarily unavailable")
	case errors.Is(err, fault.ErrAborted):
		return status.Error(codes.Aborted, "memory write was aborted; retry the complete request")
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return status.FromContextError(err).Err()
	default:
		return status.Error(codes.Internal, "memory "+operation+" failed")
	}
}

func invalidArgument(message string) error {
	return status.Error(codes.InvalidArgument, message)
}
