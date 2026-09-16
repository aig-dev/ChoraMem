package postgres_test

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/internal/storage/postgres"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

func TestConsolidateWindowMemoryIndexOpensRelevantRecollectionBeyondCanonicalBound(t *testing.T) {
	for _, candidateKind := range []memoryindex.Kind{
		memoryindex.KindRecollection,
		memoryindex.KindEpisode,
	} {
		t.Run(string(candidateKind), func(t *testing.T) {
			testConsolidationMemoryIndexOpensRelevantRecollection(t, candidateKind)
		})
	}
}

func TestConsolidateWindowIndexedEpisodeCanCompleteCrossWindowDispositionFormationEvidence(t *testing.T) {
	harness := newHarness(t)
	oldSituation := harness.source("consolidation-index-cross-window-old")
	oldSituation.Scope.SessionRef = "session-consolidation-index-cross-window-old"
	oldSituation.Text = "用户之前要求先给结论，再解释细节"
	oldEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-cross-window-old", oldSituation)
	currentText := "用户再次要求先给结论，再解释细节"
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" && query.Text == currentText {
			return []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: oldEpisode}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	currentSituation := harness.source("consolidation-index-cross-window-current")
	currentSituation.Scope.SessionRef = "session-consolidation-index-cross-window-current"
	currentSituation.Text = currentText
	currentEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-cross-window-current", currentSituation)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, "面对明确请求时，我会先给结论", oldEpisode, currentEpisode,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-cross-window-form", EpisodeRefs: []string{currentEpisode},
	}, worker)
	if err != nil {
		t.Fatalf("consolidate cross-window evidence: %v", err)
	}
	if !strings.Contains(worker.request.WindowText, "RELATED_EPISODE "+oldEpisode+"\n") {
		t.Fatalf("indexed prior Episode %s missing from Worker evidence:\n%s", oldEpisode, worker.request.WindowText)
	}
	for _, ref := range []string{oldEpisode, currentEpisode} {
		if !slices.Contains(worker.request.AllowedBasisRefs, ref) {
			t.Fatalf("cross-window Episode %s missing from allowed Basis %#v", ref, worker.request.AllowedBasisRefs)
		}
	}
	if !slices.Contains(worker.request.AllowedTargetRefs, consolidation.TargetNewDisposition) {
		t.Fatalf("cross-window evidence did not open NEW_DISPOSITION in %#v", worker.request.AllowedTargetRefs)
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("cross-window formation receipt = %#v; want one Disposition", receipt)
	}
	var basisCount int
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT count(*) FROM seed_basis_links
		WHERE tenant_ref = $1 AND seed_version_ref = $2
		  AND episode_ref = ANY($3::text[]) AND role = 'formation'
	`, harness.tenant, receipt.DispositionVersionRefs[0], []string{oldEpisode, currentEpisode}).Scan(&basisCount); err != nil {
		t.Fatalf("count cross-window formation Basis: %v", err)
	}
	if basisCount != 2 {
		t.Fatalf("cross-window formation Basis rows = %d; want 2", basisCount)
	}
}

func TestConsolidateWindowBuildsFocusedFormationGroupFromCompleteCausalPairs(t *testing.T) {
	harness := newHarness(t)
	oldOneSituation := harness.source("causal-group-old-one-situation")
	oldOneSituation.Scope.SessionRef = "session-causal-group-old-one"
	oldOneSituation.Text = "When too many options compete, I freeze before choosing"
	oldOne := harness.materializeEpisodeFromSituation(t, "causal-group-old-one", oldOneSituation)
	oldOneOutcome := reportIndexedFormationOutcome(
		t, harness, "causal-group-old-one", oldOneSituation,
		"Naming one reversible option helped me choose",
		ledger.ActorKindUser,
	)

	oldTwoSituation := harness.source("causal-group-old-two-situation")
	oldTwoSituation.Scope.SessionRef = "session-causal-group-old-two"
	oldTwoSituation.Text = "I stalled again when several choices felt equally urgent"
	oldTwo := harness.materializeEpisodeFromSituation(t, "causal-group-old-two", oldTwoSituation)
	oldTwoOutcome := reportIndexedFormationOutcome(
		t, harness, "causal-group-old-two", oldTwoSituation,
		"A single reversible next choice got me moving",
		ledger.ActorKindExternal,
	)

	missingOutcomeSituation := harness.source("causal-group-missing-outcome-situation")
	missingOutcomeSituation.Scope.SessionRef = "session-causal-group-missing-outcome"
	missingOutcome := harness.materializeEpisodeFromSituation(t, "causal-group-missing-outcome", missingOutcomeSituation)

	agentOutcomeSituation := harness.source("causal-group-agent-outcome-situation")
	agentOutcomeSituation.Scope.SessionRef = "session-causal-group-agent-outcome"
	agentOutcome := harness.materializeEpisodeFromSituation(t, "causal-group-agent-outcome", agentOutcomeSituation)
	reportIndexedFormationOutcome(
		t, harness, "causal-group-agent-outcome", agentOutcomeSituation,
		"I think that response worked",
		ledger.ActorKindAgent,
	)

	distractorSituation := harness.source("causal-group-current-noise-situation")
	distractorSituation.Scope.SessionRef = "session-causal-group-current-noise"
	distractorSituation.Text = "I bought a new lamp for the desk"
	distractor := harness.materializeEpisodeFromSituation(t, "causal-group-current-noise", distractorSituation)
	distractorOutcome := reportIndexedFormationOutcome(
		t, harness, "causal-group-current-noise", distractorSituation,
		"The room is brighter now",
		ledger.ActorKindUser,
	)

	currentSituation := harness.source("causal-group-current-situation")
	currentSituation.Scope.SessionRef = "session-causal-group-current"
	currentSituation.Text = "I am frozen because five options all seem urgent"
	current := harness.materializeEpisodeFromSituation(t, "causal-group-current", currentSituation)
	currentOutcomeText := "Choosing one reversible option helped me begin"
	currentOutcome := reportIndexedFormationOutcome(
		t, harness, "causal-group-current", currentSituation,
		currentOutcomeText,
		ledger.ActorKindUser,
	)

	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text == currentSituation.Text {
			return []memoryindex.Candidate{
				{Kind: memoryindex.KindEpisode, Ref: current},
				{Kind: memoryindex.KindEpisode, Ref: oldTwo},
				{Kind: memoryindex.KindEpisode, Ref: oldOne},
				{Kind: memoryindex.KindEpisode, Ref: distractor},
			}, nil
		}
		if strings.Contains(query.Text, currentSituation.Text) &&
			strings.Contains(query.Text, "agent response for causal-group-current") &&
			strings.Contains(query.Text, currentOutcomeText) {
			return []memoryindex.Candidate{
				{Kind: memoryindex.KindEpisode, Ref: current},
				{Kind: memoryindex.KindEpisode, Ref: oldOne},
				{Kind: memoryindex.KindEpisode, Ref: distractor},
				{Kind: memoryindex.KindEpisode, Ref: oldTwo},
				{Kind: memoryindex.KindEpisode, Ref: missingOutcome},
				{Kind: memoryindex.KindEpisode, Ref: agentOutcome},
			}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition,
		consolidation.ApplicationRelation,
		consolidation.ChangeText,
		"When the user freezes among competing choices, the Agent offers one reversible next option",
		current, currentOutcome, oldOne, oldOneOutcome, oldTwo, oldTwoOutcome,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef:           "causal-group-job",
		EpisodeRefs:      []string{distractor, oldOne, current},
		OutcomeEventRefs: []string{distractorOutcome, currentOutcome},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("formation receipt = %#v; want one Disposition", receipt)
	}

	roles := make(map[string]string)
	for _, episode := range worker.request.Evidence.Episodes {
		roles[episode.EpisodeRef] = episode.FormationRole
	}
	if roles[current] != consolidation.EvidenceFormationAnchor ||
		roles[oldOne] != consolidation.EvidenceFormationCandidate ||
		roles[oldTwo] != consolidation.EvidenceFormationCandidate {
		t.Fatalf("formation roles = %#v; queries = %#v", roles, index.queries)
	}
	var sawCausalView, sawConditionView bool
	for _, query := range index.queries {
		sawConditionView = sawConditionView || query.Text == currentSituation.Text
		sawCausalView = sawCausalView || strings.Contains(query.Text, currentOutcomeText)
	}
	if !sawCausalView || !sawConditionView {
		t.Fatalf("formation queries = %#v; want causal and condition views", index.queries)
	}
	for _, excluded := range []string{distractor, missingOutcome, agentOutcome} {
		if roles[excluded] != "" {
			t.Fatalf("ineligible Episode %s received formation role %q", excluded, roles[excluded])
		}
	}
	wantBasis := []string{current, currentOutcome, oldOne, oldOneOutcome, oldTwo, oldTwoOutcome}
	if got, ok := consolidation.DispositionFormationBasis(worker.request.Evidence); !ok || !reflect.DeepEqual(got, wantBasis) {
		t.Fatalf("focused Basis = %#v, %v; want %#v, true", got, ok, wantBasis)
	}
}

func TestConsolidateWindowRejectsFormationBasisOutsideFocusedGroup(t *testing.T) {
	harness := newHarness(t)
	oldSituation := harness.source("causal-basis-old-situation")
	oldSituation.Scope.SessionRef = "session-causal-basis-old"
	old := harness.materializeEpisodeFromSituation(t, "causal-basis-old", oldSituation)
	oldOutcome := reportIndexedFormationOutcome(t, harness, "causal-basis-old", oldSituation, "helped before", ledger.ActorKindUser)
	currentSituation := harness.source("causal-basis-current-situation")
	currentSituation.Scope.SessionRef = "session-causal-basis-current"
	current := harness.materializeEpisodeFromSituation(t, "causal-basis-current", currentSituation)
	currentOutcome := reportIndexedFormationOutcome(t, harness, "causal-basis-current", currentSituation, "helped now", ledger.ActorKindUser)
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if strings.Contains(query.Text, "helped now") {
			return []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: old}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition,
		consolidation.ApplicationRelation,
		consolidation.ChangeText,
		"untrusted worker omitted the historical Outcome",
		current, currentOutcome, old,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "causal-basis-job", EpisodeRefs: []string{current}, OutcomeEventRefs: []string{currentOutcome},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("non-exact focused Basis committed: %#v (old Outcome %s)", receipt, oldOutcome)
	}
}

func reportIndexedFormationOutcome(
	t *testing.T,
	harness *testHarness,
	suffix string,
	situation ledger.SourceEvent,
	text string,
	actorKind ledger.ActorKind,
) string {
	t.Helper()
	actorRef := "user-1"
	if actorKind == ledger.ActorKindAgent {
		actorRef = situation.Scope.AgentRef
	} else if actorKind == ledger.ActorKindExternal {
		actorRef = "observer-1"
	}
	receipt, err := harness.store.ReportOutcome(context.Background(), ledger.OutcomeReport{
		IdempotencyKey: "report-" + suffix,
		Event: ledger.SourceEvent{
			Ref: "source-" + suffix + "-outcome", Scope: situation.Scope,
			ActorKind: actorKind, ActorRef: actorRef, Text: text,
		},
		RunRef: "run-" + suffix, SourceGroupRef: "group-" + suffix,
		RelatedSourceEventRefs: []string{situation.Ref, "source-" + suffix + "-agent-act"},
	})
	if err != nil {
		t.Fatalf("ReportOutcome(%s): %v", suffix, err)
	}
	if receipt.EpisodeRef == "" {
		t.Fatalf("ReportOutcome(%s) did not bind an Episode", suffix)
	}
	return receipt.OutcomeEventRef
}

func TestConsolidateWindowIndexedEpisodeCannotWriteWithoutCurrentEvidence(t *testing.T) {
	harness := newHarness(t)
	oldEpisode := harness.materializeEpisode(t, "consolidation-index-old-only-prior", "session-consolidation-index-old-only-prior")
	currentText := "当前对话触发了相似的旧经历"
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text == currentText {
			return []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: oldEpisode}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("consolidation-index-old-only-current")
	current.Scope.SessionRef = "session-consolidation-index-old-only-current"
	current.Text = currentText
	currentEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-old-only-current", current)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationRelation,
		consolidation.ChangeText, "只由旧经历生成的记忆", oldEpisode,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-old-only-write", EpisodeRefs: []string{currentEpisode},
	}, worker)
	if err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	if !strings.Contains(worker.request.WindowText, "RELATED_EPISODE "+oldEpisode+"\n") {
		t.Fatalf("test did not exercise indexed Episode evidence:\n%s", worker.request.WindowText)
	}
	if len(receipt.RecollectionVersionRefs) != 0 || len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("old-only Basis wrote memory: %#v", receipt)
	}
	var effects int
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT count(*) FROM recollection_versions
		WHERE tenant_ref = $1 AND origin_job_ref = $2
	`, harness.tenant, "consolidation-index-old-only-write").Scan(&effects); err != nil {
		t.Fatalf("count old-only effects: %v", err)
	}
	if effects != 0 {
		t.Fatalf("old-only Basis committed %d Recollection versions", effects)
	}
}

