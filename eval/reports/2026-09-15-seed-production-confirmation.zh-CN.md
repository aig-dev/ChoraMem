# Seed 生产接线、独立复验与长历史泛化报告

日期：2026-09-15

## 结论

本报告按实验发生顺序保留旧结果；第 8 节以后是实现因果候选组后的最新结论：

1. 当前完整因果对作为锚、历史完整因果对作为候选、最终 Basis 精确绑定模型可见输入，这条生产闭环已经成立；
2. 原 Seed-essential v1 从 11 个含重复 Seed 改善为每例恰好 1 个 Seed，且 Learned Seed vs RAG 达到 8 / 8；
3. 未见模式的目标 Seed 实际形成 3 / 4，但同时形成 3 个“确认用户事实”的错误 Disposition，其中 1 个
   落在负对照；
4. 剩余失败已收敛为两个具体问题：固定 top-2 的候选召回会漏掉同类经验，以及事实型 Recollection
   与响应倾向型 Disposition 的语义边界不够明确；
5. 当前 holdout 的 Oracle vs Anti 操纵检验仍失败，因此最终行为分数不能单独证明或否定 Seed 架构。

所以，客观判断是：**因果闭环和精确归因已经得到工程验证，受控数据上也有强增量；但自然长历史中的
类型精度和候选召回仍未过关，尚不能宣称 Memory Core 已证明人格化长期陪伴优势。**

## 0. 生产接线核验

第一次生产复跑虽然生成了 Seed，但 Worker 日志显示 focused formation 调用数为零。原因是 Core 会为
真实 USER Situation 结构性提供 `ELIGIBLE_NEW_ADAPTATION`，而 Worker 误把该 marker 当成“模型已经
选择 direct ADAPT”，提前关闭了 paired lane。

修正后规则是：

- marker 只提供资格，不改变 lane；
- 统一 lane 实际返回合法 `NEW_DISPOSITION ADAPT` 时，direct ADAPT 才优先；
- 否则完整因果配对继续进入 focused formation；
- Recollection fallback 仍独立执行。

本地注入式接线核验使用实际 Core 生成的三轮请求：第一轮只有一份 Episode，未形成；第二、三轮分别
具有 2 组和 3 组配对证据，focused job 均真实调用，第二轮形成 Seed，Recollection fallback 同时调用。
相关 Worker 单元测试为 `89 passed`。

## 1. 原 v1 独立复验

### 冻结条件

- 数据：`eval/seed-essential-delayed-v1.json`；
- 数据 SHA-256：`c9938e05431347d5595f694b229be2104838d2e475e3965b79a1876ec92a12b8`；
- 全新 MySQL 8 schema 与全新 Chroma collection；
- 生产 Worker，不使用旧的 eval-only paired wrapper；
- `MiniMax-M2.5`，`https://api.minimaxi.com/v1`，temperature 0；
- Answer/Judge 各 1,024 tokens，Worker 8,192 tokens；
- Memory 预算 2,048 tokens；quiet period 5 秒，index poll 100 ms；
- 不复用 Worker、Answer 或 Judge 输出。

产物：`.cache/seed-production-confirmation-fixed-v1/`。
Manifest SHA-256：`9af1ae0a6ca6803c134101300772a21cdcf464b5bbc142793d30c5a444e4b984`。

### 结果

| 指标 | 结果 |
|---|---:|
| formed / selected / rendered | 8 / 8 / 8 |
| Learned Seed vs RAG | 6 胜 / 0 负 / 0 平 / 2 不一致 |
| Oracle Seed vs RAG | 8 胜 / 0 负 / 0 平 / 0 不一致 |
| Oracle Seed vs Anti | 8 胜 / 0 负 / 0 平 / 0 不一致 |
| Oracle vs Learned | Oracle 3 / Learned 0 / 平 3 / 不一致 2 |

自动结论仍为 `preliminary_learned_advantage`，恰好达到预注册的 Learned 至少 6 胜、RAG 0 胜门槛。

