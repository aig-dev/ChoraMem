# ChoraMem Memory Core V1 架构

## 最小心智模型

```text
one authority      = memoryd
one causal ledger  = SourceEvent / Episode / Recollection / Disposition / Activation / Delivery / Outcome
two memory forms   = Recollection + Disposition
two engines        = Select + ConsolidateWindow
Recollection       = FORM | KEEP | REVISE
Disposition        = FORM | ADAPT | REENACT | INHIBIT | REVISE
one output         = MemoryContext
optional evidence  = frozen historical Episode quotes in MemoryContext
```

在线路径不调用生成式 AI。后台仍只有一个 `ConsolidateWindow` Job 与一个 Worker RPC。
参考 Worker 先执行统一 tagged-text lane；当 Core 以当前完整因果对为 `anchor`，并从
MemoryIndex 找到可由关系库补齐唯一 non-Agent Outcome 的历史 `candidate` 时，再由 focused
lane 判断 outcome-backed 新 Disposition。focused lane 只读这些被标记的因果对，不读同一窗口
里的其他 Episode。它只返回一行倾向或 `NO_CHANGE`，Target、APPLICATION 与按因果对相邻排列的
Basis 由程序绑定；统一 lane 对同一形成资格给出的 `NEW_DISPOSITION TEXT` 会被机械移除，避免重复 Target。
普通聊天 Episode 不能单步创建 Seed：参考 Worker 会确定性移除统一 lane 的所有
`NEW_DISPOSITION ADAPT`。用户显式产品设置属于 Harness 的 Soul／Constitution 真源；聊天内直接
纠正一个已有 Seed 仍可在统一 lane 对该 exact Target 使用 `ADAPT`。因此 focused 跨 session 形成
不会再被模型自行声称的“一次直接授权”旁路。
若结果没有处理 Recollection，且窗口含有 Core 已确定身份的当前 USER 材料，现有
Recollection-only 纯文本后备继续运行，返回 1–32 行独立正文或 `NO_MEMORY`。必要时既有
feedback recovery 也保持独立。四个 lane 是一次无状态 RPC 内的文本处理，最多四次后台模型
调用，不是新的调度 Pipeline；模型都不生成数据库 ID，Core 仍是唯一提交权威。

## 公共 turn 工作流

```text
Harness
  1. Observe situation
  2. Select MemoryContext
  3. 真正注入非空 Memory／Episode evidence 后 Record Delivery
  4. 运行模型、工具与行动
  5. Observe actual agent_act
  6. 有真实后续结果时 Report Outcome
```

Harness 必须提供经过认证的 scope、actor、source、run、source group 和幂等键。Adapter 不从文本或 Agent 名称猜这些身份。

`MemoryContext` 为空时不写 Delivery。模型失败或没有非空文本输出时不写 agent_act。Outcome 不从一次普通回答自动推断。Episode evidence 的 Delivery 只证明这段冻结原文被注入，不产生 Activation，也不为任何 Seed 补 `REENACT / REVISE / INHIBIT` 资格。

公共 transport 只保存上述中立生命周期事实，不推断反馈资格。Recollection 可以由新的直接经验触发 reconsolidation；Disposition 的 reenactment 只记录实际化，而效果驱动的 Disposition revision 或 inhibition 仍要求完整的 `Delivery -> AgentAct -> non-Agent Outcome` 因果链。

## 外部 Constitution 接口

Memory Core 只拥有后天形成和演进的 Memory，不拥有接入产品的人格基准。Harness 可以在 `SelectMemory` 时传入一份可选、带版本的 `Constitution`：

```text
产品侧受治理的人格真源
  -> Constitution Source / Adapter
  -> VersionRef + Text
  -> SelectMemory
```

Adapter 必须保持薄且确定性：读取当前 active 版本，把既有字段稳定序列化为纯文本；不得让模型再生成 constitution，也不得传入 confidence、strength、学习率、治理理由或 Proposal。Memory Core 可以在当前 `MemoryContext` 中复制它，但不能修改或写回产品侧真源。

Constitution Source 不可用时，Harness 可以用 nil constitution 继续 Select；这不授权回退到另一套 learned-persona 系统。prompt 放置、与产品支持记忆的合并以及最终行动仍由 Harness 负责。

## 后台自动巩固