func testConsolidationMemoryIndexOpensRelevantRecollection(t *testing.T, candidateKind memoryindex.Kind) {
	t.Helper()
	harness := newHarness(t)
	oldEpisode := harness.materializeEpisode(t, "consolidation-index-old", "session-consolidation-index-old")
	formed, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-form", EpisodeRefs: []string{oldEpisode},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationRelation,
		consolidation.ChangeText, "对方希望先看到结论", oldEpisode,
	)})
	if err != nil || len(formed.RecollectionVersionRefs) != 1 {
		t.Fatalf("form indexed Recollection = (%#v, %v)", formed, err)
	}
	target := formed.RecollectionVersionRefs[0]

	currentText := "请直接给答案，不用长铺垫"
	fillerSituation := harness.source("consolidation-index-filler")
	fillerSituation.Scope.SessionRef = "session-consolidation-index-filler"
	fillerSituation.Text = currentText
	fillerEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-filler", fillerSituation)
	insertRecollectionFixtures(
		t, harness, fillerSituation.Scope, fillerEpisode, 64,
		"a-consolidation-index-filler", currentText,
	)

	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" && query.Text == currentText {
			candidateRef := target
			if candidateKind == memoryindex.KindEpisode {
				candidateRef = oldEpisode
			}
			return []memoryindex.Candidate{{Kind: candidateKind, Ref: candidateRef}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	currentSituation := harness.source("consolidation-index-current")
	currentSituation.Scope.SessionRef = "session-consolidation-index-current"
	currentSituation.Text = currentText
	currentEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-current", currentSituation)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		target, consolidation.ApplicationRelation, consolidation.ChangeKeep, "", currentEpisode,
	)}

	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-keep", EpisodeRefs: []string{currentEpisode},
	}, worker); err != nil {
		t.Fatalf("consolidate indexed Recollection: %v", err)
	}
	if !strings.Contains(worker.request.WindowText, "ELIGIBLE_RECOLLECTION "+target+"\n") {
		t.Fatalf("indexed Recollection %s missing from Worker window:\n%s", target, worker.request.WindowText)
	}
	if !slices.Contains(worker.request.AllowedTargetRefs, target) {
		t.Fatalf("indexed Recollection %s missing from allowed targets %#v", target, worker.request.AllowedTargetRefs)
	}
	var count int
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT count(*) FROM recollection_basis_links
		WHERE tenant_ref = $1 AND recollection_version_ref = $2
		  AND episode_ref = $3 AND role = 'support'
	`, harness.tenant, target, currentEpisode).Scan(&count); err != nil {
		t.Fatalf("count indexed KEEP support Basis: %v", err)
	}
	if count != 1 {
		t.Fatalf("indexed KEEP support Basis rows = %d; want 1", count)
	}
	if len(index.queries) != 1 || index.queries[0].Scope.RelationshipRef != "relationship-1" ||
		index.queries[0].Text != currentText {
		t.Fatalf("consolidation index queries = %#v; want one exact-owner Situation query", index.queries)
	}
}

func TestConsolidateWindowMemoryIndexAddsDirectAdaptationWithoutBehavioralEligibility(t *testing.T) {
	for _, candidateKind := range []memoryindex.Kind{
		memoryindex.KindDisposition,
		memoryindex.KindEpisode,
	} {
		t.Run(string(candidateKind), func(t *testing.T) {
			testConsolidationMemoryIndexAddsDispositionHint(t, candidateKind)
		})
	}
}

func testConsolidationMemoryIndexAddsDispositionHint(t *testing.T, candidateKind memoryindex.Kind) {
	t.Helper()
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "consolidation-index-disposition-one", "session-consolidation-index-disposition-one")
	second := harness.materializeEpisode(t, "consolidation-index-disposition-two", "session-consolidation-index-disposition-two")
	formed, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-disposition-form", EpisodeRefs: []string{first, second},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, "面对直接请求时，我会先给结论", first, second,
	)})
	if err != nil || len(formed.DispositionVersionRefs) != 1 {
		t.Fatalf("form indexed Disposition = (%#v, %v)", formed, err)
	}
	target := formed.DispositionVersionRefs[0]
	fillerEpisode := harness.materializeEpisode(t, "consolidation-index-disposition-filler", "session-consolidation-index-disposition-filler")
	insertDispositionFixtures(
		t, harness, harness.source("consolidation-index-disposition-scope").Scope,
		fillerEpisode, 64, "a-consolidation-index-disposition-filler", "unrelated filler",
	)

	currentText := "这次请直接回答，不要先铺垫"
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" && query.Text == currentText {
			candidateRef := target
			if candidateKind == memoryindex.KindEpisode {
				candidateRef = first
			}
			return []memoryindex.Candidate{{Kind: candidateKind, Ref: candidateRef}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	currentSituation := harness.source("consolidation-index-disposition-current")
	currentSituation.Scope.SessionRef = "session-consolidation-index-disposition-current"
	currentSituation.Text = currentText
	currentEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-disposition-current", currentSituation)
	worker := &scriptedWorker{taggedText: taggedMemoryChange(
		target, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", currentEpisode,
	)}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-disposition-hint", EpisodeRefs: []string{currentEpisode},
	}, worker)
	if err != nil {
		t.Fatalf("consolidate indexed Disposition hint: %v", err)
	}
	if !strings.Contains(worker.request.WindowText, "ELIGIBLE_ADAPTATION "+target+"\n") {
		t.Fatalf("indexed Disposition %s missing from direct candidates:\n%s", target, worker.request.WindowText)
	}
	if !slices.Contains(worker.request.AllowedTargetRefs, target) || strings.Contains(worker.request.WindowText, "ELIGIBLE_DISPOSITION "+target+"\n") {
		t.Fatalf("indexed Disposition %s must have direct-only eligibility in %#v", target, worker.request.AllowedTargetRefs)
	}
	if len(receipt.DispositionVersionRefs) != 0 {
		t.Fatalf("uncorroborated indexed Disposition wrote versions: %#v", receipt)
	}
	var reenactments int
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT count(*) FROM seed_basis_links
		WHERE tenant_ref = $1 AND seed_version_ref = $2
		  AND episode_ref = $3 AND role = 'reenactment'
	`, harness.tenant, target, currentEpisode).Scan(&reenactments); err != nil {
		t.Fatalf("count indexed Disposition reenactments: %v", err)
	}
	if reenactments != 0 {
		t.Fatalf("uncorroborated indexed Disposition wrote %d reenactments", reenactments)
	}
}

