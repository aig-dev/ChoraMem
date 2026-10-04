package contracttest

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/core/consolidation"
	"github.com/aig-dev/ChoraMem/internal/core/ledger"
	"github.com/aig-dev/ChoraMem/memoryindex"
)

// RunFormationCandidates observes qualification through both real Stores. The
// scripted order deliberately does not claim to test semantic retrieval.
func RunFormationCandidates(t *testing.T, factory IndexedFactory) {
	t.Helper()
	t.Run("formation_candidate_group_preserves_failure_and_filters_unqualified_pairs", func(t *testing.T) {
		f := newFormationFixture(t, factory, true)
		_, err := f.s.store.ConsolidateWindow(testContext(t), f.window("preserve"), callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
			assertFormationBasis(t, request, f.basis())
			found := false
			for _, outcome := range request.Evidence.Outcomes {
				if outcome.OutcomeRef == f.first.outcome {
					found = outcome.Text == f.first.outcomeText
				}
			}
			if !found {
				t.Fatal("same-condition negative Outcome was removed or rewritten")
			}
			return "", nil // Qualification is not semantic agreement or success.
		}))
		if err != nil {
			t.Fatal(err)
		}
	})

	t.Run("formation_candidate_basis_is_exact_visible_group", func(t *testing.T) {
		f := newFormationFixture(t, factory, false)
		basis := f.basis()
		for _, test := range []struct {
			name  string
			basis []string
		}{
			{"missing_outcome", append([]string(nil), basis[:len(basis)-1]...)},
			{"extra_unindexed_pair", append(append([]string(nil), basis...), f.noise.episode, f.noise.outcome)},
			{"duplicate", append(append([]string(nil), basis...), basis[0])},
			{"only_anchor", append([]string(nil), basis[:2]...)},
			{"exact", basis},
		} {
			t.Run(test.name, func(t *testing.T) {
				receipt, err := f.s.store.ConsolidateWindow(testContext(t), f.window(test.name), callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
					assertFormationBasis(t, request, basis)
					return taggedChange(consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText,
						"When several options feel equally urgent, compare one tradeoff and leave the choice with the user.", test.basis...), nil
				}))
				if err != nil {
					t.Fatal(err)
				}
				want := 0
				if test.name == "exact" {
					want = 1
				}
				if len(receipt.DispositionVersionRefs) != want {
					t.Fatalf("%s committed %d versions; want %d", test.name, len(receipt.DispositionVersionRefs), want)
				}
			})
		}
	})

	t.Run("formation_candidate_view_failures_keep_original_fallback", func(t *testing.T) {
		for _, mode := range []string{"causal_failure", "condition_failure", "all_filtered"} {
			t.Run(mode, func(t *testing.T) {
				f := newFormationFixture(t, factory, false)
				f.index.mode = mode
				window := f.window(mode)
				if mode == "all_filtered" {
					window.EpisodeRefs = []string{f.noise.episode, f.anchor.episode}
					window.OutcomeEventRefs = []string{f.noise.outcome, f.anchor.outcome}
				}
				receipt, err := f.s.store.ConsolidateWindow(testContext(t), window, callbackWorker(func(request consolidation.WorkerRequest) (string, error) {
					switch mode {
					case "causal_failure":
						if consolidation.HasDispositionFormationAnchor(request.Evidence) {
							t.Fatal("failed causal view unexpectedly established a focused group")
						}
					case "condition_failure":
						assertFormationBasis(t, request, f.basis())
					case "all_filtered":
						if !consolidation.HasDispositionFormationAnchor(request.Evidence) {
							t.Fatal("filtered indexed candidates lost anchor/no-op guard")
						}
						if basis, ok := consolidation.DispositionFormationBasis(request.Evidence); ok || len(basis) != 0 {
							t.Fatal("unindexed window sibling became a candidate")
						}
						return taggedChange(consolidation.TargetNewDisposition, consolidation.ApplicationRelation, consolidation.ChangeText,
							"untrusted broad-window formation", f.noise.episode, f.anchor.episode), nil
					}
					return "", nil
				}))
				if err != nil || len(receipt.DispositionVersionRefs) != 0 {
					t.Fatalf("view fallback changed no-op: %#v, %v", receipt, err)
				}
			})
		}
	})
}

func assertFormationBasis(t *testing.T, request consolidation.WorkerRequest, want []string) {
	t.Helper()
	got, ok := consolidation.DispositionFormationBasis(request.Evidence)
	if !ok || !reflect.DeepEqual(got, want) {
		t.Fatalf("model-visible group Basis=%#v, %v; want %#v", got, ok, want)
	}
}

type formationPair struct {
	suffix, episode, situation, act, outcome, outcomeText string
	scope                                                 ledger.Scope
}

type formationFixture struct {
	s                            *scenario
	index                        *formationIndex
	anchor, first, second, noise formationPair
}

func (f formationFixture) basis() []string {
	return []string{f.anchor.episode, f.anchor.outcome, f.first.episode, f.first.outcome, f.second.episode, f.second.outcome}
}

func (f formationFixture) window(suffix string) consolidation.Window {
	return consolidation.Window{JobRef: f.s.prefix + "-formation-" + suffix,
		EpisodeRefs: []string{f.anchor.episode}, OutcomeEventRefs: []string{f.anchor.outcome}}
}

