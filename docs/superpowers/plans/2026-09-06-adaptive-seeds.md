# Adaptive Seeds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 在一次后台巩固内完成用户要求/多源经验初生、只读人格基准参与、直接纠正与效果反馈分流。

**Architecture:** 扩展既有 tagged text 和因果资格，不新增流水线。SourceEvent 承载外生人格快照；
Core 管写入、scope 与幂等，Python Worker 只做文本语义加工，Harness 负责表达和人格规范权威。

**Tech Stack:** Go、Protobuf/gRPC/Connect、MySQL8/PostgreSQL、Python Worker、Go/Python/TypeScript SDK。

**Spec:** `docs/superpowers/specs/2026-09-06-adaptive-seeds-design.md`

## Global Constraints

- 保留一次后台 ConsolidateWindow，不新增模块、分类 Job 或人工决定。
- AI 输出 TARGET / APPLICATION / CHANGE / BASIS，不生成 confidence、strength 或权重。
- 在线 Select 不调用生成式 AI；不把 Agent 重复输出当成用户认可。
- Core 校验来源、owner、版本和资格；仅 Worker 判断长期要求的语义。
- MySQL8 与 PostgreSQL 语义相同；DDL 只走 migration，生成物同步。
- 仅独立 memory-core 仓库，不合并、不推送、不改 Chorai 主线或产品数据库。
- 计划保持简洁，具体行为由 spec 与失败测试约束；任何不能证明的验收项保持未完成。

### Task 1: Seed 形成和直接纠正的因果状态机

**Files:** `internal/core/consolidation/{parser,parser_test}.go`；
`internal/storage/{mysql,postgres}/{consolidation,feedback_consolidation,recollection,consolidation_jobs}.go`；
对应测试、`internal/scheduler/` 相关规则。

**Interfaces:** 产生 `consolidation.ChangeAdapt = "ADAPT"`，正文与 TEXT 同为一行；
WorkerRequest 为直接纠正提供与效果反馈不同的可写资格。
建议标记 `ELIGIBLE_ADAPTATION <version>` / `DIRECT_EPISODE <ref>`，
新 Seed 单次直接要求提供 `ELIGIBLE_NEW_ADAPTATION NEW_DISPOSITION`；
最终命名须在任务报告明确，以供 Task 3 消费。

- [x] RED：添加并运行单 Episode 的 ADAPT 解析/初生、无 Delivery/Outcome 的已有 Seed ADAPT、
  同文不增版本、未来 Select 改变的真实数据库测试；旧代码应因缺少操作/资格而失败。
- [x] GREEN：实现 ADAPT 的目标和当前用户来源限制、多源 TEXT 形成的非 Agent 依据、
  直接纠正版本与 Basis，以及单 Episode 静默调度。保持反馈 TEXT/INHIBIT 原资格。
- [x] 验证：跨 owner、旧证据独写、Agent actor 伪装直接要求、过期版本、重试；
  `go test ./internal/core/... ./internal/scheduler/...` 及两库相关集成测试。
- [x] 提交并自检；报告精确的新输入标记及运行命令，接受独立任务审查。

### Task 2: 人格基准快照从 Harness 进入同一个 Worker

**Files:** `api/memory/v1/memory.proto`、生成绑定；`internal/core/ledger/`；
`internal/storage/{mysql,postgres}/` intake/巩固/迁移；`internal/transport/`；
`sdk/{go,python,typescript}/` 的薄 turn/Adapter 和测试。

**Interfaces:** `SourceEvent.constitution` 与 `ReportOutcomeRequest.constitution` 使用现有 Constitution 消息；
内部 ledger 只读值类型与 Select 使用的 Constitution 保持一致。
来源首次录入顺序由关系库管理，不增加公开或模型字段；用来确定当前窗口的最新 Situation 基准。
Worker 窗口输入提供 `CONSTITUTION` / `MEMORY_REF <ref>` / 原文 或明确缺省标记，
最终格式在任务报告明确。不得赋予 Target/Basis 资格。

- [x] RED：先证明来源持久化、冲突幂等、窗口最新/缺省基准、同一窗口重放，
  两个 Harness 传入的基准到达 Worker 的行为测试在旧代码失败；包含同来源兼任 Outcome/Situation
  的两种到达顺序，以及迟到反馈不能使旧基准重新成为当前基准。
- [x] GREEN：新增 source 快照字段与两库 migration、公共/内部映射、冻结指纹、
  窗口组装、SDK 薄转发；不增加基准修改 RPC 或另一套权威。
