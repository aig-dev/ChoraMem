package postgres_test

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/core/selection"
	"github.com/aig-dev/ChoraMem/memoryindex"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestMigrateInstallsRecollectionTablesAndActiveVersionConstraint(t *testing.T) {
	harness := newHarness(t)

	for _, table := range []string{"recollections", "recollection_versions", "recollection_basis_links"} {
		var exists bool
		if err := harness.inspectionDB.QueryRow(context.Background(), `
			SELECT to_regclass('public.' || $1) IS NOT NULL
		`, table).Scan(&exists); err != nil {
			t.Fatalf("inspect %s migration: %v", table, err)
		}
		if !exists {
			t.Fatalf("migration 007 did not install %s", table)
		}
	}

	recollectionRef := "migration-constraint-" + harness.tenant
	if _, err := harness.inspectionDB.Exec(context.Background(), `
		INSERT INTO recollections (
			tenant_ref, scope_kind, agent_ref, relationship_ref, recollection_ref
		) VALUES ($1, 'agent', 'agent-1', '', $2)
	`, harness.tenant, recollectionRef); err != nil {
		t.Fatalf("insert migrated Recollection: %v", err)
	}
	if _, err := harness.inspectionDB.Exec(context.Background(), `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		) VALUES ($1, $2, $3, 1, 'first', 'self', 'active', 'migration-job')
	`, harness.tenant, recollectionRef, recollectionRef+"@1"); err != nil {
		t.Fatalf("insert first active RecollectionVersion: %v", err)
	}
	_, err := harness.inspectionDB.Exec(context.Background(), `
		INSERT INTO recollection_versions (
			tenant_ref, recollection_ref, recollection_version_ref, version_number,
			text, application_scope, status, origin_job_ref
		) VALUES ($1, $2, $3, 2, 'second', 'self', 'active', 'migration-job-2')
	`, harness.tenant, recollectionRef, recollectionRef+"@2")
	var postgresErr *pgconn.PgError
	if !errors.As(err, &postgresErr) || postgresErr.Code != "23505" {
		t.Fatalf("second active version error = %v; want PostgreSQL unique violation", err)
	}
}

