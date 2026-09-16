# Seed-essential Delayed Eval v1 设计

日期：2026-09-14
状态：用户已通过持续 Goal 明确批准

## 要回答的问题

在当前请求不直接复述用户偏好的条件下，跨多个 session 形成的 `Disposition` 是否比只提供
`Recollection` 的 RAG 更能使同一 Agent 延续用户特有的互动方式。

该评测只诊断三种可能：

1. Oracle 胜 RAG、Learned 不胜：Seed 形成、选择或文本质量问题；
2. Oracle 也不胜 RAG：当前 Harness 注入方式或架构没有可检测的额外价值；
3. Learned 稳定胜 RAG：得到架构优势的初步证据。

无法满足严格门槛时输出 `inconclusive`，不强迫数据支持三者之一。

## 冻结数据

数据是仓库内 Apache-2.0 的合成开发集，不借用外部 benchmark 名义。冻结四种关系模式，
每种模式有两个语义不同的延迟探针，共八个实例：

- 信息过载时，先简短承接，再只问一个优先级问题；
- 明显逃避时，直接点明逃避，只给一个带期限的行动；
- 冲突后情绪仍强时，先询问用户需要倾听、梳理还是建议，不先给方案；
- 简短 check-in 时，只问「一个进展、一个阻力、一个最小下一步」。

每个实例包含三个先前 session。每个 session 恰好有一条 Situation、一条 AgentAct 和一条
非 Agent Outcome；Outcome 只评价当次效果，不直接声明未来规则。每个 session 独立提交并等待
后台巩固，测试探针发生在三次巩固之后。

测试请求只表达新的触发情境，不包含目标回应方式、Oracle 文本、Anti 文本或 scorer rubric。
同一模式的两个探针使用独立 relationship owner，防止探针相互污染。

## 四臂

对每个实例只学习一次、只 Select 一次，再从同一只读 `MemoryContext` 派生：

| arm | 内容 |
|---|---|
| `rag` | Select 返回并在预算内渲染的 Recollection |
| `learned_seed` | 同一批 Recollection 加 Select 返回的 Disposition |
| `oracle_seed` | 同一批 Recollection，加冻结的单条正确 Disposition；不加入 Learned Seed |
| `anti_seed` | 同一批 Recollection，加冻结的单条反向 Disposition；不加入 Learned Seed |

四臂共享 2,048 `cl100k_base` 最大记忆预算、Persona、当前请求、回答模型、temperature 与输出预算。
不使用 padding；记录实际 token 数并断言都不超过同一上限。Oracle 与 Anti 必须实际渲染，
否则属于 Harness 失败。

Episode evidence 关闭。探针只写入 unbound Situation 并 Select，不写 Delivery、AgentAct 或
Outcome，因此四臂不会反向影响学习状态。

## 标签隔离与判分

回答阶段只能读取 Persona、当前请求和该 arm 的 MemoryContext，不读取 target rubric、arm 名或
其他回答。四个回答全部冻结后，Judge 才能读取 scorer label。

固定比较：

- `oracle_seed` vs `rag`；
- `learned_seed` vs `rag`；
- `oracle_seed` vs `learned_seed`；
- `oracle_seed` vs `anti_seed`。

每个比较分别以 A/B 和 B/A 两种位置调用 Judge。Judge 只输出 `A`、`B` 或 `TIE`。两次都指向
同一逻辑 arm 才记为 clear win；两次都是 `TIE` 才记为 tie；其他情况记为 inconsistent。

## 终局门槛

八个实例上，`dominates(left, right)` 定义为：left 至少 6 个 clear win，right 为 0 个 clear
win。任何正向结论还要求 `oracle_seed` dominates `anti_seed`，作为操纵有效性检查。

按以下顺序输出唯一状态：

1. 四臂、标签隔离、预算、结果或 Judge token 不完整：`data_or_harness_failure`；
2. Oracle 不支配 Anti：`manipulation_check_failed`；
3. Learned 支配 RAG，且至少 6/8 实例完整 `formed -> selected -> rendered`：
   `preliminary_learned_advantage`；
4. Oracle 支配 RAG，但 Learned 不支配 RAG：`learning_or_selection_bottleneck`，再用漏斗指出
   `formed / selected / rendered / learned_effect` 的第一个断点；
5. Oracle 不支配 RAG：`no_detectable_architecture_advantage`；
6. 其余：`inconclusive`。

这是小样本开发门，不宣称统计显著，也不允许用同一输出反向修改数据后仍称为 v1。

## 实现边界

只增加 `eval/**`、文档、Make target 和 eval 验证入口；不修改 `internal/**`、`worker/**`、
`api/**` 或 `gen/**`。复用现有 Memory API、SDK renderer、内容寻址模型缓存、MySQL 8、
Chroma MemoryIndex、后台 settle 和固定 MiniMax-M2.5 profile。

运行产物冻结 manifest、逐实例四臂回答、双向 Judge 请求 hash、Memory refs、实际 token、
每轮巩固状态、漏斗和唯一结论。凭据不得进入任何产物。