- [x] 验证：有/无/更新/未知基准，相关旧 Episode 不替代当前基准，源码生成漂移，
  现有调用及 nil intake 哈希兼容，两数据库真实升级和端到端 gRPC。
- [x] 提交并自检；报告迁移、接口、输入格式和测试证据，接受独立任务审查。

### Task 3: 统一语义 Worker、真实机制评测与中文文档

**Files:** `worker/python/src/memory_core_worker/service.py`、Worker 单元/真实模型语义测试；
`eval/` 有界机制诊断与报告；`docs/{philosophy,architecture,protocol}.zh-CN.md`（存在的对应文档）、
Worker/SDK 使用说明、`AGENTS.md` 中受影响语义。

**Interfaces:** 消费 Task 1 的 ADAPT/资格标记及 Task 2 的只读 Constitution，
仍只返回浅层 tagged text。当前经历、相关经历、已有记忆须共同用于去重和推断。

- [x] RED：新增真实模型语义用例：一次明确未来要求、用户直接纠正、不同 AgentAct 的
  多源适应、相同经历不同人格基准、仅重复 AgentAct、事实不双写、引用伪要求、无链反馈。
  先在既有 prompt 上运行，保留失败和用量；不得用字符串包含断言代替语义验证。
- [x] GREEN：调整同一个 Worker prompt，按已批准定义分流；不把语言基准当执行指令或来源。
- [x] 验证：真实 Core+DB+Worker 流程包含形成→选择→直接纠正→再选择，及真实反馈资格反例。
  使用已有真实历史做小规模抽取诊断，明确与静态 QA/人格成长评测的区别。
- [x] 同步中文文档与报告；`make release-gate` 在干净导出中通过。
- [x] 提交、自检、任务审查、全分支审查；逐条对照原 goal，全部有证据才完成。

## 完成记录（2026-09-07）

实现与完整发布验证对应提交 `2e1e1f7f0f416b85e463d1f10c2024dafe61d253`；此后的本计划勾选
仅为文档收尾，不改变已测实现。三个任务的独立审查已通过；全分支审查发现的候选截断与
测试时钟问题在一次最终修复中解决，针对性复审无新增问题。

- 明确的本人长期要求可初生或直接纠正同一 Seed；间接初生使用来源不重叠的多份经历。
  当前/相关经历、已有记忆和只读 Constitution 进入同一个后台 Worker；不复制 Agent 回复
  模式，不把重复或沉默当认可，效果归因仍保持真实行为—结果链。
- PostgreSQL/MySQL 的真实模型链均通过形成→Select→同 Seed 纠正→再 Select，并验证
  known→changed→UNKNOWN 基准和无链表扬不改 Seed/Basis。
- 固定 `deepseek-v4-flash` thinking/high 的 15 个受控用例、4 条角色确认及 6 次真实双库
  调用通过，共 25 次调用、116883 tokens。功能角色差异可重复，不等于完整人格效果。
- 两数据库 × 无/空/不可用索引的六个 70-Seed 回归先 RED 后 GREEN：修订第 70 个目标仍
  保持同一 Seed 的 `@2`，候选最多 64 个，输入 54561/262144 bytes。无索引的词面排序
  需要扫描该 owner 的 active Seed 文本，开销随数据增长，不保证所有语义改述均能召回。
- 从上述提交显式 `git archive` 后运行 `NODE_OPTIONS=--no-experimental-webstorage make release-gate`
  通过：Go/race/vet/proto/build、两库集成、SDK 46/40、Worker 43（11 个 opt-in 跳过）、
  Go→Python smoke、部署合同和 eval 109/3。原日志保留一条 Worker 回环连接 GOAWAY INFO，
  后续测试与最终发布门均通过；未压制或推定其原因。

验收边界：非 thinking 配置仍未获语义验收；原公开历史 112 turns/224 Sources 已完整录入，
但仅 3/4 窗口完成、形成 2 Recollections/0 Seed，最后窗口超时。未证明长期陪伴改善，
也未验证较大 thinking 窗口能在 stock `memoryd` 两分钟预算内完成。完整失败与用量保留在
`eval/adaptive-seeds.zh-CN.md` 及本地诊断记录中，不将部分结果改写为成功。

保留独立仓库的 `codex/adaptive-seeds` 分支；没有合并、推送或修改 Chorai 主线/产品数据库。