func TestConsolidateWindowMemoryIndexCannotRemoveCanonicalRecollectionLane(t *testing.T) {
	for _, test := range []struct {
		name       string
		candidates []memoryindex.Candidate
		err        error
	}{
		{name: "empty"},
		{name: "partial", candidates: []memoryindex.Candidate{
			{Kind: memoryindex.KindEpisode, Ref: "unknown-episode"},
			{Kind: memoryindex.KindRecollection, Ref: "unknown-recollection"},
		}},
		{name: "failure", err: errors.New("offline")},
	} {
		t.Run(test.name, func(t *testing.T) {
			harness := newHarness(t)
			basis := harness.materializeEpisode(t, "consolidation-index-fallback-basis-"+test.name, "session-consolidation-index-fallback-basis-"+test.name)
			formed, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
				JobRef: "consolidation-index-fallback-form-" + test.name, EpisodeRefs: []string{basis},
			}, &scriptedWorker{taggedText: taggedMemoryChange(
				consolidation.TargetNewRecollection, consolidation.ApplicationRelation,
				consolidation.ChangeText, "用户希望先看到结论", basis,
			)})
			if err != nil || len(formed.RecollectionVersionRefs) != 1 {
				t.Fatalf("form canonical Recollection = (%#v, %v)", formed, err)
			}
			target := formed.RecollectionVersionRefs[0]
			index := &fakeMemoryIndex{candidates: test.candidates, err: test.err}
			harness.installMemoryIndex(t, index)
			current := harness.source("consolidation-index-fallback-current-" + test.name)
			current.Scope.SessionRef = "session-consolidation-index-fallback-current-" + test.name
			current.Text = "用户希望先看到结论"
			currentEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-fallback-current-"+test.name, current)
			worker := &scriptedWorker{taggedText: taggedMemoryChange(
				target, consolidation.ApplicationRelation, consolidation.ChangeKeep, "", currentEpisode,
			)}

			if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
				JobRef: "consolidation-index-fallback-keep-" + test.name, EpisodeRefs: []string{currentEpisode},
			}, worker); err != nil {
				t.Fatalf("ConsolidateWindow: %v", err)
			}
			if !strings.Contains(worker.request.WindowText, "ELIGIBLE_RECOLLECTION "+target+"\n") ||
				!slices.Contains(worker.request.AllowedTargetRefs, target) {
				t.Fatalf("canonical Recollection disappeared after %s provider result: %#v\n%s", test.name, worker.request.AllowedTargetRefs, worker.request.WindowText)
			}
			if len(index.queries) != 1 || index.queries[0].Limit != 64 {
				t.Fatalf("provider queries = %#v; want one bounded query", index.queries)
			}
		})
	}
}