完整性核验：

- 24 个 Episode；
- 11 个 Seed / 11 个 SeedVersion；
- 45 个 Recollection / 45 个 RecollectionVersion；
- 26 个 Seed–Episode Basis 与 26 个 Seed–Outcome Basis；
- 32 个回答、64 个双向 Judge；
- 96 次 Answer/Judge provider 调用均为 `finish_reason=stop`；
- 64 次 Worker 调用，其中 16 次为 focused formation，均无错误。

但 8 个 owner 中有 3 个各形成了 2 个近义 Seed，总数为 11。模型虽然看到已有 Disposition，仍在第三轮
把语义近似文本作为新 Seed 写入。因此本轮只证明“可形成并产生行为增量”，没有证明长期语义去重稳定。

## 2. 未见模式与混合长历史

### 冻结条件

- 数据：`eval/seed-generalization-v1.json`；
- 数据 SHA-256：`8c078c229cd61a5b23896b38dbd8826cb2d29b5b0889d6af5206995eda4da28a`；
- 4 个未见正模式、4 个负对照；
- 每例 8 个不同 session，3 个 signal 被 5 个 durable-fact distractor 隔开；
- 使用另一套全新 MySQL 8 schema 与 Chroma collection；
- 模型、预算和服务时序与独立复验一致。

产物：`.cache/seed-generalization-production-fixed-v1/`。
Manifest SHA-256：`b6961fbf8afe1e10efe29252bff90cab76b9105c7c51c18cfe2d91e03e7b5adc`。

### 结果

| 指标 | 结果 |
|---|---:|
| 正例 formed / selected / rendered | 2 / 2 / 2（门槛 3 / 4） |
| Learned Seed vs RAG | 0 胜 / 1 负 / 2 平 / 1 不一致 |
| Oracle Seed vs RAG | 3 胜 / 0 负 / 0 平 / 1 不一致 |
| Oracle Seed vs Anti | 1 胜 / 0 负 / 0 平 / 3 不一致 |
| 负对照误形成 | 0 / 4 |
| 正例存在 Recollection | 4 / 4 |

自动结论为 `positive_generalization_failed`，且 manipulation check 因 `oracle_vs_anti` 失败。

完整性核验：

- 64 个 Episode；
- 2 个 Seed / 2 个 SeedVersion；
- 93 个 Recollection / 93 个 RecollectionVersion；
- 16 个回答、32 个双向 Judge、4 个负例终态；
- 40 次实际 Answer/Judge provider 调用，因无 Seed 时 Learned 与 RAG prompt 完全相同而由冻结 cache
  合并重复请求；逻辑记录仍完整；
- 157 次 Worker 调用，其中 32 次为 focused formation；全部 provider 调用均为
  `finish_reason=stop`，无 Worker 或 Answer/Judge 错误。

四个负例——效果冲突、回应不一致、反复失败、无关成功混合——均保持 0 Seed。这比旧 omnibus 路径
的 3 / 4 误形成有实质改善。

## 3. 形成失败的确定原因

### 3.1 第一个窗口把信号和干扰当成同一 Basis

每例的前五轮固定为：

```text
signal-1 -> distractor-1 -> distractor-2 -> signal-2 -> distractor-3
```

focused formation 分别看到 2、3、4、5 组 Experience。writing 样本在最后一次忽略了三个无关事实，
从两个写作信号归纳出合理 tendency，却由程序把全部 5 个 Episode 与 5 个 Outcome 绑定为 Basis。
这不是正确的“熏习”归因：正文可能合理，但来源账本错误。

decision-agency 与 depleted-start 在同样混合候选上始终返回 `NO_CHANGE`，因此没有形成。

### 3.2 后续窗口因 related Outcome 不完整而取消 focused job

