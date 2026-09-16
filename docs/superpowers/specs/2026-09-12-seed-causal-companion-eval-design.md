# Seed 因果闭环与长期陪伴评测设计

日期：2026-09-12
状态：冻结（2026-09-13 holdout 完整性修订 v2）

## 要回答的问题

本轮只回答一个问题：在相同模型、Harness 和总记忆预算下，`Disposition`
是否比只有 `Recollection` 更能让 Agent 持续贴近同一用户，并且它是否只在真实影响
行为并收到后续结果后才被修订或抑制。

在得到答案前不增加 Seed 表、权重、confidence、人工审批或新的在线模型调用。

## 两条互补证据

### 1. 外部效应：PERMA MCQ

采用 PERMA 的 clean、single-domain、incremental timeline。它检验长期互动中逐步出现、
变化并经无关对话间隔后的用户偏好。只使用官方选择题的精确答案，不使用 LLM judge。

冻结上游：

- 代码：`MINE-USTC/PERMA@d678640987170e8cfbe9260b311e0493b9cd2c31`
- 数据：`ustclsc/PERMA@440e64e4fb8baec6f7ad10c1de135505f93e7cb1`
- 数据许可：Apache-2.0
- 下载镜像只属于 transport；manifest 必须记录每个实际文件的 SHA-256。

只读取：

- `tasks/user<ID>/raw_dialogues_c.json`
- `tasks/user<ID>/interleaved_timeline.json`
- `evaluation/user<ID>/meta/overall/SD-*_2.json`
- `evaluation/user<ID>/meta/overall/SD-*_3.json`

`preferences`、profile、gold explanation 和其他隐藏字段不得进入 Core、Worker、检索或回答
模型。`gold_label` 只在答案落盘后由 scorer 读取。

#### 冻结的 persona 隔离

全集：`108, 109, 112, 123, 1377, 334, 354, 419, 507, 914`。

v1 曾按以下排序键分成 A/B：

```text
sha256("memory-core-perma-seed-v1-20260912:" + persona_id)
```

按升序前五个为 A，其余为 B：

- A：`1377, 123, 109, 914, 354`
- B：`112, 507, 334, 419, 108`

旧语义诊断运行已经查看 A 的部分效应并据此修正跨会话资格和复合 Seed，因此 A 永久降级为
开发诊断，不能再作为 holdout。旧运行在进入 B 前终止；B 的五个 persona 没有问题产物、
Memory 快照、Worker window 或索引记录。

PERMA 只有这十个 persona。为保持 100 题而不伪称 A 未参与调参，v2 在任何 B 文本、问题、
选项或标签被检查前，以

```text
sha256("memory-core-perma-holdout-v2-20260913:" + persona_id)
```

排序 B，并冻结为两个互斥 holdout：

- H1：`419, 112`
- H2：`507, 108, 334`

H1、H2 都不得再用于修改 prompt、Core、选择规则或评测参数。正式运行先 H1 后 H2，二者均
只运行一次；传输失败可以用相同请求和内容寻址缓存恢复，不能换参数重跑。若正式门失败，
本轮结论就是不通过；不能根据 H1/H2 再调参后把同一结果称作 holdout。

#### 冻结的题目选择

每个 holdout persona 只纳入同时存在 Type 2 与 Type 3 的 single-domain task。按

```text
sha256("memory-core-perma-task-v2-20260913:" + persona_id + ":" + task_id)
```

排序，取前 10 个 task；每个 task 同时评测 Type 2 和 Type 3，共 20 题/persona、
H1 40 题、H2 60 题、100 题合并。若 persona 不足 10 个完整 task，preflight 失败，不能
临时补题。

按官方 `interleaved_timeline` 的顺序逐批写入对话。每道题在其 `question_date` 对应的
时间点执行；同日期按 timeline 原顺序后再查询。只有 `conversation` 中真实的 user / assistant
文本进入 SourceEvent。每个对话 session 是一个 consolidation window，不把偏好标签写入记忆。

