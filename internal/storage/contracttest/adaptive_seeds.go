package contracttest

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/internal/scheduler"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

// RunAdaptiveSeeds tests the same real causal transitions in both databases.
func RunAdaptiveSeeds(t *testing.T, factory IndexedFactory) {
	t.Helper()
	t.Run("overlap_carries_outcomes_with_their_complete_episodes", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		first, firstSituation, firstAgentAct := s.materializeEpisode(t, "overlap-outcome-first", "The first interaction")
		firstOutcome := s.reportEpisodeOutcome(
			t, "overlap-outcome-first", "overlap-outcome-first", firstSituation, firstAgentAct,
			ledger.ActorKindUser, "The first response helped.",
		)
		lease := scheduler.LeaseRequest{
			QuietBefore: time.Now().Add(time.Minute), LeaseUntil: time.Now().Add(time.Minute),
			MaxEpisodes: 32, Overlap: 1,
		}
		firstJob, found, err := s.store.LeaseConsolidationJob(testContext(t), lease)
		if err != nil || !found {
			t.Fatalf("lease first Outcome window = (%#v, %v, %v)", firstJob, found, err)
		}
		if !reflect.DeepEqual(firstJob.OutcomeEventRefs, []string{firstOutcome}) {
			t.Fatalf("first Outcome window = %#v; want %#v", firstJob.OutcomeEventRefs, []string{firstOutcome})
		}
		if err := s.store.CompleteConsolidationJob(testContext(t), firstJob.Ref, firstJob.LeaseToken); err != nil {
			t.Fatalf("complete first Outcome window: %v", err)
		}

		second, secondSituation, secondAgentAct := s.materializeEpisode(t, "overlap-outcome-second", "The second interaction")
		secondOutcome := s.reportEpisodeOutcome(
			t, "overlap-outcome-second", "overlap-outcome-second", secondSituation, secondAgentAct,
			ledger.ActorKindUser, "The second response also helped.",
		)
		secondJob, found, err := s.store.LeaseConsolidationJob(testContext(t), lease)
		if err != nil || !found {
			t.Fatalf("lease overlapped Outcome window = (%#v, %v, %v)", secondJob, found, err)
		}
		if !reflect.DeepEqual(secondJob.EpisodeRefs, []string{first, second}) {
			t.Fatalf("overlapped Episodes = %#v; want %#v", secondJob.EpisodeRefs, []string{first, second})
		}
		if !reflect.DeepEqual(secondJob.OutcomeEventRefs, []string{firstOutcome, secondOutcome}) {
			t.Fatalf("overlapped Outcomes = %#v; want %#v", secondJob.OutcomeEventRefs, []string{firstOutcome, secondOutcome})
		}
	})

	t.Run("non_agent_outcomes_can_ground_new_disposition_formation", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		first, firstSituation, firstAgentAct := s.materializeEpisode(t, "outcome-form-first", "I was overloaded by several deadlines")
		second, secondSituation, secondAgentAct := s.materializeEpisode(t, "outcome-form-second", "I froze again when priorities collided")
		firstOutcome := s.reportEpisodeOutcome(
			t, "outcome-form-first", "outcome-form-first", firstSituation, firstAgentAct,
			ledger.ActorKindUser, "Naming one priority helped me start.",
		)
		secondOutcome := s.reportEpisodeOutcome(
			t, "outcome-form-second", "outcome-form-second", secondSituation, secondAgentAct,
			ledger.ActorKindExternal, "The user resumed work after one priority was named.",
		)
		agentOutcome := s.reportEpisodeOutcome(
			t, "outcome-form-second-agent", "outcome-form-second", secondSituation, secondAgentAct,
			ledger.ActorKindAgent, "I think my response worked perfectly.",
		)
		worker := &capturingWorker{text: taggedChange(
			consolidation.TargetNewDisposition,
			consolidation.ApplicationRelation,
			consolidation.ChangeText,
			"When competing priorities freeze the user, the Agent first helps name one priority.",
			first, second, firstOutcome, secondOutcome,
		)}

		receipt, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef:           "outcome-grounded-formation",
			EpisodeRefs:      []string{first, second},
			OutcomeEventRefs: []string{firstOutcome, secondOutcome, agentOutcome},
		}, worker)
		if err != nil {
			t.Fatalf("Outcome-grounded formation: %v", err)
		}
		if len(receipt.DispositionVersionRefs) != 1 {
			t.Fatalf("Outcome-grounded formation receipt = %#v", receipt)
		}
		for outcome, episode := range map[string]string{firstOutcome: first, secondOutcome: second} {
			if !slices.Contains(worker.request.AllowedBasisRefs, outcome) {
				t.Fatalf("non-Agent Outcome %s was not offered as formation Basis: %#v", outcome, worker.request.AllowedBasisRefs)
			}
			for _, marker := range []string{"OUTCOME " + outcome, "OUTCOME_EPISODE " + episode} {
				if !strings.Contains(worker.request.WindowText, marker) {
					t.Fatalf("formation window omitted %q:\n%s", marker, worker.request.WindowText)
				}
			}
			if got := s.store.(adaptiveStoreProbe).AdaptiveFormationOutcomeCount(t, receipt.DispositionVersionRefs[0], outcome); got != 1 {
				t.Fatalf("formation Outcome Basis %s count = %d; want 1", outcome, got)
			}
		}
		if slices.Contains(worker.request.AllowedBasisRefs, agentOutcome) || strings.Contains(worker.request.WindowText, "OUTCOME "+agentOutcome) {
			t.Fatalf("Agent-authored Outcome became formation authority: %#v\n%s", worker.request.AllowedBasisRefs, worker.request.WindowText)
		}
		if got := s.store.(adaptiveStoreProbe).AdaptiveFormationOutcomeCount(t, receipt.DispositionVersionRefs[0], agentOutcome); got != 0 {
			t.Fatalf("Agent-authored formation Outcome Basis count = %d; want 0", got)
		}
	})

	t.Run("direct_adaptation_requires_one_authoritative_episode", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		first, _, _ := s.materializeEpisode(t, "multi-adapt-first", "请修改这一次报告的结构")
		second, _, _ := s.materializeEpisode(t, "multi-adapt-second", "请在这一次报告中加入本地案例")

		formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "multi-adapt-new", EpisodeRefs: []string{first, second},
		}, textWorker(adaptChange(
			consolidation.TargetNewDisposition,
			"以后既使用结构化报告，也加入本地案例",
			first, second,
		)))
		if err != nil {
			t.Fatalf("multi-Episode NEW ADAPT: %v", err)
		}
		if len(formed.DispositionVersionRefs) != 0 {
			t.Fatalf("inferred Episodes became direct NEW ADAPT authority: %#v", formed)
		}

		authority, _, _ := s.materializeEpisode(t, "single-adapt-authority", "以后写报告时先列出结论")
		formed, err = s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "single-adapt-new", EpisodeRefs: []string{authority},
		}, textWorker(adaptChange(
			consolidation.TargetNewDisposition,
			"写报告时先列出结论",
			authority,
		)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("single authoritative NEW ADAPT = %#v, %v", formed, err)
		}

		revised, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "multi-adapt-existing", EpisodeRefs: []string{first, second},
		}, textWorker(adaptChange(
			formed.DispositionVersionRefs[0],
			"写报告时先列出结论，并使用结构化格式和本地案例",
			first, second,
		)))
		if err != nil {
			t.Fatalf("multi-Episode existing ADAPT: %v", err)
		}
		if len(revised.DispositionVersionRefs) != 0 {
			t.Fatalf("inferred Episodes revised Seed through direct ADAPT: %#v", revised)
		}
	})

	t.Run("inferred_disposition_requires_repeated_interaction_sessions", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		scope := s.scope
		scope.SessionRef = s.prefix + "-same-session"
		materialize := func(suffix, situationText string) string {
			runRef := s.prefix + "-same-session-run-" + suffix
			groupRef := s.prefix + "-same-session-group-" + suffix
			situation := s.eventInScope(
				"same-session-"+suffix+"-situation", scope,
				ledger.ActorKindUser, situationText,
			)
			s.observe(t, "same-session-"+suffix+"-situation", situation, ledger.EpisodeBinding{
				RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleSituation,
			})
			agentAct := s.eventInScope(
				"same-session-"+suffix+"-agent-act", scope,
				ledger.ActorKindAgent, "agent response: "+situationText,
			)
			receipt := s.observe(t, "same-session-"+suffix+"-agent-act", agentAct, ledger.EpisodeBinding{
				RunRef: runRef, SourceGroupRef: groupRef, Role: ledger.RoleAgentAct,
			})
			if receipt.EpisodeRef == "" {
				t.Fatalf("%s did not materialize an Episode", suffix)
			}
			return receipt.EpisodeRef
		}
		first := materialize("first", "这一轮先帮我梳理重点")
		second := materialize("second", "这一轮再帮我梳理优先级")
		worker := &capturingWorker{text: taggedChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "用户面对复杂问题时，Agent 先帮助梳理优先级", first, second,
		)}

		receipt, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "same-session-formation", EpisodeRefs: []string{first, second},
		}, worker)
		if err != nil {
			t.Fatalf("same-session formation: %v", err)
		}
		if len(receipt.DispositionVersionRefs) != 0 {
			t.Fatalf("one interaction session formed a Disposition: %#v", receipt)
		}
		if strings.Contains(worker.request.WindowText, "ELIGIBLE_NEW_DISPOSITION") {
			t.Fatalf("one interaction session was offered inferred formation:\n%s", worker.request.WindowText)
		}
	})

	t.Run("semantic_index_can_activate_disposition_without_lexical_overlap", func(t *testing.T) {
		index := &scriptedIndex{}
		s := newScenario(migratedIndexedStore(t, factory, index))
		first, _, _ := s.materializeEpisode(t, "semantic-select-one", "用户在复杂规划中感到压力")
		second, _, _ := s.materializeEpisode(t, "semantic-select-two", "用户在任务过多时感到不知所措")
		formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "semantic-select-form", EpisodeRefs: []string{first, second},
		}, textWorker(taggedChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "先帮助用户梳理优先级再提供方案", first, second,
		)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("form semantic selection Seed = %#v, %v", formed, err)
		}
		target := formed.DispositionVersionRefs[0]
		index.candidates = []memoryindex.Candidate{{
			Kind: memoryindex.KindDisposition, Ref: target,
		}}

		selected := s.selectText(t, "semantic-select-current", "I have too many moving pieces and cannot begin")
		if len(selected.Dispositions) != 1 || selected.Dispositions[0].MemoryRef != target {
			t.Fatalf("direct semantic Disposition result was discarded: %#v", selected.Dispositions)
		}
	})

	t.Run("semantic_support_episode_activates_only_its_linked_disposition", func(t *testing.T) {
		index := &scriptedIndex{}
		s := newScenario(migratedIndexedStore(t, factory, index))
		linkedFirst, _, _ := s.materializeEpisode(t, "support-index-linked-one", "用户曾在周末独自练习手动挡")
		linkedSecond, _, _ := s.materializeEpisode(t, "support-index-linked-two", "用户再次选择驾驶手动挡汽车")
		linked, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "support-index-linked-form", EpisodeRefs: []string{linkedFirst, linkedSecond},
		}, textWorker(taggedChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "推荐交通工具时优先考虑驾驶参与感", linkedFirst, linkedSecond,
		)))
		if err != nil || len(linked.DispositionVersionRefs) != 1 {
			t.Fatalf("form support-linked Seed = %#v, %v", linked, err)
		}

		siblingFirst, _, _ := s.materializeEpisode(t, "support-index-sibling-one", "用户喜欢提前规划详细行程")
		siblingSecond, _, _ := s.materializeEpisode(t, "support-index-sibling-two", "用户再次要求逐小时安排行程")
		sibling, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "support-index-sibling-form", EpisodeRefs: []string{siblingFirst, siblingSecond},
		}, textWorker(taggedChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "旅行建议应提供逐小时安排", siblingFirst, siblingSecond,
		)))
		if err != nil || len(sibling.DispositionVersionRefs) != 1 {
			t.Fatalf("form sibling Seed = %#v, %v", sibling, err)
		}

		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: linkedFirst}}
		selected := s.selectText(t, "support-index-current", "Which vehicle would make a winding mountain road more engaging?")
		if len(selected.Dispositions) != 1 || selected.Dispositions[0].MemoryRef != linked.DispositionVersionRefs[0] {
			t.Fatalf("semantic support Episode did not activate only its linked Seed: %#v", selected.Dispositions)
		}
		if selected.Dispositions[0].MemoryRef == sibling.DispositionVersionRefs[0] {
			t.Fatalf("semantic support Episode activated sibling Seed: %#v", selected.Dispositions)
		}
	})

	t.Run("consumed_reenactment_is_not_offered_as_feedback_again", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		formationOne, _, _ := s.materializeEpisode(t, "consume-form-one", "先帮助我梳理优先级")
		formationTwo, _, _ := s.materializeEpisode(t, "consume-form-two", "事情太多时先帮助我梳理优先级")
		formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "consume-form", EpisodeRefs: []string{formationOne, formationTwo},
		}, textWorker(taggedChange(
			consolidation.TargetNewDisposition, consolidation.ApplicationRelation,
			consolidation.ChangeText, "用户面对复杂任务时，Agent 先帮助梳理优先级", formationOne, formationTwo,
		)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("form feedback fixture = %#v, %v", formed, err)
		}
		target := formed.DispositionVersionRefs[0]
		finish := func(suffix string, run activatedRun, text string) string {
			agentAct := s.eventInScope(suffix+"-agent-act", run.scope, ledger.ActorKindAgent, text)
			receipt := s.observe(t, suffix+"-agent-act", agentAct, ledger.EpisodeBinding{
				RunRef: run.runRef, SourceGroupRef: run.groupRef, Role: ledger.RoleAgentAct,
			})
			if receipt.EpisodeRef == "" {
				t.Fatalf("%s did not materialize feedback Episode", suffix)
			}
			return receipt.EpisodeRef
		}

		firstRun := s.activate(t, "consume-first", "用户面对复杂任务时，Agent 先帮助梳理优先级", target)
		firstDelivery, err := s.store.RecordMemoryDelivery(testContext(t), ledger.MemoryDelivery{
			IdempotencyKey: "consume-first-delivery",
			Scope:          firstRun.scope, RunRef: firstRun.runRef, MemoryContextRef: firstRun.memoryContextRef,
			DeliveredMemoryRefs: []string{target},
		})
		if err != nil || firstDelivery.Ref == "" {
			t.Fatalf("record first feedback Delivery = %#v, %v", firstDelivery, err)
		}
		firstEpisode := finish("consume-first", firstRun, "我们先找出最重要的一件事")
		firstRequest := &capturingWorker{text: taggedChange(
			target, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", firstEpisode,
		)}
		if _, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "consume-first-feedback", EpisodeRefs: []string{firstEpisode},
		}, firstRequest); err != nil {
			t.Fatalf("record first reenactment: %v", err)
		}
		if !strings.Contains(firstRequest.request.WindowText, "FEEDBACK_EPISODE "+firstEpisode) {
			t.Fatalf("first unconsumed feedback was not offered:\n%s", firstRequest.request.WindowText)
		}

		secondRun := s.activate(t, "consume-second", "用户面对复杂任务时，Agent 先帮助梳理优先级", target)
		secondDelivery, err := s.store.RecordMemoryDelivery(testContext(t), ledger.MemoryDelivery{
			IdempotencyKey: "consume-second-delivery",
			Scope:          secondRun.scope, RunRef: secondRun.runRef, MemoryContextRef: secondRun.memoryContextRef,
			DeliveredMemoryRefs: []string{target},
		})
		if err != nil || secondDelivery.Ref == "" {
			t.Fatalf("record second feedback Delivery = %#v, %v", secondDelivery, err)
		}
		secondEpisode := finish("consume-second", secondRun, "先只看最重要的下一步")
		secondRequest := &capturingWorker{text: taggedChange(
			target, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", secondEpisode,
		)}
		if _, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "consume-second-feedback", EpisodeRefs: []string{firstEpisode, secondEpisode},
		}, secondRequest); err != nil {
			t.Fatalf("record second reenactment: %v", err)
		}
		if strings.Contains(secondRequest.request.WindowText, "FEEDBACK_EPISODE "+firstEpisode) {
			t.Fatalf("consumed feedback was offered again:\n%s", secondRequest.request.WindowText)
		}
		if !strings.Contains(secondRequest.request.WindowText, "FEEDBACK_EPISODE "+secondEpisode) {
			t.Fatalf("new feedback was not offered:\n%s", secondRequest.request.WindowText)
		}
	})

	// A lexical LIMIT before ranking loses a clearly relevant existing Seed.
	// Exercise real intake, candidate rendering and commit with no useful index.
	for _, mode := range []string{"none", "empty", "unavailable"} {
		t.Run("adaptive_correction_beyond_64_"+mode, func(t *testing.T) {
			var index memoryindex.Index
			switch mode {
			case "empty":
				index = &scriptedIndex{}
			case "unavailable":
				index = &unavailableAdaptiveIndex{}
			}
			s := newScenario(migratedIndexedStore(t, factory, index))
			probe := s.store.(adaptiveStoreProbe)
			texts := make(map[string]string)
			var versions []string
			for n := 0; n < 70; n++ {
				// Distinct long topics make the selected topic unambiguous to the
				// existing character ranker, without assuming hash allocation order.
				text := strings.Repeat(string(rune(0x4e00+n)), 24) + "时先听完再建议"
				episode, _, _ := s.materializeEpisode(t, fmt.Sprintf("ranked-%d", n), "以后"+text)
				r, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: fmt.Sprintf("ranked-form-%d", n), EpisodeRefs: []string{episode}}, textWorker(adaptChange(consolidation.TargetNewDisposition, text, episode)))
				if err != nil || len(r.DispositionVersionRefs) != 1 {
					t.Fatalf("form catalogue Seed %d: %#v, %v", n, r, err)
				}
				ref := r.DispositionVersionRefs[0]
				versions = append(versions, ref)
				texts[ref] = text
			}
			slices.Sort(versions)
			target := versions[69] // Beyond the old lexical first 64, by construction.
			newText := texts[target] + "，改为先承认担忧"
			current, _, _ := s.materializeEpisode(t, "ranked-correction", "以后请修改原有要求："+newText)
			before := probe.AdaptiveCounts(t, target, current, texts[target])
			calls, inputBytes := 0, 0
			r, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "ranked-correction", EpisodeRefs: []string{current}}, callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
				calls++
				if !slices.Contains(request.AllowedTargetRefs, target) || !strings.Contains(request.WindowText, "ELIGIBLE_ADAPTATION "+target) || !strings.Contains(request.WindowText, texts[target]) {
					t.Fatalf("lexical first-64 cutoff hid clearly relevant existing Seed %s (position 70 of 70) from Core Worker input", target)
				}
				if count := strings.Count(request.WindowText, "ELIGIBLE_ADAPTATION "); count != 64 {
					t.Fatalf("candidate presentation unbounded or incomplete: %d", count)
				}
				if !reflect.DeepEqual(request.AllowedBasisRefs, []string{current}) || strings.Contains(request.WindowText, "FEEDBACK_EPISODE ") || strings.Contains(request.WindowText, "FEEDBACK_OUTCOME ") {
					t.Fatalf("historical anchor or feedback authority leaked: %#v", request)
				}
				inputBytes = 32*1024 + len(request.WindowText)
				for _, ref := range request.AllowedTargetRefs {
					inputBytes += len("ALLOWED_TARGET\n") + len(ref) + 1
				}
				for _, ref := range request.AllowedBasisRefs {
					inputBytes += len("ALLOWED_BASIS\n") + len(ref) + 1
				}
				if inputBytes > 256*1024 {
					t.Fatalf("bounded correction input = %d bytes", inputBytes)
				}
				return adaptChange(target, newText, current), nil
			}))
			want := strings.TrimSuffix(target, "@1") + "@2"
			if err != nil || calls != 1 || !reflect.DeepEqual(r.DispositionVersionRefs, []string{want}) {
				t.Fatalf("correction must revise same Seed: %#v, %v, calls=%d; want %s", r, err, calls, want)
			}
			after := probe.AdaptiveCounts(t, want, current, newText)
			for _, table := range []string{"disposition_seeds", "memory_contexts", "memory_context_items", "memory_delivery_receipts", "outcome_events", "seed_outcome_basis_links", "feedback_basis"} {
				if after[table] != before[table] {
					t.Fatalf("direct correction changed %s: %d -> %d", table, before[table], after[table])
				}
			}
			if after["disposition_seeds"] != 70 || after["seed_versions"] != 71 || after["active_target"] != 1 || after["current_revision_basis"] != 1 || after["superseded"] != 1 {
				t.Fatalf("same-identity persisted revision or current Basis missing: %v", after)
			}
			t.Logf("position 70/70 %s -> %s; 64 direct candidates; input %d/262144 bytes; state %v", target, want, inputBytes, after)
		})
	}
	t.Run("adaptive_catalogue_does_not_exhaust_default_worker_budget", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		var target string
		for index := 0; index < 32; index++ {
			// Sixty-four distinct 4 KiB Episodes are ordinary stored history,
			// not a large current consolidation window.
			first, _, _ := s.materializeEpisodeWithTexts(t, fmt.Sprintf("catalogue-%d-a", index), strings.Repeat("u", 2048), strings.Repeat("a", 2048), s.scope)
			second, _, _ := s.materializeEpisodeWithTexts(t, fmt.Sprintf("catalogue-%d-b", index), strings.Repeat("v", 2048), strings.Repeat("b", 2048), s.scope)
			formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
				JobRef: fmt.Sprintf("catalogue-form-%d", index), EpisodeRefs: []string{first, second},
			}, textWorker(taggedChange(consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText, fmt.Sprintf("讨论主题%d时先听完再建议", index), first, second)))
			if err != nil || len(formed.DispositionVersionRefs) != 1 {
				t.Fatalf("catalogue fixture %d: %#v, %v", index, formed, err)
			}
			if index == 0 {
				target = formed.DispositionVersionRefs[0]
			}
		}
		current, _, _ := s.materializeEpisode(t, "catalogue-correction", "讨论主题0时以后先承认担忧再建议")
		modelCalls, inputBytes := 0, 0
		const defaultWorkerBudget = 256 * 1024
		const instructionReserve = 32 * 1024 // Conservative room for Worker rules, final check and envelope.
		receipt, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{
			JobRef: "catalogue-short-correction", EpisodeRefs: []string{current},
		}, callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
			inputBytes = instructionReserve + len(request.WindowText)
			for _, ref := range request.AllowedTargetRefs {
				inputBytes += len("ALLOWED_TARGET\n") + len(ref) + 1
			}
			for _, ref := range request.AllowedBasisRefs {
				inputBytes += len("ALLOWED_BASIS\n") + len(ref) + 1
			}
			if inputBytes > defaultWorkerBudget {
				return "", nil // The reference Worker skips its model and returns an empty result.
			}
			modelCalls++
			if !slices.Contains(request.AllowedTargetRefs, target) || !slices.Contains(request.AllowedBasisRefs, current) {
				t.Fatalf("budget protection removed direct eligibility: %#v", request)
			}
			return adaptChange(target, "讨论主题0时先承认担忧再建议", current), nil
		}))
		wantVersion := strings.TrimSuffix(target, "@1") + "@2"
		if err != nil || modelCalls != 1 || !reflect.DeepEqual(receipt.DispositionVersionRefs, []string{wantVersion}) {
			t.Fatalf("short correction blocked by catalogue: model calls=%d input bytes=%d budget=%d receipt=%#v error=%v", modelCalls, inputBytes, defaultWorkerBudget, receipt, err)
		}
		t.Logf("32-Seed catalogue correction committed: budgeted input %d / %d bytes (includes %d instruction reserve)", inputBytes, defaultWorkerBudget, instructionReserve)
	})

	t.Run("adaptive_single_episode_forms_and_direct_correction_changes_same_seed", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		oldText := "讨论设计时先质疑我的架构"
		newText := "讨论设计时先承认我的担忧，再质疑我的架构"
		first, _, _ := s.materializeEpisodeWithTexts(t, "direct-first", "以后"+oldText, "这个问题先放一放", s.scope)
		worker := &capturingWorker{text: adaptChange(consolidation.TargetNewDisposition, oldText, first)}
		window := consolidation.Window{JobRef: "direct-form", EpisodeRefs: []string{first}}
		formed, err := s.store.ConsolidateWindow(testContext(t), window, worker)
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("single user requirement did not form Seed: %#v, %v", formed, err)
		}
		for _, tag := range []string{"ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION", "DIRECT_EPISODE " + first, "ACTOR user user-1"} {
			if !strings.Contains(worker.request.WindowText, tag) {
				t.Fatalf("missing direct input %q: %s", tag, worker.request.WindowText)
			}
		}
		replayed, err := s.store.ConsolidateWindow(testContext(t), window, callbackWorker(func(consolidation.WorkerRequest) (string, error) {
			t.Fatal("committed retry called Worker")
			return "", nil
		}))
		if err != nil || !reflect.DeepEqual(replayed, formed) {
			t.Fatalf("formation retry = %#v, %v", replayed, err)
		}
		oldVersion := formed.DispositionVersionRefs[0]
		before := s.selectText(t, "before-direct-revision", oldText)
		if len(before.Dispositions) != 1 || before.Dispositions[0].MemoryRef != oldVersion {
			t.Fatalf("formed Seed not selected: %#v", before.Dispositions)
		}
		second, _, _ := s.materializeEpisodeWithTexts(t, "direct-second", "以后改为："+newText, "收到你的要求", s.scope)
		worker = &capturingWorker{text: adaptChange(oldVersion, newText, second)}
		revised, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "direct-revise", EpisodeRefs: []string{second}}, worker)
		wantVersion := strings.TrimSuffix(oldVersion, "@1") + "@2"
		if err != nil || !reflect.DeepEqual(revised.DispositionVersionRefs, []string{wantVersion}) {
			t.Fatalf("direct correction did not revise same Seed: %#v, %v; want %s", revised, err, wantVersion)
		}
		if !strings.Contains(worker.request.WindowText, "ELIGIBLE_ADAPTATION "+oldVersion) || strings.Contains(worker.request.WindowText, "FEEDBACK_EPISODE "+second) || strings.Contains(worker.request.WindowText, "FEEDBACK_OUTCOME ") {
			t.Fatalf("direct eligibility conflated with behavior feedback: %s", worker.request.WindowText)
		}
		after := s.selectText(t, "after-direct-revision", newText)
		if len(after.Dispositions) != 1 || after.Dispositions[0].MemoryRef != wantVersion || after.Dispositions[0].Text != newText {
			t.Fatalf("direct correction did not change future Select: %#v", after.Dispositions)
		}
		stale, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "direct-old-version", EpisodeRefs: []string{second}}, textWorker(adaptChange(oldVersion, "旧版本不得再写入", second)))
		if err != nil || len(stale.DispositionVersionRefs) != 0 {
			t.Fatalf("superseded ADAPT target wrote a version: %#v, %v", stale, err)
		}
		// A same-text ADAPT is not a semantic revision and cannot be used as a
		// surrogate reinforcement path without behavioral attribution.
		third, _, _ := s.materializeEpisode(t, "same-text", "量子海豚蓝莓火箭")
		kept, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "direct-same-text", EpisodeRefs: []string{third}}, textWorker(adaptChange(wantVersion, newText, third)))
		if err != nil || len(kept.DispositionVersionRefs) != 0 {
			t.Fatalf("same-text ADAPT created version: %#v, %v", kept, err)
		}
		byNewBasis := s.selectText(t, "same-text-basis-select", "量子海豚蓝莓火箭")
		if len(byNewBasis.Dispositions) != 0 {
			t.Fatalf("same-text ADAPT mutated the Seed selection surface: %#v", byNewBasis.Dispositions)
		}
		for _, operation := range []string{consolidation.ChangeText, consolidation.ChangeInhibit, consolidation.ChangeReenact} {
			result, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "direct-cannot-" + operation, EpisodeRefs: []string{second}}, textWorker(taggedChange(wantVersion, consolidation.ApplicationRelation, operation, "forbidden behavior claim", second)))
			if err != nil || len(result.DispositionVersionRefs) != 0 {
				t.Fatalf("ADAPT opened %s without Delivery/Outcome: %#v, %v", operation, result, err)
			}
		}
	})

	t.Run("adaptive_related_anchor_is_visible_but_not_direct_authority", func(t *testing.T) {
		index := &scriptedIndex{}
		s := newScenario(migratedIndexedStore(t, factory, index))
		old, _, _ := s.materializeEpisode(t, "hidden-anchor-initial", "以后先听完再建议")
		formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "hidden-anchor-form", EpisodeRefs: []string{old}}, textWorker(adaptChange(consolidation.TargetNewDisposition, "先听完再建议", old)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("hidden-anchor fixture: %#v, %v", formed, err)
		}
		target := formed.DispositionVersionRefs[0]
		current, _, _ := s.materializeEpisode(t, "hidden-anchor-current", "以后先承认担忧再建议")
		worker := &capturingWorker{text: adaptChange(target, "先承认担忧再建议", old, current)}
		result, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "hidden-anchor-rejected", EpisodeRefs: []string{current}}, worker)
		if slices.Contains(worker.request.AllowedBasisRefs, old) {
			t.Fatal("direct target automatically exposed an unrelated historical anchor")
		}
		if err != nil || len(result.DispositionVersionRefs) != 0 {
			t.Fatalf("hidden canonical anchor granted model write authority: %#v, %v", result, err)
		}
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: old}}
		result, err = s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "related-anchor-accepted", EpisodeRefs: []string{current}}, worker)
		if err != nil || len(result.DispositionVersionRefs) != 0 || !slices.Contains(worker.request.AllowedBasisRefs, old) {
			t.Fatalf("independently selected related evidence was lost: %#v, %v", result, err)
		}
		worker = &capturingWorker{text: adaptChange(target, "先承认担忧再建议", current)}
		result, err = s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "related-anchor-current-authority", EpisodeRefs: []string{current}}, worker)
		if err != nil || len(result.DispositionVersionRefs) != 1 || !slices.Contains(worker.request.AllowedBasisRefs, old) {
			t.Fatalf("single current authority failed with visible related evidence: %#v, %v", result, err)
		}
	})

	t.Run("adaptive_quiet_single_complete_episode_but_not_unbound_source", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		s.observe(t, "unbound", s.source("unbound", "以后请先给结论"), ledger.EpisodeBinding{})
		request := scheduler.LeaseRequest{QuietBefore: time.Now().Add(time.Minute), LeaseUntil: time.Now().Add(time.Minute), MaxEpisodes: 32}
		if _, found, err := s.store.LeaseConsolidationJob(testContext(t), request); err != nil || found {
			t.Fatalf("unbound source became learning Job: %v, %v", found, err)
		}
		episode, _, _ := s.materializeEpisode(t, "single-quiet", "以后请先给结论")
		updatedAt := s.store.(adaptiveStoreProbe).AdaptiveJobUpdatedAt(t)
		request.LeaseUntil = updatedAt.Add(time.Hour)
		request.QuietBefore = updatedAt.Add(-time.Microsecond)
		if _, found, err := s.store.LeaseConsolidationJob(testContext(t), request); err != nil || found {
			t.Fatalf("singleton skipped quiet period: %v, %v", found, err)
		}
		request.QuietBefore = updatedAt.Add(time.Microsecond)
		job, found, err := s.store.LeaseConsolidationJob(testContext(t), request)
		if err != nil || !found || !reflect.DeepEqual(job.EpisodeRefs, []string{episode}) {
			t.Fatalf("first quiet singleton stranded: %#v, %v, %v", job, found, err)
		}
		t.Logf("singleton quiet boundary: DB Job updated_at=%s; -1us not leased; +1us leased", updatedAt.Format(time.RFC3339Nano))
		processorWorker := textWorker(adaptChange(consolidation.TargetNewDisposition, "先给结论", episode))
		formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: job.Ref, EpisodeRefs: job.EpisodeRefs}, processorWorker)
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("leased singleton did not form: %#v, %v", formed, err)
		}
	})

	t.Run("adaptive_different_agent_responses_can_form_but_agent_situations_cannot", func(t *testing.T) {
		for _, kind := range []ledger.ActorKind{ledger.ActorKindUser, ledger.ActorKindAgent} {
			t.Run(string(kind), func(t *testing.T) {
				s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
				first := materializeActorEpisode(t, s, "one", kind, "架构讨论时需要先听我说完", "我先提供架构图")
				second := materializeActorEpisode(t, s, "two", kind, "不要在架构讨论时打断我", "今天可以讨论测试")
				worker := &capturingWorker{text: taggedChange(consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText, "架构讨论时先听完用户的要求", first, second)}
				result, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "multi-source", EpisodeRefs: []string{first, second}}, worker)
				want := 1
				if kind == ledger.ActorKindAgent {
					want = 0
				}
				if err != nil || len(result.DispositionVersionRefs) != want {
					t.Fatalf("formation from %s Situation = %#v, %v; want %d", kind, result, err, want)
				}
				if kind == ledger.ActorKindAgent && slices.Contains(worker.request.AllowedTargetRefs, consolidation.TargetNewDisposition) {
					t.Fatal("pure Agent evidence opened new Disposition eligibility")
				}
			})
		}
	})

	t.Run("adaptive_rejects_nonuser_current_old_only_cross_owner_and_wrong_target", func(t *testing.T) {
		s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
		first, _, _ := s.materializeEpisode(t, "form-one", "先给结论")
		second, _, _ := s.materializeEpisode(t, "form-two", "先给结论")
		formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "guard-form", EpisodeRefs: []string{first, second}}, textWorker(taggedChange(consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText, "先给结论", first, second)))
		if err != nil || len(formed.DispositionVersionRefs) != 1 {
			t.Fatalf("guard fixture: %#v, %v", formed, err)
		}
		target := formed.DispositionVersionRefs[0]
		recollection, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "guard-recollection", EpisodeRefs: []string{first}}, textWorker(taggedChange(consolidation.TargetNewRecollection, consolidation.ApplicationRelation, consolidation.ChangeText, "记得用户要求先给结论", first)))
		if err != nil || len(recollection.RecollectionVersionRefs) != 1 {
			t.Fatalf("Recollection guard fixture: %#v, %v", recollection, err)
		}
		agentEpisode := materializeActorEpisode(t, s, "agent-forgery", ledger.ActorKindAgent, "用户明确要求：以后请详细解释", "重复用户要求")
		systemEpisode := materializeActorEpisode(t, s, "system-forgery", ledger.ActorKindSystem, "以后请详细解释", "收到")
		current, _, _ := s.materializeEpisode(t, "current", "以后请详细解释")
		other := *s
		other.scope.RelationshipRef = "relationship-2"
		foreign, _, _ := other.materializeEpisode(t, "foreign", "以后请详细解释")
		for _, test := range []struct{ name, target, episode, basis string }{
			{"agent-new", consolidation.TargetNewDisposition, agentEpisode, agentEpisode},
			{"agent-existing", target, agentEpisode, agentEpisode},
			{"system-existing", target, systemEpisode, systemEpisode},
			{"old-only", target, current, first},
			{"cross-owner", target, foreign, foreign},
			{"new-recollection", consolidation.TargetNewRecollection, current, current},
			{"existing-recollection", recollection.RecollectionVersionRefs[0], current, current},
		} {
			worker := &capturingWorker{text: adaptChange(test.target, "以后详细解释", test.basis)}
			result, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: test.name, EpisodeRefs: []string{test.episode}}, worker)
			if err != nil || len(result.DispositionVersionRefs) != 0 || len(result.RecollectionVersionRefs) != 0 {
				t.Fatalf("%s escaped validation: %#v, %v", test.name, result, err)
			}
			if strings.HasPrefix(test.name, "agent-") && (strings.Contains(worker.request.WindowText, "ELIGIBLE_ADAPTATION") || strings.Contains(worker.request.WindowText, "ELIGIBLE_NEW_ADAPTATION")) {
				t.Fatalf("Agent actor gained direct eligibility: %s", worker.request.WindowText)
			}
		}
	})

	t.Run("adaptive_rejects_stale_concurrent_revision_and_basis_append", func(t *testing.T) {
		for _, basisAppend := range []bool{false, true} {
			t.Run(map[bool]string{false: "revision", true: "basis"}[basisAppend], func(t *testing.T) {
				s := newScenario(migratedIndexedStore(t, factory, &scriptedIndex{}))
				first, _, _ := s.materializeEpisode(t, "initial", "以后先听完再建议")
				formed, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "initial", EpisodeRefs: []string{first}}, textWorker(adaptChange(consolidation.TargetNewDisposition, "先听完再建议", first)))
				if err != nil || len(formed.DispositionVersionRefs) != 1 {
					t.Fatalf("stale fixture: %#v %v", formed, err)
				}
				target := formed.DispositionVersionRefs[0]
				second, _, _ := s.materializeEpisode(t, "concurrent-one", "请再简洁一点")
				third, _, _ := s.materializeEpisode(t, "concurrent-two", "请先承认担忧")
				nestedWindow := consolidation.Window{JobRef: "stale-inner", EpisodeRefs: []string{third}}
				nestedWorker := textWorker(adaptChange(target, "先承认担忧再建议", third))
				if basisAppend {
					run := s.activate(t, "concurrent-reenactment", "先听完再建议", target)
					delivery, deliveryErr := s.store.RecordMemoryDelivery(testContext(t), ledger.MemoryDelivery{
						IdempotencyKey: "concurrent-reenactment-delivery",
						Scope:          run.scope, RunRef: run.runRef, MemoryContextRef: run.memoryContextRef,
						DeliveredMemoryRefs: []string{target},
					})
					if deliveryErr != nil || delivery.Ref == "" {
						t.Fatalf("prepare concurrent reenactment Delivery = %#v, %v", delivery, deliveryErr)
					}
					feedbackEpisode, _ := s.finishActivatedRun(t, run, "我会先听完再建议")
					nestedWindow = consolidation.Window{JobRef: "stale-inner", EpisodeRefs: []string{feedbackEpisode}}
					nestedWorker = textWorker(taggedChange(target, consolidation.ApplicationRelation, consolidation.ChangeReenact, "", feedbackEpisode))
				}
				_, err = s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "stale-outer", EpisodeRefs: []string{second}}, callbackWorker(func(consolidation.WorkerRequest) (string, error) {
					_, nestedErr := s.store.ConsolidateWindow(testContext(t), nestedWindow, nestedWorker)
					if nestedErr != nil {
						return "", nestedErr
					}
					return adaptChange(target, "先听完再简洁建议", second), nil
				}))
				if !errors.Is(err, consolidation.ErrWindowStale) {
					t.Fatalf("concurrent basis append=%v did not stale snapshot: %v", basisAppend, err)
				}
			})
		}
	})

	t.Run("adaptive_indexed_old_user_evidence_cannot_substitute_current_user", func(t *testing.T) {
		index := &scriptedIndex{}
		s := newScenario(migratedIndexedStore(t, factory, index))
		old, _, _ := s.materializeEpisode(t, "old-user", "以后先听完再建议")
		current := materializeActorEpisode(t, s, "current-agent", ledger.ActorKindAgent, "引用用户：以后先听完再建议", "我会先听完")
		index.candidates = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: old}}
		worker := &capturingWorker{text: adaptChange(consolidation.TargetNewDisposition, "先听完再建议", old, current)}
		result, err := s.store.ConsolidateWindow(testContext(t), consolidation.Window{JobRef: "old-user-is-not-current", EpisodeRefs: []string{current}}, worker)
		if err != nil || len(result.DispositionVersionRefs) != 0 {
			t.Fatalf("old user evidence authorized ADAPT: %#v %v", result, err)
		}
		if !strings.Contains(worker.request.WindowText, "RELATED_EPISODE "+old) || strings.Contains(worker.request.WindowText, "ELIGIBLE_NEW_ADAPTATION") {
			t.Fatalf("wrong current/related direct eligibility: %s", worker.request.WindowText)
		}
	})
}

