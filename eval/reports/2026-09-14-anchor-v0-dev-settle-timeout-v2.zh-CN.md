# ANCHOR v0 统一 settle timeout dev 复验

日期：2026-09-14

## 唯一结论

`bottleneck: behavior_changed`

pre-consolidation 投影等待已与完整 settle 共用同一个调用方 timeout。本轮在全新空库、
全新 MemoryIndex 和全新输出目录中完成了相同的 15 个 Trajectory 与 6 个 Behavior，
不再出现旧运行的 4 秒提前截止。

运行完整后，瓶颈从 Harness 等待错误前移到真正的 Seed 效果：6/6 样本都形成了
Disposition，5/6 被选择并渲染，但 Seed 相对 Recollection 没有改变任何冻结 Judge
分数。因此当前仍不进入一次性正式 holdout；下一步只处理
`rendered -> behavior_changed`，而不是继续修改运行基础设施。

## 受控复验条件

- 数据仍为 `SalesforceAIResearch/AnchorBench` public development examples，revision
  `41bd0e20b9524ce484db301ac15dc14121bf06ad`，3 个 bank、15 个 Trajectory item；
- Behavior 仍为相同 6 个 checkpoint，manifest SHA-256
  `97146eee1c4f52f3cdb5a05ff109965938384ff5719a3d9bc9a4c48d8cdb5774`；
- 旧、新 manifest 的 source files、15+6 实例 registry、prompt、persona、modes 和
  `internal / worker / api / gen` Seed 快照完全一致；
- Answer、Judge、Worker 仍为
  `MiniMax-M2.5@api.minimaxi.com-2026-09-08`，temperature 0；
- `memory_tokens=2048`，Worker 输出上限 8192，Answer/Judge 输出上限 1024，
  `probe_workers=1`；
- projector poll 仍为 100ms、quiet period 仍为 5s；唯一机制变化是 timing profile 从
  `quiet5s-indexpoll100ms-preconsolidation4s-v1` 升级为
  `quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2`，调用方
  `settle_timeout=900s`；
- 新运行使用空 MySQL `memory_core_anchor_v0_settle_v2`、新 Chroma 目录
  `.cache/anchor-v0-index-settle-v2` 和新输出目录
  `.cache/anchor-v0-dev-settle-v2`；旧产物未覆盖；
- 三臂仍为 `none`、`recollection_only`、`seed_enabled`；查询事件不写 Delivery、
  AgentAct 或 Outcome。

summary 记录的 canonical manifest SHA-256 为
`0d53833ec216c2a109286cfd73e337ac82f7db599a94cfe72b3d7e0d448dbf1a`。

## 因果生命周期

固定 lifecycle gate 4/4 完整通过：

| 对照 | formed | selected / rendered | 结果反写 |
|---|---:|---:|---|
| no delivery | 是 | 是 | 不重演、不修订 |
| positive complete | 是 | 是 | 重演 |
| no outcome | 是 | 是 | 不修订 |
| negative complete | 是 | 是 | 修订或抑制，并改变后续选择 |

这再次确认 Core 的因果闭环可执行；本轮终态结论来自完整效果数据，而不是 lifecycle
或 Harness 失败。

## Trajectory 结果

15 个样本、45 个回答全部完成：

| arm | 正确 | 准确率 | 相对 none | 相对 RAG |
|---|---:|---:|---:|---:|
| none | 6 / 15 | 40.0% | — | +2 题 |
| recollection_only | 4 / 15 | 26.7% | -2 题 | — |
| seed_enabled | 4 / 15 | 26.7% | -2 题 | 0 题 |

Seed 在 15/15 样本形成，12/15 被选择并渲染；相对 RAG 改变了 8 个选项，其中改善
3 题、恶化 3 题，净增益仍为 0。

## Behavior 结果

6 个 checkpoint、18 个回答和 18 个 Judge 全部完成，Judge 均输出合法单分数：

| 维度 | 样本 | none | RAG | Seed | Seed - RAG |
|---|---:|---:|---:|---:|---:|
| persona continuity | 2 | 1.0 | 2.0 | 2.0 | 0 |
| relationship adaptation | 2 | 2.0 | 2.0 | 2.0 | 0 |
| relationship repair | 2 | 2.0 | 2.0 | 2.0 | 0 |

完整漏斗为：

`formed 6 -> selected 5 -> rendered 5 -> behavior_changed 0 -> score_improved 0`

5 个渲染了 Disposition 的样本中，Seed arm 的回答文本都不同于 RAG arm，但冻结 Judge
分数完全相同。这里的 `behavior_changed` 按协议定义为 Seed 与 RAG 的 Judge 分数不同，
所以文本变化不能被记作有效行为变化。这是下一轮唯一需要解释和改进的断点。

## 运行证据与限制

- Worker 完成 2,554 个窗口：2,542 个回放命中，其中 63 个 exact、2,479 个
  semantic-memory-state；12 个未命中窗口调用 provider，均
  `finish_reason=stop`；
- Answer/Judge 从旧运行复用 57 个内容寻址缓存，并为此前未完成的请求新增 16 次
  provider 调用；最终缓存 73 个，新增调用均 `finish_reason=stop`；
- 数据库最终包含 16,308 个 SourceEvent、8,140 个 Episode、5,452 个 Recollection、
  38 个 Seed、1,369 个 consolidation job 与 20,432 个索引操作；job 和索引 pending
  均为 0；
- 本轮索引最大确认延迟为 3.7853 秒，没有单条超过旧 4 秒阈值。因此 live 复验直接证明
  新机制能完整跑完同一 cohort，但没有单独制造一个超过 4 秒的延迟；“两个等待确实复用
  同一 900 秒调用方 timeout”由专门的 RED/GREEN 回归测试证明；
- 运行后重新计算的保护目录 Seed 快照与旧、新 manifest 均完全一致；
- 这是 ANCHOR public development examples 加 Memory Core 自定义 Behavior 扩展，
  不是官方 ANCHOR 分数，也不构成 holdout 结果。
