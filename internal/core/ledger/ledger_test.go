package ledger

import (
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestValidateRejectsPersistedReferencesBeyondTheCrossAdapterLimit(t *testing.T) {
	valid := SourceEvent{
		Ref: "source-1",
		Scope: Scope{
			Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1",
			RelationshipRef: "relationship-1", SessionRef: "session-1",
		},
		ActorKind: ActorKindUser, ActorRef: "user-1", Text: "hello",
	}
	tooLong := strings.Repeat("界", MaxStableRefBytes/len("界")+1)

	for name, mutate := range map[string]func(*SourceEvent){
		"source":       func(event *SourceEvent) { event.Ref = tooLong },
		"tenant":       func(event *SourceEvent) { event.Scope.TenantRef = tooLong },
		"agent":        func(event *SourceEvent) { event.Scope.AgentRef = tooLong },
		"relationship": func(event *SourceEvent) { event.Scope.RelationshipRef = tooLong },
		"session":      func(event *SourceEvent) { event.Scope.SessionRef = tooLong },
		"actor":        func(event *SourceEvent) { event.ActorRef = tooLong },
	} {
		t.Run(name, func(t *testing.T) {
			event := valid
			mutate(&event)
			if err := Validate(event, EpisodeBinding{}); !errors.Is(err, ErrInvalidSourceEvent) {
				t.Fatalf("Validate error = %v; want ErrInvalidSourceEvent", err)
			}
		})
	}

	for name, binding := range map[string]EpisodeBinding{
		"run":   {RunRef: tooLong, SourceGroupRef: "group-1", Role: RoleSituation},
		"group": {RunRef: "run-1", SourceGroupRef: tooLong, Role: RoleSituation},
	} {
		t.Run(name, func(t *testing.T) {
			if err := Validate(valid, binding); !errors.Is(err, ErrInvalidEpisodeBinding) {
				t.Fatalf("Validate error = %v; want ErrInvalidEpisodeBinding", err)
			}
		})
	}
}

func TestLedgerMaterializesEpisodeDeterministically(t *testing.T) {
	situation := source("source-situation")
	agentAct := agentSource("source-agent-act")

	first := New()
	if _, materialized, err := first.Observe(agentAct, binding(RoleAgentAct)); err != nil || materialized {
		t.Fatalf("first event: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	episodeA, materialized, err := first.Observe(situation, binding(RoleSituation))
	if err != nil || !materialized {
		t.Fatalf("complete first group: materialized = %v, err = %v", materialized, err)
	}

	second := New()
	if _, materialized, err := second.Observe(situation, binding(RoleSituation)); err != nil || materialized {
		t.Fatalf("first event in reverse order: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	episodeB, materialized, err := second.Observe(agentAct, binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("complete reverse-order group: materialized = %v, err = %v", materialized, err)
	}

	if episodeA.Ref != episodeB.Ref {
		t.Fatalf("episode refs differ by input order: %q != %q", episodeA.Ref, episodeB.Ref)
	}
	if !reflect.DeepEqual(episodeA.Links, episodeB.Links) {
		t.Fatalf("episode links differ by input order:\n%#v\n%#v", episodeA.Links, episodeB.Links)
	}
	wantLinks := []SourceLink{
		{SourceEventRef: situation.Ref, Role: RoleSituation},
		{SourceEventRef: agentAct.Ref, Role: RoleAgentAct},
	}
	if !reflect.DeepEqual(episodeA.Links, wantLinks) {
		t.Fatalf("links = %#v; want %#v", episodeA.Links, wantLinks)
	}
	wantBinding := binding(RoleSituation)
	if episodeA.Scope != situation.Scope || episodeA.RunRef != wantBinding.RunRef || episodeA.SourceGroupRef != wantBinding.SourceGroupRef {
		t.Fatalf("episode boundary = %#v; want scope %#v and binding %#v", episodeA, situation.Scope, wantBinding)
	}
}

func TestLedgerRequiresSituationAndAgentAct(t *testing.T) {
	tests := []struct {
		name  string
		roles []SourceRole
	}{
		{name: "situation only", roles: []SourceRole{RoleSituation}},
		{name: "agent act only", roles: []SourceRole{RoleAgentAct}},
		{name: "situation and outcome", roles: []SourceRole{RoleSituation, RoleOutcome}},
		{name: "agent act and outcome", roles: []SourceRole{RoleAgentAct, RoleOutcome}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ledger := New()
			for i, role := range tt.roles {
				event := sourceForRole(tt.name+string(rune('a'+i)), role)
				if _, materialized, err := ledger.Observe(event, binding(role)); err != nil {
					t.Fatalf("observe %q: %v", event.Ref, err)
				} else if materialized {
					t.Fatalf("incomplete roles materialized after %q", event.Ref)
				}
			}
		})
	}
}

func TestLedgerTreatsSourceEventRefAsImmutableIdempotencyBoundary(t *testing.T) {
	ledger := New()
	situation := source("source-situation")

	if _, _, err := ledger.Observe(situation, binding(RoleSituation)); err != nil {
		t.Fatalf("first observation: %v", err)
	}
	if _, materialized, err := ledger.Observe(situation, binding(RoleSituation)); err != nil || materialized {
		t.Fatalf("identical retry: materialized = %v, err = %v; want false, nil", materialized, err)
	}

	conflict := situation
	conflict.Text = "different text"
	if _, _, err := ledger.Observe(conflict, binding(RoleSituation)); !errors.Is(err, ErrSourceEventConflict) {
		t.Fatalf("conflicting source ref error = %v; want ErrSourceEventConflict", err)
	}

	episode, materialized, err := ledger.Observe(agentSource("source-agent-act"), binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("complete group: materialized = %v, err = %v", materialized, err)
	}
	if len(episode.Links) != 2 {
		t.Fatalf("links after identical retry = %d; want 2", len(episode.Links))
	}
}

func TestLedgerNamespacesStableGroupAndSourceRefByExactScope(t *testing.T) {
	tests := []struct {
		name  string
		scope Scope
	}{
		{name: "tenant", scope: Scope{Kind: ScopeKindRelationship, TenantRef: "tenant-other", AgentRef: "agent-1", RelationshipRef: "relationship-1", SessionRef: "session-1"}},
		{name: "agent", scope: Scope{Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-other", RelationshipRef: "relationship-1", SessionRef: "session-1"}},
		{name: "relationship", scope: Scope{Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-other", SessionRef: "session-1"}},
		{name: "agent scope", scope: Scope{Kind: ScopeKindAgent, TenantRef: "tenant-1", AgentRef: "agent-1", SessionRef: "session-1"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ledger := New()
			if _, _, err := ledger.Observe(source("source-situation"), binding(RoleSituation)); err != nil {
				t.Fatalf("observe first-scope situation: %v", err)
			}
			first, materialized, err := ledger.Observe(agentSource("source-agent-act"), binding(RoleAgentAct))
			if err != nil || !materialized {
				t.Fatalf("materialize first scope: materialized = %v, err = %v", materialized, err)
			}

			otherSituation := source("source-situation")
			otherSituation.Scope = tt.scope
			if _, materialized, err := ledger.Observe(otherSituation, binding(RoleSituation)); err != nil || materialized {
				t.Fatalf("observe other-scope situation: materialized = %v, err = %v; want false, nil", materialized, err)
			}
			otherAct := agentSource("source-agent-act")
			otherAct.Scope = tt.scope
			otherAct.ActorRef = tt.scope.AgentRef
			second, materialized, err := ledger.Observe(otherAct, binding(RoleAgentAct))
			if err != nil || !materialized {
				t.Fatalf("materialize other scope: materialized = %v, err = %v", materialized, err)
			}
			if first.Ref == second.Ref {
				t.Fatalf("different scopes share episode ref %q", first.Ref)
			}
			if second.Scope != tt.scope {
				t.Fatalf("second episode scope = %#v; want %#v", second.Scope, tt.scope)
			}
		})
	}
}

func TestLedgerAllowsOneSourceEventToLinkDifferentEpisodesWithDifferentRoles(t *testing.T) {
	ledger := New()
	if _, _, err := ledger.Observe(source("first-situation"), groupBinding("group-1", RoleSituation)); err != nil {
		t.Fatalf("observe first situation: %v", err)
	}
	first, materialized, err := ledger.Observe(agentSource("first-act"), groupBinding("group-1", RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("materialize first episode: materialized = %v, err = %v", materialized, err)
	}

	correction := source("shared-correction")
	firstWithOutcome, materialized, err := ledger.Observe(correction, groupBinding("group-1", RoleOutcome))
	if err != nil || !materialized {
		t.Fatalf("link shared source as outcome: materialized = %v, err = %v", materialized, err)
	}
	if first.Ref != firstWithOutcome.Ref {
		t.Fatalf("outcome link changed first episode identity: %q -> %q", first.Ref, firstWithOutcome.Ref)
	}

	if _, materialized, err := ledger.Observe(correction, groupBinding("group-2", RoleSituation)); err != nil || materialized {
		t.Fatalf("reuse same source as next situation: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	second, materialized, err := ledger.Observe(agentSource("second-act"), groupBinding("group-2", RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("materialize second episode: materialized = %v, err = %v", materialized, err)
	}
	if first.Ref == second.Ref {
		t.Fatalf("different stable groups share episode ref %q", first.Ref)
	}
	if !reflect.DeepEqual(firstWithOutcome.Links, []SourceLink{
		{SourceEventRef: "first-situation", Role: RoleSituation},
		{SourceEventRef: "first-act", Role: RoleAgentAct},
		{SourceEventRef: "shared-correction", Role: RoleOutcome},
	}) {
		t.Fatalf("first episode links = %#v", firstWithOutcome.Links)
	}
	if !reflect.DeepEqual(second.Links, []SourceLink{
		{SourceEventRef: "shared-correction", Role: RoleSituation},
		{SourceEventRef: "second-act", Role: RoleAgentAct},
	}) {
		t.Fatalf("second episode links = %#v", second.Links)
	}
}

func TestLedgerAppendsLateOutcomeWithoutChangingEpisodeIdentity(t *testing.T) {
	ledger := New()
	if _, _, err := ledger.Observe(source("source-situation"), binding(RoleSituation)); err != nil {
		t.Fatalf("observe situation: %v", err)
	}
	before, materialized, err := ledger.Observe(agentSource("source-agent-act"), binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("materialize: materialized = %v, err = %v", materialized, err)
	}

	after, materialized, err := ledger.Observe(source("source-outcome"), binding(RoleOutcome))
	if err != nil || !materialized {
		t.Fatalf("append outcome: materialized = %v, err = %v", materialized, err)
	}
	if before.Ref != after.Ref {
		t.Fatalf("late outcome changed episode identity: %q -> %q", before.Ref, after.Ref)
	}
	wantLinks := []SourceLink{
		{SourceEventRef: "source-situation", Role: RoleSituation},
		{SourceEventRef: "source-agent-act", Role: RoleAgentAct},
		{SourceEventRef: "source-outcome", Role: RoleOutcome},
	}
	if !reflect.DeepEqual(after.Links, wantLinks) {
		t.Fatalf("links after late outcome = %#v; want %#v", after.Links, wantLinks)
	}
	if len(before.Links) != 2 {
		t.Fatalf("previously returned episode was mutated: links = %#v", before.Links)
	}
}

func TestLedgerSealsSituationAndAgentActAfterMaterialization(t *testing.T) {
	ledger := New()
	situation := source("source-situation")
	if _, _, err := ledger.Observe(situation, binding(RoleSituation)); err != nil {
		t.Fatalf("observe situation: %v", err)
	}
	if _, materialized, err := ledger.Observe(agentSource("source-agent-act"), binding(RoleAgentAct)); err != nil || !materialized {
		t.Fatalf("materialize: materialized = %v, err = %v", materialized, err)
	}

	if _, materialized, err := ledger.Observe(source("late-situation"), binding(RoleSituation)); !errors.Is(err, ErrEpisodeSealed) || materialized {
		t.Fatalf("late situation: materialized = %v, err = %v; want ErrEpisodeSealed", materialized, err)
	}
	if _, materialized, err := ledger.Observe(agentSource("late-agent-act"), binding(RoleAgentAct)); !errors.Is(err, ErrEpisodeSealed) || materialized {
		t.Fatalf("late agent act: materialized = %v, err = %v; want ErrEpisodeSealed", materialized, err)
	}
	if episode, materialized, err := ledger.Observe(situation, binding(RoleSituation)); err != nil || !materialized || len(episode.Links) != 2 {
		t.Fatalf("existing link replay: episode = %#v, materialized = %v, err = %v", episode, materialized, err)
	}
}

func TestLedgerAdmitsSourceWithoutCompleteBindingAndLinksItOnRetry(t *testing.T) {
	ledger := New()
	situation := source("source-situation")
	act := agentSource("source-agent-act")

	if _, materialized, err := ledger.Observe(situation, EpisodeBinding{}); err != nil || materialized {
		t.Fatalf("admit source without binding: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	partial := binding(RoleAgentAct)
	partial.SourceGroupRef = ""
	if _, materialized, err := ledger.Observe(act, partial); err != nil || materialized {
		t.Fatalf("admit source with partial binding: materialized = %v, err = %v; want false, nil", materialized, err)
	}

	conflict := situation
	conflict.Text = "different text"
	if _, _, err := ledger.Observe(conflict, EpisodeBinding{}); !errors.Is(err, ErrSourceEventConflict) {
		t.Fatalf("unbound source was not retained immutably: %v", err)
	}

	if _, materialized, err := ledger.Observe(situation, binding(RoleSituation)); err != nil || materialized {
		t.Fatalf("link admitted situation: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	episode, materialized, err := ledger.Observe(act, binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("link admitted act and materialize: materialized = %v, err = %v", materialized, err)
	}
	if len(episode.Links) != 2 {
		t.Fatalf("linked episode has %d links; want 2", len(episode.Links))
	}
}

func TestLedgerRejectsInvalidCompleteBindingWithoutCreatingLink(t *testing.T) {
	ledger := New()
	event := source("source-with-invalid-binding")
	invalid := groupBinding("source-group-1", SourceRole("invalid"))

	if _, materialized, err := ledger.Observe(event, invalid); !errors.Is(err, ErrInvalidEpisodeBinding) || materialized {
		t.Fatalf("invalid binding: materialized = %v, err = %v; want false, ErrInvalidEpisodeBinding", materialized, err)
	}

	replacement := event
	replacement.Text = "replacement text"
	if _, materialized, err := ledger.Observe(replacement, EpisodeBinding{}); err != nil || materialized {
		t.Fatalf("invalid binding partially admitted source: replacement materialized = %v, err = %v", materialized, err)
	}
	if _, _, err := ledger.Observe(event, EpisodeBinding{}); !errors.Is(err, ErrSourceEventConflict) {
		t.Fatalf("replacement did not become immutable source: %v", err)
	}

	if _, materialized, err := ledger.Observe(replacement, binding(RoleSituation)); err != nil || materialized {
		t.Fatalf("retry source with valid binding: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	episode, materialized, err := ledger.Observe(agentSource("source-agent-act"), binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("materialize after valid retry: materialized = %v, err = %v", materialized, err)
	}
	if !reflect.DeepEqual(episode.Links, []SourceLink{
		{SourceEventRef: event.Ref, Role: RoleSituation},
		{SourceEventRef: "source-agent-act", Role: RoleAgentAct},
	}) {
		t.Fatalf("invalid binding leaked into links: %#v", episode.Links)
	}
}

func TestLedgerIsolatesSameExternalRefsAcrossSessions(t *testing.T) {
	ledger := New()
	if _, _, err := ledger.Observe(source("source-situation"), binding(RoleSituation)); err != nil {
		t.Fatalf("observe first-session situation: %v", err)
	}
	first, materialized, err := ledger.Observe(agentSource("source-agent-act"), binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("materialize first session: materialized = %v, err = %v", materialized, err)
	}

	secondSituation := source("source-situation")
	secondSituation.Scope.SessionRef = "session-2"
	if _, materialized, err := ledger.Observe(secondSituation, binding(RoleSituation)); err != nil || materialized {
		t.Fatalf("observe second-session situation: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	secondAct := agentSource("source-agent-act")
	secondAct.Scope.SessionRef = "session-2"
	second, materialized, err := ledger.Observe(secondAct, binding(RoleAgentAct))
	if err != nil || !materialized {
		t.Fatalf("materialize second session: materialized = %v, err = %v", materialized, err)
	}

	if first.Ref == second.Ref {
		t.Fatalf("sessions share episode ref %q", first.Ref)
	}
	if second.Scope.SessionRef != "session-2" {
		t.Fatalf("second episode session = %q; want session-2", second.Scope.SessionRef)
	}
}

func TestLedgerValidatesScopeKindAndRelationshipCombination(t *testing.T) {
	tests := []struct {
		name  string
		scope Scope
	}{
		{name: "missing kind", scope: Scope{TenantRef: "tenant-1", AgentRef: "agent-1"}},
		{name: "agent scope with relationship", scope: Scope{Kind: ScopeKindAgent, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1"}},
		{name: "relationship scope without relationship", scope: Scope{Kind: ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			event := source("source-invalid-scope")
			event.Scope = tt.scope
			if _, _, err := New().Observe(event, EpisodeBinding{}); !errors.Is(err, ErrInvalidSourceEvent) {
				t.Fatalf("scope validation error = %v; want ErrInvalidSourceEvent", err)
			}
		})
	}
}

func TestLedgerRequiresTrustedAgentActorForAgentActLink(t *testing.T) {
	tests := []struct {
		name  string
		event SourceEvent
	}{
		{name: "user actor", event: source("source-user")},
		{name: "different agent", event: func() SourceEvent {
			event := agentSource("source-other-agent")
			event.ActorRef = "agent-other"
			return event
		}()},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ledger := New()
			if _, materialized, err := ledger.Observe(tt.event, binding(RoleAgentAct)); !errors.Is(err, ErrInvalidEpisodeBinding) || materialized {
				t.Fatalf("invalid agent act: materialized = %v, err = %v; want false, ErrInvalidEpisodeBinding", materialized, err)
			}

			replacement := tt.event
			replacement.Text = "replacement text"
			if _, materialized, err := ledger.Observe(replacement, EpisodeBinding{}); err != nil || materialized {
				t.Fatalf("invalid actor binding partially admitted source: materialized = %v, err = %v", materialized, err)
			}
		})
	}
}

func TestLedgerRequiresKnownActorKindAndRef(t *testing.T) {
	tests := []struct {
		name      string
		actorKind ActorKind
		actorRef  string
	}{
		{name: "missing kind", actorRef: "user-1"},
		{name: "unknown kind", actorKind: ActorKind("unknown"), actorRef: "user-1"},
		{name: "missing ref", actorKind: ActorKindUser},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			event := source("source-invalid-actor")
			event.ActorKind = tt.actorKind
			event.ActorRef = tt.actorRef
			if _, _, err := New().Observe(event, EpisodeBinding{}); !errors.Is(err, ErrInvalidSourceEvent) {
				t.Fatalf("actor validation error = %v; want ErrInvalidSourceEvent", err)
			}
		})
	}
}

func TestLedgerAcceptsEveryProtocolActorKind(t *testing.T) {
	for _, kind := range []ActorKind{
		ActorKindAgent,
		ActorKindUser,
		ActorKindSystem,
		ActorKindTool,
		ActorKindExternal,
	} {
		t.Run(string(kind), func(t *testing.T) {
			event := source("source-" + string(kind))
			event.ActorKind = kind
			if _, _, err := New().Observe(event, EpisodeBinding{}); err != nil {
				t.Fatalf("actor kind %q rejected: %v", kind, err)
			}
		})
	}
}

func TestLedgerDoesNotUseOneSourceAsBothSituationAndAgentActGate(t *testing.T) {
	ledger := New()
	shared := agentSource("shared-source")
	if _, materialized, err := ledger.Observe(shared, binding(RoleSituation)); err != nil || materialized {
		t.Fatalf("link shared source as situation: materialized = %v, err = %v; want false, nil", materialized, err)
	}
	if _, materialized, err := ledger.Observe(shared, binding(RoleAgentAct)); err != nil || materialized {
		t.Fatalf("same source satisfied both role gates: materialized = %v, err = %v; want false, nil", materialized, err)
	}

	episode, materialized, err := ledger.Observe(source("distinct-situation"), binding(RoleSituation))
	if err != nil || !materialized {
		t.Fatalf("distinct situation did not complete episode: materialized = %v, err = %v", materialized, err)
	}
	if len(episode.Links) != 3 {
		t.Fatalf("episode links = %#v; want all three typed relations", episode.Links)
	}
}

func TestValidateIsThePureCausalIntakeValidationBoundary(t *testing.T) {
	event := agentSource("source-agent-act")
	if err := Validate(event, binding(RoleAgentAct)); err != nil {
		t.Fatalf("Validate valid event and binding: %v", err)
	}

	event.Text = ""
	if err := Validate(event, binding(RoleAgentAct)); !errors.Is(err, ErrInvalidSourceEvent) {
		t.Fatalf("Validate empty text error = %v; want ErrInvalidSourceEvent", err)
	}

	event.Text = strings.Repeat("x", MaxSourceTextBytes+1)
	if err := Validate(event, binding(RoleAgentAct)); !errors.Is(err, ErrInvalidSourceEvent) {
		t.Fatalf("Validate oversized text error = %v; want ErrInvalidSourceEvent", err)
	}

	event = source("source-user")
	if err := Validate(event, binding(RoleAgentAct)); !errors.Is(err, ErrInvalidEpisodeBinding) {
		t.Fatalf("Validate user agent_act error = %v; want ErrInvalidEpisodeBinding", err)
	}
}

func TestEpisodeRefIncludesEveryExactGroupBoundaryField(t *testing.T) {
	base := source("source-1").Scope
	baseRef := EpisodeRef(base, "run-1", "group-1")
	tests := []struct {
		name  string
		scope Scope
		run   string
		group string
	}{
		{name: "scope kind", scope: Scope{Kind: ScopeKindAgent, TenantRef: base.TenantRef, AgentRef: base.AgentRef, SessionRef: base.SessionRef}, run: "run-1", group: "group-1"},
		{name: "tenant", scope: Scope{Kind: base.Kind, TenantRef: "tenant-2", AgentRef: base.AgentRef, RelationshipRef: base.RelationshipRef, SessionRef: base.SessionRef}, run: "run-1", group: "group-1"},
		{name: "agent", scope: Scope{Kind: base.Kind, TenantRef: base.TenantRef, AgentRef: "agent-2", RelationshipRef: base.RelationshipRef, SessionRef: base.SessionRef}, run: "run-1", group: "group-1"},
		{name: "relationship", scope: Scope{Kind: base.Kind, TenantRef: base.TenantRef, AgentRef: base.AgentRef, RelationshipRef: "relationship-2", SessionRef: base.SessionRef}, run: "run-1", group: "group-1"},
		{name: "session", scope: Scope{Kind: base.Kind, TenantRef: base.TenantRef, AgentRef: base.AgentRef, RelationshipRef: base.RelationshipRef, SessionRef: "session-2"}, run: "run-1", group: "group-1"},
		{name: "run", scope: base, run: "run-2", group: "group-1"},
		{name: "group", scope: base, run: "run-1", group: "group-2"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := EpisodeRef(tt.scope, tt.run, tt.group); got == baseRef {
				t.Fatalf("EpisodeRef did not isolate %s: %q", tt.name, got)
			}
		})
	}
	if got := EpisodeRef(base, "run-1", "group-1"); got != baseRef {
		t.Fatalf("EpisodeRef is nondeterministic: %q != %q", got, baseRef)
	}
}

func source(ref string) SourceEvent {
	return SourceEvent{
		Ref: ref,
		Scope: Scope{
			Kind:            ScopeKindRelationship,
			TenantRef:       "tenant-1",
			AgentRef:        "agent-1",
			RelationshipRef: "relationship-1",
			SessionRef:      "session-1",
		},
		ActorKind: ActorKindUser,
		ActorRef:  "user-1",
		Text:      "text for " + ref,
	}
}

func agentSource(ref string) SourceEvent {
	event := source(ref)
	event.ActorKind = ActorKindAgent
	event.ActorRef = event.Scope.AgentRef
	return event
}

func sourceForRole(ref string, role SourceRole) SourceEvent {
	if role == RoleAgentAct {
		return agentSource(ref)
	}
	return source(ref)
}

func binding(role SourceRole) EpisodeBinding {
	return groupBinding("source-group-1", role)
}

func groupBinding(group string, role SourceRole) EpisodeBinding {
	return EpisodeBinding{RunRef: "run-1", SourceGroupRef: group, Role: role}
}
