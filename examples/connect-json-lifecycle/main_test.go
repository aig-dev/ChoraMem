package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	memoryv1 "github.com/aig-dev/ChoraMem/gen/memory/v1"
	"github.com/aig-dev/ChoraMem/gen/memory/v1/memoryv1connect"
)

func TestRunLifecycleUsesConnectJSONInExactCausalOrder(t *testing.T) {
	var calls []string
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		calls = append(calls, request.URL.Path)
		if got := request.Header.Get("Authorization"); got != "Bearer tenant-token" {
			t.Fatalf("Authorization = %q", got)
		}
		if got := request.Header.Get("Content-Type"); got != "application/json" {
			t.Fatalf("Content-Type = %q", got)
		}
		if got := request.Header.Get("Connect-Protocol-Version"); got != "1" {
			t.Fatalf("Connect-Protocol-Version = %q", got)
		}

		writer.Header().Set("Content-Type", "application/json")
		switch request.URL.Path {
		case memoryv1connect.MemoryCoreObserveSourceEventProcedure:
			if len(calls) == 1 {
				_, _ = writer.Write([]byte(`{"sourceEventRef":"situation-event"}`))
			} else {
				_, _ = writer.Write([]byte(`{"sourceEventRef":"agent-act-event","episodeRef":"episode-1"}`))
			}
		case memoryv1connect.MemoryCoreSelectMemoryProcedure:
			var input map[string]any
			if err := json.NewDecoder(request.Body).Decode(&input); err != nil {
				t.Fatalf("decode SelectMemory: %v", err)
			}
			if !reflect.DeepEqual(input["situationSourceEventRefs"], []any{"situation-event"}) {
				t.Fatalf("situationSourceEventRefs = %#v", input["situationSourceEventRefs"])
			}
			_, _ = writer.Write([]byte(`{"contextRef":"context-1","runRef":"run-1","constitution":{"memoryRef":"constitution-v1","text":"Stay honest."},"recollections":[{"memoryRef":"recollection-1","text":"Remember the boundary.","applicationScope":"MEMORY_APPLICATION_SCOPE_SITUATION"}],"dispositions":[{"memoryRef":"disposition-1","text":"Clarify first.","applicationScope":"MEMORY_APPLICATION_SCOPE_RELATION"}]}`))
		case memoryv1connect.MemoryCoreRecordMemoryDeliveryProcedure:
			var input struct {
				DeliveredMemoryRefs []string `json:"deliveredMemoryRefs"`
			}
			if err := json.NewDecoder(request.Body).Decode(&input); err != nil {
				t.Fatalf("decode RecordMemoryDelivery: %v", err)
			}
			want := []string{"constitution-v1", "recollection-1", "disposition-1"}
			if !reflect.DeepEqual(input.DeliveredMemoryRefs, want) {
				t.Fatalf("deliveredMemoryRefs = %#v, want %#v", input.DeliveredMemoryRefs, want)
			}
			_, _ = writer.Write([]byte(`{"receiptRef":"delivery-1"}`))
		case memoryv1connect.MemoryCoreReportOutcomeProcedure:
			var input struct {
				DeliveryReceiptRefs    []string `json:"deliveryReceiptRefs"`
				RelatedSourceEventRefs []string `json:"relatedSourceEventRefs"`
			}
			if err := json.NewDecoder(request.Body).Decode(&input); err != nil {
				t.Fatalf("decode ReportOutcome: %v", err)
			}
			if !reflect.DeepEqual(input.DeliveryReceiptRefs, []string{"delivery-1"}) {
				t.Fatalf("deliveryReceiptRefs = %#v", input.DeliveryReceiptRefs)
			}
			if !reflect.DeepEqual(input.RelatedSourceEventRefs, []string{"situation-event", "agent-act-event"}) {
				t.Fatalf("relatedSourceEventRefs = %#v", input.RelatedSourceEventRefs)
			}
			_, _ = writer.Write([]byte(`{"outcomeEventRef":"outcome-1","episodeRef":"episode-1"}`))
		default:
			http.Error(writer, "unexpected path", http.StatusNotFound)
		}
	}))
	defer server.Close()

	result, err := runLifecycle(context.Background(), config{
		Endpoint:        server.URL,
		Token:           "tenant-token",
		TenantRef:       "tenant-1",
		AgentRef:        "agent-1",
		RelationshipRef: "relationship-1",
		SessionRef:      "session-1",
		RunRef:          "run-1",
		SourceGroupRef:  "group-1",
	}, server.Client())
	if err != nil {
		t.Fatalf("runLifecycle: %v", err)
	}
	if result.OutcomeEventRef != "outcome-1" || result.EpisodeRef != "episode-1" {
		t.Fatalf("result = %#v", result)
	}
	wantCalls := []string{
		memoryv1connect.MemoryCoreObserveSourceEventProcedure,
		memoryv1connect.MemoryCoreSelectMemoryProcedure,
		memoryv1connect.MemoryCoreRecordMemoryDeliveryProcedure,
		memoryv1connect.MemoryCoreObserveSourceEventProcedure,
		memoryv1connect.MemoryCoreReportOutcomeProcedure,
	}
	if !reflect.DeepEqual(calls, wantCalls) {
		t.Fatalf("calls = %#v, want %#v", calls, wantCalls)
	}
}

