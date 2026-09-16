# Production Paired Disposition Formation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一次生产 `ConsolidateWindow` Worker RPC 中可靠形成 outcome-backed Disposition，并保留 Recollection 与既有变化。

**Architecture:** 新的纯 Python material/binder 从已有 typed evidence 构造无 ID 的因果配对文本；`InferenceService` 将 focused 结果与现有统一、反馈、Recollection lane 合并。Core 协议、数据库和调度不变。

**Tech Stack:** Python 3.11、protobuf、grpc.aio、pytest。

**Spec:** `docs/superpowers/specs/2026-09-15-production-paired-formation-design.md`

## Global Constraints

- 不新增 RPC、表、调度 Job、confidence、权重、JSON 或人工审批。
- 不从 raw user text 生成或猜测 refs；模型只输出一行正文或 `NO_CHANGE`。
- 不提交、不清理当前脏工作区；每个行为严格 RED → GREEN。

---

### Task 1: 因果配对 material 与 binder

**Files:**
- Create: `worker/python/src/memory_core_worker/disposition_materials.py`
- Create: `worker/python/tests/test_disposition_materials.py`

**Interfaces:**
- Produces: `DispositionFormationMaterial(application, experiences, existing_dispositions, basis_refs)`
- Produces: `disposition_formation_material(request) -> DispositionFormationMaterial | None`
- Produces: `build_disposition_formation_input(material) -> str`
- Produces: `material.bind_disposition(body) -> str`

- [x] 写失败测试：两个不同 session 的完整非 Agent Outcome 配对生成无 ref prompt，并把一行正文绑定为四个 Basis refs。
- [x] 写失败测试：缺配对、同 session、Agent Outcome、direct ADAPT、越界权限返回 `None`；协议词、多行、超长正文抛错。
- [x] 运行：

```bash
cd worker/python && .venv/bin/pytest -q tests/test_disposition_materials.py
```

期望：模块不存在而失败。

- [x] 写最小实现并重跑，14 个测试通过。

### Task 2: 同一 Worker 响应组合 Seed 与 Recollection

**Files:**
- Modify: `worker/python/src/memory_core_worker/service.py`
- Modify: `worker/python/tests/test_service.py`

**Interfaces:**
- Consumes: Task 1 material/binder。
- Produces: `_remove_inferred_new_disposition(text) -> str`
- Produces: 一个同时包含 focused Seed 与既有合法 tagged blocks 的响应。

- [x] 写失败测试：primary `NO_CHANGE`、focused 一行、Recollection 一行时，最终响应同时包含 `NEW_DISPOSITION` 与 `NEW_RECOLLECTION`。
- [x] 写失败测试：primary 的 `NEW_DISPOSITION TEXT` 被 focused 结果替换，其他 existing target block 保留；focused `NO_CHANGE` 不允许 primary 宽泛形成。
- [x] 写失败测试：direct ADAPT、feedback-only、无 typed evidence 的旧请求保持原调用路径。
- [x] 运行：

```bash
cd worker/python && .venv/bin/pytest -q tests/test_service.py
```

期望：新增组合断言失败。

- [x] 写最小组合逻辑并重跑 Task 1/2 测试，完整 Worker suite 为 128 passed、11 skipped。

### Task 3: 文档与生产边界回归

**Files:**
- Modify: `docs/architecture.zh-CN.md`
- Modify: `worker/python/README.md`
- Modify: `scripts/verify-worker.sh` only if the existing verifier does not execute the real service composition path.

- [x] 更新调用上限与 lane 说明，明确仍是一个 Job/RPC，在线 Select 为零次生成调用。
- [x] 运行：

```bash
make verify-worker
make build
git diff --check
```

- [x] `make verify-worker`、`make build`、`git diff --check` 均通过。
