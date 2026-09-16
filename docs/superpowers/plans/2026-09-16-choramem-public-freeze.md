# ChoraMem Public Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the current standalone Memory Core as public `aig-dev/ChoraMem` at `v0.1.0-alpha.1`, migrate Chorai `main` to that identity, and verify a real MiniMax-backed chat.

**Architecture:** Preserve the existing local development history long enough to fast-forward the completed feature branch into its local `main`, then export only the verified project tree into a new clean-history `ChoraMem` repository. ChoraMem remains a protocol-first service; Chorai only changes module/repository identity and sibling checkout wiring, then proves the integration through package, transport, and real-chat checks.

**Tech Stack:** Go 1.25/1.26 workspace, Protobuf/gRPC/Connect, Python Worker and SDK, TypeScript SDK, PostgreSQL/MySQL 8, Docker Compose, Git/GitHub, MiniMax OpenAI-compatible API.

**Spec:** `docs/superpowers/specs/2026-09-16-choramem-public-freeze-design.md`

## Global Constraints

- Public repository is exactly `aig-dev/ChoraMem`, public, with default branch `main`.
- Public version is exactly `v0.1.0-alpha.1` and must be a GitHub pre-release.
- Public Go module is exactly `github.com/aig-dev/ChoraMem`.
- Python `memory_core`, TypeScript `@chorai/memory-core`, `memoryd`, Proto packages, RPC services, and database schema names do not change.
- No Chorai product code, environment file, credential, user data, database, model output, cache, or downloaded benchmark corpus enters ChoraMem.
- README is Chinese-first, calls the release Research Alpha, and separates implementation evidence from unproven research claims.
- Chorai uses the sibling `../ChoraMem` checkout locally and the public Go module identity in source.
- The final live chat uses the existing private MiniMax configuration without printing or copying its credential.
- No force push, history rewrite, destructive checkout cleanup, or deletion of the old `memory-core` checkout.

---

### Task 1: Freeze the public project identity

**Files:**
- Modify: `go.mod`
- Modify: `api/memory/v1/memory.proto`
- Modify: `api/memory/inference/v1/inference.proto`
- Modify: `api/memoryindex/v1/memory_index.proto`
- Modify: all Go imports and generated Go/Python descriptors containing `github.com/chorai/memory-core`
- Modify: SDK and architecture documentation containing the old public repository identity

**Interfaces:**
- Consumes: current `memory.v1`, `memory.inference.v1`, and `memoryindex.v1` contracts.
- Produces: the same runtime protocols under Go module `github.com/aig-dev/ChoraMem`.

- [x] **Step 1: Record the exact old-identity surface**

Run:

```bash
rg -l 'github\.com/chorai/memory-core' --glob '!**/.git/**' --glob '!**/.cache/**' | sort
```

Expected: only project source, generated bindings, tests, and documentation are listed; no environment or cache files.

- [x] **Step 2: Replace the public Go identity**

Mechanically replace the exact string `github.com/chorai/memory-core` with
`github.com/aig-dev/ChoraMem` in the recorded files. Do not rename Proto package names,
Python modules, TypeScript package names, binaries, services, or schemas.

- [x] **Step 3: Regenerate and validate bindings**

Run:

```bash
make proto-generate
make proto-check
go test ./internal/contract ./cmd/memoryd ./sdk/go/memorycore
```

Expected: generation drift is empty and all named Go packages pass.

- [x] **Step 4: Prove the old identity is gone from active project files**

Run:

```bash
rg -n 'github\.com/chorai/memory-core' --glob '!docs/superpowers/**' --glob '!**/.git/**' --glob '!**/.cache/**'
```

Expected: no matches. Historical specs and plans may retain the old identity as history.

- [x] **Step 5: Commit the identity migration with the README task**

Do not commit yet; Task 2 must land in the same public-freeze commit so repository identity and public explanation cannot drift.

### Task 2: Publish an evidence-bounded README

**Files:**
- Modify: `README.md`
- Modify: `docs/philosophy.zh-CN.md` only if the README exposes a terminology contradiction.
- Modify: `docs/architecture.zh-CN.md` only if the new public name or canonical-source statement is stale.

