package postgres

import (
	"strings"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func TestHashFeedbackStateIncludesCommittedBasis(t *testing.T) {
	target := &feedbackTarget{
		Owner:   ownerScope{Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1"},
		SeedRef: "seed-1", VersionRef: "seed-1@1", VersionNumber: 1, Tendency: "先确认问题",
		Episodes: map[string]struct{}{"feedback-episode": {}},
		Receipts: map[string]map[string]struct{}{
			"feedback-episode": {"delivery-1": {}},
		},
		Outcomes:        map[string]struct{}{"outcome-1": {}},
		OutcomeEpisodes: map[string]string{"outcome-1": "feedback-episode"},
		Anchors:         map[string]struct{}{"formation-episode": {}},
		BasisState:      map[string]struct{}{"episode\x00formation\x00formation-episode": {}},
	}
	before := hashFeedbackState(map[string]*feedbackTarget{target.VersionRef: target})

	target.Owner = ownerScope{Kind: ledger.ScopeKindAgent, TenantRef: "tenant-1", AgentRef: "agent-1"}
	afterOwner := hashFeedbackState(map[string]*feedbackTarget{target.VersionRef: target})
	if afterOwner == before {
		t.Fatal("feedback state hash ignored target owner")
	}
	target.Owner = ownerScope{Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1"}

	target.BasisState["episode\x00reenactment\x00feedback-episode"] = struct{}{}
	afterEpisodeBasis := hashFeedbackState(map[string]*feedbackTarget{target.VersionRef: target})
	if afterEpisodeBasis == before {
		t.Fatal("feedback state hash ignored a concurrently committed Episode Basis")
	}

	target.BasisState["outcome\x00inhibition\x00outcome-1"] = struct{}{}
	afterOutcomeBasis := hashFeedbackState(map[string]*feedbackTarget{target.VersionRef: target})
	if afterOutcomeBasis == afterEpisodeBasis {
		t.Fatal("feedback state hash ignored a concurrently committed Outcome Basis")
	}

	target.OutcomeEpisodes["outcome-1"] = "different-feedback-episode"
	afterOutcomeMapping := hashFeedbackState(map[string]*feedbackTarget{target.VersionRef: target})
	if afterOutcomeMapping == afterOutcomeBasis {
		t.Fatal("feedback state hash ignored Outcome to Episode mapping")
	}
}

func TestConsolidationWorkerRequestIncludesRevisionAnchorEvidence(t *testing.T) {
	anchor := episodeEvidence{
		Ref: "formation-episode", SessionRef: "formation-session",
		Sources: []sourceEvidence{
			{Ref: "formation-situation", Role: "situation", Text: "用户希望先澄清真正的问题"},
			{Ref: "formation-agent-act", Role: "agent_act", Text: "我会先确认目标，再给方案"},
		},
	}
	target := &feedbackTarget{
		Owner:   ownerScope{Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1"},
		SeedRef: "seed-1", VersionRef: "seed-1@1", VersionNumber: 1, Tendency: "先确认问题",
		Episodes: map[string]struct{}{"feedback-episode": {}}, Receipts: map[string]map[string]struct{}{},
		Outcomes: map[string]struct{}{}, OutcomeEpisodes: map[string]string{}, Anchors: map[string]struct{}{anchor.Ref: {}},
		AnchorEvidence: map[string]episodeEvidence{anchor.Ref: anchor}, BasisState: map[string]struct{}{},
	}

	request := consolidationWorkerRequest("feedback-job", nil, nil, nil, map[string]*feedbackTarget{target.VersionRef: target}, nil, nil, nil)
	for _, want := range []string{
		"REVISION_ANCHOR formation-episode",
		"REVISION_ANCHOR_EVIDENCE",
		"SOURCE formation-situation\n用户希望先澄清真正的问题",
		"SOURCE formation-agent-act\n我会先确认目标，再给方案",
	} {
		if !strings.Contains(request.WindowText, want) {
			t.Fatalf("Worker window omitted %q:\n%s", want, request.WindowText)
		}
	}
}

func TestConsolidationWorkerRequestExcludesOutcomeLinksAndUnattributedOutcomes(t *testing.T) {
	episode := episodeEvidence{
		Ref: "feedback-episode", SessionRef: "session-1",
		Sources: []sourceEvidence{
			{Ref: "situation-1", Role: ledger.RoleSituation, Text: "用户提出问题"},
			{Ref: "agent-act-1", Role: ledger.RoleAgentAct, Text: "Agent 给出回答"},
			{Ref: "self-outcome-source", Role: ledger.RoleOutcome, Text: "Agent 自评非常成功"},
		},
	}
	target := &feedbackTarget{
		Owner:   ownerScope{Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1"},
		SeedRef: "seed-1", VersionRef: "seed-1@1", VersionNumber: 1, Tendency: "先确认问题",
		Episodes: map[string]struct{}{episode.Ref: {}}, Receipts: map[string]map[string]struct{}{},
		Outcomes: map[string]struct{}{}, OutcomeEpisodes: map[string]string{}, Anchors: map[string]struct{}{},
		AnchorEvidence: map[string]episodeEvidence{}, BasisState: map[string]struct{}{},
	}
	outcomes := map[string]feedbackOutcome{
		"self-outcome": {
			Ref: "self-outcome", EpisodeRef: episode.Ref, Text: "Agent 自评非常成功",
			ActorKind: ledger.ActorKindAgent, ActorRef: "agent-1",
		},
	}

	request := consolidationWorkerRequest("feedback-job", []episodeEvidence{episode}, nil, outcomes, map[string]*feedbackTarget{target.VersionRef: target}, nil, nil, nil)
	if strings.Contains(request.WindowText, "Agent 自评非常成功") || strings.Contains(request.WindowText, "self-outcome-source") || strings.Contains(request.WindowText, "OUTCOME self-outcome") {
		t.Fatalf("Worker received an Outcome without eligible actor-bound attribution:\n%s", request.WindowText)
	}
	for _, want := range []string{"SOURCE situation-1\n用户提出问题", "SOURCE agent-act-1\nAgent 给出回答"} {
		if !strings.Contains(request.WindowText, want) {
			t.Fatalf("Worker omitted causal Episode source %q:\n%s", want, request.WindowText)
		}
	}
}

func TestConsolidationLockOwnersIncludesWindowAndTargetOwnersInStableOrder(t *testing.T) {
	windowOwner := ownerScope{
		Kind: ledger.ScopeKindRelationship, TenantRef: "tenant-1", AgentRef: "agent-1", RelationshipRef: "relationship-1",
	}
	agentOwner := ownerScope{Kind: ledger.ScopeKindAgent, TenantRef: "tenant-1", AgentRef: "agent-1"}
	targets := map[string]*feedbackTarget{
		"relationship-seed@1": {Owner: windowOwner},
		"agent-seed@1":        {Owner: agentOwner},
		"agent-seed@2":        {Owner: agentOwner},
	}

	got := consolidationLockOwners(windowOwner, targets)
	want := []ownerScope{agentOwner, windowOwner}
	if len(got) != len(want) {
		t.Fatalf("lock owners = %#v; want %#v", got, want)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("lock owners = %#v; want stable %#v", got, want)
		}
	}
}
