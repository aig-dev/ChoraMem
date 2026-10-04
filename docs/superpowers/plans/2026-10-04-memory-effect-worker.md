# ChoraMem G3 / G5 feedback and Worker coverage

> Execute inline with the existing executing-plans workflow; the full five-direction goal remains active.

**Goal:** correct omissions in qualified feedback and independent facts, align the Worker prompt with actual write eligibility, and make rejected/no-change/stale paths diagnosable without new authority or persistent workflow.

**Architecture:** Python makes at most four bounded text-model calls in one stateless inference RPC. Core owns identity, exact Target/Basis qualification, immutable windows and transaction commit. Existing direct corrections may ADAPT an eligible Seed; ordinary chat never creates a new ADAPT Seed. No new RPC, Memory type, confidence, weight, table, or Proposal is needed for these tasks.

**Named failures:** one emitted feedback target currently suppresses every other eligible target; primary feedback normalization can erase a valid existing ADAPT correction; any Recollection block currently suppresses every other independent fact; the prompt teaches new ADAPT which execution unconditionally removes; silent safe no-op paths hide whether extraction failed or had no eligible meaning.

## Task 1: Qualified feedback coverage and actual ADAPT contract

**Files:** `worker/python/src/memory_core_worker/service.py`, `worker/python/tests/test_service.py`, `AGENTS.md`.

- [x] Start Task 1; reproduce missing second-target feedback and lost existing ADAPT with RED tests. Add a prompt contract test that no new ADAPT operation is offered.
- [x] Normalize feedback per complete target block while retaining ordinary existing ADAPT for Core validation. Recover only missing offered feedback targets in one call; never duplicate a completed target, infer an identity/link, or add qualification.
- [x] Teach only existing eligible ADAPT with an explicit lasting direct correction. Keep the new-ADAPT rejection defense and formation/feedback boundaries.
- [x] Run focused tests then the complete Worker suite, record RED/GREEN and commit.

## Task 2: Residual declarative facts

**Files:** `worker/python/src/memory_core_worker/service.py`, `worker/python/src/memory_core_worker/source_materials.py` only if required, `worker/python/tests/test_service.py`.

- [x] Reproduce a primary KEEP/TEXT/new fact swallowing another independent current-source fact.
- [x] Offer existing Recollections plus valid primary Recollection results as quoted coverage to the same single fact call. A primary revision supersedes its old coverage. Preserve chronological source, attribution, temporary scope and withdrawal rules; factual extraction cannot prescribe Agent behavior.
- [x] Filter exact duplicate bodies and cap the existing bounded list/output; ask the model to exclude already-covered meaning. Invalid primary output stays under Core qualification; no invented Target/Basis or repair of identity.
- [x] Verify new/revised/KEEP coverage, duplicates, temporal/temporary/withdrawn/quoted source safeguards and the four-call ceiling. Run Worker suite and commit.

## Task 3: Safe operational diagnostics

**Files:** `worker/python/src/memory_core_worker/service.py`; equivalent `internal/storage/mysql/consolidation.go` and `internal/storage/postgres/consolidation.go`; corresponding tests; `worker/python/src/memory_core_worker/server.py` and `cmd/memoryd/main.go` for the existing logging sinks.

- [x] RED tests distinguish no_change, no_eligible_material, rejected_format, input/output_budget, rejected_qualification and stale_snapshot using safe existing logs.
- [x] Emit static lane/status plus bounded counts; never emit source/model text, credentials, or model rationales. Keep failed Worker retry and safe no-op commit semantics unchanged; existing scheduler OnError continues to expose stale retry.
- [x] Exercise controlled Worker and shared real-database contracts for both Stores. Run relevant tests and commit.

## Task 4: Contract evidence and independent review

- [x] Synchronize architecture, Worker docs and authoritative goal with exact causal meaning, calls and limitations. Trace requirements to named tests and actual DB evidence.
- [x] Run Worker/full Core suites, relevant package/lint checks and shared DB integration; no external-provider or natural-distribution uplift claim from fixture results.
- [x] Commit and dispatch one fresh whole-plan reviewer. Re-grade findings and each declined-to-judge item; fix Critical/Important in one RED-to-GREEN pass, retain every Ruling and cost. Keep G4/common held-out behavior validation open until independently proven.

## Self-review

Task 1 preserves direct correction and covers every eligible feedback target without fabricating association; Task 2 covers residual fact meaning without another model call; Task 3 observes existing outcomes without adding authority; Task 4 supplies evidence and review. Unknown actor/outcome relationships remain unknown. The neuroscience-motivated separation of trace, accessibility and use is diagnostic; no brain-shaped module or inferred success metric is introduced.

## Execution evidence

Task 1: three RED regressions proved missing second-target feedback, erased existing ADAPT, and an impossible new-ADAPT prompt contract. GREEN normalizes feedback per target block, preserves the direct existing correction, recovers only missing eligible targets, and rejects a recovery attempt to repeat a completed target. Mixed primary Recollection/feedback keeps the fact and binds feedback only to offered causal refs. Full Worker suite:142 passed,11 optional live cases skipped; no external-provider effect claim. Core keeps exact Target/Basis/identity/commit qualification.