**Interfaces:**
- Consumes: the frozen service boundary and the completed evaluation reports.
- Produces: the primary public explanation and navigation entry point for ChoraMem.

- [x] **Step 1: Rewrite the README opening and information architecture**

The opening must include this meaning without stronger claims:

```text
ChoraMem is a protocol-first long-term memory service inspired by Yogacara's causal account of latent tendencies and reenactment. It does not implement consciousness or the complete eight-consciousness doctrine. Its research hypothesis is that outcome-conditioned Dispositions may add relationship-specific response continuity beyond factual recollection; that hypothesis remains unproven at framework scale.
```

The Chinese body must present, in order: status, claims/non-claims, causal loop,
concept boundaries, architecture, quick start, SDKs, storage/index providers, evidence,
limitations, roadmap, verification, and license.

- [x] **Step 2: State the exact evidence without converting it into a framework claim**

Include these bounded results:

```text
PersonaMem/OmniMemEval historical run: 1,942 / 4,999 = 38.8478%, old Core revision and MiniMax-M2.5; not a same-protocol Mem0 comparison.
PersonaMem learned_core confirmation: 34.62% vs none 32.05%, +2.56pp with 95% CI [-5.03pp, +10.60pp]; stable benefit not reproduced.
ANCHOR companion-disposition v1: formed/selected/rendered 4/4, negative false positives 0/4; 3 manipulable cases and learned_seed won 3/3; one Oracle ceiling case prevents an architecture verdict.
```

- [x] **Step 3: Verify README links and forbidden claims**

Run:

```bash
rg -n '优于 Mem0|领先主流|实现八识|已证明人格|production-ready|stable release' README.md
rg -n 'Research Alpha|v0\.1\.0-alpha\.1|Disposition|Recollection|MemoryContext|Apache' README.md
```

Expected: the first command has no claim matches; the second confirms all required boundaries are present.

- [x] **Step 4: Validate Markdown and diff integrity**

Run:

```bash
git diff --check
git diff -- README.md docs/philosophy.zh-CN.md docs/architecture.zh-CN.md
```

Expected: no whitespace error and no unsupported evaluation claim.

### Task 3: Verify, commit, and merge the standalone implementation to local main

**Files:**
- Include: every intended tracked and currently untracked project file under the standalone repository.
- Exclude: `.git/`, `.cache/`, `.env*`, virtual environments, build outputs, databases, downloaded corpora, and model caches.

**Interfaces:**
- Consumes: Tasks 1–2 and the existing adaptive Seed, MemoryIndex, Worker, SDK, and Eval implementation.
- Produces: a clean local `main` tree ready for public export.

- [x] **Step 1: Audit untracked and ignored material**

Run:

```bash
git status --short
git status --ignored --short
git ls-files --others --exclude-standard
```

Expected: every untracked non-ignored file is a source, test, spec, plan, fixed eval definition, or bounded report intended for the release.

- [x] **Step 2: Scan the publishable tree without printing secret values**

Run filename-only/content-safe checks from the standalone root:

```bash
rg -l --hidden \
  -g '!.git/**' -g '!.cache/**' -g '!**/.venv/**' -g '!**/node_modules/**' \
  '(BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|MINIMAX_API_KEY[[:space:]]*=|OPENAI_API_KEY[[:space:]]*=|ANTHROPIC_API_KEY[[:space:]]*=|JWT_SECRET[[:space:]]*=|AKIA[0-9A-Z]{16})' .
find . -type f \
  \( -name '*.sql.gz' -o -name '*.dump' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.db' \) \
  -not -path './.git/*' -not -path './.cache/*'
find . -type f -size +10M \
  -not -path './.git/*' -not -path './.cache/*' -not -path '*/.venv/*' -not -path '*/node_modules/*'
```

Expected: commands print paths only. Inspect every hit and exclude or redact it before staging;
never print matching lines or secret values.

- [x] **Step 3: Run the complete standalone release gate**

Run:

```bash
make release-gate
```

Expected: isolated export, Go, race, vet, Proto, both relational adapters, SDKs, Worker, deployment, and Eval gates all pass.

- [x] **Step 4: Stage only the audited project tree and commit**

Run:

```bash
git add -A
git status --short
git commit -m "feat: freeze ChoraMem research alpha"
```