func TestConsolidateWindowDiscardsCrossOwnerAndWrongKindIndexRefs(t *testing.T) {
	harness := newHarness(t)
	siblingFirst := harness.materializeEpisodeInRelationship(t, "consolidation-index-sibling-one", "session-consolidation-index-sibling-one", "relationship-2")
	siblingSecond := harness.materializeEpisodeInRelationship(t, "consolidation-index-sibling-two", "session-consolidation-index-sibling-two", "relationship-2")
	siblingMemory, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-sibling-form", EpisodeRefs: []string{siblingFirst, siblingSecond},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, "只属于另一段关系的倾向", siblingFirst, siblingSecond,
	)})
	if err != nil || len(siblingMemory.DispositionVersionRefs) != 1 {
		t.Fatalf("form sibling memory = (%#v, %v)", siblingMemory, err)
	}
	siblingDisposition := siblingMemory.DispositionVersionRefs[0]
	currentText := "当前关系中的输入"
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text != currentText {
			return nil, nil
		}
		return []memoryindex.Candidate{
			{Kind: memoryindex.KindEpisode, Ref: siblingFirst},
			{Kind: memoryindex.KindDisposition, Ref: siblingDisposition},
			{Kind: memoryindex.KindRecollection, Ref: siblingDisposition},
		}, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("consolidation-index-sibling-current")
	current.Scope.SessionRef = "session-consolidation-index-sibling-current"
	current.Text = currentText
	currentEpisode := harness.materializeEpisodeFromSituation(t, "consolidation-index-sibling-current", current)
	worker := &scriptedWorker{}

	if _, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "consolidation-index-sibling-check", EpisodeRefs: []string{currentEpisode},
	}, worker); err != nil {
		t.Fatalf("ConsolidateWindow: %v", err)
	}
	for _, forbidden := range []string{siblingFirst, siblingDisposition, "只属于另一段关系的倾向"} {
		if strings.Contains(worker.request.WindowText, forbidden) ||
			slices.Contains(worker.request.AllowedBasisRefs, forbidden) ||
			slices.Contains(worker.request.AllowedTargetRefs, forbidden) {
			t.Fatalf("cross-owner or wrong-kind value %q leaked into consolidation request: %#v %#v\n%s",
				forbidden, worker.request.AllowedTargetRefs, worker.request.AllowedBasisRefs, worker.request.WindowText)
		}
	}
}