第八轮的统一窗口已包含第二、第三个 signal，并召回更早的 `RELATED_EPISODE`。但是 typed evidence 只为
当前窗口 Episode 提供配对 Outcome，相关旧 Episode 没有一并获得可用 Outcome。当前 builder 把
`current + related` 全部视为候选，又要求每一项都有 Outcome；因此整个 material 变为 `None`，
focused formation 在后续三轮均没有执行。第三个 signal 从未和前两个 signal 一起进入专用判断。

### 3.3 第二个“成功”实际来自 direct ADAPT

`witness-win` 的 Seed 只有 2 个 Episode Basis、0 个 Outcome Basis，来自统一 lane 的 direct
`ADAPT`，不是 outcome-backed paired formation。故生产 paired formation 的严格正例成功实际只有
1 / 4；且唯一成功的 writing Basis 又包含三个无关 distractor。

## 4. 干净候选组反事实

为了区分模型能力和 Core 候选组错误，本轮没有改代码或数据，只把每个冻结 case 的三条 signal
`Situation -> AgentAct -> Outcome` 交给同一 production formation prompt、同一 MiniMax-M2.5：

| 输入 | 结果 |
|---|---:|
| 4 个正例的干净三配对 | 4 / 4 形成 |
| 4 个负例的干净三配对 | 4 / 4 `NO_CHANGE` |

8 次 provider 调用全部 `finish_reason=stop`。产物：
`.cache/seed-generalization-candidate-ablation-v1/usage.jsonl`。

这项反事实说明：模型和极简纯文本规则能区分四类新 tendency 与四类反例；生产失败的首要原因是
候选组边界和 Outcome 配对，而非需要增加 confidence、权重、第二个 verifier 或人工审批。

## 5. 行为评测自身的限制

本轮 `oracle_vs_anti` 只有 1 / 4 clear win，不能作为合格的 manipulation check，原因可从回答直接看出：

- Persona 明确要求 preserve agency，而 decision 的 Anti 要求 Agent 接管决定；回答 prompt 又允许忽略
  与 Persona 冲突的 memory，因此模型经常不执行 Anti；
- writing 的当前请求没有附上待评文本，但 target 要求指出一个具体元素并做局部修改，当前回合不可执行；
- depleted-start 的 Anti 要求多阶段高压计划，但实际 Anti 回答仍给了比 Oracle 更小的“只打开门”动作；
- witness-win 的 RAG 已召回 bus、phone call、piano 等具体胜利事实，不依赖 Seed 也能生成贴近目标的回应，
  因而没有隔离 Seed 的独立增量。

此外，Recollection 共存虽为 4 / 4，但文本质量并不等于合格：抽样发现 transient event 被写成
Recollection，以及一条正文尾部残留 `NO_MEMORY`。因此“存在 Recollection”只能证明并行链路工作，
不能证明其抽取质量。

## 6. 架构判断与下一步

本节记录的是第一次泛化失败后的判断；其中提出的最小目标已经在第 8 节以后实现并复验。

本轮没有证明 Memory Core 在自然长期陪伴上优于普通 RAG，也没有证明当前实现可直接作为默认生产方案。
但它同样没有否定 Seed 架构：干净候选组上的 4 / 4 正例与 4 / 4 负例说明，
“跨情境重复的有效回应形成潜在倾向”是可工程化、可证伪的；失败发生在形成之前的证据组织层。

下一个最小目标应只有一个：

```text
以当前完整因果对为锚
  -> 只召回拥有配对 Outcome 的历史因果对
  -> 按“用户条件 + Agent 回应”组成一个小型同类候选组
  -> focused model 对该组输出一行 tendency 或 NO_CHANGE
  -> BASIS 精确等于模型实际看到的候选组
```

检索只负责提出候选，不能以通用的“85% 阈值”授予写入资格；模型仍只做纯文本语义判断，Core 仍拥有
refs、资格、绑定与提交。先用同一冻结 generalization v1 复跑这一处变化，再讨论新的 Memory 类型、
数值强化或新的 benchmark。

## 7. 凭据与运行说明

