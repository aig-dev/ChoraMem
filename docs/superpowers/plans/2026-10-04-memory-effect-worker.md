# ChoraMem G3 / G5 feedback and Worker coverage

> Execute inline with the existing executing-plans workflow; the full five-direction goal remains active.

**Goal:** correct omissions in qualified feedback and independent facts, align the Worker prompt with actual write eligibility, and make rejected/no-change/stale paths diagnosable without new authority or persistent workflow.

**Architecture:** Python makes at most four bounded text-model calls in one stateless inference RPC. Core owns identity, exact Target/Basis qualification, immutable windows and transaction commit. Existing direct corrections may ADAPT an eligible Seed; ordinary chat never creates a new ADAPT Seed. No new RPC, Memory type, confidence, weight, table, or Proposal is needed for these tasks.

**Named failures:** one emitted feedback target currently suppresses every other eligible target; primary feedback normalization can erase a valid existing ADAPT correction; any Recollection block currently suppresses every other independent fact; the prompt teaches new ADAPT which execution unconditionally removes; silent safe no-op paths hide whether extraction failed or had no eligible meaning.

## Task 1: Qualified feedback coverage and actual ADAPT contract

**Files:** `worker/python/src/memory_core_worker/service.py`, `worker/python/tests/test_service.py`, `AGENTS.md`.

- [ ] Start Task 1; reproduce missing second-target feedback and lost existing ADAPT with RED tests. Add a prompt contract test that no new ADAPT operation is offered.
- [ ] Normalize feedback per complete target block while retaining ordinary existing ADAPT for Core validation. Recover only missing offered feedback targets in one call; never duplicate a completed target, infer an identity/link, or add qualification.
- [ ] Teach only existing eligible ADAPT with an explicit lasting direct correction. Keep the new-ADAPT rejection defense and formation/feedback boundaries.
- [ ] Run focused tests then the complete Worker suite, record RED/GREEN and commit.

## Task 2: Residual declarative facts

**Files:** `worker/python/src/memory_core_worker/service.py`, `worker/python/src/memory_core_worker/source_materials.py` only if required, `worker/python/tests/test_service.py`.

- [ ] Reproduce a primary KEEP/TEXT/new fact swallowing another independent current-source fact.
- [ ] Offer existing Recollections plus valid primary Recollection results as quoted coverage to the same single fact call. A primary revision supersedes its old coverage. Preserve chronological source, attribution, temporary scope and withdrawal rules; factual extraction cannot prescribe Agent behavior.
- [ ] Filter exact duplicate bodies and cap the existing bounded list/output; ask the model to exclude already-covered meaning. Invalid primary output stays under Core qualification; no invented Target/Basis or repair of identity.
- [ ] Verify new/revised/KEEP coverage, duplicates, temporal/temporary/withdrawn/quoted source safeguards and the four-call ceiling. Run Worker suite and commit.

## Task 3: Safe operational diagnostics

**Files:** `worker/python/src/memory_core_worker/service.py`; equivalent `internal/storage/mysql/consolidation.go` and `internal/storage/postgres/consolidation.go`; corresponding tests.

- [ ] RED tests distinguish no_change, no_eligible_material, rejected_format, input/output_budget, rejected_qualification and stale_snapshot using safe existing logs.
- [ ] Emit static lane/status plus bounded counts; never emit source/model text, credentials, or model rationales. Keep failed Worker retry and safe no-op commit semantics unchanged; existing scheduler OnError continues to expose stale retry.
- [ ] Exercise controlled Worker and shared real-database contracts for both Stores. Run relevant tests and commit.

## Task 4: Contract evidence and independent review

- [ ] Synchronize architecture, Worker docs and authoritative goal with exact causal meaning, calls and limitations. Trace requirements to named tests and actual DB evidence.
- [ ] Run Worker/full Core suites, relevant package/lint checks and shared DB integration; no external-provider or natural-distribution uplift claim from fixture results.
- [ ] Commit and dispatch one fresh whole-plan reviewer. Re-grade findings and each declined-to-judge item; fix Critical/Important in one RED-to-GREEN pass, retain every Ruling and cost. Keep G4/common held-out behavior validation open until independently proven.

## Self-review

Task 1 preserves direct correction and covers every eligible feedback target without fabricating association; Task 2 covers residual fact meaning without another model call; Task 3 observes existing outcomes without adding authority; Task 4 supplies evidence and review. Unknown actor/outcome relationships remain unknown. The neuroscience-motivated separation of trace, accessibility and use is diagnostic; no brain-shaped module or inferred success metric is introduced.

## Execution evidence

Pending.