Expected: ignored private/runtime material is absent and the commit succeeds on `codex/adaptive-seeds`.

- [x] **Step 5: Fast-forward the standalone local main**

Run:

```bash
git switch main
git merge --ff-only codex/adaptive-seeds
```

Expected: `main` points at the freeze commit with a clean working tree; the old branch remains recoverable.

### Task 4: Create and independently verify the clean-history ChoraMem checkout

**Files:**
- Create repository root: `/Users/luffylu/Documents/Chorai/ChoraMem`
- Preserve archive: `/Users/luffylu/Documents/Chorai/memory-core`

**Interfaces:**
- Consumes: the verified tree at old standalone `main`.
- Produces: the future canonical local checkout with a single public root commit.

- [ ] **Step 1: Refuse to overwrite an existing target**

Run:

```bash
test ! -e /Users/luffylu/Documents/Chorai/ChoraMem
```

Expected: success. If the path exists, inspect it and reconcile rather than deleting it.

- [ ] **Step 2: Export only committed files from standalone main**

Create the target and extract `git archive main` into it. The export must not contain
`.git`, `.cache`, `.env`, databases, model output, or ignored artifacts.

- [ ] **Step 3: Initialize public history**

Run inside the new directory:

```bash
git init -b main
git add -A
git commit -m "feat: publish ChoraMem v0.1.0-alpha.1"
```

Expected: exactly one root commit and a clean working tree.

- [ ] **Step 4: Re-run the independent release gate**

Run:

```bash
make release-gate
```

Expected: all release checks pass from the future canonical checkout.

### Task 5: Create the public GitHub repository and immutable alpha release

**Files/State:**
- Create external repository: `https://github.com/aig-dev/ChoraMem`
- Create annotated tag: `v0.1.0-alpha.1`
- Create GitHub pre-release: `v0.1.0-alpha.1`

**Interfaces:**
- Consumes: Task 4 root commit.
- Produces: the public canonical repository consumed by users and Chorai releases.

- [ ] **Step 1: Create the public repository**

Use the authenticated GitHub organization session to create `aig-dev/ChoraMem` as
public with no generated README, license, or `.gitignore`. Description:

```text
Protocol-first, Yogacara-inspired long-term memory service for agent harnesses.
```

- [ ] **Step 2: Push main without force**

Run:

```bash
git remote add origin git@github.com:aig-dev/ChoraMem.git
git push -u origin main
```

Expected: remote `main` equals the verified local root commit.

- [ ] **Step 3: Tag and publish the pre-release**

Create annotated tag `v0.1.0-alpha.1`, push it, and create a GitHub pre-release whose notes
state that the service mechanisms are implemented but cross-framework and naturalistic
companion advantages remain unproven.

- [ ] **Step 4: Verify public state and a fresh clone**

Confirm public visibility, default branch, tag target, release target, and clone URL. Clone
into a temporary directory and run:

```bash
make verify-standalone
```

Expected: the public clone passes without access to either old local repository.

### Task 6: Migrate Chorai main to the ChoraMem identity

**Files:**
- Modify: `go.work`
- Modify: `chorai-go/go.mod`
- Modify: `chorai-go/cmd/server/main.go`
- Modify: `chorai-go/internal/memorycore/*.go`
- Modify: `chorai-go/tests/integration/agent_chat_live_memory_core_integration_test.go`
- Modify: `scripts/dev/start_memory_core.sh`
- Modify: `scripts/dev/stop_memory_core.sh`
- Modify: `scripts/deploy/release.sh`
- Modify: active rules, task cards, and canonical-repository documentation returned by the exact identity search.
- Regenerate or synchronize: `Chorai_Agent/agents/memory_index/v1/memory_index_pb2.py` when descriptor identity changes.

**Interfaces:**
- Consumes: `github.com/aig-dev/ChoraMem` and sibling `/Users/luffylu/Documents/Chorai/ChoraMem`.
- Produces: unchanged Chorai chat/runtime behavior using the renamed canonical dependency.

- [ ] **Step 1: Replace only canonical repository and Go import identity**

Replace `github.com/chorai/memory-core` with `github.com/aig-dev/ChoraMem` in active source,
module/workspace files, deployment validation, and active documentation. Change default local
source paths from `../memory-core` to `../ChoraMem`. Preserve service names and deployed
filesystem paths such as `chorai-memory-core` and `/opt/chorai/memory-core` unless a check
specifically refers to the Go module identity.