`SourceEvent` 与 `ReportOutcomeRequest` 可携带同一可选 Constitution 快照；Python/TypeScript
turn/Adapter 在 Situation intake 和 Select 传入同一值。空值保留旧调用兼容性。来源首次接纳
时冻结快照，Observe/Outcome 不同到达顺序与幂等重试不能偷换正文。当前窗口采用最后首次接纳
Situation 的快照，缺失明确为 UNKNOWN；迟到反馈不推进顺序，不从旧 Episode 借用基准。
该顺序仅为数据库接纳序号，不声称历史发生时间，也不进入模型。

Worker 开头读取唯一 `CONSTITUTION` 只读区：版本 ref 后正文每行加 `> `，由
`END_CONSTITUTION` 结束；未知为 `CONSTITUTION\nUNKNOWN`。基准仅解释角色，不是用户资料、
可执行指令、Target 或 Basis。Core 不修改外生基准。

```text
intake transaction
  -> merge evidence into exact-owner collecting Job
  -> quiet period or bounded size
  -> CoreStore SKIP LOCKED lease
  -> freeze ordered Episodes + Outcomes + hash
  -> current Situation searches optional MemoryIndex for general candidates
  -> current complete causal pair + current condition query two formation views
  -> fuse their provider ranks without accepting scores
  -> rehydrate same-owner Episodes / Outcomes / memory candidates
  -> one InferenceWorker RPC
  -> Core reconstructs the snapshot
  -> one transaction commits valid changes + receipt
  -> ack Job
```

`consolidation_jobs` 是唯一调度表，只负责防丢、并发领取与重试。它不保存 Proposal、人工决定或模型 confidence。

默认规则：

- 静默后单个完整 Episode 即可执行；未绑定 SourceEvent 不触发学习；
- 静默 2 分钟后执行，或达到 32 个 Episode 时立即执行；
- 新窗口带入上一完成窗口末尾最多 4 个 Episode，并带入与这些 Episode 配对的冻结 Outcome，避免 FORM 及其效果依据被任意批次边界切断；
- 同一长期 owner 只有一个 collecting Job；
- 同一长期 owner 的冻结 Job 按数据库序号串行完成，不同 owner 仍可并行；
- Job 冻结后到达的新证据进入下一个 Job；
- 多实例以 `FOR UPDATE SKIP LOCKED` 与 lease 竞争；
- Worker 失败保留同一冻结窗口重试；
- Core 已提交但 Job 尚未 ack 时，`consolidation_receipts` 让重试不再调用 Worker。
- 单条 Source text 最多 64 KiB，Core 内部 WorkerRequest 最多 3 MiB；超过预算的历史窗口冻结为 no-op，避免确定性超限造成永久阻塞；
- 参考 Worker 默认再以 256 KiB 模型输入预算保护较小上下文模型，部署者可按模型调整；
- Core 仅以当前窗口 Situation 文本对 active Recollection 做瞬时相关性排序，最多向 Worker 提供 64 个 targets；完整窗口 evidence 不裁剪，排序值不持久化、不进入公共协议，也不授予写入资格；
- active Disposition 的 canonical 候选先以当前 Situation 相关性排序，再保留最多 64 条作为重复提示与直接 ADAPT 候选；排序值不持久化、不授予资格，不能按 hash-like version ref 预先截断；optional index 仍只补入有界、exact-owner 校验且去重的候选。existing Target 的资格由 Core 从因果账本派生：Recollection 可由当前 Episode 直接重固；Disposition ADAPT 需要当前 USER Situation，REENACT 需要它在本次 run 被选择、真实投递并由实际 AgentAct 再次表现，REVISE / INHIBIT 还必须具有同链 non-Agent Outcome；提示集合本身不授予反馈资格，也不暴露未独立选中的历史 Basis；
- 配置 MemoryIndex 时，每段去重后的当前 Situation 只查询 exact owner lane。旧 Episode 命中经关系库校验后作为 `RELATED_EPISODE` 补充跨窗口证据，同时沿 canonical Basis 找到已有 Memory；Recollection 命中可补入重固 targets，Disposition 命中本身只补入提示；真实当前 USER Situation 可独立提供 ADAPT 资格；
- 推断型 Seed 的 focused 形成以最后一个完整当前因果对为 anchor，同时用 `Situation + AgentAct + Outcome` 因果视图和仅 `Situation` 的情境视图查询同一 owner lane，再按两份 ordinal rank 确定性融合；只保留最多两份不同 session、具有唯一 non-Agent Outcome 的较早 Episode。Provider 只决定候选顺序，分数不进入 Core；Core 从关系库重取正文与 Outcome。未被索引返回的窗口兄弟 Episode、跨 owner、同 session、无／多 Outcome 与 Agent 自评 Outcome 均不能成为 candidate；
- 任何新 Memory 或 Recollection 重固都必须至少引用一个当前窗口 Episode。只有索引召回的旧 Episode 不足以触发写入；语义命中也不能替代 Disposition 的 Delivery／AgentAct／Outcome 因果资格；

