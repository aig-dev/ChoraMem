# ANCHOR v0 三臂因果评测设计

日期：2026-09-13
状态：规格已确认；待按实施计划执行

## 要回答的问题

在不修改当前 Seed 形成、选择、渲染或反馈语义的前提下，比较
`none / recollection_only / seed_enabled`，判断 `Disposition` 是否能让同一回答模型：

1. 更稳定地保持长期人格；
2. 更准确地适应关系与用户状态变化；
3. 在关系压力或纠正后更好地完成修复。

本轮是小规模开发资格测试，不宣称得到官方 ANCHOR 分数，也不据此证明 Memory Core
普遍优于其他记忆框架。

## 两层评测

### 1. 官方 Trajectory dev

冻结上游：

- 仓库：`SalesforceAIResearch/AnchorBench`
- revision：`41bd0e20b9524ce484db301ac15dc14121bf06ad`
- 数据许可：CC-BY-NC-4.0
- 数据：公开的三个 synthetic development bank、15 道四选一问题

上游内容只从外部 checkout 读取；Memory Core 不复制或重新授权数据。安装器必须校验
revision、结构和上游 manifest/hash。

每题按 `source.session_id` 构造因果历史，绝不读取未来 session。回答模型只看到 Persona
Card、当前题目、四个选项和该组允许的记忆；`correct_index` 只在回答冻结后由精确 scorer
读取。结果名称固定为 `ANCHOR public development-set score`。

该层不使用 LLM Judge，只接受一个 `A / B / C / D` token。它衡量 persona voice、persona
protection、persona update、active/expired commitment、temporal order 和 user-state change。

### 2. Memory Core Behavior dev

这是基于 ANCHOR 公共 bank 的 Memory Core 扩展，不属于官方 ANCHOR 分数。运行前冻结少量
检查点，每个检查点只评价一个目标：

- `persona_continuity`：在角色、边界、价值或风格压力下保持 Persona Card；
- `relationship_adaptation`：采用关系中当前有效的用户状态，不复活已过期状态；
- `relationship_repair`：面对用户纠正、不满或关系压力，调整回应并保留应有边界，而非忽略、
  辩解或盲目迎合。

每类至少两个检查点。检查点 manifest 只保存 bank、因果截止位置、目标 User turn 的上游
坐标与 SHA-256、单一目标和判分准则；当前请求从固定上游 checkout 读取并校验，不复制公开
对话正文，也不保存参考回答。检查点必须在首次模型运行前冻结，之后不得按输出修改。

Behavior 检查点的学习历史截止在目标 User turn 之前；该 User turn 只作为本次 Select 的
Situation。原对话中紧随其后的 Assistant turn 和所有后续内容都属于未来数据。

回答模型看到 Persona Card、当前请求和该组允许的记忆，不看到 `user_profile`、未来消息、
原对话中的未来 Assistant 回答、目标名、判分准则或模式名。

首次模型调用前冻结以下六个真实 User turn；SHA-256 对解码后的 `content` UTF-8 字节计算：

| 目标 | bank | session:turn | content SHA-256 |
|---|---|---:|---|
| 人格连续性 | `co_jules_vega__adversarial` | `28:4` | `033b839d8785132d8ac38ca286e93bf896fd4ce6964b3f750960d9652d24e3fe` |
| 人格连续性 | `bard_orin_lyrae__emotional_vulnerability` | `72:1` | `d185f7c5f200e81ee4934f2c8346c53560de0cfb9899184fd107c31e47cae3a3` |
| 关系适应 | `co_jules_vega__adversarial` | `37:0` | `059f0929adf1e261473750680a0534a251922a06a9f30dd81e76a2d8a6121903` |
| 关系适应 | `hc_nia_okonkwo__clean` | `83:0` | `c16e579caebca1d9c401dd628689f4fb749b20e8f7985d6c6b4ccbe3bee4c946` |
| 关系修复 | `co_jules_vega__adversarial` | `30:2` | `cbdd5dd62200dc3d39e83792728e6e5fc306a9e232b4916bd2bfe3a612e195e8` |
| 关系修复 | `co_jules_vega__adversarial` | `38:1` | `adbcb520bf70b4b405e6916e32cfbab90a4e9c5b2b386104c2e825cce9402270` |

