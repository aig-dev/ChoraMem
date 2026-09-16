# ChoraMem

**Research Alpha · `v0.1.0-alpha.1`**

ChoraMem is a protocol-first long-term memory service inspired by Yogacara's causal account of latent tendencies and reenactment. It does not implement consciousness or the complete eight-consciousness doctrine. Its research hypothesis is that outcome-conditioned Dispositions may add relationship-specific response continuity beyond factual recollection; that hypothesis remains unproven at framework scale.

ChoraMem 是一个协议优先、可独立部署的长期记忆服务。它把记忆的权威状态与 Agent Harness 分离，让不同 Harness 通过同一套 HTTP/gRPC 合同接入；Harness 决定 prompt、规划与行动，ChoraMem 只返回中立的 `MemoryContext`。

## 项目状态

`v0.1.0-alpha.1` 是首个 **Research Alpha** 工程冻结版，不是稳定生产版本。

已经实现：

- 独立的 Go `memoryd` 服务，以及 native gRPC 与 Connect JSON 接口；
- PostgreSQL 和 MySQL 8 两种关系库 Adapter，单次部署只选择一个权威存储；
- Python inference Worker，后台使用浅层 tagged text 处理记忆；
- Go、Python、TypeScript 薄 SDK，以及 OpenAI Agents SDK / Vercel AI SDK 参考 Adapter；
- 可替换的 `MemoryIndex` port，可接 pgvector、Chroma、Milvus、Qdrant 等语义索引；
- Recollection 与 Disposition 的形成、选择、投递、结果反馈和版本演化闭环；
- 无模型 Golden tests、双数据库合同测试、SDK / Worker / 部署 / Eval 发布门。

尚未证明：

- ChoraMem 相比 Mem0 或其他主流长期记忆框架具有可复现优势；
- Disposition 在不同模型、Harness 和自然长期关系中具有稳定增益；
- 当前评测足以代表完整的人格陪伴质量。

## 我们主张什么，不主张什么

ChoraMem 借用唯识学的一个有限结构假设：潜在倾向在相应境遇中现起，实际行为及其结果又改变未来可能现起的倾向。这个假设在软件中必须表现为真实的读写因果闭环，才有工程意义。

项目不声称建立八识的完整软件化对应，也不声称实现阿赖耶识、末那识、人类意识、灵魂、业或修行论。佛学术语不是数据库对象的别名；对应关系、文献依据与禁止宣称详见[理念与概念](docs/philosophy.zh-CN.md)。

研究假设是：除了“发生过什么”的事实性回忆，系统还可以从跨场景、带结果的经历中形成“在类似关系情境中更可能怎样回应”的倾向，从而为长期关系连续性提供额外信号。当前工程机制已经存在，但框架级效果仍需更大规模、跨 Harness 复验。

## 因果闭环

```text
Observe SourceEvent
  -> deterministic Episode
  -> background ConsolidateWindow
  -> Recollection / Disposition
  -> SelectMemory
  -> frozen MemoryContext
  -> exact Delivery
  -> actual Agent act
  -> source-bound Outcome
  -> next consolidation changes a future Select
```

关键约束：

- 在线 `SelectMemory` 不调用生成式 AI；模型语义处理只发生在后台巩固；
- 新经验可以直接重固 Recollection；
- 新 Disposition 来自跨 session 的重复、条件性经验，而不是复制 Agent 过去的回答；
- 已有 Disposition 的重演只记录再次现行；修订或抑制必须具有完整的 `Delivery -> AgentAct -> non-Agent Outcome` 链；
- Worker 只返回纯文本变化，不生成 stable ID、confidence、权重或数据库决定；
- `memoryd` 校验来源、scope、写入资格与版本后，在事务中提交状态。

这条循环是 ChoraMem 与普通“抽取文本 + 向量检索”方案的核心区别：被选择的记忆必须能够影响行为，行为结果也必须能够改变未来的记忆选择。

## 核心概念边界

