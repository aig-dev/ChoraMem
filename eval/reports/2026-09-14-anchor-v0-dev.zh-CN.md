# ANCHOR v0 因果 dev 基线

日期：2026-09-14

## 唯一结论

`bottleneck: data_isolation_or_harness`

本轮不具备进入一次性正式 holdout 的条件。唯一下一瓶颈不是 Seed 形成、选择或
Judge 契约，而是 Harness 在 consolidation 前只等待 MemoryIndex 投影 4 秒：完整运行
结束时 18,677 个索引操作已全部确认，但最大确认延迟为 8.676 秒，其中 11 个超过
4 秒；因此三个 Behavior 维度各有一个 checkpoint 被同一个超时阻断。

下一步只修这一点：让 pre-consolidation 投影等待复用已有的整体 settle timeout，随后
在全新空库、相同数据、相同模型和不变 Seed 下重跑同一 dev cohort。修复前不进入
holdout，也不据本轮不完整 Behavior 结果优化 Seed。

## 冻结条件

- 数据：`SalesforceAIResearch/AnchorBench` public development examples，revision
  `41bd0e20b9524ce484db301ac15dc14121bf06ad`，3 个 bank、15 个 Trajectory item；
- Behavior 扩展：6 个 checkpoint，manifest SHA-256
  `97146eee1c4f52f3cdb5a05ff109965938384ff5719a3d9bc9a4c48d8cdb5774`；
- Answer、Judge、Worker：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`；
- Core / Worker revision：`0855b244458cfe0685f54602030201411635d55c`；
- MemoryIndex：Chroma 1.5.5、`all-MiniLM-L6-v2@913d7300`、exact cosine、
  `episode-alpha-v1`、全 scope 语义 tie-break；
- `memory_tokens=2048`，Worker 输出上限 8192，Answer/Judge 输出上限 1024；
- 时序：projector poll 100ms、quiet period 5s、pre-consolidation wait 4s；
- 三臂：`none`、`recollection_only`、`seed_enabled`；查询事件不写 Delivery、AgentAct
  或 Outcome；
- 运行前数据库为空；运行后重新计算的 `internal / worker / api / gen` Seed 快照与
  manifest 完全一致。

运行 manifest SHA-256：
`4bec433a6e848aefefb8ae6f2cfeb6d0336bca6a98e7d62c015de1c384b65d80`。

## 因果生命周期

固定 lifecycle gate 完整通过：

| 对照 | formed | selected / rendered | 结果反写 |
|---|---:|---:|---|
| no delivery | 是 | 是 | 不重演、不修订 |
| positive complete | 是 | 是 | 重演 |
| no outcome | 是 | 是 | 不修订 |
| negative complete | 是 | 是 | 修订或抑制，并改变后续选择 |

这证明当前 Core 的 `formed → selected → rendered → behavior → outcome → Seed change`
闭环可运行；本轮失败发生在外部 eval Harness 的索引等待门，而不是 lifecycle gate。

## Trajectory 结果

15 个样本、45 个回答全部完成，选项解析 45/45，stage error 为 0。

| arm | 正确 | 准确率 | 相对 none | 相对 RAG |
|---|---:|---:|---:|---:|
| none | 6 / 15 | 40.0% | — | +2 题 |
| recollection_only | 4 / 15 | 26.7% | -2 题 | — |
| seed_enabled | 4 / 15 | 26.7% | -2 题 | 0 题 |

Seed 漏斗：

| 阶段 | 样本数 |
|---|---:|
| formed（存在 active Disposition） | 15 / 15 |
| selected | 12 / 15 |
| rendered | 12 / 15 |
| behavior changed（相对 RAG 选项变化） | 8 / 15 |
| score improved（相对 RAG） | 3 / 15 |
| score worsened（相对 RAG） | 3 / 15 |

因此 Seed 已能到达并改变行为，但改善与恶化各 3 题，净增益为 0；相对 none 还少
2 题。Trajectory 本身不支持进入 holdout。

## Behavior 结果

6 个 checkpoint 都产生了原子结果文件，但只有 3 个完成三臂 Answer + Judge；另外
3 个分别来自 persona continuity、relationship adaptation、relationship repair，均以
`MemoryIndex projection did not settle before consolidation` 失败。成功部分共 9 个回答、
9 个 Judge，Judge 均输出单个合法分数，解析率为 9/9。

下表每个维度只有 1/2 checkpoint 完成，因而只是诊断值，不是维度得分：

| 维度 | none | RAG | Seed | Seed - RAG | Seed - none | Seed rendered |
|---|---:|---:|---:|---:|---:|---:|
| persona continuity | 0 | 2 | 2 | 0 | +2 | 否 |
| relationship adaptation | 2 | 2 | 2 | 0 | 0 | 是 |
| relationship repair | 2 | 2 | 2 | 0 | 0 | 是 |

成功 checkpoint 的 Seed 漏斗为 `formed 3 → selected 2 → rendered 2 →
behavior_changed 0 → score_improved 0`。persona continuity 的 +2 来自 Recollection，
因为该样本没有选择或渲染 Disposition，不能归因给 Seed。

## 运行证据与限制

- summary 固定为 54 个回答、9 个 Judge 和一个
  `data_isolation_or_harness` bottleneck；离线复算得到相同结果；
- Worker 共记录 2,326 个完成窗口，其中 175 次真实 provider 调用；Answer/Judge 共
  39 次真实调用，全部 `finish_reason=stop`；
- 三个超时 scope 的索引操作最终都被确认，结束时 pending 为 0，排除了永久投影失败；
- 这是 ANCHOR public development examples 加 Memory Core 自定义 Behavior 扩展，
  不是官方 ANCHOR 分数；
- Behavior 只有一半 checkpoint 完成，不能据此声明人格连续性、关系适应或关系修复
  的总体收益，也不能据此调优 Seed。
