# CUPID Seed Effect Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立冻结、无标签泄漏、可恢复的 CUPID 三组配对评测，并在未见 H1/H2 上验证 Seed 相对 Recollection 的增量效应。

**Architecture:** pinned loader 只向回放层暴露对话正文，scorer 标签单独保存；每行历史只学习一次，同一次 Select 派生三组回答；官方 rubric 的独立 Judge 在回答冻结后评分。所有正式结论按 persona 聚类。

**Tech Stack:** Python 3.11+、PyArrow 可选数据依赖、现有 Memory Core Python SDK、gRPC、pytest、MiniMax OpenAI-compatible API。

**Spec:** `docs/superpowers/specs/2026-09-13-cupid-seed-effect-eval-design.md`

## Global Constraints

- Split、数据 revision 与 SHA-256 必须逐字匹配 `eval/cupid-split-v1.json`。
- H1/H2 内容不得在正式运行前人工查看；只用 dev 调试。
- Core/Worker/Answer 只接收 dialogue role/content 和 current request；标签只进入 scorer。
- 三组共享一次学习和一次 Select；memory 预算固定 2,048 tokens。
- 在线 Select 无生成式 AI；Worker 只输出既有浅层 tagged text。
- 不增加 owner decision、confidence、weight、benchmark 特例，不修改 Chorai。
- 本任务不 commit 或 push 现有 dirty worktree。

---

### Task 1: 冻结 loader 与标签防火墙

**Files:**
- Create: `eval/python/src/memory_core_eval/cupid_data.py`
- Create: `eval/python/tests/test_cupid_data.py`
- Modify: `eval/python/pyproject.toml`

**Interfaces:**
- Produces: `load_cupid(path, split_manifest) -> CupidDataset`，其中 `instances` 不含 scorer-only 字段，`labels` 以 `instance_ref` 单独索引。

- [x] 先用 synthetic parquet/rows 测试 pinned hash、756/252/3 结构、hash split、owner opaque ref、H1/H2 身份摘要、标签不出现在 replay/answer 对象、manifest drift fail-closed。
- [x] 运行 `pytest tests/test_cupid_data.py -q`，确认因模块缺失而 RED。
- [x] 实现 frozen dataclass 与 loader；只读取本地 pinned parquet，不运行上游代码。
- [x] 重跑目标测试并用真实文件只做结构 preflight；不得打印 H1/H2 语义字段。

### Task 2: 忠实回放多轮反馈

**Files:**
- Create: `eval/python/src/memory_core_eval/cupid_runner.py`
- Create: `eval/python/tests/test_cupid_runner.py`

**Interfaces:**
- Consumes: `CupidInstance.history_sessions`。
- Produces: `replay_instance(...)` 与 `evaluate_instance(...)`；前者写真实 Episode/Outcome，后者只读 Select。

- [x] 用 fake Memory API 写 RED 测试：八个 session 顺序巩固；中间 USER 同一 source 同时绑定上一 Outcome/下一 Situation；无历史 Delivery；当前 probe 不写回；每题只 Select 一次。
- [x] 实现最小回放和 opaque scope/ref 生成。
- [x] 增加 resume 测试：已完成实例不重复模型请求，输入或 manifest hash 漂移直接失败。
- [x] 运行 `pytest tests/test_cupid_runner.py -q` 至 GREEN。

### Task 3: 三组渲染、Judge 与配对统计

**Files:**
- Create: `eval/python/src/memory_core_eval/cupid_effect.py`
- Create: `eval/python/tests/test_cupid_effect.py`

**Interfaces:**
- Reuses: `perma_seed_effect.build_seed_contexts`。
- Produces: `arm_order(instance_ref)`、`judge_prompt(...)`、`parse_score(...)`、`summarize_cupid_effect(results)`。

- [x] 先测试同一 Select 派生 none/recollection/seed、固定 2,048-token 总预算、Answer/Judge 看不到禁用字段或模式名、1–10 严格解析、三组 hash 交错。
- [x] 先测试 H1/H2 差值、persona-cluster bootstrap、三种 instance type 分层、漏斗和七条 gate；缺题、错误、重复或未渲染 Seed 必须拒绝。
- [x] 确认 RED 后实现纯函数并运行目标测试至 GREEN。

### Task 4: CLI、缓存与运行 manifest

**Files:**
- Create: `eval/python/src/memory_core_eval/cupid_cli.py`
- Create: `eval/python/tests/test_cupid_cli.py`
- Modify: `eval/python/pyproject.toml`
- Modify: `Makefile`
- Modify: `scripts/verify-eval.sh`
- Create: `eval/cupid-seed.zh-CN.md`

**Interfaces:**
- Produces: `memory-core-eval-cupid prepare|run|summarize` 与 `make eval-cupid-seed`。

- [x] 写 RED 测试：prepare 校验 pinned 文件；run manifest 冻结所有模型、prompt、源码和预算 hash；只允许 dev/H1/H2；resume 只接受逐字相同配置；summary 只读取完整冻结结果。
- [x] 接入现有 OpenAI-compatible model、Memory client、cl100k tokenizer 与内容寻址缓存。
- [x] 把 synthetic contract tests 加入 `scripts/verify-eval.sh`，更新中文使用说明。
- [x] 运行四个 CUPID 测试文件和 `make verify-eval`。

### Task 5: 只在 dev 修复一般性 Seed 形成错误

**Files:**
- Modify only if dev evidence requires: `worker/python/src/memory_core_worker/service.py`
- Test: `worker/python/tests/test_service.py`
- Create: `eval/reports/2026-09-13-cupid-dev-diagnostic.zh-CN.md`

**Interfaces:**
- Consumes: dev 的 formed/selected/rendered/score-changed 漏斗和原始 Worker 输出。
- Produces: 一个通用语义回归，不含 CUPID 专名或答案。

- [x] 先跑 dev 小样本，再定位首个因果断点；不以最终分数直接猜修复。
- [x] 若重复对话形状形成伪 Seed，用两段通用人工互动写真实 Worker 行为 RED：每个 Basis 必须各自同时支持同一境遇条件和同一未来响应调整；补充说明模板本身不支持该调整。
- [x] 观察预期 RED 后做最小 prompt/normalization 改动，跑真实模型语义回归与完整 Worker tests。
- [x] 重新跑 dev；在报告中记录有无改善及仍存失败，不读取 H1/H2。

### Task 6: 一次性正式验证与回归审计

**Files:**
- Create: `eval/reports/2026-09-13-cupid-seed-effect.zh-CN.md`

**Interfaces:**
- Produces: H1/H2/pooled 配对结果、完整漏斗、运行 manifest 和 goal completion evidence。

- [ ] 冻结最终 Core/Worker/Harness/Judge 源码 hash，确认 H1/H2 尚无结果文件。
- [ ] 依次运行 H1、H2；只允许同内容请求的传输恢复，禁止改参重跑。
- [ ] 运行 `summarize` 并逐条核对七条通过门。
- [ ] 若 Core/Worker/Select 改动，按原参数重跑 PersonaMem A/B；运行四类 lifecycle、相关 Go/Python tests、`make verify-eval` 与 `make release-gate`。
- [ ] 只有全部权威证据同时通过才完成 active goal；否则保留失败结论并继续定位新的未见评测入口。
