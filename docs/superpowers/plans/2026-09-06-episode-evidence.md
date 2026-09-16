# Episode Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让相关 Episode 原文在固定预算内经公开协议被真实投递。

**Architecture:** 保留现有 Select 和两种 learned Memory，增加独立的原文 evidence 输出。Core 冻结 canonical 原文并提供字节界限，Harness 负责模型相关的总 token 预算。

**Tech Stack:** Go、Protobuf/Connect/gRPC、MySQL 8、PostgreSQL、Python/TypeScript SDK；现有可替换 MemoryIndex。

**Spec:** `docs/superpowers/specs/2026-09-06-episode-evidence-design.md`

## Global Constraints

- 只改独立 Memory Core；不改 Chorai、产品数据库、Worker prompt 或 Seed 资格。
- Core 请求 `episode_evidence_max_bytes`：0 关闭，1–16384 启用；最多 8 个完整 Episode；无在线生成式 AI。
- 总 token 预算由 SDK 的确定性计数器执行；模型只消费纯文本；不伪造来源或投递。
- 保持旧零预算请求 hash；新增 002 只创建冻结证据表，不改旧表和数据。
- 真实 eval：同一冻结学习状态，2048-token 总预算；公开失败结果，不把高分设为硬编码验收。

## Task 1: 公共契约与 Core 原文投递

**Files:** `api/memory/v1/memory.proto`、`internal/core/selection/{context.go,episode_evidence.go}`、`internal/transport/grpc/server.go`、`internal/storage/{mysql,postgres}/{memory_context.go,episode_evidence.go,feedback.go,store.go}`、`migrations/{mysql,postgres}/002_episode_evidence.sql`、`migrations/embed.go`、生成代码。

**Interfaces:** 新增 `EpisodeEvidence{MemoryRef,Text}`，`MemoryContext.EpisodeEvidence`，`SelectRequest.EpisodeEvidenceMaxBytes`；公共对应字段为 `episode_evidence`、`episode_evidence_max_bytes`。CoreStore/MemoryIndex RPC 不变。

- [x] 写并观察失败测试：索引命中无 Recollection 的 Episode 时公开 Context 应有原文；跨 owner/当前 Situation 拒绝；完整条目装入预算；snapshot 重放不变；Evidence-only Delivery 不授予 Seed 权限。
- [x] 实现 additive schema、生成 bindings、Core 字节预算与两关系库快照/Delivery；用现有实际关系库 harness 验证，不用假数据库代替事务行为。
- [x] 验证旧零预算 run hash、002 升级/重复迁移；运行 Go 与协议检查，提交这一纵向能力。

## Task 2: 两 SDK 与薄 Adapter 的预算装配

**Files:** `sdk/python/src/memory_core/{rendering.py,adapters/openai_agents.py}`、Python tests/README；`sdk/typescript/src/{render.ts,vercel-ai.ts,index.ts}`、TS tests/README。

**Interfaces:** `render_memory_context(context, *, max_tokens=None, token_count=None)`；`renderMemoryContext(context, {maxTokens, countTokens}?)`；返回原有 text + exact refs。Adapter 通过可选配置传入证据字节预算和总 token 预算。

- [x] 先写失败测试：原文带引号而无 ref、总文本含包装仍不超限、超大首项跳过后仍选可容纳项、UTF-8/多行保留、重复/缺失 ref 与无效计数拒绝、Evidence-only turn 真正注入并 exact Delivery。
- [x] 实现预算装配；无选项时旧三段渲染字节兼容；启用证据需提供预算和计数器。
- [x] 运行 `make verify-sdks`，更新可运行示例和中文说明；独立审查。

## Task 3: 真实闭路验证、报告与收尾

**Files:** `eval/python/src/memory_core_eval/personamem.py`、对应测试、独立配对 runner、`eval/reports/`、`docs/{architecture,memory-index,philosophy}.zh-CN.md`。

- [x] 先测试 eval 的总预算与 Episode refs，然后复用生产 renderer，避免独立预算算法漂移。
- [x] 在专用 MySQL + 真实 Chroma/MiniLM 上，对同一冻结 100 题比较 evidence 关闭/开启；查询与答案不产生训练 Episode/Outcome，前后验证版本/Basis/Job 不变。
- [x] 记录实际投递与来源覆盖、模型请求/输出、官方 MCQ 分数、错误、延迟和 tokens；保留旧基线，不把数据集标注喂给模型。
- [x] 同步架构说明及公开结果；运行 `make release-gate`、独立审查和逐条完成审计；仅保留本地提交，停止本次实验服务并保留数据。

## 执行约定

延续用户要求的极简、自主推进：此计划按三个模块执行，不增加逐项人工审批。协议确定后 SDK 可以与关系库存储独立工作；各自先测后改，不交叉编辑。索引开启但原文未真正进入模型，或者只通过 mock/连通性检查，都不算完成。
