# Memory Effect Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this bounded sub-plan task-by-task. The parent goal remains active for G2–G5 and behavior validation.

**Goal:** Prevent unrelated inhibition evidence from suppressing a semantically recalled Disposition while preserving relevant support/inhibition competition.

**Architecture:** Keep pure deterministic selection, existing Ranker and Policy, exact canonical Basis and indexed candidate handling. Qualify contextual inhibition before comparison; persist no new state.

**Tech Stack:** Go, existing selection unit tests and module checks.

**Spec:** `docs/superpowers/specs/2026-09-30-memory-effect-optimization-goal.md`, G1.

## Global Constraints

- No new Memory forms, tables, RPC fields, confidence or strength.
- No generative AI in online Select.
- Index order remains a transient candidate fact; live canonical Basis remains required.
- Relevant inhibition still wins when support does not strictly beat it.
- Main checkout stays untouched; execute in the linked Core worktree.

## Review Focus

- Semantic-only support with unrelated zero-score inhibition must remain selected.
- Below-threshold positive lexical noise must not become a contextual veto.
- At-threshold relevant inhibition must still veto weaker support.
- MinimumScore zero must not make a zero-score non-match inhibitory.
- Malformed, NaN or stale anchors must not bypass canonical support gating.

### Task 1: Qualify inhibition relevance

**Files:**
- Modify: `internal/core/selection/selection.go`
- Modify: `internal/core/selection/ranker.go`
- Test: `internal/core/selection/selection_test.go`
- Update: `docs/architecture.zh-CN.md`, parent goal execution record

**Interfaces:**
- Consumes: `Select([]Query, []Candidate, Ranker, Policy) ([]Activation, error)` and existing `Policy.MinimumScore`.
- Produces: unchanged public signatures; only positive inhibition scores at or above the contextual minimum may compete.

- [ ] Write `TestSelectIgnoresUnrelatedInhibitionForSemanticDisposition` with semantic hit, zero lexical support, valid unrelated inhibition and expected one activation. Cover both zero and below-minimum inhibition and a zero policy minimum. Include a relevant at-minimum inhibition case expecting none.
- [ ] Run `go test ./internal/core/selection -run TestSelectIgnoresUnrelatedInhibitionForSemanticDisposition -count=1 -v`; expected RED because zero/below-minimum inhibition currently vetoes.
- [ ] Add the positive-and-minimum relevance requirement before support/inhibition comparison. Increment the default policy ref to identify changed selection behavior.
- [ ] Run `go test ./internal/core/selection -count=1`; expected PASS, including existing tie/strong-inhibition cases.
- [ ] Document the relevance gate and update G1's actual evidence record; run `gofmt` and `git diff --check`.
- [ ] Commit this bounded change and perform fresh-context review under executing-plans. Do not mark the five-direction goal complete.

## Self-review

G1 scope and existing policy semantics are sufficient. This task does not fix the broader semantic ranking quality or claim behavior uplift; those stay in G4 and the shared effect gate. No shared interfaces with later tasks change.