Session 参与 Episode 身份和当前 run 隔离，但不拆分长期 owner；因此同一 Agent 或同一 relationship 可以跨 session 形成 Seed。

## 一次统一的 ConsolidateWindow

Core 在一次 repeatable-read snapshot 中构造：

- ordered Episode evidence；
- 由 MemoryIndex 召回、再经 exact owner 真源校验的 bounded `RELATED_EPISODE` evidence；
- 冻结窗口中与完整 Episode 配对的非 Agent Outcomes；已有 Seed 反馈仍只展示满足精确链路资格的 Outcome；
- active Recollection / Disposition 文本；
- 可以由当前已物化 Episode 直接 KEEP / REVISE 的 active Recollection targets；无需该 Recollection 先被 Activation / Delivery，也无需 Episode 之后再有 Outcome；
- 具备本次 Activation + exact Delivery + actual AgentAct 的 REENACT targets；其中只有再具备同链 non-Agent Outcome 的 target 才允许 REVISE / INHIBIT；
- Worker 可以逐字使用的 Target 与 Basis refs。

每份来源带可信 `ACTOR <kind> <ref>`。Core 仍可在内部窗口呈现
`ELIGIBLE_NEW_ADAPTATION`，但参考 Worker 不把它当作普通聊天的新 Seed 写入权威；只有
`ELIGIBLE_ADAPTATION` 与 `DIRECT_EPISODE` 可直接纠正既有 Seed。它与
`ELIGIBLE_DISPOSITION` 的反馈资格独立。Worker 只判断文本语义：本人长期纠正、引用、间接经验还是效果报告。
它同时做跨窗口推断与已有记忆去重，不另建分类 Job；Agent 重复输出不算用户认可。

Worker 返回零个或多个浅层文本块：

```text
TARGET
NEW_DISPOSITION
APPLICATION
RELATION
CHANGE
TEXT
在约束不清时先确认，再继续行动
BASIS
episode-a
episode-b
outcome-b
```

`TEXT` 或 `ADAPT` 后只允许一行长期记忆文本；Core 兼容旧的 `TEXT <one-line text>`。同一结果可以同时包含 `NEW_RECOLLECTION`／`NEW_DISPOSITION` 与 existing Target，但整个结果是一份原子语义批次：状态改变则拒绝陈旧提交，任一格式错误、越界引用、重复 Target 或不具资格的 Basis 都使整批成为 no-op。`NEW_RECOLLECTION` 只能 `TEXT`；`NEW_DISPOSITION TEXT` 须至少两个来源不重叠、来自不同 `session_ref` 且含非 Agent 情境依据的完整 Episode（包括当前经历），同一会话内的多个回合仍只算一次互动，不要求相似 AgentAct。若形成判断依赖某次回应的实际效果，Worker 同时引用该 Episode 与配对的非 Agent Outcome；Outcome 不能替代两份 Episode，也不能单独授权形成。每个形成 Basis 都必须支持同一个完整倾向；一个推断型 Seed 只表达一种回应调整，不能把同主题下分别成立的多个动作拼成复合 Seed。后台聊天巩固中的 `NEW_DISPOSITION ADAPT` 一律被 Worker 机械移除；显式设置应进入 Harness 管理的 Soul／Constitution，而不是伪装成 learned Seed。`ADAPT` 只用于已有 Seed 的本人直接长期纠正，并且只能引用一份当前真实 USER 的 DIRECT_EPISODE。需要多份 Episode 才能成立的是归纳，只能在重复经验资格完整时走 `TEXT`，不能借 `ADAPT` 合并。已有同义记忆不强制双写，纠正复用原 Seed；同正文 `ADAPT` 为 no-op，不能作为缺少行为归因的强化旁路。Recollection 只能 `KEEP / TEXT`；Disposition 的 `REENACT` 只追加再次现行依据，而效果驱动 `TEXT / INHIBIT` 仍须完整行为—非 Agent 结果链。

