package postgres

import (
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func TestRequestHashLengthPrefixesEveryField(t *testing.T) {
	left := ledger.SourceEvent{
		Ref:       "a",
		Scope:     ledger.Scope{Kind: "bc", TenantRef: "tenant", AgentRef: "agent"},
		ActorKind: ledger.ActorKindUser,
		ActorRef:  "user",
		Text:      "text",
	}
	right := left
	right.Ref = "ab"
	right.Scope.Kind = "c"

	leftHash := requestHash(left, ledger.EpisodeBinding{})
	rightHash := requestHash(right, ledger.EpisodeBinding{})
	if leftHash == rightHash {
		t.Fatal("requestHash is ambiguous across adjacent variable-length fields")
	}
}

func TestRequestHashCoversCompleteEventAndBinding(t *testing.T) {
	base := ledger.SourceEvent{
		Ref: "source",
		Scope: ledger.Scope{
			Kind:            ledger.ScopeKindRelationship,
			TenantRef:       "tenant",
			AgentRef:        "agent",
			RelationshipRef: "relationship",
			SessionRef:      "session",
		},
		ActorKind: ledger.ActorKindUser,
		ActorRef:  "user",
		Text:      "text",
	}
	binding := ledger.EpisodeBinding{RunRef: "run", SourceGroupRef: "group", Role: ledger.RoleSituation}
	baseHash := requestHash(base, binding)

	tests := []struct {
		name    string
		event   ledger.SourceEvent
		binding ledger.EpisodeBinding
	}{
		{name: "source ref", event: func() ledger.SourceEvent { value := base; value.Ref = "other"; return value }(), binding: binding},
		{name: "scope kind", event: func() ledger.SourceEvent { value := base; value.Scope.Kind = ledger.ScopeKindAgent; return value }(), binding: binding},
		{name: "tenant", event: func() ledger.SourceEvent { value := base; value.Scope.TenantRef = "other"; return value }(), binding: binding},
		{name: "agent", event: func() ledger.SourceEvent { value := base; value.Scope.AgentRef = "other"; return value }(), binding: binding},
		{name: "relationship", event: func() ledger.SourceEvent { value := base; value.Scope.RelationshipRef = "other"; return value }(), binding: binding},
		{name: "session", event: func() ledger.SourceEvent { value := base; value.Scope.SessionRef = "other"; return value }(), binding: binding},
		{name: "actor kind", event: func() ledger.SourceEvent { value := base; value.ActorKind = ledger.ActorKindTool; return value }(), binding: binding},
		{name: "actor ref", event: func() ledger.SourceEvent { value := base; value.ActorRef = "other"; return value }(), binding: binding},
		{name: "text", event: func() ledger.SourceEvent { value := base; value.Text = "other"; return value }(), binding: binding},
		{name: "run", event: base, binding: ledger.EpisodeBinding{RunRef: "other", SourceGroupRef: binding.SourceGroupRef, Role: binding.Role}},
		{name: "group", event: base, binding: ledger.EpisodeBinding{RunRef: binding.RunRef, SourceGroupRef: "other", Role: binding.Role}},
		{name: "role", event: base, binding: ledger.EpisodeBinding{RunRef: binding.RunRef, SourceGroupRef: binding.SourceGroupRef, Role: ledger.RoleOutcome}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := requestHash(tt.event, tt.binding); got == baseHash {
				t.Fatalf("requestHash omitted %s", tt.name)
			}
		})
	}
}