func TestSelectMemoryMemoryIndexCannotCauseCanonicalFalseNegative(t *testing.T) {
	for _, test := range []struct {
		name       string
		candidates []memoryindex.Candidate
		err        error
	}{
		{name: "empty"},
		{name: "partial", candidates: []memoryindex.Candidate{{Kind: memoryindex.KindDisposition, Ref: "unknown"}}},
		{name: "failure", err: errors.New("offline")},
	} {
		t.Run(test.name, func(t *testing.T) {
			harness := newHarness(t)
			canonical := harness.formSeed(t, "canonical-"+test.name, "canonical fallback signal", "relationship-1")
			index := &fakeMemoryIndex{candidates: test.candidates, err: test.err}
			harness.installMemoryIndex(t, index)
			current := harness.source("canonical-current-" + test.name)
			current.Scope.SessionRef = "session-canonical-" + test.name
			current.Text = "canonical fallback signal"
			harness.observe(t, "canonical-current-"+test.name, current, ledger.EpisodeBinding{})

			contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
				Scope: current.Scope, RunRef: "run-canonical-" + test.name,
				SituationSourceRefs: []string{current.Ref},
			})
			if err != nil {
				t.Fatalf("SelectMemory: %v", err)
			}
			if !containsDisposition(contextValue.Dispositions, canonical) {
				t.Fatalf("canonical Disposition %s missing from %#v", canonical, contextValue.Dispositions)
			}
			wantScopes := []memoryindex.Scope{
				{TenantRef: harness.tenant, AgentRef: "agent-1"},
				{TenantRef: harness.tenant, AgentRef: "agent-1", RelationshipRef: "relationship-1"},
			}
			if got := queryScopes(index.queries); !reflect.DeepEqual(got, wantScopes) {
				t.Fatalf("query scopes = %#v; want one call per owner lane %#v", got, wantScopes)
			}
		})
	}
}