- [ ] **Step 2: Synchronize Go workspace and generated descriptor**

Run:

```bash
go work sync
MEMORY_CORE_SOURCE_DIR=/Users/luffylu/Documents/Chorai/ChoraMem \
  ./Chorai_Agent/agents/memory_index/scripts/generate-bindings.sh generate
MEMORY_CORE_SOURCE_DIR=/Users/luffylu/Documents/Chorai/ChoraMem \
  ./Chorai_Agent/agents/memory_index/scripts/generate-bindings.sh --check
```

The generator owns the MemoryIndex Python descriptor; do not hand-edit serialized bytes.

- [ ] **Step 3: Run affected package and integration tests**

Run:

```bash
go test ./chorai-go/internal/memorycore ./chorai-go/cmd/server
go test ./chorai-go/tests/integration -run 'MemoryCore|ConversationAssembly|AgentChatMemory' -count=1
```

Expected: all selected packages pass and no old Go identity remains in active source.

- [ ] **Step 4: Run broader Chorai verification proportional to the module migration**

Run:

```bash
cd chorai-go
go test ./... -count=1
make verify-memory-core-deployment
make test-agent-core
```

Expected: all Go packages, deployment wiring, and Agent/Core boundary tests pass. Any failure must
be diagnosed rather than bypassed.

- [ ] **Step 5: Commit and push Chorai main only after live verification**

Keep the migration changes uncommitted until Task 7 proves the real chat. Then commit the six
pre-existing Memory contract documentation changes together with the identity migration and push
`main` without force.

### Task 7: Prove Chorai chat with MiniMax and ChoraMem

**Files/Runtime:**
- Use: `env/prod/chorai_agent.env` privately through the existing environment loader.
- Use: `scripts/dev/start_memory_core.sh` and existing backend/agent startup paths.
- Do not modify or print the private env file.

**Interfaces:**
- Consumes: ChoraMem service, Chorai backend, Python Agent, MiniMax provider.
- Produces: a concrete health/chat/memory verification record.

- [ ] **Step 1: Start ChoraMem and verify readiness**

Run the existing `make memory-core-up` flow after the sibling path migration. Confirm the configured
MySQL-backed service and Worker are healthy at their existing local endpoints.

- [ ] **Step 2: Prove MiniMax connectivity independently**

Run the existing opt-in `TestAgentPromptLiveSmokeMinimax` using the private environment loader.
Expected: a real MiniMax response succeeds without logging the key.

- [ ] **Step 3: Prove the live Memory Core transport and prompt assembly**

Run `TestAgentChatUsesLiveMemoryCoreSelectionInHarnessPrompt` with the live ChoraMem gRPC address
and isolated test database. Expected: real consolidation produces selectable memory and the Chorai
Harness receives rendered text without leaking stable refs.

- [ ] **Step 4: Execute a real chat turn**

Start or reuse the local Chorai backend and Python Agent through the profile-aware scripts. Submit
one isolated chat conversation through the production chat entry point. Require:

```text
HTTP/API success
non-empty final assistant text
MiniMax upstream success
no fatal Observe/Select/Delivery error
backend health remains 200 after the turn
```

- [ ] **Step 5: Commit and push Chorai main**

Run `git diff --check`, review the exact diff, commit the identity/docs migration, and push
`origin main` without force.

### Task 8: Final completion audit

**Evidence:**
- ChoraMem public repository settings and refs.
- ChoraMem local/public commit and tag hashes.
- Release-gate and fresh-clone output.
- Chorai `main` local/remote commit hashes.
- MiniMax prompt smoke, live memory integration, API chat, and health outputs.

- [ ] **Step 1: Verify every spec completion criterion**

Check public visibility, default branch, release/tag target, README boundaries, both release gates,
Chorai module identity, remote main equality, and the real chat result individually. Absence of an
error is not evidence; capture a direct command/API result for every item.

- [ ] **Step 2: Report exact limitations**

State that `v0.1.0-alpha.1` is an engineering freeze, not proof of cross-framework superiority or
naturalistic companionship benefit.