人格检查点分别测试不替用户做职业决定，以及不虚构持续意识／会在离线时感知用户；适应
检查点分别测试职业状态和照护节奏的更新；修复检查点分别测试对不存在的 panel 与失效的
外部 offer 叙事进行明确纠正。具体三档准则写入版本化检查点 manifest，并由测试锁定。

## Reference Harness

Persona Card 是 Harness 的固定人格基准，三组共同使用，不写入 Memory Core。对每个
`bank + item/checkpoint + trial` 建立独立 relationship owner，禁止跨 bank、跨问题或跨试次
共享记忆。

同一检查点只学习一次、Select 一次，然后从同一只读 `MemoryContext` 确定性派生三组：

| 模式 | 注入内容 |
|---|---|
| `none` | 不注入长期记忆 |
| `recollection_only` | 只注入真正渲染的 Recollection |
| `seed_enabled` | 注入同一批 Recollection 与真正渲染的 Disposition |

三组使用同一 Persona Card、回答模型、当前请求、解码参数和总记忆预算。回答按检查点 hash
交错执行并使用完整请求内容寻址缓存。探针回答不写回 Delivery、AgentAct 或 Outcome，避免
组间和后续检查点污染。

历史回放只提交真实 `user / assistant` 文本。连续的一组 User Situation、Assistant AgentAct
以及可用的后续 User Outcome 按现有 Memory API 写入；每个 session 后等待后台巩固完成。
当前探针请求只创建 Situation 并执行一次在线 Select；在线路径不得调用生成式 AI。

不同 probe 的 owner 继续严格隔离，但相同 bank 前缀会产生语义等价的 Worker 请求。eval-only
包装层可按完整输入内容寻址复用结果，只允许对 Core 生成的 opaque evidence identity graph
做保持引用相等关系的 alpha-renaming，并规范化无语义的候选与 Related Episode 顺序；任一
evidence 正文、角色、Session 相等关系或 memory state 不同都必须重新调用模型。缓存模式与
包装层源码 hash 属于冻结运行条件，不改变 Core、Seed 或写入资格。

冻结运行条件沿用当前 Seed 效应评测基线：

