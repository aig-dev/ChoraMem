package selection

import (
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func TestMemoryContextSeparatesConstitutionRecollectionsAndDispositions(t *testing.T) {
	scope := ledger.Scope{
		Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1",
		RelationshipRef: "relationship-1", SessionRef: "session-1",
	}
	contextValue := MemoryContext{
		Ref: "context-1", RunRef: "run-1", Scope: scope,
		Constitution: Constitution{MemoryRef: "constitution-v1", Text: "stay curious"},
		Recollections: []Recollection{
			{MemoryRef: "recollection-1@1", Text: "self memory", Application: ApplicationScopeSelf},
			{MemoryRef: "recollection-2@1", Text: "other memory", Application: ApplicationScopeOther},
			{MemoryRef: "recollection-3@1", Text: "relationship memory", Application: ApplicationScopeRelation},
			{MemoryRef: "recollection-4@1", Text: "situation memory", Application: ApplicationScopeSituation},
		},
		Dispositions: []Disposition{
			{MemoryRef: "disposition-1@1", Text: "self tendency", Application: ApplicationScopeSelf},
			{MemoryRef: "disposition-2@1", Text: "relationship tendency", Application: ApplicationScopeRelation},
		},
	}

	if !reflect.DeepEqual(contextValue.Scope, scope) {
		t.Fatalf("MemoryContext scope = %#v; want %#v", contextValue.Scope, scope)
	}
	if got := []ApplicationScope{
		contextValue.Recollections[0].Application,
		contextValue.Recollections[1].Application,
		contextValue.Recollections[2].Application,
		contextValue.Recollections[3].Application,
	}; !reflect.DeepEqual(got, []ApplicationScope{
		ApplicationScopeSelf, ApplicationScopeOther, ApplicationScopeRelation, ApplicationScopeSituation,
	}) {
		t.Fatalf("Recollection applications = %#v", got)
	}
	if got := []ApplicationScope{
		contextValue.Dispositions[0].Application, contextValue.Dispositions[1].Application,
	}; !reflect.DeepEqual(got, []ApplicationScope{ApplicationScopeSelf, ApplicationScopeRelation}) {
		t.Fatalf("Disposition applications = %#v", got)
	}
}