Task 2: three RED cases proved new/KEEP/revised Recollection swallowing an independent fact. GREEN always considers typed current material when NEW_RECOLLECTION is allowed; valid primary results provide quoted transient duplicate coverage and a revision replaces only its prior coverage. Exact duplicates are filtered before binding. Application/Target/current Episode Basis controls prevent invalid primary blocks from becoming coverage. Four-lane regression makes exactly four calls and retains an independent residual fact; formation KEEP fixture now returns NO_MEMORY from the residual call. Full Worker suite:147 passed,11 optional live cases skipped. Chronological correction/withdrawal/temporary/source attribution rules remain in the fact prompt and typed-material validator; semantic faithfulness/paraphrase dedup still need the full goal's live held-out checks.

Task 3: behavioral RED proved absent safe diagnostic classes in Worker and real MySQL contracts, an oversized raw feedback response being normalized into an accepted change, and the runtime retry sink echoing a private error sentinel. GREEN distinguishes normal no-change, no eligible material, format/qualification rejection, input/output budgets, Worker failure and stale snapshots using fixed strings and counts only. Raw feedback is bounded before normalization. Model failure remains a failed retryable RPC with a static message; Core keeps original errors for retry while the runtime log class omits their text. Worker155 passed,11 optional live skipped; uncached full Core Go passed; PostgreSQL and MySQL complete migrated Store suites plus running-memoryd public RPC tests passed. Runtime retry-sink unit/full-Core verification was run after those DB scripts; the Task4 gate will exercise the final runtime revision. No schema or protocol change.

Requirement-to-evidence trace (controlled fixtures do not establish model semantic benefit):

| Requirement | Authoritative test / artifact |
|---|---|
| Every eligible feedback target, no completed-target repetition | `test_worker_recovers_each_missing_feedback_target_once`, `test_worker_feedback_recovery_cannot_repeat_an_already_processed_target` |
| Existing ADAPT survives, impossible new route absent | `test_worker_preserves_existing_direct_adapt_when_feedback_is_also_eligible`, `test_worker_prompt_offers_only_existing_adaptation` |
| New / KEEP / revision retain independent facts, exact duplicates excluded | `test_primary_recollection_keeps_residual_fact_and_filters_exact_duplicate`, `test_primary_keep_does_not_suppress_an_independent_current_fact`, `test_primary_existing_revision_replaces_old_coverage_and_keeps_residual_fact` |
| Coverage has no new qualification, fixed call ceiling | `test_invalid_primary_fact_does_not_gain_duplicate_coverage`, `test_worker_all_four_lanes_keep_residual_fact_with_fixed_call_ceiling` |
| Safe outcome diagnostics, raw feedback budget, RPC retry | `test_worker_reports_safe_outcome_class_without_source_or_credentials`, `test_oversized_feedback_is_rejected_before_normalization_can_hide_its_budget`, `test_worker_grpc_failure_keeps_retry_and_never_exposes_provider_text` |
| Both Stores preserve safe no-op and stale transaction semantics | shared `consolidation_safe_diagnostics_distinguish_noop_rejection_and_budget`, existing stale concurrent-state contract with safe log assertion; complete PostgreSQL/MySQL runtime scripts |
| Runtime sink omits raw error payload | `TestMemoryRetryLogDoesNotEchoSourceOrProviderErrorPayload` |

The eleven opt-in primary-prompt cases call the model directly; they do not prove full-service residual extraction, semantic dedup, per-target causal attribution, or committed behavioral benefit. Those remain in the full goal's live held-out gate. No schema/protocol changes in this plan.

Task4 verification at26f03f8 plus documentation only: uncached full Core Go and go vet passed; verify-worker passed155 tests/11 optional live skipped, exact generated bindings, clean wheel and production Go-to-Python bridge. PostgreSQL runtime script passed25.806s Store/0.323s RPC; MySQL41.531s Store/0.442s RPC. These scripts migrate fresh databases, build/start the final memoryd and check readiness before public RPC. Independent review remains pending.

Final independent review at90ab07f: Critical0/Important1/Minor1. Executor re-graded aggregate formation-budget omission and the declined cross-target recovery failure as Important. One correction pass:10 behavior regressions RED→GREEN, complete Worker165 passed/11 optional primary-live skipped; final verify-worker passed exact bindings, clean wheel and production Go-to-Python smoke. Inline TEXT/ADAPT matches Core's existing syntax, coverage excludes repeated exact body, recovery cannot borrow another Target's causal pair, aggregate omission logs output_budget rather than produced. No deferred minors, no re-review. All17 Rulings and costs plus every declined scope are retained in [durable review](../../evidence/2026-10-05-memory-effect-worker-review.md). G4/common effect remain open.
