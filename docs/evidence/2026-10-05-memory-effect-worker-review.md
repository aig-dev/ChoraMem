# ChoraMem G3/G5 Worker implementation review

Review date:2026-10-05. Full five-direction goal remains active.

One fresh read-only reviewer examined the plan and full branch package82a657c..90ab07f, focusing on dc9d48e/528dda6/26f03f8. Original findings:Critical0,Important1,Minor1. Reviewer independently reran155 Worker tests with11 optional primary-model cases skipped; other execution evidence is the author's recorded command output.

The inline ADAPT/TEXT compatibility finding was Important: Core supports both inline and separate-body forms, while the new normalization/coverage path lost legal inline corrections and exact coverage. Aggregate formation overflow was re-graded from Minor to Important because it omitted a required budget diagnostic while reporting produced. A declined cross-target recovery behavior was re-graded Important because the global feedback map could attach another target's causal pair and cause Core to reject valid primary work.

The single correction pass reproduced all three classes with10 behavioral RED cases, then GREEN. Inline support copies Core's exact ASCII-space TEXT/ADAPT compatibility; it adds no new grammar/qualification. Feedback normalization uses only the current Target's offered markers and never substitutes a guessed pair. Formation logs output_budget when aggregate append omits its text, with no produced class for that omission. Complete Worker suite165 passed,11 optional primary-live cases skipped. Core's atomic format/qualification/stale/commit rules remain unchanged. No re-review was dispatched.

The unchanged Go implementation passed uncached full Core/vet and both disposable database/runtime scripts during Task4:PostgreSQL25.806s Store/0.323s RPC;MySQL41.531s Store/0.442s RPC. The final Python correction is additionally checked through the clean-wheel/production Go-to-Python verification gate; exact result is recorded in the plan execution evidence. These are engineering results, not external-model semantic or behavioral benefit claims.

All decisions, in ledger order, with their cost if wrong:

1. Task1 Ruling: preserve a primary existing ADAPT block unchanged rather than feed it through feedback-only normalization — direct correction has a different existing qualification, validated by Core, and can share a Target with feedback. Cost if wrong: malformed/unqualified ADAPT remains Core's format/qualification rejection, not silently converted to a feedback operation.

2. Task2 Ruling: valid primary Recollection results are transient duplicate coverage, not committed truth; a valid revision replaces its old text only in that coverage — otherwise unchanged old coverage can suppress extraction of a changed fact and a whole-window off-switch loses independent facts. Cost if wrong: semantic coverage and faithfulness still rely on the same bounded model call and must be measured live; exact duplicates and structural qualification are checked deterministically, Core remains sole commit authority.

3. Task3 Ruling: expose only fixed status/lane/count diagnostics and replace provider failure text at the Worker RPC boundary with a static retryable error — raw exception messages and the existing runtime OnError sink can expose source or credentials while logging a safe class changes no commit/retry authority. Cost if wrong: operators lose raw provider/DB error detail and must use the safe stage/class and controlled reproduction; original Core errors still drive retry.

4. Task3 Ruling: reject raw feedback exceeding the existing64KiB response cap before normalization — repeated BASIS separators could otherwise shrink an over-budget response into an accepted operation, as the RED test proved. Cost if wrong: an oversized but compressible provider response no longer contributes feedback in that attempt; the accepted primary text remains and no refs or qualification are repaired.

5. Final: Ruling: re-grade aggregate formation overflow from Minor to Important and fix it in the single correction pass — the stated diagnostic contract must distinguish a budget-caused omission from produced output — Cost if wrong: one added safe status branch must stay aligned with the aggregate append guard.

6. Final: Ruling: leave natural-language ADAPT/feedback polarity/fact correctness to full-service live semantic acceptance — controlled routing tests do not establish what the configured model means — Cost if wrong: engineering success can coexist with wrong semantic decisions until the live gate is completed.

7. Final: Ruling: keep paraphrase deduplication and meaningful residual coverage as live requirements — exact-string filtering is deterministic while semantic coverage remains the same bounded model call — Cost if wrong: near-duplicate memories or omitted meanings remain possible and cannot be claimed fixed from fixtures.

8. Final: Ruling: verify temporal scope/withdrawal/quotation/attribution with real model output later — existing source validator and prompt safeguards are preserved, not behavioral proof — Cost if wrong: a shape-valid result may still misrepresent source/time/ownership.

9. Final: Ruling: preserve existing forgetting-constraint and non-prescriptive-fact policy without inventing a new forget workflow — this plan fixes coverage/routing and the full goal must still test actual policy compliance — Cost if wrong: incorrect constraints or response-prescribing facts may reduce quality until detected by live cases.

10. Final: Ruling: keep G3 late association and production adjacency semantics open under upstream trusted linkage — Worker cannot infer missing identities or create a causal association from text — Cost if wrong: unlinked late feedback stays excluded and adjacent but unrelated text must not be treated as benefit.

11. Final: Ruling: execute G4 in the separate candidate diagnosis plan, now committed as c7d6a04 — no G4 mechanism or semantic candidate claim belongs to these G3/G5 commits — Cost if wrong: candidate loss/mixed conditions can still undermine effect despite green Worker tests.

12. Final: Ruling: leave common held-out benefit/Oracle separation/correction latency/natural-distribution uplift open in the full goal — primary-prompt tests were skipped and controlled contracts are not behavioral evidence — Cost if wrong: no memory-effect superiority or full goal completion can be inferred from this engineering review.

13. Final: Ruling: retain the earlier independent G1/G2 review and exact evidence without claiming this reviewer reran them — the reviewer inspected those boundaries as context and only reran this Worker suite — Cost if wrong: cross-component behavior still needs the common gate; repeated unchanged implementation review adds no new evidence.

14. Final: Ruling: use author-observed full Core/vet/both-DB-runtime/clean-wheel bridge results, distinguish them from reviewer execution — the reviewer only independently reran Worker tests, and the actual task4 command logs are authoritative for the other gates — Cost if wrong: database/runtime/package correctness has execution evidence but not a second independent rerun by that reviewer.

15. Final: Ruling: keep malformed/duplicate existing-Target batches subject to Core atomic rejection; provisional per-block coverage is not committed truth — this plan adds no whole-batch parser or partial-commit authority merely to salvage an invalid response — Cost if wrong: invalid primary batches may waste a residual call or suppress candidate facts inside a batch that will be rejected as a whole.

16. Final: Ruling: re-grade cross-target recovery mistakes as Important and filter only the exact Target markers already offered by Core — RED proved that another Target's Episode/Outcome could poison an otherwise valid primary result; local filtering neither guesses association nor grants write eligibility — Cost if wrong: wrong-ref recovery is omitted in that attempt; Core still validates the complete resulting batch and missing feedback may need later evidence.

17. Final: Ruling: keep provider retry costs/latency/concurrency outside this engineering effect claim — fixed call count and RPC retry behavior are tested but no external load/provider experiment ran — Cost if wrong: deployment latency and provider cost can still be material and need measured evidence.

Deferred minors:none. The budget omission was re-graded Important and fixed.

G4 candidate diagnosis and the common held-out/Oracle/Delivery/correction-latency gate remain required. Semantic fidelity, paraphrase coverage, late association and provider performance were not silently closed by this review.