func TestSelectMemoryDeduplicatesCanonicalAndIndexedCandidate(t *testing.T) {
	harness := newHarness(t)
	target := harness.formSeed(t, "index-deduplicate", "deduplicate semantic signal", "relationship-1")
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" {
			return []memoryindex.Candidate{{Kind: memoryindex.KindDisposition, Ref: target}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("index-deduplicate-current")
	current.Scope.SessionRef = "session-index-deduplicate-current"
	current.Text = "deduplicate semantic signal"
	harness.observe(t, "index-deduplicate-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-index-deduplicate-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	count := 0
	for _, disposition := range contextValue.Dispositions {
		if disposition.MemoryRef == target {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("canonical/index union contains %d copies of %s: %#v", count, target, contextValue.Dispositions)
	}
}

func TestSelectMemoryIndexExpandsBeyondCanonicalOwnerBound(t *testing.T) {
	harness := newHarness(t)
	scope := harness.source("index-bound-scope").Scope
	basisSituation := harness.source("index-bound-basis")
	basisSituation.Scope.SessionRef = "session-index-bound-basis"
	basisSituation.Text = "irrelevant bounded filler"
	basis := harness.materializeEpisodeFromSituation(t, "index-bound-basis", basisSituation)
	insertDispositionFixtures(t, harness, scope, basis, 40, "a-index-bound", "irrelevant bounded filler")
	target := harness.formSeed(t, "z-index-target", "semantic target beyond bound", "relationship-1")
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" {
			return []memoryindex.Candidate{{Kind: memoryindex.KindDisposition, Ref: target}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("index-bound-current")
	current.Scope.SessionRef = "session-index-bound-current"
	current.Text = "semantic target beyond bound"
	harness.observe(t, "index-bound-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-index-bound-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if !containsDisposition(contextValue.Dispositions, target) {
		t.Fatalf("indexed outside-bound Disposition %s missing from %#v", target, contextValue.Dispositions)
	}
	queryCount := len(index.queries)
	index.search = func(memoryindex.Query) ([]memoryindex.Candidate, error) { return nil, errors.New("offline") }
	frozen, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-index-bound-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("frozen SelectMemory retry: %v", err)
	}
	if !reflect.DeepEqual(frozen, contextValue) || len(index.queries) != queryCount {
		t.Fatalf("frozen retry = %#v queries=%d; want %#v queries=%d", frozen, len(index.queries), contextValue, queryCount)
	}
}

func TestSelectMemoryCapsProviderOutputEvenWhenProviderIgnoresLimit(t *testing.T) {
	harness := newHarness(t)
	scope := harness.source("index-cap-scope").Scope
	basisSituation := harness.source("index-cap-basis")
	basisSituation.Scope.SessionRef = "session-index-cap-basis"
	basisSituation.Text = "bounded filler"
	basis := harness.materializeEpisodeFromSituation(t, "index-cap-basis", basisSituation)
	insertDispositionFixtures(t, harness, scope, basis, 40, "a-index-cap", "bounded filler")
	target := harness.formSeed(t, "z-index-cap-target", "must stay beyond provider cap", "relationship-1")
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef != "relationship-1" {
			return nil, nil
		}
		candidates := make([]memoryindex.Candidate, 0, query.Limit+1)
		for index := 0; index < query.Limit; index++ {
			candidates = append(candidates, memoryindex.Candidate{
				Kind: memoryindex.KindDisposition, Ref: fmt.Sprintf("unknown-%03d", index),
			})
		}
		return append(candidates, memoryindex.Candidate{Kind: memoryindex.KindDisposition, Ref: target}), nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("index-cap-current")
	current.Scope.SessionRef = "session-index-cap-current"
	current.Text = "must stay beyond provider cap"
	harness.observe(t, "index-cap-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-index-cap-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if containsDisposition(contextValue.Dispositions, target) {
		t.Fatalf("provider candidate beyond requested limit entered context: %#v", contextValue.Dispositions)
	}
}

func TestSelectMemoryCallsProviderBeforeTakingSharedOwnerLocks(t *testing.T) {
	harness := newHarness(t)
	current := harness.source("index-lock-current")
	current.Scope.SessionRef = "session-index-lock-current"
	current.Text = "index lock signal"
	harness.observe(t, "index-lock-current", current, ledger.EpisodeBinding{})
	basis := harness.materializeEpisode(t, "index-lock-consolidation", "session-index-lock-consolidation")
	started := make(chan struct{})
	release := make(chan struct{})
	released := false
	defer func() {
		if !released {
			close(release)
		}
	}()
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text != current.Text {
			return nil, nil
		}
		select {
		case <-started:
		default:
			close(started)
		}
		<-release
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	selectDone := make(chan error, 1)
	go func() {
		_, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
			Scope: current.Scope, RunRef: "run-index-lock-current", SituationSourceRefs: []string{current.Ref},
		})
		selectDone <- err
	}()
	<-started
	consolidationDone := make(chan error, 1)
	go func() {
		_, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
			JobRef: "index-lock-job", EpisodeRefs: []string{basis},
		}, &scriptedWorker{})
		consolidationDone <- err
	}()
	select {
	case err := <-consolidationDone:
		if err != nil {
			t.Fatalf("ConsolidateWindow while provider blocked: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("consolidation blocked behind MemoryIndex call; owner lock was taken too early")
	}
	close(release)
	released = true
	if err := <-selectDone; err != nil {
		t.Fatalf("SelectMemory after provider release: %v", err)
	}
}

func TestConsolidateWindowCallsProviderBeforeTakingOwnerLocks(t *testing.T) {
	harness := newHarness(t)
	situation := harness.source("consolidation-index-lock-situation")
	situation.Scope.SessionRef = "session-consolidation-index-lock"
	situation.Text = "consolidation index lock signal"
	basis := harness.materializeEpisodeFromSituation(t, "consolidation-index-lock", situation)
	started := make(chan struct{})
	release := make(chan struct{})
	released := false
	defer func() {
		if !released {
			close(release)
		}
	}()
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Text != situation.Text {
			return nil, nil
		}
		select {
		case <-started:
		default:
			close(started)
		}
		<-release
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	indexedDone := make(chan error, 1)
	go func() {
		_, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
			JobRef: "consolidation-index-lock-blocked", EpisodeRefs: []string{basis},
		}, &scriptedWorker{})
		indexedDone <- err
	}()
	<-started

	plainStore := harness.openStore(t)
	defer plainStore.Close()
	plainDone := make(chan error, 1)
	go func() {
		_, err := plainStore.ConsolidateWindow(context.Background(), consolidation.Window{
			JobRef: "consolidation-index-lock-plain", EpisodeRefs: []string{basis},
		}, &scriptedWorker{})
		plainDone <- err
	}()
	select {
	case err := <-plainDone:
		if err != nil {
			t.Fatalf("plain consolidation while provider blocked: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("provider call held the shared owner lock")
	}
	close(release)
	released = true
	if err := <-indexedDone; err != nil {
		t.Fatalf("indexed consolidation after provider release: %v", err)
	}
}

func TestSelectMemoryEpisodeHitTraversesOnlyLiveBasisMemories(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "episode-index-first", "session-episode-index-first")
	second := harness.materializeEpisode(t, "episode-index-second", "session-episode-index-second")
	text := "episode-linked semantic signal"
	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "episode-index-form", EpisodeRefs: []string{first, second},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationRelation,
		consolidation.ChangeText, text, first,
	) + "\n" + taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, text, first, second,
	)})
	if err != nil || len(receipt.RecollectionVersionRefs) != 1 || len(receipt.DispositionVersionRefs) != 1 {
		t.Fatalf("form Episode-linked memories = (%#v, %v)", receipt, err)
	}
	fillSituation := harness.source("episode-index-fill")
	fillSituation.Scope.SessionRef = "session-episode-index-fill"
	fillSituation.Text = "unrelated filler"
	fillEpisode := harness.materializeEpisodeFromSituation(t, "episode-index-fill", fillSituation)
	insertDispositionFixtures(t, harness, fillSituation.Scope, fillEpisode, 40, "a-episode-index", "unrelated filler")
	insertRecollectionFixtures(t, harness, fillSituation.Scope, fillEpisode, 40, "a-episode-index", "unrelated filler")
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" {
			return []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: first}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("episode-index-current")
	current.Scope.SessionRef = "session-episode-index-current"
	current.Text = text
	harness.observe(t, "episode-index-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-episode-index-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if !containsRecollection(contextValue.Recollections, receipt.RecollectionVersionRefs[0]) ||
		!containsDisposition(contextValue.Dispositions, receipt.DispositionVersionRefs[0]) {
		t.Fatalf("Episode-linked context = %#v / %#v; want receipt %#v", contextValue.Recollections, contextValue.Dispositions, receipt)
	}
	for _, item := range contextValue.Recollections {
		if item.MemoryRef == first {
			t.Fatalf("Episode leaked as public Recollection: %#v", item)
		}
	}
	for _, item := range contextValue.Dispositions {
		if item.MemoryRef == first {
			t.Fatalf("Episode leaked as public Disposition: %#v", item)
		}
	}
}

