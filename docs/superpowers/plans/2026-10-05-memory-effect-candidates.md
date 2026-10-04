# ChoraMem G4 candidate diagnosis implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. The user already authorized the complete five-direction goal. One fresh whole-plan review follows all tasks; no per-task subagent.

**Goal:** prove which eligible cross-task causal experiences reach focused formation, retain relevant conflict/failure evidence, and repair only a reproduced loss before interpreting learned Disposition behavior.

**Architecture:** keep the current complete-causal/Situation ordinal rank fusion, exact-owner relational rehydration and one anchor plus at most two historical pairs. Semantic condition and response judgment belongs to the same Python Worker. Candidate presence, semantic agreement, exact committed Basis and future applicability are separate checks. No new Memory type, score, table, RPC, durable Proposal or classification job.

**Tech Stack:** existing Go CoreStore contracts, Python reference Worker/eval, disposable MySQL/PostgreSQL, existing eval-only Chroma1.5.5/MiniLM provider.

**Spec:** `docs/superpowers/specs/2026-09-30-memory-effect-optimization-goal.md` G4 plus causal invariants. The common four-arm behavior gate remains a separate plan; controlled candidate contracts cannot close it.

## Global Constraints

- Keep original workspaces unchanged; work in the attached `codex/memory-effect-optimizations` worktrees.
- Canonical Core owns exact owner, current anchor, independent session/source, unique non-Agent Outcome, Target/Basis, immutable snapshot and commit.
- Provider returns ordered kind/ref only; no similarity/confidence/weight crosses the public port or becomes authority.
- Current group remains anchor plus at most two cross-session historical complete pairs until an observed recall loss justifies a recorded change.
- Failed primary causal view retains the original canonical fallback; failed Situation view preserves causal order. Indexed but unqualified candidates leave anchor-only focused no-op, never broad mixed-window formation.
- A relevant failed/conflicting Outcome is evidence, never a reason to remove that candidate in order to obtain a positive Seed. Different-condition exceptions are not globally negative learned authority.
- Every focused Basis equals all and only the Core-qualified pairs the model actually saw, in adjacent Episode/Outcome order; arbitrary active Seeds do not count as target formation.
- Model execution stays in Python; online Select calls no generative model. Formation/feedback/fact work stays within the existing four-call RPC ceiling.
- No schema or API change is planned. If diagnosis demands one, name the causal failure and update this plan before implementing it.

## Review Focus

- Two successful neighbors and a semantically closer failed experience compete for two historical slots: report the loss honestly, never inspect Outcome positivity when ranking.
- One physical source occurs in different sessions: source independence remains canonical, not inferred from text/session labels.
- Different-condition exceptions share topic vocabulary with the anchor: neither mixed conditional formation nor global suppression is justified by topic alone.
- Provider repeats, returns stale/cross-owner/invalid refs, or fails one view: no broad-window recovery grants formation authority.
- A Seed exists for a different strategy: target formation/selection requires reading its actual tendency and exact Basis, not counting any active row.

## Task 1: Shared candidate qualification and failure-preservation contracts

**Files:** Create `internal/storage/contracttest/formation_candidates.go`; modify `internal/storage/mysql/contract_integration_test.go` and `internal/storage/postgres/contract_integration_test.go` to call it. Reuse `IndexedFactory`, `newScenario`, typed Observe/Outcome and callback Worker. Do not alter unrelated contract fixtures.

**Interfaces:** consume existing `storage.CoreStore`, `memoryindex.Index`, `consolidation.DispositionFormationBasis`; expose only test helper `RunFormationCandidates(t *testing.T, factory IndexedFactory)`.

- [x] Build one real-store cohort containing complete same-condition experiences in different tasks, a same-condition negative Outcome, same-topic/different-condition noise, same-session/cross-owner/invalid/missing/multiple/Agent Outcome controls. Use a test-only index whose ordered results are explicit; this is a qualification test, not proof of semantic retrieval.
- [x] Assert exactly the two indexed eligible historical pairs and current anchor reach Worker; negative Outcome text is preserved verbatim and never removed by positivity. The Worker receives no unindexed sibling solely because it shares the window.
- [x] Assert partial/missing/extra/duplicate focused Basis cannot commit and exact visible Basis can; preserve no-op and current-evidence constraints in both Stores.
- [x] Exercise causal-view failure, Situation-view failure and all-indexed-candidates-filtered behavior through public Store methods. If current code passes, record existing coverage; do not manufacture a defect/RED. For any actual failure, retain failing output then fix the smallest shared rule/equivalent Store code and rerun it GREEN.
- [x] Run both DB scripts, changed packages and full Go; record test names/results and commit.

## Task 2: Frozen semantic candidate probe, no benchmark tuning

**Files:** Create `eval/cases/memory-effect-candidates-v1.json`; create `eval/python/src/memory_core_eval/candidate_diagnostic.py` and `eval/python/tests/test_candidate_diagnostic.py`; reuse `local_memory_index.py`, generated MemoryIndex bindings, `disposition_materials.py`, `InferenceService` and existing `ChatTextModel` model port. Runtime outputs go in this plan's ignored workspace.

**Interfaces:** `load_candidate_probe(path)` returns validated frozen cases; diagnostic records ordered candidate refs, visible pair/source text, expected condition groups (scorer-only), exact Basis and completed model calls. The scorer never supplies group labels/Oracle to retrieval or formation.

