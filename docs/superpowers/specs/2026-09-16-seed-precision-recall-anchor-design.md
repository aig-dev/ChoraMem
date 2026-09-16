# Seed 精度、召回与 ANCHOR 可测性设计

## 目标

在不增加 Memory Core LLM 调用的前提下，让冻结的 `seed-generalization-v1` 达到：

- 目标 Seed 语义形成 `4 / 4`；
- 纯事实型错误 Seed `0`；
- 负对照误形成 `0 / 4`。

随后用能够区分 Recollection 基础能力与 Disposition 人格增量的 Reference Harness 复验行为效果。

## 当前失败

1. outcome-backed formation 只用 `Situation + AgentAct + Outcome` 做一次语义查询。writing 样本的第二份同类历史经历被游泳事实挤出 top-2，目标 Seed 只有 `3 / 4`。
2. focused Worker 会把“Agent 复述事实，用户确认准确”归纳为长期响应倾向。事实应进入 Recollection，不应生成 Disposition。
3. ANCHOR v0 的关系适应与修复题主要检验事实更新，`recollection_only` 已经满分；它们不能证明 Seed 是否产生人格增量。

## 生产设计

### 1. 双视图候选检索

同一个完整 anchor 产生两个 MemoryIndex 查询：

1. 完整因果视图：`Situation + AgentAct + Outcome`；
2. 情境视图：仅当前非 Agent `Situation`。

Core 只使用 Provider 返回顺序，按两个 ordinal rank 的和排序；同分时依次使用较差 rank、完整因果视图 rank、首次出现顺序和 ref，得到稳定顺序。候选仍须回关系库验证 exact owner、不同 session、完整 `Situation + AgentAct` 和唯一 non-Agent Outcome。分数不持久化、不进入协议、不进入 prompt，也不授予写资格。

选择这一方案是因为冻结索引上的离线消融显示：单完整视图正例 recall@2 为 `3 / 4`；完整视图与情境视图的秩融合为 `4 / 4`。它只增加一次 VDB 查询，不增加生成式 AI 调用。

若情境查询失败，保留完整因果查询的旧排序；若完整因果查询失败，则不建立 focused group，维持当前 fail-closed 语义。

### 2. 事实边界

focused Worker 仍只调用一次现有文本模型。规则新增一条语义边界：若共同模式只是保存、复述、改写或确认用户的生平、关系、地点、日程、健康信息、物品或项目等 durable fact，且 Outcome 只确认准确性，则输出 `NO_CHANGE`。只有能跨具体事实改变未来回应方式的策略，才可形成 Disposition。

Core 不用关键词硬编码事实分类；模型仍只输出一行 tendency 或 `NO_CHANGE`，Core 继续绑定 APPLICATION 和 exact BASIS。

### 3. 普通聊天的确定性初生边界

实跑证明仅靠 prompt 不能可靠区分“一次成功／个人经历”和“本人直接长期设置”：统一 lane 仍会
把单次 witness 经验或重复失败反推成 `NEW_DISPOSITION ADAPT/TEXT`。因此参考 Worker 对普通聊天
确定性移除所有 `NEW_DISPOSITION ADAPT`；具备 focused formation anchor 时也移除统一 lane 的
`NEW_DISPOSITION TEXT`，只接受专用跨 session 因果组的结果。

显式产品设置属于 Harness 的 Soul／Constitution 真源；已有 Seed 的本人直接长期纠正仍可
`ADAPT` exact Target。这一边界不增加调用、表、协议字段或数值判断。

## ANCHOR 复验设计

ANCHOR v1 Reference Harness 分成两条结论，不能混写：

- AnchorBench trajectory 与事实更新题：验证长期 Recollection 基础能力，不要求 Seed 优于 Recollection；
- companion-disposition 题：验证 `seed_enabled` 是否优于 `recollection_only`。

companion-disposition 使用盲双向 pairwise Judge，并加入 `oracle_seed` 与 `anti_seed` 操纵臂。只有 Oracle 明显优于 Anti 且优于 Recollection，才认定评测没有天花板。Reference Harness 的 prompt 明确解释：Recollection 是过去经验的理解，Disposition 是在相关场景中应影响回应方式的已学习倾向；当前请求仍优先。该改动不改变 Core 输出，也不增加 Core 的 LLM 调用。

判定顺序固定为：

```text
formation precision/recall
-> selected
-> rendered
-> oracle manipulation check
-> learned Seed vs recollection_only
```

只有前四项通过而最后一项仍无稳定正收益，才把瓶颈定为架构层：中立文本 Disposition 经合理 Harness 解释后，仍不足以稳定塑造行为。

## 不做

- 不增加 confidence、strength、阈值或模型生成分数；
- 不新增 Memory 类型、数据库表、协议字段、Proposal 或人工审批；
- 不把 Provider 相似度变成写入资格；
- 不把 ANCHOR 自定义扩展声称为官方 AnchorBench 分数。

## 2026-09-16 复验结果

- 形成门：目标 Seed `4 / 4`、事实型错误 `0`、负对照 `0 / 4`；四个正例均为
  `[0,0,0,0,0,0,0,1]`，没有提前形成；
- ANCHOR companion：formed / selected / rendered 均为 `4 / 4`；
- 3 / 4 checkpoint 同时通过 Oracle-vs-Anti 与 Oracle-vs-RAG，learned 在这三题为 `3 / 3`；
- `witness-win` 的 Oracle-vs-RAG 位置不一致，因此最终为 `evaluation_ceiling`，不作架构失败判决。