本轮使用的是仓库 `env/prod/chorai_agent.env` 中已更换的 Token Plan key，实测 MiniMax endpoint 正常。
运行时默认优先读取的外部 `~/.config/chorai/env/prod/chorai_agent.env` 仍指向旧 key；本轮通过显式环境
注入避免使用旧值，没有复制、覆盖或输出任何 secret。

## 8. 因果候选组实现

本次实现只改变形成资格和证据流，不增加表、公开 RPC、置信度或审核节点：

```text
当前完整 Situation -> AgentAct -> non-Agent Outcome（anchor）
  -> 用完整因果对查询 MemoryIndex
  -> Core 从 owner 数据库回填至多 2 个不同 session 的完整历史因果对（candidate）
  -> Worker 只看 anchor + candidates，输出一行 tendency 或 NO_CHANGE
  -> Core 按顺序校验 Episode / Outcome Basis 与模型实际可见组完全一致
  -> 合法时写入一个新 SeedVersion
```

如果索引返回了 Episode 候选但全部不具备完整 Outcome，Core 不再退回宽泛窗口形成 Seed。无索引、索引
错误或索引完全没有 Episode 候选时，则保留原 canonical lane，避免 MemoryIndex 成为基础记忆写入的硬依赖。

首次实跑还发现一个实现语义错误：调度窗口可能重叠，较早 session 的 Episode 仍可能带有 `current`
origin。origin 表示本批次成员关系，不表示时间新旧。因此最终规则改为：anchor 必须是 current；candidate
可以是 current 或 related，但必须不是 anchor、来自不同 session、由本次索引命中，且拥有唯一的
non-Agent Outcome。错误版本在完成 4 / 8 前主动中止，产物保留于
`.cache/seed-causal-group-exact-aborted-current-origin-v1/`，没有混入正式结果。

## 9. 原 v1 的同数据精确复跑

冻结条件与第 1 节相同，数据 SHA-256 仍为
`c9938e05431347d5595f694b229be2104838d2e475e3965b79a1876ec92a12b8`。
产物：`.cache/seed-causal-group-exact-v2/`。
Canonical Manifest SHA-256：`940d96586a76d72e2fa50921e9a1dfedde5a0f305b25f6b743f3f4376426919f`。

| 指标 | 因果候选组 v2 | 第 1 节旧结果 |
|---|---:|---:|
| formed / selected / rendered | 8 / 8 / 8 | 8 / 8 / 8 |
| Learned Seed vs RAG | 8 胜 / 0 负 / 0 平 / 0 不一致 | 6 / 0 / 0 / 2 |
| Oracle Seed vs RAG | 7 胜 / 0 负 / 0 平 / 1 不一致 | 8 / 0 / 0 / 0 |
| Oracle Seed vs Anti | 8 胜 / 0 负 / 0 平 / 0 不一致 | 8 / 0 / 0 / 0 |
| Seed / owner | 1 | 1 至 2 |
| Seed 总数 | 8 | 11 |

数据库中共有 24 个 Episode、24 个 Outcome、8 个 Seed / SeedVersion 和 32 个 Recollection。7 个 Seed
各绑定 2 对 Episode / Outcome，1 个绑定 3 对；没有再次形成近义 Seed。15 次 focused formation 中
8 次形成、7 次 `NO_CHANGE`。Worker 共 63 次逻辑调用，其中 25 次命中语义 replay、38 次真实 provider
调用；全部正常结束。Answer/Judge 96 次调用也全部正常结束。

这证明新结构确实修复了旧实现中的错误 Basis 和重复形成问题；但这是已知模式的受控复跑，不能外推为
自然长历史优势。

## 10. 未见模式与混合长历史复跑

数据与第 2 节完全相同，SHA-256 仍为
`8c078c229cd61a5b23896b38dbd8826cb2d29b5b0889d6af5206995eda4da28a`。
产物：`.cache/seed-causal-group-generalization-v1/`。
Canonical Manifest SHA-256：`fff702b86b5f92aa045989a26abf8c6df0a6a1858c9465eb268f9f760527faa2`。