func newFormationFixture(t *testing.T, factory IndexedFactory, negative bool) formationFixture {
	t.Helper()
	index := &formationIndex{}
	s := newScenario(migratedIndexedStore(t, factory, index))
	pair := func(suffix, condition string, scope ledger.Scope) formationPair {
		if scope.SessionRef == "" {
			scope.SessionRef = s.prefix + "-session-" + suffix
		}
		binding := ledger.EpisodeBinding{RunRef: s.prefix + "-run-" + suffix, SourceGroupRef: s.prefix + "-group-" + suffix, Role: ledger.RoleSituation}
		situation := s.eventInScope(suffix+"-situation", scope, ledger.ActorKindUser, condition)
		s.observe(t, suffix+"-situation", situation, binding)
		act := s.eventInScope(suffix+"-act", scope, ledger.ActorKindAgent, "I compared one tradeoff and left the decision with you.")
		binding.Role = ledger.RoleAgentAct
		r := s.observe(t, suffix+"-act", act, binding)
		if r.EpisodeRef == "" {
			t.Fatal("formation fixture did not materialize an Episode")
		}
		return formationPair{suffix: suffix, episode: r.EpisodeRef, situation: situation.Ref, act: act.Ref, scope: scope}
	}
	outcome := func(p formationPair, suffix, text string, kind ledger.ActorKind) formationPair {
		event := s.eventInScope(p.suffix+"-outcome-"+suffix, p.scope, kind, text)
		r, err := s.store.ReportOutcome(testContext(t), ledger.OutcomeReport{
			IdempotencyKey: s.prefix + "-report-" + p.suffix + "-" + suffix, Event: event,
			RunRef: s.prefix + "-run-" + p.suffix, SourceGroupRef: s.prefix + "-group-" + p.suffix,
			RelatedSourceEventRefs: []string{p.situation, p.act},
		})
		if err != nil || r.OutcomeEventRef == "" {
			t.Fatalf("formation fixture Outcome: %#v, %v", r, err)
		}
		p.outcome, p.outcomeText = r.OutcomeEventRef, text
		return p
	}
	scope := s.scope
	scope.SessionRef = ""
	first := pair("first", "I cannot choose a workshop; all options feel equally urgent.", scope)
	firstText := "That one tradeoff helped me choose."
	if negative {
		firstText = "That framing felt dismissive; it did not help me choose."
	}
	first = outcome(first, "user", firstText, ledger.ActorKindUser)
	second := outcome(pair("second", "I cannot pick a garden layout; every option feels equally urgent.", scope), "user", "The one tradeoff helped me decide.", ledger.ActorKindUser)
	noise := outcome(pair("noise", "I have already chosen the workshop; just give me its start time.", scope), "user", "The start time was accurate.", ledger.ActorKindUser)
	missing := pair("missing", "I cannot choose among equally urgent routes.", scope)
	agent := outcome(pair("agent-only", "I cannot choose among equally urgent recipes.", scope), "agent", "I think my response helped.", ledger.ActorKindAgent)
	multiple := outcome(pair("multiple", "I cannot choose among equally urgent purchases.", scope), "one", "That helped.", ledger.ActorKindUser)
	_ = outcome(multiple, "two", "Actually it did not.", ledger.ActorKindUser)
	foreignScope := scope
	foreignScope.RelationshipRef += "-foreign"
	foreign := outcome(pair("foreign", "I cannot choose among equally urgent plans.", foreignScope), "user", "That helped.", ledger.ActorKindUser)
	anchorScope := scope
	anchorScope.SessionRef = s.prefix + "-anchor-session"
	sameSession := outcome(pair("same-session", "I cannot choose among equally urgent tasks.", anchorScope), "user", "That helped.", ledger.ActorKindUser)
	anchor := outcome(pair("anchor", "I cannot choose an exhibition slot; each option feels equally urgent.", anchorScope), "user", "One tradeoff helped me decide.", ledger.ActorKindUser)
	index.scope = memoryindex.Scope{TenantRef: scope.TenantRef, AgentRef: scope.AgentRef, RelationshipRef: scope.RelationshipRef}
	index.unqualified = []memoryindex.Candidate{{Kind: memoryindex.KindEpisode, Ref: "missing-canonical-episode"},
		{Kind: memoryindex.KindEpisode, Ref: foreign.episode}, {Kind: memoryindex.KindEpisode, Ref: sameSession.episode},
		{Kind: memoryindex.KindEpisode, Ref: missing.episode}, {Kind: memoryindex.KindEpisode, Ref: agent.episode},
		{Kind: memoryindex.KindEpisode, Ref: multiple.episode}, {Kind: memoryindex.KindDisposition, Ref: noise.episode}}
	index.candidates = append(append([]memoryindex.Candidate(nil), index.unqualified...),
		memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: first.episode},
		memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: first.episode},
		memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: second.episode},
		memoryindex.Candidate{Kind: memoryindex.KindEpisode, Ref: noise.episode})
	return formationFixture{s: s, index: index, anchor: anchor, first: first, second: second, noise: noise}
}

type formationIndex struct {
	scope                   memoryindex.Scope
	mode                    string
	candidates, unqualified []memoryindex.Candidate
}

func (index *formationIndex) Search(_ context.Context, query memoryindex.Query) ([]memoryindex.Candidate, error) {
	if query.Scope != index.scope {
		return nil, errors.New("candidate query escaped exact owner")
	}
	causal := len(query.Text) >= len("USER_CONDITION\n") && query.Text[:len("USER_CONDITION\n")] == "USER_CONDITION\n"
	if (index.mode == "causal_failure" && causal) || (index.mode == "condition_failure" && !causal) {
		return nil, errors.New("controlled view failure")
	}
	if index.mode == "all_filtered" {
		return append([]memoryindex.Candidate(nil), index.unqualified...), nil
	}
	return append([]memoryindex.Candidate(nil), index.candidates...), nil
}
func (*formationIndex) Upsert(context.Context, []memoryindex.Document) error { return nil }
func (*formationIndex) Delete(context.Context, memoryindex.Scope, []memoryindex.Candidate) error {
	return nil
}
func (*formationIndex) Reset(context.Context) error { return nil }
