package postgres

import (
	"fmt"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/ledger"
)

func TestBoundedActiveRecollectionsKeepsRelevantOldTargetWithoutUnboundedPrompt(t *testing.T) {
	now := time.Date(2026, 8, 30, 0, 0, 0, 0, time.UTC)
	recollections := make([]activeRecollection, 0, maxActiveRecollectionTargets+1)
	recollections = append(recollections, activeRecollection{
		VersionRef: "recollection-relevant@1", Text: "用户希望回答保持简洁", CreatedAt: now.Add(-time.Hour),
	})
	for index := 0; index < maxActiveRecollectionTargets; index++ {
		recollections = append(recollections, activeRecollection{
			VersionRef: fmt.Sprintf("recollection-filler-%03d@1", index),
			Text:       "完全无关的旅行记录",
			CreatedAt:  now.Add(time.Duration(index) * time.Minute),
		})
	}
	episodes := []episodeEvidence{{Sources: []sourceEvidence{{
		Role: ledger.RoleSituation, Text: "请用简洁方式回答这次问题",
	}}}}

	bounded := boundedActiveRecollections(recollections, episodes, nil)

	if len(bounded) != maxActiveRecollectionTargets {
		t.Fatalf("bounded Recollections = %d; want %d", len(bounded), maxActiveRecollectionTargets)
	}
	found := false
	for _, recollection := range bounded {
		found = found || recollection.VersionRef == "recollection-relevant@1"
	}
	if !found {
		t.Fatal("semantically relevant old Recollection was dropped by the prompt bound")
	}
}

func TestRecollectionWindowQueryUsesSituationExperienceOnlyAndIsBounded(t *testing.T) {
	oversized := make([]byte, maxRecollectionQueryBytes+128)
	for index := range oversized {
		oversized[index] = 'x'
	}
	episodes := []episodeEvidence{{Sources: []sourceEvidence{
		{Role: ledger.RoleSituation, Text: string(oversized)},
		{Role: ledger.RoleAgentAct, Text: "must not become the direct experience query"},
	}}}

	query := recollectionWindowQuery(episodes)

	if len(query) > maxRecollectionQueryBytes {
		t.Fatalf("query bytes = %d; want <= %d", len(query), maxRecollectionQueryBytes)
	}
	if query == "" || query == "must not become the direct experience query" {
		t.Fatalf("unexpected direct experience query %q", query)
	}
}