#### 三个回答模式

| 模式 | 注入内容 |
|---|---|
| `none` | 空长期上下文 |
| `recollection_only` | 同一次 Select 中真正渲染的 Recollection |
| `seed_enabled` | 同一次 Select 中真正渲染的 Recollection + Disposition |

两个 Core 模式共享同一 persona 因果账和同一次 Select，只改变确定性的渲染过滤；不各自学习，
避免 Worker 随机性成为模式差异。三组回答按 question hash 确定性交错，并使用内容寻址缓存。

冻结运行条件：

- 回答模型与 Worker：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`
- Reference Harness：`perma-seed-mcq-v2`
- Constitution：空
- learned memory 总预算：2,048 `cl100k_base` tokens
- Episode evidence：关闭
- 回答只要求一个选项 token；`max_output_tokens=32768`
- Core、Worker、MemoryIndex revision、prompt hash、文件 hash 和所有超时进入 manifest

外部 MCQ 是只读探针：不记录 Delivery、AgentAct 或 Outcome，保证后一道题不会被评测答案
反向污染。它证明形成后 Seed 的输出增益，不单独证明反馈更新。

### 2. 因果资格：固定 companion lifecycle

使用不来自 PERMA 的人工固定文本，运行真实 Memory API、scheduler、Worker 和 Reference Harness：

```text
两个来源不重叠的间接互动窗口
  -> 后台形成 NEW Disposition
相关新境遇
  -> Select 并渲染关联 Recollection + Disposition
  -> exact Delivery
  -> 模型真实 AgentAct
  -> 固定的非 Agent 用户 Outcome
  -> 后台 REENACT / REVISE / INHIBIT
  -> 延迟复测改变未来选择或回答
```

场景使用固定选择项，因此行为与用户结果可精确评分，不使用 LLM judge。必须同时包含：

- 正链：Seed 确实进入 prompt、行为表现该倾向、有用户结果；允许 Seed 更新。
- 无投递控制：Seed 未进入 prompt；相同结果不得更新 Seed。
- 无结果控制：有投递和 AgentAct、没有非 Agent Outcome；不得修订或抑制。
- 负反馈链：完整链存在；旧倾向被情境抑制或语义修订，并改变延迟复测。

模型仍只处理文本。Worker 仍只输出 `TARGET / APPLICATION / CHANGE / BASIS`，Core 独立校验
refs、owner、版本与因果资格；没有 owner decision。

## 主要指标和通过门

PERMA 以 question-paired accuracy difference 为单位，按 persona 整簇 bootstrap 2,000 次。

必须同时满足：

1. H1 中 `seed_enabled > recollection_only`。
2. H2 中 `seed_enabled > recollection_only`。
3. H1+H2 的 persona-cluster 95% bootstrap 区间下界大于 0。
4. 两个 split 都至少出现一个由 Disposition 导致的净正确翻转，且错误、空输出、截断为 0。
5. manifest、逐题选中/渲染 refs、token 数、Seed 形成/现行覆盖率可复算。
6. companion lifecycle 的四类因果场景全部通过，且在线 Select 没有生成式 AI。
7. 现有 PersonaMem `learned_core` 冻结样本成绩不低于已发布基线：A `18/35`、B `15/32`；
   若 Core/Worker/选择规则没有变化，以同 revision 的既有产物加完整回归测试证明不退化；
   若有变化，必须在相同参数下重跑。

若门失败，先根据 `formed -> selected -> rendered -> answer flip -> feedback update` 漏斗定位唯一
瓶颈，再修改 Core。不得为了某个 task、persona、选项或关键词写特例。

## 不属于本轮

- 不以 PERMA Type 1、interactive LLM judge 或 ES-MemEval 作为主要门。
- 不对外声称验证了佛学或神经科学理论。
- 不把 Recollection 与 Disposition 合并，也不预先新增显式关系表。
- 不修改 Chorai 主线；所有代码、数据缓存和报告都留在独立 `memory-core`。