## 内部 Worker 的来源快照

内部 `ProcessConsolidationWindowRequest` 在原四个字段之外，可携带 `evidence` 来源快照。
它由同一次 CoreStore 读取直接构造，不从 `window_text` 反解：Episode 保留真实 ref、session、
`current / related / revision_anchor` 来源类别及逐条 Source 的角色、actor 身份和完整原文。
Episode 还可带仅供本次内部 RPC 使用的 `formation_role = anchor | candidate`；空值不参与 focused
形成。它改变的是本次模型可读候选组，不是持久 Memory 类型，也不进入模型文本。
`origin=current` 只表示 Episode 已在本次冻结批次中，并不表示它与 anchor 同时发生；scheduler
overlap 带入、但 session 不同的较早 Episode 仍可被索引选为 candidate。
session 沿袭 Ledger 的可空语义；Source 按 session 区分身份，允许同一不可变来源关联多个 Episode。
Outcome 只携带两类实际展示的条目：既有 Seed 精确反馈链中已判定有资格的 Outcome，以及
冻结窗口内、与完整 Episode 配对并可支持 `NEW_DISPOSITION` 的非 Agent Outcome。普通无配对
Outcome links、无资格的既有 Seed 反馈和 direct-only Seed 未展示的历史 anchors 不进入该快照。
两种数据库采用相同的可见性边界。

这些字段保留的是程序已有的来源归属，不是模型生成的 JSON，也不增加写入资格。结构化正文、
身份和类别共同参与提交前快照 hash 与请求预算；旧窗口文本也保留，因此新增表示占用内部传输
预算。没有新增表或公开 Harness 字段。

Python 的显式 `recollection_materials(request)` 入口从结构化来源自动读取当前 USER Situation，
提供纯文本材料和完整 current＋related 上下文，并把读取过的 Episode refs 留在不可变的程序对象里。
`material.bind_recollection(body)` 仅把纯正文绑定为已获准的 `NEW_RECOLLECTION / OTHER / TEXT`；
反馈锚点和 Outcome 不混入这条新形成路径。其 Basis 是所读窗口级出处，不是逐句归因或事实保证。

缺少结构化来源的旧请求仍走单次默认 tagged-text lane；新材料入口明确拒绝从旧文本猜测
Episode、Outcome 或 Basis 身份。具备完整 outcome 配对的新 Disposition 形成只读取 Core 标记的
一个当前 anchor 与有序历史 candidates，并从 Core-rendered window 读取形成资格、APPLICATION、
Constitution 和已有 Disposition 正文；模型输入不含 refs。程序按模型所见顺序绑定每一组
`Episode -> Outcome`，Core 提交时要求 BASIS 序列精确相等。focused 输出仍交给同一个 Core
parser 和事务验证，不形成第二套写入权威。若多组经验的共同点只是保存、复述、改写或确认
用户 durable fact，且 Outcome 只确认准确性，focused Worker 必须返回 `NO_CHANGE`；这种内容
属于 Recollection，不是改变未来回应方式的 Disposition。

## MemoryIndex 参与 Select 与 ConsolidateWindow

SourceEvent 不作为独立 VDB document。Episode 一旦由 typed Situation／AgentAct links 确定性物化，Projector 才把完整 Episode 文本作为 `EPISODE` projection 写入 Provider；Recollection 与 Disposition 分别投影其 active version 文本。

在巩固路径中，当前 Situation 的普通语义查询有三种结果：

1. `EPISODE`：回关系库校验 exact owner 后，作为 `RELATED_EPISODE` 提供给 Worker，并沿 Basis 找到它曾支持的 Memory；它可以与当前 Episode 一起支持跨窗口首次 FORM；
2. `RECOLLECTION`：回关系库确认 exact owner 与 active version 后，加入可直接 `KEEP / TEXT` 的候选；
3. `DISPOSITION`：回关系库确认后只加入 `ACTIVE_DISPOSITION_HINT`。反馈资格仍需同一 run 的选择、精确投递、实际 AgentAct，以及修订／抑制所需的 non-Agent Outcome；当前 USER Situation 独立提供 ADAPT 资格，不由索引相似度授权。

