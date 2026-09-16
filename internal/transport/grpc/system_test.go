package memorygrpc_test

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

func TestRunningMemorydPersistsEpisodeThroughPublicGRPC(t *testing.T) {
	address := os.Getenv("MEMORY_TEST_GRPC_ADDR")
	if address == "" {
		t.Skip("MEMORY_TEST_GRPC_ADDR is not set")
	}

	connection, err := grpc.NewClient(address, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial memoryd: %v", err)
	}
	defer connection.Close()
	client := memoryv1.NewMemoryCoreClient(connection)

	identity := fmt.Sprintf("system-test-%d", time.Now().UnixNano())
	scope := &memoryv1.MemoryScope{
		Kind:            memoryv1.MemoryScopeKind_MEMORY_SCOPE_KIND_RELATIONSHIP,
		TenantRef:       identity,
		AgentRef:        "agent-1",
		RelationshipRef: "relationship-1",
		SessionRef:      "session-1",
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	situationRequest := &memoryv1.ObserveSourceEventRequest{
		IdempotencyKey: identity + "-situation-request",
		SourceEvent: &memoryv1.SourceEvent{
			Scope: scope, SourceRef: identity + "-situation", ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_USER,
			ActorRef: "user-1", Text: "The user asks for a concise architecture decision.",
		},
		EpisodeBinding: &memoryv1.EpisodeBinding{
			RunRef: identity + "-run", SourceGroupRef: identity + "-group",
			Role: memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_SITUATION,
		},
	}
	first, err := client.ObserveSourceEvent(ctx, situationRequest)
	if err != nil {
		t.Fatalf("observe situation: %v", err)
	}
	if first.GetSourceEventRef() == "" || first.GetEpisodeRef() != "" {
		t.Fatalf("situation receipt = %#v", first)
	}

	selected, err := client.SelectMemory(ctx, &memoryv1.SelectMemoryRequest{
		Scope:                    scope,
		RunRef:                   identity + "-run",
		SituationSourceEventRefs: []string{situationRequest.GetSourceEvent().GetSourceRef()},
		Constitution: &memoryv1.Constitution{
			MemoryRef: "constitution-v1",
			Text:      "Stay curious, honest, and concise.",
		},
	})
	if err != nil {
		t.Fatalf("select MemoryContext: %v", err)
	}
	if selected.GetContextRef() == "" || selected.GetRunRef() != identity+"-run" {
		t.Fatalf("MemoryContext identity = %#v", selected)
	}
	if selected.GetConstitution().GetMemoryRef() != "constitution-v1" ||
		len(selected.GetRecollections()) != 0 || len(selected.GetDispositions()) != 0 {
		t.Fatalf("MemoryContext = %#v; want frozen Constitution only", selected)
	}

	delivery, err := client.RecordMemoryDelivery(ctx, &memoryv1.MemoryDeliveryReceipt{
		IdempotencyKey:      identity + "-delivery",
		Scope:               scope,
		RunRef:              identity + "-run",
		MemoryContextRef:    selected.GetContextRef(),
		DeliveredMemoryRefs: []string{"constitution-v1"},
	})
	if err != nil {
		t.Fatalf("record Context delivery: %v", err)
	}
	if delivery.GetReceiptRef() == "" {
		t.Fatal("Context delivery receipt ref is empty")
	}

	actRequest := &memoryv1.ObserveSourceEventRequest{
		IdempotencyKey: identity + "-act-request",
		SourceEvent: &memoryv1.SourceEvent{
			Scope: scope, SourceRef: identity + "-act", ActorKind: memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_AGENT,
			ActorRef: "agent-1", Text: "The agent gives one concise recommendation.",
		},
		EpisodeBinding: &memoryv1.EpisodeBinding{
			RunRef: identity + "-run", SourceGroupRef: identity + "-group",
			Role: memoryv1.EpisodeSourceRole_EPISODE_SOURCE_ROLE_AGENT_ACT,
		},
	}
	materialized, err := client.ObserveSourceEvent(ctx, actRequest)
	if err != nil {
		t.Fatalf("observe agent act: %v", err)
	}
	if materialized.GetEpisodeRef() == "" {
		t.Fatal("agent act did not materialize an Episode")
	}

	retry, err := client.ObserveSourceEvent(ctx, actRequest)
	if err != nil {
		t.Fatalf("retry exact request: %v", err)
	}
	if retry.GetSourceEventRef() != materialized.GetSourceEventRef() || retry.GetEpisodeRef() != materialized.GetEpisodeRef() {
		t.Fatalf("retry receipt = %#v; want %#v", retry, materialized)
	}

	outcomeRequest := &memoryv1.ReportOutcomeRequest{
		IdempotencyKey:         identity + "-outcome",
		Scope:                  scope,
		RunRef:                 identity + "-run",
		SourceGroupRef:         identity + "-group",
		Text:                   "The user explicitly asks for one clarification.",
		DeliveryReceiptRefs:    []string{delivery.GetReceiptRef()},
		RelatedSourceEventRefs: []string{situationRequest.GetSourceEvent().GetSourceRef(), actRequest.GetSourceEvent().GetSourceRef()},
		SourceRef:              identity + "-outcome-source",
		ActorKind:              memoryv1.SourceActorKind_SOURCE_ACTOR_KIND_USER,
		ActorRef:               "user-1",
	}
	outcome, err := client.ReportOutcome(ctx, outcomeRequest)
	if err != nil {
		t.Fatalf("report Outcome: %v", err)
	}
	if outcome.GetOutcomeEventRef() == "" || outcome.GetEpisodeRef() != materialized.GetEpisodeRef() {
		t.Fatalf("Outcome receipt = %#v; want Episode %s", outcome, materialized.GetEpisodeRef())
	}
	retriedOutcome, err := client.ReportOutcome(ctx, outcomeRequest)
	if err != nil {
		t.Fatalf("retry Outcome: %v", err)
	}
	if retriedOutcome.GetOutcomeEventRef() != outcome.GetOutcomeEventRef() || retriedOutcome.GetEpisodeRef() != outcome.GetEpisodeRef() {
		t.Fatalf("retry Outcome = %#v; want %#v", retriedOutcome, outcome)
	}
}
