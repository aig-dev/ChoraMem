package memoryconnect

import (
	"context"
	"errors"

	"connectrpc.com/connect"
	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type Service interface {
	ObserveSourceEvent(context.Context, *memoryv1.ObserveSourceEventRequest) (*memoryv1.SourceEventReceipt, error)
	SelectMemory(context.Context, *memoryv1.SelectMemoryRequest) (*memoryv1.MemoryContext, error)
	RecordMemoryDelivery(context.Context, *memoryv1.MemoryDeliveryReceipt) (*memoryv1.ReceiptAck, error)
	ReportOutcome(context.Context, *memoryv1.ReportOutcomeRequest) (*memoryv1.OutcomeReceipt, error)
}

type Server struct {
	service Service
}

func NewServer(service Service) *Server {
	return &Server{service: service}
}

func (server *Server) ObserveSourceEvent(ctx context.Context, request *connect.Request[memoryv1.ObserveSourceEventRequest]) (*connect.Response[memoryv1.SourceEventReceipt], error) {
	if server.service == nil {
		return nil, connect.NewError(connect.CodeInternal, errors.New("memory service is unavailable"))
	}
	response, err := server.service.ObserveSourceEvent(ctx, request.Msg)
	return connectResponse(response, err)
}

func (server *Server) SelectMemory(ctx context.Context, request *connect.Request[memoryv1.SelectMemoryRequest]) (*connect.Response[memoryv1.MemoryContext], error) {
	if server.service == nil {
		return nil, connect.NewError(connect.CodeInternal, errors.New("memory service is unavailable"))
	}
	response, err := server.service.SelectMemory(ctx, request.Msg)
	return connectResponse(response, err)
}

func (server *Server) RecordMemoryDelivery(ctx context.Context, request *connect.Request[memoryv1.MemoryDeliveryReceipt]) (*connect.Response[memoryv1.ReceiptAck], error) {
	if server.service == nil {
		return nil, connect.NewError(connect.CodeInternal, errors.New("memory service is unavailable"))
	}
	response, err := server.service.RecordMemoryDelivery(ctx, request.Msg)
	return connectResponse(response, err)
}

func (server *Server) ReportOutcome(ctx context.Context, request *connect.Request[memoryv1.ReportOutcomeRequest]) (*connect.Response[memoryv1.OutcomeReceipt], error) {
	if server.service == nil {
		return nil, connect.NewError(connect.CodeInternal, errors.New("memory service is unavailable"))
	}
	response, err := server.service.ReportOutcome(ctx, request.Msg)
	return connectResponse(response, err)
}

func connectResponse[Message any](message *Message, err error) (*connect.Response[Message], error) {
	if err != nil {
		grpcStatus := status.Convert(err)
		return nil, connect.NewError(connectCode(grpcStatus.Code()), errors.New(grpcStatus.Message()))
	}
	return connect.NewResponse(message), nil
}

func connectCode(code codes.Code) connect.Code {
	switch code {
	case codes.Canceled:
		return connect.CodeCanceled
	case codes.Unknown:
		return connect.CodeUnknown
	case codes.InvalidArgument:
		return connect.CodeInvalidArgument
	case codes.DeadlineExceeded:
		return connect.CodeDeadlineExceeded
	case codes.NotFound:
		return connect.CodeNotFound
	case codes.AlreadyExists:
		return connect.CodeAlreadyExists
	case codes.PermissionDenied:
		return connect.CodePermissionDenied
	case codes.ResourceExhausted:
		return connect.CodeResourceExhausted
	case codes.FailedPrecondition:
		return connect.CodeFailedPrecondition
	case codes.Aborted:
		return connect.CodeAborted
	case codes.OutOfRange:
		return connect.CodeOutOfRange
	case codes.Unimplemented:
		return connect.CodeUnimplemented
	case codes.Internal:
		return connect.CodeInternal
	case codes.Unavailable:
		return connect.CodeUnavailable
	case codes.DataLoss:
		return connect.CodeDataLoss
	case codes.Unauthenticated:
		return connect.CodeUnauthenticated
	default:
		return connect.CodeUnknown
	}
}