| 概念 | 含义 | 不是什么 |
|---|---|---|
| `SourceEvent` | 由可信 Adapter 提交的不可变来源事件 | 模型自行猜测的事实 |
| `Episode` | 一次情境、行动与可选结果的确定性经历单元 | 长期人格本身 |
| `Recollection` | 可由新经验重固的事实性或经历性回忆 | confidence 加权的真理 |
| `Disposition` / Seed | 在相似关系情境中可能影响回应方式的潜在倾向 | fact、聊天原文、Agent 口头承诺或 Soul |
| `Constitution` | Harness 提供的只读人格基准；Core 不拥有也不修改 | 自动学习出的 Seed |
| `MemoryContext` | 某次选择后冻结的中立上下文 | prompt、SelfStory 或最终回复 |
| `Delivery` | Harness 确实暴露了哪些 Context item | 模型一定采用了这些记忆的证明 |
| `Outcome` | 有来源的后续结果 | Agent 对自己回答的自评 |

工程类比只限于：Disposition 对“种子”的潜在生成倾向、读写闭环对“种子生现行／现行熏种子”的结构启发，以及追加式版本对因果相续的有限类比。V1 没有 `AlayaStore`、`ManasEngine`、`SelfStory` 或 Soul 自动学习器。

## 架构

```text
Agent Harness
  |  Observe / Select / Delivery / Outcome
  v
stable gRPC + Connect JSON contract
  |
  v
memoryd  -----------------> optional MemoryIndex Provider
  |                               (recomputable projection)
  |
  +--> PostgreSQL or MySQL 8
  |      (single source of truth + job queue)
  |
  +--> background inference Worker
         (bounded text processing; no database access)
```

最小职责划分：

| 组件 | 唯一职责 |
|---|---|
| `memoryd` | 来源接纳、Episode 物化、选择、写入资格、版本与事务提交 |
| `CoreStore` Adapter | 权威因果账、记忆版本、Context、投递、结果与后台 Job |
| inference Worker | 对 Core 已限定的文本窗口做后台语义加工 |
| `MemoryIndex` Provider | 提供可重建的语义候选，只返回带 kind 的稳定 refs |
| SDK / Harness Adapter | 映射可信身份、注入 Context、记录真实投递与实际输出 |
| Agent Harness | 模型、prompt、规划、工具、行动、安全与最终表达 |

完整状态机、窗口冻结、写入资格与失败语义见[架构说明](docs/architecture.zh-CN.md)。

## 快速开始

需要 Docker，以及一个供参考 Worker 使用的 OpenAI-compatible 模型端点。

```bash
cp .env.example .env

export MEMORY_POSTGRES_PASSWORD='replace-with-a-long-random-alphanumeric-password'
export MEMORYD_JWT_HS256_SECRET='replace-with-at-least-32-random-bytes'
export MEMORYD_JWT_ISSUER='your-memory-auth-issuer'
export MEMORYD_JWT_AUDIENCE='memory-core'
export MEMORY_WORKER_OPENAI_MODEL='your-model-id'
export OPENAI_API_KEY='your-provider-key'

docker compose up --build
```

默认端点：

- readiness：`http://127.0.0.1:8080/health/ready`
- Connect JSON：`http://127.0.0.1:8080/memory.v1.MemoryCore/<Method>`
- public gRPC：`127.0.0.1:8081`
- internal Worker：Compose 网络内 `worker:8082`

四个公共生命周期方法：

```text
ObserveSourceEvent
SelectMemory
RecordMemoryDelivery
ReportOutcome
```

不依赖外部模型的 Connect JSON 生命周期 smoke：

```bash
go run ./examples/connect-json-lifecycle \
  -endpoint http://127.0.0.1:8080 \
  -token "$MEMORY_CORE_TOKEN" \
  -tenant smoke-tenant
```

生产接入必须使用 tenant-bound authentication；`trusted_loopback` 只用于显式的本机开发模式。

## SDK 与 Harness 接入

| 语言 / Harness | 入口 |
|---|---|
| Go | [sdk/go](sdk/go/README.md) |
| Python + OpenAI Agents | [sdk/python](sdk/python/README.md) |
| TypeScript + Vercel AI SDK | [sdk/typescript](sdk/typescript/README.md) |
| 参考 inference Worker | [worker/python](worker/python/README.md) |
| 固定 Reference Harness + Eval | [eval](eval/README.zh-CN.md) |

Harness 只需完成四件事：提交可信来源、在生成前选择记忆、只为实际注入的条目记录 Delivery、在产生真实后续结果时上报 Outcome。如何把 `MemoryContext` 放入 prompt、如何规划和表达，仍由 Harness 决定。

