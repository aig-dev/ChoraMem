# Seed Causal Candidate Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以当前完整因果对为锚，只向 focused Worker 提供具有配对 Outcome 的语义近邻历史因果对，并让最终 BASIS 精确等于模型可见候选组。

**Architecture:** Core 在现有 consolidation snapshot 中构造瞬时 formation role；关系库负责补齐并校验 Outcome，MemoryIndex 只排序候选。Python Worker 根据 role 生成无 ID 文本，Core 在提交前校验 exact BASIS。无新表、公共 RPC 或新 Job。

**Tech Stack:** Go、PostgreSQL、MySQL 8、protobuf/gRPC、Python 3.11、pytest、Chroma/MiniLM、MiniMax API。

**Spec:** `docs/superpowers/specs/2026-09-15-seed-causal-candidate-group-design.md`

## Global Constraints

- 不改冻结数据、题目、顺序、门槛、温度、模型或 MemoryIndex Provider 配置。
- 不引入 confidence、strength、阈值分数、JSON 输出、第二个 AI Job 或人工节点。
- 不清理、不提交当前脏工作区；行为代码严格 RED → GREEN。
- live 运行显式加载 `../Chorai_Go/env/prod/chorai_agent.env`，不读取外部旧 key。

### Task 1: 冻结内部候选组合同

**Files:**
- Modify: `internal/core/consolidation/evidence.go`
- Modify: `internal/core/consolidation/window.go`
- Modify: `api/memory/inference/v1/inference.proto`
- Modify: `internal/inference/grpcworker/worker.go`
- Modify: generated Go/Python protobuf files
- Test: `internal/core/consolidation/evidence_test.go`
- Test: `internal/inference/grpcworker/worker_test.go`

- [x] 先写失败测试：formation role 被映射并进入结构 hash；exact formation BASIS helper 保留 pair 顺序。
- [x] 运行定向测试并确认因字段／行为缺失而 RED。
- [x] 加入最小字段、映射与生成代码；重跑确认 GREEN。

### Task 2: Core 构造完整因果候选组

**Files:**
- Modify: `internal/storage/postgres/consolidation.go`
- Modify: `internal/storage/postgres/feedback_consolidation.go`
- Modify: `internal/storage/postgres/recollection.go`
- Modify: `internal/storage/mysql/consolidation.go`
- Modify: `internal/storage/mysql/feedback_consolidation.go`
- Modify: `internal/storage/mysql/recollection.go`
- Test: `internal/storage/postgres/memory_index_selection_integration_test.go`
- Test: `internal/storage/mysql/consolidation_memory_index_integration_test.go`
- Test: dialect-local unit tests as needed

- [x] 先写失败测试：完整 anchor 查询包含 Situation／AgentAct／Outcome；索引候选必须从关系库补齐唯一 non-Agent Outcome；混杂窗口只标记 anchor 与最多两份有资格历史候选。
- [x] 先写失败测试：跨 owner、同 session、无 Outcome、Agent Outcome 与多 Outcome 被跳过。
- [x] 先写失败测试：第三方 Worker 省略或增加 formation BASIS 时整批 no-op。
- [x] 运行 PostgreSQL／MySQL 定向测试并确认 RED。
- [x] 实现两种 Adapter 的等价候选构造、snapshot 重建与 exact BASIS 校验；重跑确认 GREEN。

### Task 3: Worker 只消费标记组

**Files:**
- Modify: `worker/python/src/memory_core_worker/disposition_materials.py`
- Modify: `worker/python/src/memory_core_worker/service.py`
- Modify: `worker/python/tests/test_disposition_materials.py`
- Modify: `worker/python/tests/test_service.py`

- [x] 先写失败测试：未标记的混杂 Episode 不进入 focused prompt；BASIS 逐对精确绑定；只有 anchor 时形成 no-op 且 primary 宽泛块被移除。
- [x] 运行定向 pytest 并确认 RED。
- [x] 实现 role 校验与 suppression；重跑完整 Worker suite。

### Task 4: 文档、全量验证与冻结复跑

**Files:**
- Modify: `docs/architecture.zh-CN.md`
- Modify: `worker/python/README.md`
- Modify: `eval/reports/2026-09-15-seed-production-confirmation.zh-CN.md`

- [x] 同步候选组、无索引和 BASIS 语义；不扩大项目主张。
- [x] 运行 Go、Worker、eval、SDK、build 与 `git diff --check` 的相关 release gate。
- [x] 使用新空 MySQL schema、新 Chroma collection、新 Worker/cache/output，原样复跑 exact 与 generalization 冻结数据。
- [x] 对比形成／选择／渲染、Learned vs RAG、负对照、BASIS 污染和调用完整性；把客观结论写回报告。