func TestRenderMemoryContextPreservesMeaningWithoutExposingRefs(t *testing.T) {
	text, refs, err := renderMemoryContext(&memoryv1.MemoryContext{
		Constitution: &memoryv1.Constitution{MemoryRef: "constitution-ref", Text: " baseline "},
		Recollections: []*memoryv1.Recollection{
			{MemoryRef: "self-ref", Text: " self memory ", ApplicationScope: memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SELF},
			{MemoryRef: "other-ref", Text: " other memory ", ApplicationScope: memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_OTHER},
			{MemoryRef: "situation-ref", Text: " situation memory ", ApplicationScope: memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_SITUATION},
		},
		Dispositions: []*memoryv1.Disposition{
			{MemoryRef: "relation-ref", Text: " relation tendency ", ApplicationScope: memoryv1.MemoryApplicationScope_MEMORY_APPLICATION_SCOPE_RELATION},
		},
	})
	if err != nil {
		t.Fatalf("renderMemoryContext: %v", err)
	}
	wantText := "CONSTITUTION\nbaseline\n\nRECOLLECTIONS\nSELF self memory\nOTHER other memory\nSITUATION situation memory\n\nDISPOSITIONS\nRELATION relation tendency"
	if text != wantText {
		t.Fatalf("rendered text = %q, want %q", text, wantText)
	}
	wantRefs := []string{"constitution-ref", "self-ref", "other-ref", "situation-ref", "relation-ref"}
	if !reflect.DeepEqual(refs, wantRefs) {
		t.Fatalf("refs = %#v, want %#v", refs, wantRefs)
	}
	for _, ref := range refs {
		if strings.Contains(text, ref) {
			t.Fatalf("rendered text exposed ref %q", ref)
		}
	}
}

func TestRenderMemoryContextRejectsAmbiguousOrMalformedContext(t *testing.T) {
	_, _, err := renderMemoryContext(&memoryv1.MemoryContext{
		Constitution:  &memoryv1.Constitution{MemoryRef: "same", Text: "baseline"},
		Recollections: []*memoryv1.Recollection{{MemoryRef: "same", Text: "memory"}},
	})
	if err == nil {
		t.Fatal("duplicate memory_ref was accepted")
	}

	_, _, err = renderMemoryContext(&memoryv1.MemoryContext{
		Recollections: []*memoryv1.Recollection{{Text: "memory without identity"}},
	})
	if err == nil {
		t.Fatal("nonblank memory without memory_ref was accepted")
	}

	_, _, err = renderMemoryContext(&memoryv1.MemoryContext{
		Recollections: []*memoryv1.Recollection{{MemoryRef: "memory-1", Text: "memory without application"}},
	})
	if err == nil {
		t.Fatal("nonblank scoped memory without application_scope was accepted")
	}
}