## 存储与语义索引

PostgreSQL 是参考 Compose 的默认关系库；MySQL 8 实现同一 `CoreStore` 合同。一次部署只配置一个 Adapter，不双写，也不要求迁移另一数据库的历史数据。

`MemoryIndex` 是可选 port，不是权威存储。实现可以使用 pgvector、Chroma、Milvus、Qdrant 或其他 VDB，但必须满足：

- 只返回有序 `{kind, ref}`，相似度分数不跨 Provider 边界；
- Core 使用前回关系库校验 exact owner、active version 与 canonical evidence；
- 索引可从关系库真源完整重建；
- Provider 为空、部分返回或不可用时，canonical lane 仍然工作；
- `SourceEvent` 不单独进入 VDB，Episode 在关系库物化后才投影。

协议见[MemoryIndex Provider 合同](docs/memory-index.zh-CN.md)。

## 当前证据

以下结果说明“工程链路能运行到哪里”，不是跨框架优越性声明：

| 评测 | 结果 | 可以说明 | 不能说明 |
|---|---|---|---|
| PersonaMem / OmniMemEval 历史运行 | `1,942 / 4,999 = 38.8478%` | 旧 Core revision + MiniMax-M2.5 完成了公开数据上的端到端运行 | 不是同协议 Mem0 对比，不能据此宣称不输主流框架 |
| PersonaMem `learned_core` 确认性复验 | `34.62%` vs `none 32.05%`，`+2.56pp`；95% CI `[-5.03pp, +10.60pp]` | 点估计为正，长期记忆信号可被隔离测量 | 没有复现稳定正收益 |
| ANCHOR companion-disposition v1 | formed / selected / rendered `4/4`；负例误形成 `0/4`；3 个可操纵 checkpoint 上 learned 胜 RAG `3/3` | 当前 Disposition 链在三个可辨识陪伴行为上产生了初步独立增量 | 另 1 题触及 Oracle 天花板，不能形成架构结论或外推到自然陪伴 |

对应报告：

- [Omni / MiniMax baseline](eval/reports/2026-09-09-omni-minimax-baseline.zh-CN.md)
- [PersonaMem learned_core 确认性复验](eval/reports/2026-09-13-personamem-learned-core-confirmatory.zh-CN.md)
- [ANCHOR companion-disposition v1](eval/reports/2026-09-16-anchor-companion-v1.zh-CN.md)

## 局限

- 当前证据来自有限数据、固定 Harness 与少数模型，尚无同协议、多框架公开横评；
- learned Disposition 的形成质量、选择精度和中立文本执行精度仍可能成为瓶颈；
- Delivery 只能证明上下文被注入，不能证明模型因它而行动；
- Outcome 的质量取决于 Harness 是否能提供真实、可归因的后续结果；
- V1 不包含 Claim 子系统、完整关系心理模型、SelfStory、人工审批、数值 confidence / strength 或通用工作流引擎；
- 当前 API 与 schema 在 Research Alpha 阶段仍可能发生不兼容变化。

## 路线图

1. 固定公开协议与跨数据库合同，补全版本升级说明；
2. 发布至少两个主流 Harness 的端到端接入样例；
3. 建立同模型、同预算、同检索资料、同评分协议的框架横评；
4. 扩大陪伴关系轨迹评测，优先验证 Disposition 相对相同 Recollection RAG 的独立增量；
5. 只有当稳定失败模式被命名后，才增加新的运行时概念或数值机制。

## 验证

```bash
make verify             # Go、race、vet、Proto lint/drift、build
make integration        # PostgreSQL、MySQL 8、public gRPC 合同
make verify-sdks        # Go / Python / TypeScript SDK
make verify-worker      # Worker binding、测试、打包、无 key smoke
make verify-deployment  # Compose、安全边界与必填配置
make verify-eval        # 固定 Eval 合同与失败语义
make verify-standalone  # 无父目录的隔离 checkout
make release-gate       # 完整发布门
```

无模型测试不需要 API key；真实模型评测必须显式启用，并将产物留在被忽略的 `.cache/` 中。

## 许可证

Copyright 2026 ChoraMem contributors。

ChoraMem 采用 [Apache License 2.0](LICENSE) 开源。
