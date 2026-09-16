// Package memoryindexgrpc adapts the generated memoryindex.v1 client to the
// provider-neutral MemoryIndex port.
package memoryindexgrpc

import (
	"context"
	"errors"
	"strings"
	"time"

	memoryindexv1 "github.com/aig-dev/ChoraMem/gen/memoryindex/v1"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

type Config struct {
	Address     string
	RPCToken    string
	CallTimeout time.Duration
}

type Client struct {
	generated memoryindexv1.MemoryIndexClient
	ownedConn *grpc.ClientConn
	config    Config
}

func New(generated memoryindexv1.MemoryIndexClient, config Config) *Client {
	if config.CallTimeout <= 0 {
		config.CallTimeout = 10 * time.Second
	}
	return &Client{generated: generated, config: config}
}

func Dial(_ context.Context, config Config) (*Client, error) {
	if strings.TrimSpace(config.Address) == "" {
		return nil, errors.New("MemoryIndex gRPC address is required")
	}
	connection, err := grpc.NewClient(
		strings.TrimSpace(config.Address),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		return nil, err
	}
	client := New(memoryindexv1.NewMemoryIndexClient(connection), config)
	client.ownedConn = connection
	return client, nil
}

func (client *Client) Close() error {
	if client == nil || client.ownedConn == nil {
		return nil
	}
	return client.ownedConn.Close()
}

func (client *Client) Search(ctx context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	callContext, cancel, err := client.callContext(ctx)
	if err != nil {
		return nil, err
	}
	defer cancel()
	response, err := client.generated.Search(callContext, &memoryindexv1.SearchRequest{Query: queryMessage(query)})
	if err != nil {
		return nil, err
	}
	candidates := make([]memoryindex.Candidate, 0, len(response.GetCandidates()))
	for _, candidate := range response.GetCandidates() {
		candidates = append(candidates, memoryindex.Candidate{
			Kind: kindFromMessage(candidate.GetKind()), Ref: candidate.GetRef(),
		})
	}
	return candidates, nil
}

func (client *Client) Upsert(ctx context.Context, documents []memoryindex.Document) error {
	if len(documents) == 0 {
		return nil
	}
	callContext, cancel, err := client.callContext(ctx)
	if err != nil {
		return err
	}
	defer cancel()
	request := &memoryindexv1.UpsertRequest{Documents: make([]*memoryindexv1.Document, 0, len(documents))}
	for _, document := range documents {
		request.Documents = append(request.Documents, &memoryindexv1.Document{
			Kind: kindToMessage(document.Kind), Ref: document.Ref,
			Scope: scopeMessage(document.Scope), Text: document.Text,
		})
	}
	_, err = client.generated.Upsert(callContext, request)
	return err
}

func (client *Client) Delete(ctx context.Context, scope memoryindex.Scope, candidates []memoryindex.Candidate) error {
	if len(candidates) == 0 {
		return nil
	}
	callContext, cancel, err := client.callContext(ctx)
	if err != nil {
		return err
	}
	defer cancel()
	request := &memoryindexv1.DeleteRequest{
		Scope: scopeMessage(scope), Candidates: make([]*memoryindexv1.Candidate, 0, len(candidates)),
	}
	for _, candidate := range candidates {
		request.Candidates = append(request.Candidates, &memoryindexv1.Candidate{
			Kind: kindToMessage(candidate.Kind), Ref: candidate.Ref,
		})
	}
	_, err = client.generated.Delete(callContext, request)
	return err
}

func (client *Client) Reset(ctx context.Context) error {
	callContext, cancel, err := client.callContext(ctx)
	if err != nil {
		return err
	}
	defer cancel()
	_, err = client.generated.Reset(callContext, &memoryindexv1.ResetRequest{})
	return err
}

func (client *Client) callContext(ctx context.Context) (context.Context, context.CancelFunc, error) {
	if client == nil || client.generated == nil {
		return nil, nil, memoryindex.ErrUnavailable
	}
	if ctx == nil {
		ctx = context.Background()
	}
	if token := strings.TrimSpace(client.config.RPCToken); token != "" {
		ctx = metadata.AppendToOutgoingContext(ctx, "x-agent-rpc-token", token)
	}
	callContext, cancel := context.WithTimeout(ctx, client.config.CallTimeout)
	return callContext, cancel, nil
}

func queryMessage(query memoryindex.Query) *memoryindexv1.Query {
	return &memoryindexv1.Query{Scope: scopeMessage(query.Scope), Text: query.Text, Limit: int32(query.Limit)}
}

func scopeMessage(scope memoryindex.Scope) *memoryindexv1.Scope {
	return &memoryindexv1.Scope{
		TenantRef: scope.TenantRef, AgentRef: scope.AgentRef, RelationshipRef: scope.RelationshipRef,
	}
}

func kindToMessage(kind memoryindex.Kind) memoryindexv1.MemoryKind {
	switch kind {
	case memoryindex.KindEpisode:
		return memoryindexv1.MemoryKind_MEMORY_KIND_EPISODE
	case memoryindex.KindRecollection:
		return memoryindexv1.MemoryKind_MEMORY_KIND_RECOLLECTION
	case memoryindex.KindDisposition:
		return memoryindexv1.MemoryKind_MEMORY_KIND_DISPOSITION
	default:
		return memoryindexv1.MemoryKind_MEMORY_KIND_UNSPECIFIED
	}
}

func kindFromMessage(kind memoryindexv1.MemoryKind) memoryindex.Kind {
	switch kind {
	case memoryindexv1.MemoryKind_MEMORY_KIND_EPISODE:
		return memoryindex.KindEpisode
	case memoryindexv1.MemoryKind_MEMORY_KIND_RECOLLECTION:
		return memoryindex.KindRecollection
	case memoryindexv1.MemoryKind_MEMORY_KIND_DISPOSITION:
		return memoryindex.KindDisposition
	default:
		return ""
	}
}

var _ memoryindex.Index = (*Client)(nil)