func adaptChange(target, text string, basis ...string) string {
	return "TARGET\n" + target + "\nAPPLICATION\nRELATION\nCHANGE\nADAPT\n" + text + "\nBASIS\n" + strings.Join(basis, "\n")
}

func materializeActorEpisode(t *testing.T, s *scenario, suffix string, kind ledger.ActorKind, situationText, agentText string) string {
	t.Helper()
	scope := s.scope
	scope.SessionRef = s.prefix + "-session-" + suffix
	binding := ledger.EpisodeBinding{RunRef: s.prefix + "-run-" + suffix, SourceGroupRef: s.prefix + "-group-" + suffix, Role: ledger.RoleSituation}
	s.observe(t, suffix+"-situation", s.eventInScope(suffix+"-situation", scope, kind, situationText), binding)
	binding.Role = ledger.RoleAgentAct
	receipt := s.observe(t, suffix+"-agent", s.eventInScope(suffix+"-agent", scope, ledger.ActorKindAgent, agentText), binding)
	if receipt.EpisodeRef == "" {
		t.Fatal("actor fixture did not materialize Episode")
	}
	return receipt.EpisodeRef
}

type callbackWorker func(consolidation.WorkerRequest) (string, error)

func (worker callbackWorker) ProcessConsolidationWindow(_ context.Context, request consolidation.WorkerRequest) (string, error) {
	return worker(request)
}