func TestSelectMemoryDiscardsStaleWrongKindAndWrongOwnerIndexRefs(t *testing.T) {
	harness := newHarness(t)
	foreignHarness := newHarness(t)
	valid := harness.formSeed(t, "valid-index-ref", "valid indexed semantic signal", "relationship-1")
	stale := harness.formSeed(t, "stale-index-ref", "stale indexed semantic signal", "relationship-1")
	sibling := harness.formSeed(t, "sibling-index-ref", "sibling indexed semantic signal", "relationship-2")
	foreign := foreignHarness.formSeed(t, "foreign-index-ref", "valid indexed semantic signal", "relationship-1")
	if _, err := harness.inspectionDB.Exec(context.Background(), `
		UPDATE seed_versions SET status = 'superseded'
		WHERE tenant_ref = $1 AND seed_version_ref = $2
	`, harness.tenant, stale); err != nil {
		t.Fatalf("supersede stale indexed ref: %v", err)
	}
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef != "relationship-1" {
			return nil, nil
		}
		return []memoryindex.Candidate{
			{Kind: memoryindex.KindDisposition, Ref: valid},
			{Kind: memoryindex.KindRecollection, Ref: valid},
			{Kind: memoryindex.KindDisposition, Ref: stale},
			{Kind: memoryindex.KindDisposition, Ref: sibling},
			{Kind: memoryindex.KindDisposition, Ref: foreign},
			{Kind: memoryindex.KindDisposition, Ref: "unknown"},
		}, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("invalid-index-current")
	current.Scope.SessionRef = "session-invalid-index-current"
	current.Text = "valid indexed semantic signal"
	harness.observe(t, "invalid-index-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-invalid-index-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if !reflect.DeepEqual(contextValue.Dispositions, []selection.Disposition{{
		MemoryRef: valid, Text: "valid indexed semantic signal", Application: selection.ApplicationScopeRelation,
	}}) {
		t.Fatalf("rehydrated Dispositions = %#v", contextValue.Dispositions)
	}
	if len(contextValue.Recollections) != 0 {
		t.Fatalf("wrong-kind ref became Recollection: %#v", contextValue.Recollections)
	}
}