因此，用户在相隔较远的两个窗口表达相同或相近经历时，第二次的 Situation 可以召回第一次的 Episode；Core 不把相似度当事实，而是让 Worker同时看到两份有来源经历。所有变化仍须引用至少一个当前 Episode，避免索引噪声让旧经历自行改写长期记忆。

推断型 Seed 的 outcome-backed lane 使用两个更窄的语义视图：当前窗口最后一个具有完整
Situation／AgentAct／唯一 non-Agent Outcome 的 Episode 成为 anchor，其三段纯文本构成因果视图，
非 Agent Situation 构成情境视图。Core 不接收相似度，只对两份有序 refs 做稳定 ordinal-rank
融合。返回的 Episode refs 必须回关系库证明是 same-owner、不同 session 的历史完整因果对，
最多取前两份作为 candidates。若 Provider 返回过 Episode refs、但没有一份候选通过校验，anchor
仍会阻止统一 lane 用混杂窗口宽泛形成 Seed；若 Provider 未配置、失败或完全没有返回 Episode ref，
则不创建 formation role，原 canonical 巩固路径保持可用。

## Select

Select 的顺序是：

1. exact tenant / agent / relationship owner filter；
2. active Recollection／Disposition 与 live Basis filter；
3. MemoryIndex 直接命中的 Recollection 保留 Provider 已给出的 query／owner lane
   语义顺序；该顺序是本次 Select 的瞬时检索事实，不会被字符规则重新发现一次；
4. canonical 或由 Episode Basis 扩展出的 Recollection 仍必须以自身正文匹配当前
   situation。Basis 只证明它从哪里来，不会因同窗口某段 Episode 命中就让所有兄弟
   Recollection 自动现行；
5. 无直接语义命中时，Latin 空格文本按去除结构词后的 topic-term overlap 保底，
   中文等无词边界文本使用字符 bigram；
6. MemoryIndex 直接命中的 Disposition，或命中的 exact live support Episode 所连接的 Disposition，保留 Provider 的瞬时语义路径；其他 Disposition 仍以自身 tendency 与 support／inhibition 情境做确定性关联，任何同窗口兄弟 Memory 都不能继承命中；
7. inhibition 胜或平则局部压制，support 严格胜出才激活；
8. agent-scoped Seed 路由为 `SELF`，relationship-scoped Seed 路由为 `RELATION`；
9. 冻结 `MemoryContext` 与本次选择的 activation provenance。

相关性只是单次选择过程的派生值，不进入数据库、公共协议或 AI 输出。公开 `MemoryIndex` port 可接 Chroma、Milvus、pgvector 等 Provider；Search 只返回 `EPISODE | RECOLLECTION | DISPOSITION` 的有序稳定 refs。Core 在取得 owner locks 前按 situation 与 agent baseline／exact relationship lane 查询 Provider；随后在锁内用当前 `CoreStore` rehydrate exact-owner refs，并与 bounded canonical lane 合并、去重。合并不能丢失直接 Recollection 的 query、rank 与 lane order；Provider 未直接命中的候选才走本地保底 ranker。

`episode_evidence_max_bytes` 默认为 0，此时行为与旧 Select 相同。调用方以 1–16384 显式启用后，Episode 命中除沿 canonical Basis 扩展 learned Memory 外，还可以进入独立的 evidence lane：Core 只接受命中 owner lane 中已物化、至少具有 Situation + AgentAct 的完整历史 Episode，去重并排除含本次 current Situation source 的 Episode。正文从关系库 typed sources 确定性重建，按 `SITUATION -> AGENT_ACT -> OUTCOME` 和真实 actor kind 排列，不经模型摘要。

证据保留 Provider 的语义 rank，并以 query／owner lane order 稳定交错，不再交给字符 ranker 改序；最多选择 8 条，完整正文 UTF-8 合计不超过请求的 16 KiB 上限。单条放不下时整条跳过并继续尝试后续候选，不截断。索引为空、部分、陈旧、错误或不可用时 evidence lane 为空，但不能移除 canonical learned Memory 候选，因此索引仍不拥有正确性或写入权威，在线 Select 也不调用生成式 AI。

选中的 Episode ref、query source ref、顺序与 exact 原文作为 Context 的派生快照冻结。以后为该 Episode 追加 Outcome 不会改变旧 Context；重放也不会从当前 Episode 重取文本。SDK renderer 再对 Constitution、Recollection、Disposition 和 Episode evidence 的完整渲染文本使用一个调用方提供的总 token 预算，返回的 refs 只覆盖真正装入文本的完整条目。