func TestConsolidateWindowFormsKeepsAndRevisesRecollectionFromSingleEpisodes(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "recollection-form", "session-recollection-form")
	formWorker := &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection,
		consolidation.ApplicationSituation,
		consolidation.ChangeText,
		"我把那次追问记成一次共同澄清",
		first,
	)}
	formWindow := consolidation.Window{JobRef: "recollection-form-job", EpisodeRefs: []string{first}}

	formed, err := harness.store.ConsolidateWindow(context.Background(), formWindow, formWorker)
	if err != nil {
		t.Fatalf("FORM Recollection: %v", err)
	}
	if len(formed.RecollectionVersionRefs) != 1 || len(formed.DispositionVersionRefs) != 0 {
		t.Fatalf("FORM receipt = %#v; want one RecollectionVersion", formed)
	}
	versionOne := formed.RecollectionVersionRefs[0]
	if operations := projectionOperationsForKind(t, harness, memoryindex.KindRecollection); len(operations) != 1 ||
		operations[0].action != memoryindex.ActionUpsert || operations[0].ref != versionOne || operations[0].sequence != 1 {
		t.Fatalf("Recollection FORM projection operations = %#v", operations)
	}
	if !reflect.DeepEqual(formWorker.request.AllowedTargetRefs, []string{consolidation.TargetNewRecollection, consolidation.TargetNewDisposition}) {
		t.Fatalf("single user Episode targets = %#v; want Recollection and direct Seed formation", formWorker.request.AllowedTargetRefs)
	}
	for _, want := range []string{
		"ELIGIBLE_NEW_RECOLLECTION " + consolidation.TargetNewRecollection,
		"APPLICATIONS SELF OTHER RELATION SITUATION",
	} {
		if !strings.Contains(formWorker.request.WindowText, want) {
			t.Fatalf("Recollection Worker request omitted %q:\n%s", want, formWorker.request.WindowText)
		}
	}

	retryWorker := &scriptedWorker{err: errors.New("must not be called for frozen retry")}
	retried, err := harness.store.ConsolidateWindow(context.Background(), formWindow, retryWorker)
	if err != nil || !reflect.DeepEqual(retried, formed) || retryWorker.calls != 0 {
		t.Fatalf("FORM retry = (%#v, %v, calls=%d); want frozen split receipt", retried, err, retryWorker.calls)
	}
	if got := len(projectionOperationsForKind(t, harness, memoryindex.KindRecollection)); got != 1 {
		t.Fatalf("frozen FORM replay projection operations = %d; want 1", got)
	}

	second := harness.materializeEpisode(t, "recollection-keep", "session-recollection-keep")
	keepWorker := &scriptedWorker{taggedText: taggedMemoryChange(
		versionOne,
		consolidation.ApplicationSituation,
		consolidation.ChangeKeep,
		"",
		second,
	)}
	kept, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-keep-job", EpisodeRefs: []string{second},
	}, keepWorker)
	if err != nil {
		t.Fatalf("KEEP Recollection: %v", err)
	}
	if len(kept.RecollectionVersionRefs) != 0 || len(kept.DispositionVersionRefs) != 0 {
		t.Fatalf("KEEP receipt created a version: %#v", kept)
	}
	for _, want := range []string{
		"ELIGIBLE_RECOLLECTION " + versionOne,
		"APPLICATION SITUATION",
		"TEXT 我把那次追问记成一次共同澄清",
	} {
		if !strings.Contains(keepWorker.request.WindowText, want) {
			t.Fatalf("existing Recollection target omitted %q:\n%s", want, keepWorker.request.WindowText)
		}
	}
	if count := harness.count(t, "recollection_versions", "recollection_version_ref = $2", versionOne); count != 1 {
		t.Fatalf("KEEP version rows = %d; want one unchanged version", count)
	}
	if count := harness.count(t, "recollection_basis_links", "recollection_version_ref = $2 AND role = 'support'", versionOne); count != 1 {
		t.Fatalf("KEEP support Basis rows = %d; want one", count)
	}
	if got := len(projectionOperationsForKind(t, harness, memoryindex.KindRecollection)); got != 1 {
		t.Fatalf("KEEP projection operations = %d; want unchanged 1", got)
	}

	duplicateKeep := &scriptedWorker{taggedText: keepWorker.taggedText}
	duplicateKeepReceipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-keep-duplicate-job", EpisodeRefs: []string{second},
	}, duplicateKeep)
	if err != nil || len(duplicateKeepReceipt.RecollectionVersionRefs) != 0 {
		t.Fatalf("duplicate KEEP = (%#v, %v); want semantic no-op", duplicateKeepReceipt, err)
	}
	if count := harness.count(t, "recollection_basis_links", "recollection_version_ref = $2 AND role = 'support'", versionOne); count != 1 {
		t.Fatalf("duplicate KEEP support Basis rows = %d; want one", count)
	}

	third := harness.materializeEpisode(t, "recollection-revise", "session-recollection-revise")
	reviseWorker := &scriptedWorker{taggedText: taggedMemoryChange(
		versionOne,
		consolidation.ApplicationSituation,
		consolidation.ChangeText,
		"我现在把那次追问理解为善意纠偏",
		third,
	)}
	revised, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-revise-job", EpisodeRefs: []string{third},
	}, reviseWorker)
	if err != nil {
		t.Fatalf("REVISE Recollection: %v", err)
	}
	if len(revised.RecollectionVersionRefs) != 1 || revised.RecollectionVersionRefs[0] == versionOne {
		t.Fatalf("REVISE receipt = %#v; want one next version", revised)
	}
	versionTwo := revised.RecollectionVersionRefs[0]
	if operations := projectionOperationsForKind(t, harness, memoryindex.KindRecollection); len(operations) != 3 ||
		operations[0].stream != operations[1].stream || operations[1].stream != operations[2].stream ||
		operations[1].action != memoryindex.ActionDelete || operations[1].ref != versionOne || operations[1].sequence != 2 ||
		operations[2].action != memoryindex.ActionUpsert || operations[2].ref != versionTwo || operations[2].sequence != 3 {
		t.Fatalf("Recollection revision projection operations = %#v", operations)
	}
	var oldStatus, newStatus, newText string
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT old.status, current.status, current.text
		FROM recollection_versions AS old
		JOIN recollection_versions AS current
		  ON current.tenant_ref = old.tenant_ref
		 AND current.recollection_ref = old.recollection_ref
		WHERE old.tenant_ref = $1
		  AND old.recollection_version_ref = $2
		  AND current.recollection_version_ref = $3
	`, harness.tenant, versionOne, versionTwo).Scan(&oldStatus, &newStatus, &newText); err != nil {
		t.Fatalf("load revised Recollection versions: %v", err)
	}
	if oldStatus != "superseded" || newStatus != "active" || newText != "我现在把那次追问理解为善意纠偏" {
		t.Fatalf("revised state = old:%s new:%s text:%q", oldStatus, newStatus, newText)
	}
	if count := harness.count(t, "recollection_versions", "recollection_ref = (SELECT recollection_ref FROM recollection_versions WHERE tenant_ref = $1 AND recollection_version_ref = $2)", versionOne); count != 2 {
		t.Fatalf("Recollection lineage version rows = %d; want old + new", count)
	}
	if count := harness.count(t, "recollection_versions", "recollection_ref = (SELECT recollection_ref FROM recollection_versions WHERE tenant_ref = $1 AND recollection_version_ref = $2) AND status = 'active'", versionOne); count != 1 {
		t.Fatalf("active Recollection versions = %d; want exactly one", count)
	}
}

func TestConsolidateWindowNoOpsExactDuplicateRecollectionFormation(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "recollection-duplicate-one", "session-recollection-duplicate-one")
	second := harness.materializeEpisode(t, "recollection-duplicate-two", "session-recollection-duplicate-two")
	text := "我把重复文本记成同一个理解"

	firstReceipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-duplicate-job-one", EpisodeRefs: []string{first},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationSelf, consolidation.ChangeText, text, first,
	)})
	if err != nil || len(firstReceipt.RecollectionVersionRefs) != 1 {
		t.Fatalf("first exact Recollection FORM = (%#v, %v)", firstReceipt, err)
	}
	secondReceipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "recollection-duplicate-job-two", EpisodeRefs: []string{second},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewRecollection, consolidation.ApplicationSelf, consolidation.ChangeText, text, second,
	)})
	if err != nil || len(secondReceipt.RecollectionVersionRefs) != 0 {
		t.Fatalf("duplicate Recollection FORM = (%#v, %v); want no-op", secondReceipt, err)
	}
	if count := harness.count(t, "recollections", "TRUE", nil); count != 1 {
		t.Fatalf("exact duplicate Recollections = %d; want one", count)
	}
}

func TestConsolidateWindowCommitsMultipleNewBlocksOfEachKind(t *testing.T) {
	harness := newHarness(t)
	first := harness.materializeEpisode(t, "multiple-new-one", "session-multiple-new-one")
	second := harness.materializeEpisode(t, "multiple-new-two", "session-multiple-new-two")
	worker := &scriptedWorker{taggedText: strings.Join([]string{
		taggedMemoryChange(
			consolidation.TargetNewRecollection, consolidation.ApplicationSelf,
			consolidation.ChangeText, "我记得第一次澄清改变了讨论方向", first,
		),
		taggedMemoryChange(
			consolidation.TargetNewRecollection, consolidation.ApplicationOther,
			consolidation.ChangeText, "我也记得第二次回应补全了边界", second,
		),
		taggedMemoryChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "相似分歧中先确认共同目标", first, second,
		),
		taggedMemoryChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "相似分歧中先复述约束", first, second,
		),
	}, "\n")}

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "multiple-new-blocks-job", EpisodeRefs: []string{first, second},
	}, worker)
	if err != nil {
		t.Fatalf("multiple NEW blocks: %v", err)
	}
	if len(receipt.RecollectionVersionRefs) != 2 || len(receipt.DispositionVersionRefs) != 2 {
		t.Fatalf("multiple NEW receipt = %#v; want two versions of each kind", receipt)
	}
	if count := harness.count(t, "recollections", "TRUE", nil); count != 2 {
		t.Fatalf("multiple NEW Recollections = %d; want two", count)
	}
	if count := harness.count(t, "disposition_seeds", "TRUE", nil); count != 2 {
		t.Fatalf("multiple NEW Dispositions = %d; want two", count)
	}
}

func TestRelationshipRunRevisesAgentOwnedDispositionWithExactOutcomeChain(t *testing.T) {
	harness := newHarness(t)
	agentScope := harness.source("agent-revision-scope").Scope
	agentScope.Kind = ledger.ScopeKindAgent
	agentScope.RelationshipRef = ""
	oldText := "先确认真正的问题"
	newText := "先确认目标，再复述当前约束"
	agentVersion := harness.formSeedInScope(t, "agent-owned-revision", oldText, agentScope)

	situation := harness.source("agent-owned-revision-situation")
	situation.Scope.SessionRef = "session-agent-owned-revision"
	situation.Text = oldText
	run := activatedRun{
		scope: situation.Scope, runRef: "run-agent-owned-revision", groupRef: "group-agent-owned-revision",
		situationRef: situation.Ref, seedVersionRef: agentVersion,
	}
	harness.observe(t, "agent-owned-revision-situation", situation, ledger.EpisodeBinding{
		RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleSituation,
	})
	contextValue, err := harness.store.SelectMemory(context.Background(), selection.SelectRequest{
		Scope: run.scope, RunRef: run.runRef, SituationSourceRefs: []string{run.situationRef},
	})
	if err != nil {
		t.Fatalf("assemble relationship run with agent Disposition: %v", err)
	}
	run.context = contextValue
	delivery := harness.recordSeedDelivery(t, "agent-owned-revision-delivery", run)
	episode := harness.materializeActivatedRun(t, "agent-owned-revision", run, oldText)
	outcome := harness.reportRunOutcome(t, "agent-owned-revision", run, episode, delivery.Ref, newText)

	receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "agent-owned-revision-job", EpisodeRefs: []string{episode},
		OutcomeEventRefs: []string{outcome.OutcomeEventRef},
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		agentVersion, consolidation.ApplicationSelf, consolidation.ChangeText, newText,
		episode, outcome.OutcomeEventRef,
	)})
	if err != nil {
		t.Fatalf("revise agent-owned Disposition from relationship run: %v", err)
	}
	if len(receipt.DispositionVersionRefs) != 1 || receipt.DispositionVersionRefs[0] == agentVersion {
		t.Fatalf("agent-owned revision receipt = %#v; want one next DispositionVersion", receipt)
	}
	newVersion := receipt.DispositionVersionRefs[0]
	var scopeKind ledger.ScopeKind
	var relationshipRef, status string
	if err := harness.inspectionDB.QueryRow(context.Background(), `
		SELECT seed.scope_kind, seed.relationship_ref, version.status
		FROM disposition_seeds AS seed
		JOIN seed_versions AS version
		  ON version.tenant_ref = seed.tenant_ref AND version.seed_ref = seed.seed_ref
		WHERE seed.tenant_ref = $1 AND version.seed_version_ref = $2
	`, harness.tenant, newVersion).Scan(&scopeKind, &relationshipRef, &status); err != nil {
		t.Fatalf("load revised agent-owned Disposition: %v", err)
	}
	if scopeKind != ledger.ScopeKindAgent || relationshipRef != "" || status != "active" {
		t.Fatalf("revised target owner = (%s, %q, %s); want agent SELF active", scopeKind, relationshipRef, status)
	}
}

func TestDispositionInhibitAndTextRequireExactNonAgentOutcome(t *testing.T) {
	harness := newHarness(t)
	run := harness.prepareActivatedRun(t, "disposition-outcome-gate", "先确认真正的问题")
	harness.recordSeedDelivery(t, "disposition-outcome-gate-delivery", run)
	episode := harness.materializeActivatedRun(t, "disposition-outcome-gate", run, "先确认真正的问题")

	tests := []struct {
		name      string
		operation string
		text      string
	}{
		{name: "inhibit", operation: consolidation.ChangeInhibit},
		{name: "text", operation: consolidation.ChangeText, text: "先确认问题并复述约束"},
		{name: "keep", operation: consolidation.ChangeKeep},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
				JobRef: "disposition-outcome-gate-" + test.name, EpisodeRefs: []string{episode},
			}, &scriptedWorker{taggedText: taggedMemoryChange(
				run.seedVersionRef, consolidation.ApplicationRelation, test.operation, test.text, episode,
			)})
			if err != nil {
				t.Fatalf("%s without Outcome: %v", test.operation, err)
			}
			if len(receipt.DispositionVersionRefs) != 0 || len(receipt.RecollectionVersionRefs) != 0 {
				t.Fatalf("%s without Outcome wrote %#v", test.operation, receipt)
			}
		})
	}
	if count := harness.count(t, "seed_basis_links", "seed_version_ref = $2 AND role = 'inhibition'", run.seedVersionRef); count != 0 {
		t.Fatalf("Outcome-less INHIBIT Basis rows = %d; want zero", count)
	}
	if count := harness.count(t, "seed_versions", "seed_ref = (SELECT seed_ref FROM seed_versions WHERE tenant_ref = $1 AND seed_version_ref = $2)", run.seedVersionRef); count != 1 {
		t.Fatalf("Outcome-less TEXT created %d Disposition versions; want one original", count)
	}
}

func TestDispositionRejectsCrossRunEpisodeOutcomeSplicing(t *testing.T) {
	for _, test := range []struct {
		name      string
		operation string
		text      string
		basisRole string
	}{
		{name: "inhibit", operation: consolidation.ChangeInhibit, basisRole: "inhibition"},
		{name: "text", operation: consolidation.ChangeText, text: "先确认问题，再复述约束", basisRole: "revision"},
	} {
		t.Run(test.name, func(t *testing.T) {
			harness := newHarness(t)
			oldText := "先确认真正的问题"
			firstRun := harness.prepareActivatedRun(t, "cross-run-first-"+test.name, oldText)
			harness.recordSeedDelivery(t, "cross-run-first-delivery-"+test.name, firstRun)
			firstEpisode := harness.materializeActivatedRun(t, "cross-run-first-"+test.name, firstRun, oldText)

			secondRun := prepareAutomaticRun(
				t, harness, "cross-run-second-"+test.name, firstRun.seedVersionRef, oldText,
			)
			secondDelivery := harness.recordSeedDelivery(t, "cross-run-second-delivery-"+test.name, secondRun)
			secondEpisode := harness.materializeActivatedRun(t, "cross-run-second-"+test.name, secondRun, oldText)
			secondOutcome := harness.reportRunOutcome(
				t, "cross-run-second-"+test.name, secondRun, secondEpisode, secondDelivery.Ref,
				"第二次 run 的外部结果",
			)

			worker := &scriptedWorker{taggedText: strings.Join([]string{
				taggedMemoryChange(
					firstRun.seedVersionRef, consolidation.ApplicationRelation,
					test.operation, test.text, firstEpisode, secondOutcome.OutcomeEventRef,
				),
				taggedMemoryChange(
					consolidation.TargetNewRecollection, consolidation.ApplicationSelf,
					consolidation.ChangeText, "invalid sibling must cancel this Recollection", firstEpisode,
				),
			}, "\n")}
			receipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
				JobRef:           "cross-run-splice-" + test.name,
				EpisodeRefs:      []string{firstEpisode, secondEpisode},
				OutcomeEventRefs: []string{secondOutcome.OutcomeEventRef},
			}, worker)
			if err != nil {
				t.Fatalf("cross-run %s: %v", test.operation, err)
			}
			if len(receipt.RecollectionVersionRefs) != 0 || len(receipt.DispositionVersionRefs) != 0 {
				t.Fatalf("cross-run %s wrote split receipt %#v", test.operation, receipt)
			}
			if count := harness.count(t, "recollections", "TRUE", nil); count != 0 {
				t.Fatalf("cross-run %s partially wrote %d Recollections", test.operation, count)
			}
			if count := harness.count(t, "seed_basis_links", "seed_version_ref = $2 AND role = '"+test.basisRole+"'", firstRun.seedVersionRef); count != 0 {
				t.Fatalf("cross-run %s wrote %d Episode Basis rows", test.operation, count)
			}
			if count := harness.count(t, "seed_outcome_basis_links", "seed_version_ref = $2 AND role = '"+test.basisRole+"'", firstRun.seedVersionRef); count != 0 {
				t.Fatalf("cross-run %s wrote %d Outcome Basis rows", test.operation, count)
			}
			if count := harness.count(t, "seed_versions", "seed_ref = (SELECT seed_ref FROM seed_versions WHERE tenant_ref = $1 AND seed_version_ref = $2)", firstRun.seedVersionRef); count != 1 {
				t.Fatalf("cross-run %s changed Disposition lineage to %d versions", test.operation, count)
			}
		})
	}
}

func TestNewDispositionApplicationMustMatchOwner(t *testing.T) {
	harness := newHarness(t)
	relationshipEpisodes := []string{
		harness.materializeEpisode(t, "wrong-relation-application-one", "session-wrong-relation-application-one"),
		harness.materializeEpisode(t, "wrong-relation-application-two", "session-wrong-relation-application-two"),
	}
	relationshipReceipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "wrong-relation-application-job", EpisodeRefs: relationshipEpisodes,
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationSelf,
		consolidation.ChangeText, "wrong relationship application", relationshipEpisodes...,
	)})
	if err != nil || len(relationshipReceipt.DispositionVersionRefs) != 0 {
		t.Fatalf("relationship owner accepted SELF Disposition = (%#v, %v)", relationshipReceipt, err)
	}

	agentScope := harness.source("wrong-agent-application-scope").Scope
	agentScope.Kind = ledger.ScopeKindAgent
	agentScope.RelationshipRef = ""
	agentEpisodes := make([]string, 0, 2)
	for _, suffix := range []string{"one", "two"} {
		situation := harness.source("wrong-agent-application-" + suffix)
		situation.Scope = agentScope
		situation.Scope.SessionRef = "session-wrong-agent-application-" + suffix
		agentEpisodes = append(agentEpisodes, harness.materializeEpisodeFromSituation(t, "wrong-agent-application-"+suffix, situation))
	}
	agentReceipt, err := harness.store.ConsolidateWindow(context.Background(), consolidation.Window{
		JobRef: "wrong-agent-application-job", EpisodeRefs: agentEpisodes,
	}, &scriptedWorker{taggedText: taggedMemoryChange(
		consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
		consolidation.ChangeText, "wrong agent application", agentEpisodes...,
	)})
	if err != nil || len(agentReceipt.DispositionVersionRefs) != 0 {
		t.Fatalf("agent owner accepted RELATION Disposition = (%#v, %v)", agentReceipt, err)
	}
	if count := harness.count(t, "disposition_seeds", "TRUE", nil); count != 0 {
		t.Fatalf("wrong applications wrote %d Dispositions", count)
	}
}

func taggedMemoryChange(target, application, operation, text string, basis ...string) string {
	change := operation
	if operation == consolidation.ChangeText {
		change += " " + text
	}
	block := []string{"TARGET", target, "APPLICATION", application, "CHANGE", change, "BASIS"}
	block = append(block, basis...)
	return strings.Join(block, "\n")
}