func TestSelectMemoryIndexedRecollectionUsesCanonicalApplication(t *testing.T) {
	harness := newHarness(t)
	basis := harness.materializeEpisode(t, "index-application-basis", "session-index-application-basis")
	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "index-application-form", EpisodeRefs: []string{basis},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationOther,
		consolidation.ChangeText, "canonical other-facing recollection", basis,
	)})
	if err != nil || len(receipt.RecollectionVersionRefs) != 1 {
		t.Fatalf("form indexed Recollection = (%#v, %v)", receipt, err)
	}
	target := receipt.RecollectionVersionRefs[0]
	fillSituation := harness.source("index-application-fill")
	fillSituation.Scope.SessionRef = "session-index-application-fill"
	fillSituation.Text = "unrelated application filler"
	fillEpisode := harness.materializeEpisodeFromSituation(t, "index-application-fill", fillSituation)
	insertRecollectionFixtures(t, harness, fillSituation.Scope, fillEpisode, 40, "a-index-application", "unrelated application filler")
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" {
			return []memoryindex.Candidate{{Kind: memoryindex.KindRecollection, Ref: target}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("index-application-current")
	current.Scope.SessionRef = "session-index-application-current"
	current.Text = "canonical other-facing recollection"
	harness.observe(t, "index-application-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-index-application-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	var indexed *selection.Recollection
	for index := range contextValue.Recollections {
		if contextValue.Recollections[index].MemoryRef == target {
			indexed = &contextValue.Recollections[index]
			break
		}
	}
	if indexed == nil || indexed.Text != "canonical other-facing recollection" || indexed.Application != selection.ApplicationScopeOther {
		t.Fatalf("indexed Recollection = %#v; want target %s with canonical application", contextValue.Recollections, target)
	}
}

func TestSelectMemoryPreservesDirectIndexMatchWithoutLexicalOverlap(t *testing.T) {
	harness := newHarness(t)
	basis := harness.materializeEpisode(t, "index-semantic-basis", "session-index-semantic-basis")
	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "index-semantic-form", EpisodeRefs: []string{basis},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationRelation,
		consolidation.ChangeText, "stick-shift cars", basis,
	)})
	if err != nil || len(receipt.RecollectionVersionRefs) != 1 {
		t.Fatalf("form indexed Recollection = (%#v, %v)", receipt, err)
	}
	target := receipt.RecollectionVersionRefs[0]
	index := &fakeMemoryIndex{search: func(query memoryindex.Query) ([]memoryindex.Candidate, error) {
		if query.Scope.RelationshipRef == "relationship-1" {
			return []memoryindex.Candidate{{Kind: memoryindex.KindRecollection, Ref: target}}, nil
		}
		return nil, nil
	}}
	harness.installMemoryIndex(t, index)
	current := harness.source("index-semantic-current")
	current.Scope.SessionRef = "session-index-semantic-current"
	current.Text = "mountain vehicle"
	harness.observe(t, "index-semantic-current", current, ledger.EpisodeBinding{})

	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: current.Scope, RunRef: "run-index-semantic-current", SituationSourceRefs: []string{current.Ref},
	})
	if err != nil {
		t.Fatalf("SelectMemory: %v", err)
	}
	if !containsRecollection(contextValue.Recollections, target) {
		t.Fatalf("direct indexed Recollection %s missing from %#v", target, contextValue.Recollections)
	}
}

type fakeMemoryIndex struct {
	mu         sync.Mutex
	candidates []memoryindex.Candidate
	err        error
	search     func(memoryindex.Query) ([]memoryindex.Candidate, error)
	queries    []memoryindex.Query
}

func (index *fakeMemoryIndex) Search(_ context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	index.mu.Lock()
	index.queries = append(index.queries, query)
	index.mu.Unlock()
	if index.search != nil {
		return index.search(query)
	}
	return append([]memoryindex.Candidate(nil), index.candidates...), index.err
}

func (*fakeMemoryIndex) Upsert(context.Context, []memoryindex.Document) error { return nil }
func (*fakeMemoryIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (*fakeMemoryIndex) Reset(context.Context) error { return nil }

func (harness *testHarness) installMemoryIndex(t *testing.T, index memoryindex.Index) {
	t.Helper()
	harness.store.Close()
	store, err := postgres.New(context.Background(), harness.databaseURL, postgres.WithMemoryIndex(index))
	if err != nil {
		t.Fatalf("postgres.New with MemoryIndex: %v", err)
	}
	harness.store = store
}

func queryScopes(queries []memoryindex.Query) []memoryindex.Scope {
	result := make([]memoryindex.Scope, 0, len(queries))
	for _, query := range queries {
		result = append(result, query.Scope)
	}
	return result
}

func containsRecollection(recollections []selection.Recollection, ref string) bool {
	for _, recollection := range recollections {
		if recollection.MemoryRef == ref {
			return true
		}
	}
	return false
}

func insertRecollectionFixtures(t *testing.T, harness *testHarness, scope ledger.Scope, episodeRef string, count int, prefix, text string) {
	t.Helper()
	for index := 0; index < count; index++ {
		ref := fmt.Sprintf("%s-%03d-%s", prefix, index, harness.tenant)
		ctx := context.Background()
		tx, err := harness.inspectionDB.Begin(ctx)
		if err != nil {
			t.Fatalf("begin bounded Recollection %d: %v", index, err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO recollections (tenant_ref, scope_kind, agent_ref, relationship_ref, recollection_ref)
			VALUES ($1, $2, $3, $4, $5)
		`, harness.tenant, scope.Kind, scope.AgentRef, scope.RelationshipRef, ref); err != nil {
			_ = tx.Rollback(ctx)
			t.Fatalf("insert bounded Recollection %d: %v", index, err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO recollection_versions (
				tenant_ref, recollection_ref, recollection_version_ref, version_number,
				text, application_scope, status, origin_job_ref
			) VALUES ($1, $2, $3, 1, $4, 'relation', 'active', 'index-bound-fixture')
		`, harness.tenant, ref, ref+"@1", text); err != nil {
			_ = tx.Rollback(ctx)
			t.Fatalf("insert bounded RecollectionVersion %d: %v", index, err)
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO recollection_basis_links (tenant_ref, recollection_version_ref, episode_ref, role)
			VALUES ($1, $2, $3, 'formation')
		`, harness.tenant, ref+"@1", episodeRef); err != nil {
			_ = tx.Rollback(ctx)
			t.Fatalf("insert bounded Recollection %d: %v", index, err)
		}
		if err := tx.Commit(ctx); err != nil {
			t.Fatalf("commit bounded Recollection %d: %v", index, err)
		}
	}
}