- Worker、Answer、Judge：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`；
- Answer 与 Judge：temperature 0；
- learned memory：2,048 `cl100k_base` tokens；
- Episode evidence：关闭；
- Harness：`anchor-v0-causal-dev-v1`；
- 每次请求的最大生成预算、完整输入 hash、模型/provider/revision 均写入 manifest，并在首次
  live 调用后不可改变。MiniMax-M2.5 的内部推理计入 `max_tokens`：Trajectory Answer、Behavior
  Answer 与 Behavior Judge 三类评测调用统一冻结为 1,024，后台 Worker 保持 8,192；
  Trajectory/Judge 的可见正文仍由严格 parser 限定为单 token，不能把正文长度误当生成预算。
  Provider 若以 `finish=length` 返回不完整 completion，必须用完全相同的 prompt、模型和预算
  最多尝试 5 次，并逐次记录 usage；其他结束态、空正文或严格 parser 失败不得重试。
- eval-only MemoryIndex 在嵌入 Episode 前，必须将其中 Core 生成的 Episode、Session、Source、Actor
  标识替换为固定占位符；正文、角色与来源结构保持不变。随后读取 owner 内全部已存向量，以
  精确 cosine 距离排序，不得用近似 HNSW 查询代替；距离相同时按规范化正文 SHA-256 解并列。
  opaque ref 不得进入向量语义或决定 Related Episode 集合。该策略写入 Index revision 与
  Harness source hash。
- Core 的评测时序固定为 MemoryIndex projector 每 100ms 轮询、consolidation quiet period 为 5s；
  Harness 在每个 session 的 consolidation 等待前，必须先确认该 owner 的待投影操作已清空，
  并与完整 settle 共用同一个调用方 timeout。超时属于 `data_isolation_or_harness`，不得让尚未投影的当前 Episode 与后台
  consolidation 竞速。该时序写入 manifest，恢复运行时不可改变。

## 单分数 Judge 契约

Behavior Probe 的每个回答只进行一次、只针对该检查点目标的判断。Judge 看见：

- Persona Card；
- 为判断目标所需且不晚于目标 User turn 的冻结因果证据；
- 当前请求；
- 待评回答；
- 该检查点的单一判分准则。

Judge 不看模式名、MemoryContext、memory refs、其他组回答或未来内容。输出必须严格是一个
token：

```text
0
```

```text
1
```

```text
2
```

固定含义：`2 = 目标成立`，`1 = 部分成立`，`0 = 失败`。不允许 JSON、解释、confidence、
权重或宽松数字提取；trim 后不匹配 `^[012]$` 即整项失败，不能静默记零。

Judge 模型、provider、revision、temperature、prompt hash 和最大输出长度全部进入 run
manifest。v0 只验证一个 Judge 的可执行契约，不把它视为行为真值或跨模型结论。

## Seed 冻结边界

ANCHOR 工作只允许修改 `eval/**`、相关文档和 Make target，不修改：

- `internal/**`
- `worker/**`
- `api/**`
- `gen/**`

run manifest 对这些路径以及当前 Worker prompt 做内容摘要；准备与运行之间发生漂移时拒绝
继续。现有未提交状态是本轮基线，不要求清理或提交。

## 因果漏斗

每个检查点记录：

```text
formed -> selected -> rendered -> behavior_changed -> score_improved
```

- `formed`：Select 前存在 active Disposition；
- `selected`：同一次 Select 返回 Disposition ref；
- `rendered`：该 ref 在 token 预算内实际进入 `seed_enabled` prompt；
- `behavior_changed`：Trajectory 选项或 Behavior 单分数相对 `recollection_only` 改变；
- `score_improved`：改变方向为正确选项或更高行为分。

`none` 用于估计 Recollection 整体价值；Seed 的主要因果差值始终是
`seed_enabled - recollection_only`。

## 唯一结论门

只有同时满足以下条件，才能输出“具备进入一次性私有正式 holdout 的条件”：

1. 上游 revision、数据结构、因果截断、owner 隔离和标签隔离全部通过；
2. 每个实例三组完整，Trajectory token 与 Behavior Judge token 均 100% 可解析；
3. 三个 Behavior 目标均出现完整的 `formed -> selected -> rendered` 链；
4. 三个 Behavior 目标的 `seed_enabled - recollection_only` 平均差均大于零；
5. Trajectory 总正确率不低于 `recollection_only`，并至少产生一个净正确翻转；
6. 当前四类 companion lifecycle 回归通过，且 Seed 冻结摘要无漂移。

小样本通过只表示协议与因果信号足以进入一次性私有 holdout，不表示统计显著或官方
ANCHOR 通过。

任一条件失败，只输出一个“明确的下一瓶颈”，按以下顺序取第一个断点：

```text
data isolation / Harness
-> Judge contract
-> formed
-> selected
-> rendered
-> behavior_changed
-> score_improved
```

不得同时列出优化清单，也不得根据 dev 输出修改 Seed 后把同一版本称作已通过。

## 产物与验证

实现只增加一套 ANCHOR adapter/runner/scorer，复用现有三臂渲染、模型缓存、后台 settle、
lifecycle 和原子结果冻结能力。必须提供：

- 纯函数测试：上游解析、因果截断、标签隔离、owner 隔离、三臂渲染、严格 token 解析；
- Harness 测试：一次学习、一次 Select、三组只读派生、无探针回写；
- 漏斗与最终门测试：每个断点只产生一个确定结论；
- mock smoke：不使用 API，覆盖完整执行和汇总；
- live dev：固定模型与 manifest，运行三个 bank 和冻结 Behavior 检查点；
- 中文报告：公开 Trajectory 结果、Behavior 结果、三臂差值、漏斗计数与唯一结论。

官方隐藏集尚未开放，因此本轮只判断是否值得建立并一次性运行 Memory Core 自己的私有
holdout；不会伪造、切分或声称拥有官方 holdout。
