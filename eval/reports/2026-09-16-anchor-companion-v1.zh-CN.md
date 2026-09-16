# ANCHOR companion-disposition v1 实测

日期：2026-09-16

## 结论

当前不能把剩余失败判为 Memory Core 架构瓶颈，首要瓶颈仍是 **评测天花板**。

形成与写入工程门已经通过：目标 Seed `4 / 4`，全部只在第 8 个跨 session 经验完成后形成；
选择 `4 / 4`，渲染 `4 / 4`，纯事实型错误 Seed `0`，四个负对照误形成 `0 / 4`。

行为侧有 3 / 4 个 checkpoint 同时满足 Oracle 胜 Anti、Oracle 胜 RAG。只在这三个可操纵
checkpoint 上比较，learned Seed 对 RAG 为 **3 胜 / 0 负 / 0 平 / 0 不一致**。最后一个
`witness-win` 中 Oracle 对 RAG 本身为位置不一致，因此 learned 输给 RAG 不能归因于
Disposition 架构；该题不产生架构判决。

严格结论为：

```text
status: evaluation_ceiling
bottleneck: oracle_vs_rag
architecture_verdict: false
```

这只支持“当前架构在三个可辨识的陪伴行为上产生了初步独立增量”，不支持完整人格陪伴优势，
也不支持对其他 Harness、模型或自然分布外推。

## ANCHOR 的边界

上游 AnchorBench 的公开 Trajectory 题主要检验长程 persona／状态轨迹；它不是纯粹的“人格陪伴
benchmark”。本项目的 companion-disposition 是自定义扩展，不属于官方 AnchorBench 分数。

本轮没有把事实更新题继续当作 Seed 增量：

- AnchorBench Trajectory 与事实更新只承担 Recollection／长期轨迹基础能力；
- 本轮四个 companion checkpoint 才比较 learned Disposition 与相同 Recollection RAG；
- 每题先要求 Oracle 同时胜 Anti 与 RAG，之后才允许解释 learned-vs-RAG。

## 单变量改良

形成历史、四个负对照、模型、预算与 Core 均保持不变。行为 overlay 只修复旧题的可执行性：

- 写作题实际提供待评文本；
- depleted 题不再在请求中直接写出“不要变成项目”的答案；
- witness 题允许“见证”与“下一步计划”成为真实竞争回应；
- Reference Harness 明确：Recollection 是相关往事／上下文，Disposition 是相关时应影响回应方式的
  learned tendency，但不是当前指令，不能覆盖 Persona 或当前请求。

这些变化没有增加 Core 在线或后台的生产 AI 调用次数；四臂 Answer 与双向 Judge 是隔离的 eval
调用，不进入 Core、索引或数据库。

## 结果

| checkpoint | Oracle vs Anti | Oracle vs RAG | learned vs RAG | 可作架构归因 |
|---|---|---|---|---|
| writing voice | Oracle | Oracle | learned | 是 |
| decision agency | Oracle | Oracle | learned | 是 |
| depleted start | Oracle | Oracle | learned | 是 |
| witness win | Oracle | inconsistent | RAG | 否 |

四个负对照的 active Disposition 均为 0：冲突效果、同条件不同有效回应、重复失败、无关成功混合。
全库因此只有四个正例 Seed，没有通过“反推失败的相反建议”或单轮 `ADAPT` 误生新 Seed。

## `witness-win` 为什么不构成架构反证

learned 回应正确见证了意义，也没有把小胜利变成指标系统，但连续问了两个相近的反思问题；RAG
回应只问一个问题，因此双向 Judge 选择 RAG。Oracle 回应严格只问一个问题，但 Oracle-vs-RAG
一向判 Oracle、反向判平局，操纵本身未达到位置无关的一致性。

这说明中立 Disposition 文本的执行精度仍是值得继续观察的架构风险；但在 Oracle 都不能稳定拉开
RAG 的题上，不能把 learned 的失败归咎于表示架构。若未来四题全部通过 Oracle 操纵，而 learned
仍稳定不优于 RAG，才应把瓶颈升级为“中立文本 Disposition 不足以稳定塑造行为”。

## 运行证据

- 数据 overlay：`eval/anchor-companion-v1.json`，SHA-256
  `539ce2dd4fcb481b54f5c768c759fe19abb9d2f47d9dae531f3921bba37ab256`；
- 运行产物：`.cache/anchor-companion-v1-live-v1/`；
- canonical manifest SHA-256：
  `4785e7947bb71e7e8f427742df4d17b34e4ed83d0f900676c27f5b05cb2ec15d`；
- Answer / Judge：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`，48 次真实调用；
- Worker：180 个逻辑窗口，118 次语义 replay、62 次真实 provider 调用，无 Core retry；
- MemoryIndex：Chroma + `all-MiniLM-L6-v2` exact scoped cosine；
- MySQL 8 隔离空库；运行结束后容器已移除，未复用 Chorai 数据。

首次自动 summary 的漏斗 bottleneck 被误标为 `formed`：四样本运行仍引用了旧八样本常量 6。
原始 funnel 与逐题结果没有受影响。回归修复后使用传入的 `dominance_wins=3`；同时新增
ANCHOR 专用严格汇总，禁止总体投票掩盖单题 Oracle 天花板。
