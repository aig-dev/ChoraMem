# Worker Source Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将真实来源边界从 Core 传到 Python，并提供纯文本材料读取与程序绑定接口。

**Architecture:** 只追加一个内部 evidence 快照，保留旧模型路径。Go 按既有资格构造来源，Python 按结构读取，不反解原始聊天。

**Tech Stack:** Go、Protobuf/gRPC、Python；无新第三方依赖。

**Spec:** `docs/superpowers/specs/2026-09-11-worker-source-boundary-design.md`

## Global Constraints

- 不修改公开 API、数据库 schema、Core 写入资格、Seed 规则或 Chorai。
- 不新增付费模型调用，不部署或重启已有评测服务，不改动原基线。
- 默认 Worker 的模型输入和单次调用行为保持不变。
- 无新第三方依赖；用户原有未提交改动全部保留；本次不 commit／push。
- 不使用新表、Proposal、confidence、临时来源别名或人工运行时审批。
- 实施者不创建子代理；主线程统一安排复核。

### Task 1: 内部来源快照与可消费的正文绑定

这是一个端到端协议单元，必须一起验证，不拆成只增加闲置字段的小任务。

**Files:**

- 修改 `api/memory/inference/v1/inference.proto`；重新生成 `gen/memory/inference/v1/*` 和 `worker/python/src/memory_core_worker/v1/*`。
- 修改 `internal/core/consolidation/window.go`；必要的独立 evidence 类型／预算辅助代码放在 `internal/core/consolidation/evidence.go`。
- 修改两种数据库的 `internal/storage/{mysql,postgres}/feedback_consolidation.go`；对应映射辅助代码可放在各目录 `worker_evidence.go`，共享规则留在 core，不复制大段通用逻辑。
- 修改 `internal/inference/grpcworker/worker.go`，增加边界映射测试。
- 新增 `worker/python/src/memory_core_worker/source_materials.py` 与 `worker/python/tests/test_source_materials.py`。
- 两种数据库新增 `worker_evidence_test.go`；扩展 `internal/core/consolidation/window_test.go`、`internal/inference/grpcworker/system_test.go`、`worker/python/tests/fake_worker_server.py`；必要时新增专用本地跨进程 fixture，不能修改默认生产模型行为。
- 主线程同步 `docs/architecture.zh-CN.md`、`worker/python/README.md` 和验证记录；实施者不编辑这些文档，以免冲突。

**Interfaces:**

```proto
// append to ProcessConsolidationWindowRequest, preserving fields 1–4
WindowEvidence evidence = 5;

message WindowEvidence {
  repeated EvidenceEpisode episodes = 1;
  repeated EvidenceOutcome outcomes = 2;
}
message EvidenceEpisode {
  string episode_ref = 1;
  string session_ref = 2;
  string origin = 3;
  repeated EvidenceSource sources = 4;
}
message EvidenceSource {
  string source_ref = 1;
  string role = 2;
  string actor_kind = 3;
  string actor_ref = 4;
  string text = 5;
}
message EvidenceOutcome {
  string outcome_ref = 1;
  string episode_ref = 2;
  string actor_kind = 3;
  string actor_ref = 4;
  string text = 5;
}
```

Go 使用同名纯数据类型，`WorkerRequest.Evidence *WindowEvidence` 区分旧请求。来源身份/角色取现有 ledger 值，不从正文生成。Python 函数 `recollection_materials(request) -> tuple[RecollectionMaterial, ...]` 返回 frozen 材料；每项提供 `episode_ref / source_ref / text / context_text / basis_refs` 和 `bind_recollection(body: str) -> str`。

- [x] 写 RED：在两种数据库真实 `consolidationWorkerRequest` 上断言原文含伪 `SITUATION/ACTOR/SOURCE` 时，结构化 source 仍保留原身份和正文；验证 current、related、feedback anchor 的选择、去重，以及 direct-only anchor 和无资格 Outcome 均不暴露。
- [x] 写 RED：来源划分不同但旧窗口文本相同时，`hashConsolidationWorkerRequest` 必须不同；evidence 的来源身份、origin、正文、Outcome 配对变化均参与 hash。`FitsWorkerRequestLimit` 必须计入新增结构。先运行并记录失败，再添加类型、构造和传输映射。
- [x] 生成绑定：使用仓库现有 buf Go 生成配置及 `worker/python/scripts/generate-bindings.sh`，不手改生成文件或公开协议。
- [x] 写 RED：Python 对任意真实 refs 自动形成 USER 材料，保留全部 current＋related 原文作为角色引用上下文；不会用 related/anchor 冒充 current，不能把 source ref 当 Episode Basis。旧请求／重复 Episode 或 link／同作用域 Source 冲突／未知 origin 或角色／越界 Basis refs 拒绝新材料读取；允许空 session、跨 session 同名 Source、跨 Episode 合法共享 Source。材料创建后原 protobuf 再被修改不能改变已绑定快照。然后实现最小纯函数和 frozen 材料对象。
- [x] 写 RED：`material.bind_recollection("用户喜欢茶。")` 输出下面的精确协议；`NO_MEMORY` 返回空串，空值／混合 NO_MEMORY／独立协议行／代码围栏／大于 4 KiB 的正文拒绝。完整结果不得超过 64 KiB。再实现绑定，不添加模型调用或新 CLI 模式。

```text
TARGET
NEW_RECOLLECTION
APPLICATION
OTHER
CHANGE
TEXT
用户喜欢茶。
BASIS
episode-current
episode-related
```

- [x] 加入真实本地 Go → gRPC → Python 材料读取和绑定 → Go parser 测试。纯文本输出由本地固定 fixture 提供；不替换内部转码/绑定为 mock。保留旧 `TestPythonWorkerProcessContract`；新旧请求的默认 `build_model_input` 必须一致，不把新增结构悄悄放进旧模型请求。
- [x] 跑 GREEN 与完整相关回归，记录真实命令/结果；再自查：新字段是否被真实消费者使用、两种数据库过滤是否一致、是否改变隐私边界/资格/旧行为。

```sh
go test -count=1 ./internal/core/consolidation ./internal/inference/grpcworker ./internal/storage/mysql ./internal/storage/postgres ./internal/contract
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=worker/python/src python3 -m pytest -q worker/python/tests
make proto-check proto-lint
./scripts/verify-worker.sh
git diff --check
```

不运行 `scripts/test-mysql.sh`、`make build`、现有线上模型/评测脚本；它们可能覆盖活跃服务使用的 binary/端口或消耗提供商额度。实际数据库调用不属于本次离线接口验证；两 adapter 的纯请求构造需真实运行。
