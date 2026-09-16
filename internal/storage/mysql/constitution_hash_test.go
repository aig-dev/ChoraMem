package mysql

import (
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func TestConstitutionHashesPreserveLegacyAndFreezeSnapshot(t *testing.T) {
	event := ledger.SourceEvent{Ref: "source", Scope: ledger.Scope{Kind: ledger.ScopeKindAgent, TenantRef: "tenant", AgentRef: "agent"}, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "text"}
	binding := ledger.EpisodeBinding{RunRef: "run", SourceGroupRef: "group", Role: ledger.RoleSituation}
	legacy := hashFields("observe-request.v1", "source", "agent", "tenant", "agent", "", "", "user", "user", "text", "run", "group", "situation")
	if requestHash(event, binding) != legacy {
		t.Fatal("nil snapshot changed legacy Observe receipt hash")
	}
	report := ledger.OutcomeReport{Event: event, RunRef: "run", SourceGroupRef: "group"}
	legacyOutcome := hashFields("outcome-report.v1", "agent", "tenant", "agent", "", "", "run", "group", "source", "user", "user", "text", "related-sources")
	if outcomeReportHash(report, nil, nil) != legacyOutcome {
		t.Fatal("nil snapshot changed legacy Outcome receipt hash")
	}
	field := reflect.ValueOf(&event).Elem().FieldByName("Constitution")
	if !field.IsValid() {
		t.Fatal("snapshot absent from immutable intake")
	}
	field.FieldByName("MemoryRef").SetString("v1")
	field.FieldByName("Text").SetString("role")
	first := requestHash(event, binding)
	report.Event = event
	firstOutcome := outcomeReportHash(report, nil, nil)
	if first == legacy || firstOutcome == legacyOutcome {
		t.Fatal("snapshot excluded from intake hash")
	}
	field.FieldByName("Text").SetString("changed")
	report.Event = event
	if requestHash(event, binding) == first || outcomeReportHash(report, nil, nil) == firstOutcome {
		t.Fatal("snapshot text excluded from intake hash")
	}
}

func TestConstitutionExtendedHashCannotAliasLegacyOutcomeRelatedRefs(t *testing.T) {
	event := ledger.SourceEvent{Ref: "source", Scope: ledger.Scope{Kind: ledger.ScopeKindAgent, TenantRef: "tenant", AgentRef: "agent"}, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "text"}
	report := ledger.OutcomeReport{Event: event, RunRef: "run", SourceGroupRef: "group"}
	legacy := outcomeReportHash(report, nil, []string{"constitution.v1", "v1", "role"})
	report.Event.Constitution = ledger.Constitution{MemoryRef: "v1", Text: "role"}
	if outcomeReportHash(report, nil, nil) == legacy {
		t.Fatal("extended snapshot hash aliases a legal nil-baseline outcome")
	}
}
