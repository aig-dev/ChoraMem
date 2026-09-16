# Seed-essential Delayed Eval v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline execution; use `superpowers:subagent-driven-development` only when the user explicitly requests delegated execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现并运行八实例、四臂、双向成对判分的 Seed-essential delayed dev eval，输出架构、学习工程或无可检测增益的唯一结论。

**Architecture:** 版本化 JSON 将回答输入与 scorer label 分离；Runner 对三个跨 session 的完整因果链逐次巩固，然后一次 Select 派生 `rag / learned_seed / oracle_seed / anti_seed`。Effect 层只做等预算渲染、严格单 token 成对判分和冻结终局门；CLI 复用现有 live Core、Worker、Index 与模型缓存。

**Tech Stack:** Python 3.11+、pytest/pytest-asyncio、Memory Core Python SDK/gRPC、MySQL 8、Chroma、MiniMax-M2.5。

**Spec:** `docs/superpowers/specs/2026-09-14-seed-essential-delayed-eval-design.md`

## Global Constraints

- 只修改 `eval/**`、文档、`Makefile` 和 `scripts/verify-eval.sh`；不修改 `internal/**`、`worker/**`、`api/**`、`gen/**`。
- 四臂共享 2,048-token memory cap、相同 Persona、请求、模型、temperature 与回答预算；Episode evidence 为 0。
- 每个实例三个独立 session，且每个 session 都有 Situation、AgentAct、非 Agent Outcome，并在下一 session 前 settle。
- scorer label 只在四臂回答全部完成后进入双向 Judge。
- 不提交、不清理用户现有脏工作区；所有新增行为遵循 RED → GREEN。

---

### Task 1: 冻结 Seed-essential 数据合同

**Files:**
- Create: `eval/seed-essential-delayed-v1.json`
- Create: `eval/python/src/memory_core_eval/delayed_seed_data.py`
- Test: `eval/python/tests/test_delayed_seed_data.py`

**Interfaces:**
- Produces: `DelayedSeedDataset(instances, labels, source_sha256)`
- Produces: `load_delayed_seed(path: Path) -> DelayedSeedDataset`

- [x] 写失败测试：要求 4 个 pattern、8 个独立 instance、每例 3 个完整 session、每个 pattern 2 个 probe；回答输入对象不携带 rubric/oracle/anti。
- [x] 运行 `cd eval/python && pytest -q tests/test_delayed_seed_data.py`，确认因模块缺失而 RED。
- [x] 实现最小 dataclass、严格 loader 和版本化 JSON。
- [x] 重跑同一测试确认 GREEN。

### Task 2: 四臂等预算渲染与双向终局门

**Files:**
- Create: `eval/python/src/memory_core_eval/delayed_seed_effect.py`
- Test: `eval/python/tests/test_delayed_seed_effect.py`

**Interfaces:**
- Produces: `DelayedSeedMode(RAG, LEARNED_SEED, ORACLE_SEED, ANTI_SEED)`
- Produces: `build_delayed_contexts(selected, label, max_tokens, token_count)`
- Produces: `build_answer_prompt(...)`, `build_pairwise_prompt(...)`, `parse_pairwise_token(...)`
- Produces: `pair_consensus(...)`, `summarize_delayed_seed(results, labels)`

- [x] 写失败测试：四臂从一份 MemoryContext 派生；RAG/Oracle/Anti 不含 Learned Seed；四臂 cap 相同且 Oracle/Anti 真正渲染。
- [x] 写失败测试：Judge 看不到 mode/memory refs；A/B 交换后映射到同一逻辑 winner；位置冲突记 inconsistent。
- [x] 写失败测试：用手工 8 例分别触发 `preliminary_learned_advantage`、`learning_or_selection_bottleneck`、`no_detectable_architecture_advantage` 和 manipulation failure。
- [x] 运行 `pytest -q tests/test_delayed_seed_effect.py` 确认 RED。
- [x] 实现最小 Effect 层并重跑确认 GREEN。

### Task 3: 跨 session 因果回放和标签后置

**Files:**
- Create: `eval/python/src/memory_core_eval/delayed_seed_runner.py`
- Test: `eval/python/tests/test_delayed_seed_runner.py`

**Interfaces:**
- Produces: `prepare_delayed_instance(...) -> PreparedDelayedInstance`
- Produces: `generate_delayed_answers(...) -> dict`
- Produces: `judge_delayed_answers(generated, label, judge_model) -> dict`
- Produces: `freeze_result(...)`, `load_frozen_result(...)`

- [x] 写失败测试：三个 session 各自写完整因果链并 settle，最后 unbound probe 只 Select 一次且不写 Delivery/AgentAct/Outcome。
- [x] 写失败测试：四个 answer call 全部发生在第一个 Judge call 之前；Learned/RAG prompt 不含 scorer label。
- [x] 写失败测试：每个比较恰好正反两次、token 严格解析、结果原子冻结并拒绝漂移。
- [x] 运行 `pytest -q tests/test_delayed_seed_runner.py` 确认 RED。
- [x] 实现 Runner 并重跑确认 GREEN。

### Task 4: 可复现 CLI、smoke 与开源入口

**Files:**
- Create: `eval/python/src/memory_core_eval/delayed_seed_cli.py`
- Test: `eval/python/tests/test_delayed_seed_cli.py`
- Create: `eval/seed-essential-delayed.zh-CN.md`
- Modify: `eval/python/pyproject.toml`
- Modify: `eval/README.zh-CN.md`
- Modify: `README.md`
- Modify: `Makefile`
- Modify: `scripts/verify-eval.sh`

**Interfaces:**
- Produces commands: `prepare`, `smoke`, `run`, `summarize`
- Produces Make target: `make eval-seed-essential-delayed DELAYED_SEED_ARGS="..."`

- [x] 写失败测试：manifest 冻结数据、prompt、保护路径、模型、预算和 8 个 instance；凭据不进入 manifest。
- [x] 写失败测试：mock smoke 完成 8×4 answers 与 8×4×2 Judge calls；resume 只复用逐字一致结果。
- [x] 运行 `pytest -q tests/test_delayed_seed_cli.py` 确认 RED。
- [x] 实现 CLI、入口和中文运行边界；重跑确认 GREEN。
- [x] 运行四个 delayed eval 测试文件与全部 `eval/python` 测试。

### Task 5: Live dev 与客观报告

**Files:**
- Create: `eval/reports/2026-09-14-seed-essential-delayed-v1.zh-CN.md`

**Interfaces:**
- Produces: `.cache/seed-essential-delayed-v1/{manifest.json,instances,model-cache,summary.json}`

- [x] 启动新的空 MySQL、Chroma、Worker 和 memoryd；使用独立端口与目录，不清空或覆盖旧运行。
- [x] 使用冻结 MiniMax-M2.5 profile 执行 `run`，等待 8 个实例全部完成。
- [x] 执行离线 `summarize`，核对 8×4 answers、64 个双向 Judge 判定、formed/selected/rendered 和唯一 decision。
- [x] 写中文报告，只陈述结果支持的结论，不把合成 dev 称作统计证明。
- [x] 运行 `pytest -q`、`./scripts/verify-eval.sh`、`git diff --check` 与保护路径基线核对。
