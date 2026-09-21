# ChoraMem

[English](README.md) | [简体中文](README.zh-CN.md)

Research Alpha · `v0.1.0-alpha.1`

ChoraMem 是受《成唯识论》启发的开源长期记忆框架，面向长期对话、个性化 Agent 与 AI 陪伴。

它以独立服务管理经历、回忆与潜在生成倾向，通过 HTTP/gRPC 返回中立的 `MemoryContext`，由 Agent Harness 组织提示词、规划与行动。

## 设计理念

ChoraMem 从《成唯识论》中种子、现行与熏习的因果关系获得结构启发。Seed 表达未来理解、关注与回应的潜在倾向。

相关境遇使倾向参与当前回应，交互中的新经验与反馈继续塑造后续倾向。

工程模型采用有限的概念对应。记忆的来源关联与追加式版本记录其演化过程，完整定义及文献依据见[理念与概念](docs/philosophy.zh-CN.md)。

| 概念 | 作用 |
|---|---|
| `Episode` | 保存具体情境、Agent 行动及可附加的后续结果 |
| `Recollection` | 保存长期事实、偏好、约定或经历的可修订记忆 |
| `Disposition`（Seed） | 表达在相似境遇中参与未来理解与回应的潜在倾向 |
| `Constitution` | 提供由 Harness 维护的版本化角色基准 |
| `MemoryContext` | 提供按当前境遇选择并冻结的中立上下文 |

## 记忆闭环

```text
交互经历（Episode）
  -> 后台巩固（ConsolidateWindow）
  -> 回忆与倾向（Recollection / Disposition）
  -> 在线选择（SelectMemory）
  -> 中立上下文（MemoryContext）
  -> Harness 注入与实际行动
  -> 后续结果（Outcome）
  -> 下一次巩固与记忆选择
```

Recollection 可以被新经验直接重固。Seed 从跨会话、来源独立的重复经验中形成，已有 Seed 接收明确的长期纠正，或依据完整的投递、行为与非 Agent 结果链进行修订和局部抑制。

记忆选择同时利用记忆正文与其关联经历，相关 Episode 为 Seed 提供情境依据。Agent 与关系作用域隔离长期记忆，来源关联与版本历史保留演化路径。

生成式文本加工在后台 Python Worker 中完成，输出纯文本或浅层 tagged text。在线 `SelectMemory` 通过确定性规则和可选语义索引选择记忆，输出冻结的 `MemoryContext`。

Core 负责来源校验、写入资格、版本与事务提交。

## 架构与接入

ChoraMem 脱胎于 Chorai，独立拥有公共协议和记忆状态。Go `memoryd` 通过原生 gRPC 与 HTTP/Connect JSON 提供服务，Harness 通过薄 Adapter 完成来源录入、上下文注入及反馈提交。

关系存储支持 PostgreSQL 与 MySQL 8，一次部署选择一个 `CoreStore` Adapter。语义索引通过可替换的 `MemoryIndex` Provider 接入，索引投影可从关系库重建。

| SDK | 接入入口 | 文档 |
|---|---|---|
| Go | 公共协议 Client | [Go SDK](sdk/go/README.md) |
| Python | OpenAI Agents SDK Adapter | [Python SDK](sdk/python/README.md) |
| TypeScript | Vercel AI SDK Adapter | [TypeScript SDK](sdk/typescript/README.md) |

公共协议提供 `ObserveSourceEvent`、`SelectMemory`、`RecordMemoryDelivery` 与 `ReportOutcome` 四个生命周期方法。Harness 维护角色基准，选择提示词放置方式，并运行模型、工具与行动。

## 快速开始

参考部署使用 Docker Compose、PostgreSQL 和 Python Worker。Worker 默认调用兼容 OpenAI Responses API 的文本模型端点。

```bash
cp .env.example .env
```

将 `.env` 中的数据库密码、JWT 配置、模型名称与 API Key 设置为部署环境的值，然后启动服务。

```bash
docker compose up --build -d
```

服务就绪后，检查 readiness 端点。

```bash
curl -f http://127.0.0.1:8080/health/ready
```

- HTTP/Connect JSON 使用 `http://127.0.0.1:8080/memory.v1.MemoryCore/<Method>`。
- 原生 gRPC 监听 `127.0.0.1:8081`。

公共调用使用与 tenant 绑定的 JWT，连接及认证参数见对应 SDK 文档。当前版本处于 Research Alpha 阶段，API 与数据结构随迭代演进。

## 文档与验证

- [设计理念与文献依据](docs/philosophy.zh-CN.md)
- [架构与生命周期](docs/architecture.zh-CN.md)
- [语义索引协议](docs/memory-index.zh-CN.md)
- [Python Worker](worker/python/README.md)
- [评测协议](eval/README.zh-CN.md)与[实验报告](eval/reports/)

离线验证覆盖 Core、双数据库合同、SDK、Worker、部署配置与评测流程。真实模型评测通过评测入口显式启用。

```bash
make verify
make release-gate
```

## 许可证

Copyright 2026 ChoraMem contributors。

ChoraMem 采用 [Apache License 2.0](LICENSE) 开源。
