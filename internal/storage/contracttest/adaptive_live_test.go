package contracttest

import (
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func TestLiveBaselineConflictFixturesReachImmutableIdentityValidation(t *testing.T) {
	for _, baseline := range []ledger.Constitution{{}, {MemoryRef: "role@1", Text: "known role"}} {
		for _, conflicting := range liveConstitutionConflicts(baseline) {
			event := ledger.SourceEvent{Ref: "source", Scope: ledger.Scope{Kind: ledger.ScopeKindAgent, TenantRef: "tenant", AgentRef: "agent"}, ActorKind: ledger.ActorKindUser, ActorRef: "user", Text: "source text", Constitution: conflicting}
			if err := ledger.Validate(event, ledger.EpisodeBinding{}); err != nil {
				t.Fatalf("conflict fixture rejected before immutable identity check: baseline=%#v changed=%#v err=%v", baseline, conflicting, err)
			}
			if reflect.DeepEqual(conflicting, baseline) {
				t.Fatal("conflict fixture did not change baseline")
			}
			if baseline.MemoryRef != "" && (conflicting.MemoryRef != baseline.MemoryRef) == (conflicting.Text != baseline.Text) {
				t.Fatal("known snapshot must test each field independently")
			}
		}
	}
}