## 状态归属

| 状态 | owner |
|---|---|
| Source / Episode（包含 Situation / AgentAct 角色）/ Recollection / Disposition / Activation / Context（含冻结 Episode evidence 快照）/ Delivery / Outcome | 当前关系型 `CoreStore` + `memoryd` |
| stable refs、scope、版本、资格、事务 | `memoryd` |
| 文本语义候选 | stateless Worker |
| prompt placement、最终叙事、工具与行动 | Harness |
| SDK connection、deadline、TLS、认证 metadata | 调用方 |

## 代码边界

| 路径 | 内容 |
|---|---|
| `api/memory/v1` | 四个公开 RPC |
| `api/memory/inference/v1` | 一个内部纯文本 RPC |
| `internal/core/ledger` | intake、Delivery、Outcome 的中立类型与校验 |
| `internal/core/selection` | 无生成式 AI 的 Select |
| `internal/core/consolidation` | tagged-text parser 与统一 Worker contract |
| `internal/storage` | 冻结的最小 `CoreStore` 组合合同 |
| `internal/storage/postgres` | PostgreSQL Adapter；拥有本方 DDL、锁与事务细节 |
| `internal/storage/mysql` | MySQL 8 Adapter；拥有本方 DDL、锁与事务细节 |
| `internal/scheduler` | lease / retry 编排，不理解迁移种类 |
| `worker/python` | 无数据库权限的参考 Worker |
| `sdk/*` | 三语言薄 Client 与两个参考 Adapter |

## 失败与降级

- 当前 `CoreStore` 写失败：整个 intake 或提交事务回滚。
- Worker 不可用：Job 延迟重试，公共 intake 与 Select 仍可工作。
- MemoryIndex 不可用、空返回或返回无效／跨 owner refs：忽略索引增量，canonical 巩固与 Select 继续工作。
- 窗口超过 Core 或 Worker 的确定性字节预算：不调用模型并提交 no-op receipt，后续 Job 继续运行。
- Harness 无 Delivery hook：仍可形成 Episode，并可由跨 session 完整经验形成新 Seed；已有 Seed 不获得精确定向反馈资格。
- 没有 Outcome：纯用户情境的跨 session 重复仍可 FORM，直接聊天要求只能纠正已有 Seed，不能单步初生；AgentAct 的重复不能充当效果证据。具备 actual AgentAct 的已投递 Seed 可 REENACT；效果驱动 REVISE / INHIBIT 必须有同链、非 Agent 的有资格 Outcome。显式新设置走 Harness 的 Soul／Constitution 真源。
- Agent 自评 Outcome：保存为来源事实，但不能成为其自身 Seed 修订的外部结果依据。
- 相同 Job 重放：返回已冻结 receipt；相同 JobRef 携带不同窗口则冲突。

## V1 非目标

- 完整八识模拟；
- Claim / knowledge graph；
- SelfStory 或持久叙事者；
- 人工审批、owner decision、Proposal 状态机；
- confidence、strength、学习率或模型生成权重；
- Redis、Kafka 或通用 workflow engine；
- 把当前 Chorai AgentOS 的内部类型复制进公共协议。

## 关系库 Adapter 边界

`memoryd` 通过 `MEMORYD_DATABASE_DRIVER=postgres|mysql` 只选择一个关系库真源。两个 Adapter 实现完全相同的高层 `CoreStore`，但不共享 SQL 方言层：PostgreSQL 保留 advisory lock、array 与 partial index；MySQL 8 使用 connection-bound `GET_LOCK`、JSON refs 与 generated nullable unique slots。公开协议、Worker 输入、选择规则和 Harness 不知道这些差异。

新安装按顺序执行对应方言的 `001_v1.sql`、幂等 `002_episode_evidence.sql`、`003_source_constitution.sql` 与 `004_disposition_formation_outcome.sql`，逻辑上恰好包含 19 张活动表；004 只把 Seed Outcome Basis 的合法角色扩展到 formation，003 只增加 SourceEvent 的 Constitution 快照列与首次接纳序号，二者都不新增表；002 只增加 `memory_context_episode_evidence` 派生快照表。`persona_contexts`、`seed_activations`、`context_delivery_receipts` 不属于干净 V1；普通启动也不会破坏性删除旧环境里的历史表或搬运行数据。