Observed Task2 repair scope also includes `worker/python/src/memory_core_worker/service.py` and `worker/python/tests/test_service.py`: the real primary model omitted the TEXT opcode while the focused lane produced a correct tendency; the merged batch was rejected by Core. A narrow missing-operation guard discards the whole malformed primary lane before independently bound lanes are appended. It does not infer an opcode, parse/qualify the whole batch, or change Core's atomic submission. Candidate bounds/ranking remain unchanged. Two captured-shape REDs→GREEN; Worker167 pass/11 optional skips. This is a newly observed G5 transport loss found during G4 diagnosis.

- [x] Freeze before any external call a finite cohort of same-condition/cross-task paraphrases, same-topic/different-condition cases, relevant failure/conflict and irrelevant exception controls. Record dataset, harness, model, budget, Core/Worker/provider hashes; do not reuse PersonaMem labels for tuning.
- [x] RED tests reject invalid/missing input identity, incomplete call/result sets and 'any active Seed' target scoring; test scorer-label isolation and exact visible-pair/Basis comparison. Implement only the diagnostic, not a new runtime classifier.
- [x] Use the existing real Chroma/MiniLM provider and production reference Worker with the configured model. Record candidate recall separately from model semantic precision. Call a valid typed request only after Core Store qualification evidence exists; direct prompt results alone do not prove transaction commit.
- [x] Read actual tendency/body and negative controls; distinguish candidate loss, mixed group, Worker semantic failure and Harness incompleteness. When current bounds already pass, retain them.
- [x] A reproduced loss gets one minimal causal repair with RED→GREEN and both Store equivalents; do not blindly increase K or select only successful outcomes. Otherwise commit frozen data/diagnostic/evidence with no production mechanism change.

## Task 3: Exact evidence, final verification and one independent review

**Files:** authoritative goal, this plan, `docs/memory-index.zh-CN.md` only if semantics changed, and `eval/reports/2026-10-05-memory-effect-candidates.zh-CN.md` (use actual completion date if later).

- [x] Map every G4 engineering assertion to actual shared Store, Worker and real-provider evidence. Record remaining semantic/effect uncertainty, failure denominators and hashes.
- [x] Run complete relevant suites, both database/runtime scripts and fixed Worker call-budget regression after any repair. Preserve existing single-view fallback and exact owner/source/Outcome rejection.
- [x] Commit; dispatch one fresh whole-plan reviewer. Re-grade every finding and each declined-to-judge item. Critical/Important get one test-backed RED→GREEN fix pass and green suite; record all Rulings/costs and deferred minors durably.
- [x] Update G4 status only to the scope proven. The full goal remains active until the separate common held-out four-arm, actual Delivery and correction-latency requirements pass their audit.

Task1 evidence: the current production candidate rules already passed all new controlled qualification cases on first execution; there is no claimed RED or production repair. Real MySQL focused suite3.289s verified negative Outcome preservation, unqualified candidates filtered, exact/missing/extra/duplicate/anchor-only Basis and both original view fallbacks/anchor-only no-op. Full Go and vet passed. Complete DB/runtime scripts passed PostgreSQL25.185s Store/0.331s RPC, MySQL41.709s Store/0.470s RPC. These scripted orders prove canonical qualification, not semantic recall/condition precision; Task2 remains required.

Task2 pre-call freeze: `memory-effect-candidates-v1.json` SHA256 `cde6bba9dc59b055a2b3c25860c81618683b2a91e0ab97750d63516f902d84b2`, six synthetic diagnostic cases, two positive/four negative; expectations are scorer-only and excluded from replay inputs. Diagnostic tests19 pass; complete eval suite424 pass/2 optional skips. The initial missing-module collection failure establishes only new diagnostic capability. Two later behavioral REDs reproduced rejection of valid empty/no-target evidence and acceptance of an invalid single-pair target; both are GREEN. No live-model result or production defect is claimed yet. Runtime controls/hashes and actual canonical observations will be frozen separately before external calls.

Task2 complete evidence: production Core/MySQL+Chroma1.5.5+reference InferenceService completed the frozen six cases:2/2 exact target bodies formed,0/4 negative Seeds, positive final groups all3/3 supporting sessions, exact origin-job visible Basis equals committed Episode/Outcome refs.54/54 model calls stop-completed over20/20 RPCs,0 retries/failures,2–3 calls each. Read-only analysis is separate from replay; model alias/server build and synthetic scope limits are explicit. See the G4 report. No candidate-bound/ranking repair; only actual missing primary TEXT opcode loss repaired in bee69d3 (2 RED→GREEN;167 Worker pass/11 optional skips).

Task3 complete: one fresh whole-plan review126 independent tests and raw54call/20RPC/Basis audit,0Critical/0Important/1Minor. Executor regraded scorer completenessImportant and fixed it in one5RED→GREEN pass; other six declined items each ruled with costs. Supplementary pre-frozen crowdedc7 completed11calls/4RPC and kept failed evidence/noSeed; canonical-source and existing Chorai physical-source guards verified without changing Source identity. FinalPython596pass/13optional skips, fullCore/vet, PostgreSQL25.470s/0.336s andMySQL43.684s/0.449s pass. All12Rulings and limits are durable in docs/evidence/2026-10-05-memory-effect-candidates-review.md. No second reviewer; full goal stays active for common effect/G3/G5 semantic gates.
