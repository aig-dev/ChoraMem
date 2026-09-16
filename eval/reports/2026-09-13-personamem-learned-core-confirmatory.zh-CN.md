# PersonaMem learned_core 确认性复验

日期：2026-09-13

## 结论

本次确认性复验**未在新确认样本中复现 `learned_core` 相对 `none` 的稳定正收益**。

| 配对 arm | 正确数 | 准确率 |
|---|---:|---:|
| `none` | 50 / 156 | 32.05% |
| `learned_core` | 54 / 156 | 34.62% |

预注册主效应 `learned_core - none` 为 **+2.56 个百分点**。按 persona 整簇、
seed=0、2,000 次 bootstrap 得到的 95% CI 为 **[-5.03pp, +10.60pp]**。

预注册通过条件是 CI 下界大于 0；本次下界为 -5.03pp，因此不通过。点估计虽为正，
但不足以声明“基础长期个性化记忆有效”。此前 24 个 persona、67 题上的正向结果没有在
本次更大的确认样本中得到统计复现。

**阶段判定：未在本次确认样本中复现正收益。**这里的“复现”按预注册的区间门槛判断，
不是把 +2.56pp 的点估计改写成负数。

## 预注册设计

- 数据：PersonaMem-v2 validation / 32k histories，50 个 persona、156 道题；
- 主比较：只读取 `none` 与 `learned_core` 的配对结果；
- 不确定性：question-weighted difference，persona-cluster paired bootstrap，seed=0，
  2,000 次；
- 回答模型与 Worker：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`；
- Harness：沿用冻结的六 arm PersonaMem effect Harness，逐题 arm 顺序采用
  `sha256-interleaved-v1`；其他 arm 仅作既有诊断，不进入主判定；
- Core 上下文预算：2,048 tokens；
- `episode_evidence_max_bytes=16384`、`batch_turns=28`、回答温度 0、回答上限
  8,192 tokens；
- 预注册文件：`.cache/learned-core-confirmatory-v1/preregistration.json`；
- 运行 manifest：`.cache/learned-core-confirmatory-v1/manifest.json`；
- 完整结果：`.cache/learned-core-confirmatory-v1/summary.json` 与
  `.cache/learned-core-confirmatory-v1/questions/`。

这里的“相同 Harness”指与 2026-09-12 B 样本相同的回答 contract、prompt、renderer、
scorer、六 arm 定义和交错规则，并非整个 evaluator 目录逐字节相同。两次运行之间六份
Harness 源码有四份哈希完全相同；另外两份只增加 Worker replay／记录能力，以及要求每个
历史 Episode 都已进入完成的 consolidation job 后才开始答题。正式运行没有启用 replay；
这些变化没有改变 `none` 或 `learned_core` 的回答输入构造和评分，只把学习完成条件变得
更严格。

## “全新 persona”的边界

抽样只查看 persona ID，不查看其历史、问题或答案。salt 固定为
`learned-core-confirmatory-20260913-v30`。选出的 50 个 persona 与下列 56 个开发暴露
persona 零重叠：此前 learned_core A/B/smoke、PersonaMem 开发 smoke、2026-09-11
Recollection 诊断，以及本轮开始前人工查看的 persona 518。

需要如实披露：更早曾启动过一次基于旧 Core 的 all-persona validation 运行，因此这里的
“全新”严格指**没有进入当前 learned_core 机制的开发、提示词诊断或 A/B 结论**，而不是
从未被任何历史版本的 evaluator 枚举。确认样本、排除集合和选择 manifest 的哈希都在
运行前冻结；选择 manifest 为：

`b0a07490a4b0af426a24b5d5ade08e112fb1670225f74b527a5a5fc13f52d491`

## 完整性与规则冻结

- 50/50 个 persona 都有 `memory-before-*.json` 与 `memory-*.json` 完成证明；
- 156/156 道题均包含完整六 arm，共 936 个逻辑答案；所有 arm 均无 transport error；
- `none` 与 `learned_core` 均为 0 无效回答；
- 每个 persona 的完成证明都确认答题阶段 memory 未改变，MemoryIndex pending 为 0；
- 七次 Worker `APIConnectionError` 由既有 Core 重试恢复，没有丢题或改变样本；
- 运行后的 Core binary、Worker、MemoryIndex 与六份 Harness 源码哈希均与预注册一致；
- 没有修改来源归属、遗忘、更新资格、选择器、提示词或 token 预算；正式运行启动后没有
  为通过门槛重启、换样本或提前停止。

关键冻结哈希：

| 对象 | SHA-256 |
|---|---|
| run script | `dbf33e465474d99241c26ff8cd8783d988b93b4e50d86c50ae8e17f02d58ffc4` |
| Core binary | `e101c618adb47aa19c811d38a83b84b569848320152122cab96f76969e328568` |
| Worker service | `619a2dd8019374eb37ff151244ef22e5864c3a3207f4ddaa0a2175d5ecfa0002` |
| Worker source boundary | `9ac236a1b7f233377c18bcf4c35b33f07e7b460a55f658384b99fd28c63a1f1e` |
| MemoryIndex source | `420cfc4449ef6ab0e9f444baf2d7c85595dec96d3c8750704b3b28d332c0bb87` |

## 辅助结果

辅助 arm 表明本次数据与回答模型仍能测出长期上下文信号：`full_history` 为
69/156（44.23%），`oracle_episode` 为 110/156（70.51%）。`semantic_top40` 与
`current_core` 均为 64/156（41.03%），而只输出 learned memory 的 `learned_core` 为
54/156（34.62%），平均只使用 40.2 个 memory tokens。

这些结果可以说明当前 compact learned path 尚未稳定兑现可用增益，但不用于继续针对
PersonaMem 做提示词、抽取、ranker 或阈值调参。

## 阶段关闭与下一步

本报告关闭 PersonaMem 的定向优化阶段：后续 PersonaMem 只可作为冻结版本的回归检查，
不再作为机制调参集。Seed 不属于本阶段验收项，也不因本次结果增加 Seed 补丁。

下一阶段转向**陪伴关系轨迹 eval**：比较 `none / episodic RAG / learned_core`，测试同一
Agent 与用户在多阶段互动中能否形成关系、维持连续性，并在新经验出现后合理修正，而不再
把“能否答对用户事实题”当作人格化长期记忆的主要代理指标。