### 10.1 自动聚合结果

| 指标 | 结果 |
|---|---:|
| 正例 formed / selected / rendered | 4 / 4 / 4 |
| Learned Seed vs RAG | 2 胜 / 1 负 / 1 平 / 0 不一致 |
| Oracle Seed vs RAG | 2 胜 / 0 负 / 1 平 / 1 不一致 |
| Oracle Seed vs Anti | 2 胜 / 0 负 / 1 平 / 1 不一致 |
| Oracle vs Learned | Oracle 2 / Learned 0 / 平 2 / 0 不一致 |
| 负对照误形成 | 1 / 4 |

自动结论为 `negative_control_failed`；正例内部的操纵检验仍为 `manipulation_check_failed`。

### 10.2 语义核对

自动 funnel 只检查“是否存在任意 active Seed”，会把错误类型也算作 formed。逐例读取实际 tendency 后：

| 正例 | 目标 Seed | 错误 Seed |
|---|---:|---:|
| writing voice | 否 | 1 个事实确认倾向 |
| decision agency | 是 | 0 |
| depleted start | 是 | 1 个事实确认倾向 |
| witness win | 是 | 0 |

所以真实目标形成率是 **3 / 4**，不是自动报表表面的 4 / 4。负例 `repeated-failure` 也形成了同一类错误：

> When the user provides personal factual information about themselves ... the Agent restates and confirms that information.

全库最终有 6 个 Seed：3 个目标 Seed、3 个事实确认型错误 Seed；每个 Seed 均精确绑定 3 个 Episode 与
3 个 Outcome。因此现在的问题不是 Basis 串错，而是模型把“事实被复述并获确认”误判成了值得塑造未来
人格行为的 Disposition。这些长期事实应进入 Recollection，而不是 Seed。

writing 的第三个 signal 被正确用作 anchor，但 top-2 候选只包含第一个写作 signal 和一个固定游泳事实，
第二个写作 signal 被挤出；严格规则据此返回 `NO_CHANGE`。其余 3 个正例的第三个 signal 都召回了前两条
同类经验，并形成目标 Seed。由此可把目标漏形明确归因于 **candidate recall@2**，而不是 focused model
无法理解该模式。

Worker window 日志共有 185 条，其中 57 次是 focused formation：6 次输出 tendency、51 次
`NO_CHANGE`，另有 4 条明确标记为语义 replay。provider usage 日志记录 180 次实际调用，全部为
`finish_reason=stop`、无错误；两类日志口径存在 1 条差异，本报告不拿该差异推导效果结论。
Answer/Judge 共 48 次真实调用，也全部正常结束。

## 11. 最终判断

当前证据支持把问题分成两层：

- **架构层已成立**：记忆被索引唤起、以完整行为—结果链获得写入资格、形成 Seed、再影响未来选择；
  模型所见与数据库 Basis 已精确一致。这不是普通 RAG 的别名。
- **产品优势尚未成立**：未见长历史中目标 Seed 是 3 / 4，且有 3 个类型误判；行为 Judge 又没有稳定
  通过 Oracle vs Anti 操纵检验。现在不能声称优于主流长期记忆框架。

因此下一步不应换 benchmark，也不应加入 confidence、人工审核或新的 Memory 类型。最有信息量的下一步
是一个双断言、单变量实验：

1. formation prompt 明确排除“仅保存、复述或确认 durable fact”的组，这些组必须返回 `NO_CHANGE`；
2. 在不增加模型调用的前提下，离线比较完整因果对单查询与 condition / response / outcome 多视图秩融合，
   看能否把 writing 的两个历史 signal 同时送入 bounded candidate group。

只有先同时做到“目标候选召回”和“事实型组拒写”，再原样复跑当前 4 正 4 负数据，才能判断剩余问题是否
只是工程优化；若仍不能稳定达到目标 4 / 4、负例 0 / 4，再认真考虑 Seed 架构本身是否缺少必要机制。
