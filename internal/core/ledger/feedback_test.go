package ledger

import (
	"errors"
	"strings"
	"testing"
)

func TestValidateMemoryDeliveryRequiresExactStableRefs(t *testing.T) {
	valid := MemoryDelivery{
		IdempotencyKey: "delivery-key",
		Scope: Scope{
			Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1",
			RelationshipRef: "relationship-1", SessionRef: "session-1",
		},
		RunRef: "run-1", MemoryContextRef: "context-1",
		DeliveredMemoryRefs: []string{"constitution-v1", "disposition-1@1"},
	}
	if err := ValidateMemoryDelivery(valid); err != nil {
		t.Fatalf("valid delivery: %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*MemoryDelivery)
	}{
		{name: "missing key", mutate: func(value *MemoryDelivery) { value.IdempotencyKey = "" }},
		{name: "missing run", mutate: func(value *MemoryDelivery) { value.RunRef = "" }},
		{name: "missing context", mutate: func(value *MemoryDelivery) { value.MemoryContextRef = "" }},
		{name: "no memories", mutate: func(value *MemoryDelivery) { value.DeliveredMemoryRefs = nil }},
		{name: "duplicate memory", mutate: func(value *MemoryDelivery) {
			value.DeliveredMemoryRefs = []string{"disposition-1@1", "disposition-1@1"}
		}},
		{name: "unstable memory", mutate: func(value *MemoryDelivery) { value.DeliveredMemoryRefs = []string{"disposition 1"} }},
		{name: "invalid scope", mutate: func(value *MemoryDelivery) { value.Scope.RelationshipRef = "" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			value := valid
			value.DeliveredMemoryRefs = append([]string(nil), valid.DeliveredMemoryRefs...)
			test.mutate(&value)
			if err := ValidateMemoryDelivery(value); !errors.Is(err, ErrInvalidMemoryDelivery) {
				t.Fatalf("error = %v; want ErrInvalidMemoryDelivery", err)
			}
		})
	}
}

func TestValidateOutcomeReportKeepsAttributionOptionalButSourceExplicit(t *testing.T) {
	valid := OutcomeReport{
		IdempotencyKey: "outcome-key",
		Event: SourceEvent{
			Ref: "outcome-source-1",
			Scope: Scope{
				Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1",
				RelationshipRef: "relationship-1", SessionRef: "session-1",
			},
			ActorKind: ActorKindUser, ActorRef: "user-1", Text: "that was helpful",
		},
		RunRef: "run-1", SourceGroupRef: "group-1",
	}
	if err := ValidateOutcomeReport(valid); err != nil {
		t.Fatalf("valid unattributed outcome: %v", err)
	}

	invalid := valid
	invalid.Event.ActorKind = ""
	if err := ValidateOutcomeReport(invalid); !errors.Is(err, ErrInvalidOutcomeReport) {
		t.Fatalf("missing actor error = %v; want ErrInvalidOutcomeReport", err)
	}
	invalid = valid
	invalid.DeliveryReceiptRefs = []string{"delivery-1", "delivery-1"}
	if err := ValidateOutcomeReport(invalid); !errors.Is(err, ErrInvalidOutcomeReport) {
		t.Fatalf("duplicate delivery error = %v; want ErrInvalidOutcomeReport", err)
	}
	invalid = valid
	invalid.RelatedSourceEventRefs = []string{"source with spaces"}
	if err := ValidateOutcomeReport(invalid); !errors.Is(err, ErrInvalidOutcomeReport) {
		t.Fatalf("unstable related source error = %v; want ErrInvalidOutcomeReport", err)
	}
}

func TestFeedbackValidationRejectsOversizedIdempotencyAndMemoryRefs(t *testing.T) {
	tooLong := strings.Repeat("r", MaxStableRefBytes+1)
	delivery := MemoryDelivery{
		IdempotencyKey: tooLong,
		Scope: Scope{
			Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1",
			RelationshipRef: "relationship-1", SessionRef: "session-1",
		},
		RunRef: "run-1", MemoryContextRef: "context-1", DeliveredMemoryRefs: []string{"memory-1"},
	}
	if err := ValidateMemoryDelivery(delivery); !errors.Is(err, ErrInvalidMemoryDelivery) {
		t.Fatalf("oversized delivery key error = %v; want ErrInvalidMemoryDelivery", err)
	}
	delivery.IdempotencyKey = "delivery-1"
	delivery.DeliveredMemoryRefs = []string{tooLong}
	if err := ValidateMemoryDelivery(delivery); !errors.Is(err, ErrInvalidMemoryDelivery) {
		t.Fatalf("oversized memory ref error = %v; want ErrInvalidMemoryDelivery", err)
	}
}
