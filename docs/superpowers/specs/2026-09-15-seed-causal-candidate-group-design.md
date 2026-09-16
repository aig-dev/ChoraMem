# Seed 因果候选组设计

日期：2026-09-15
状态：用户已通过持续 Goal 批准

## 要解决的问题

当前 focused Disposition 形成会把窗口内全部 `current + related` Episode 当成一个经验组。
无关经历会污染判断；索引命中的旧 Episode 又没有带回其配对 Outcome，导致真正重复的第三次
经历也无法加入形成。模型最后绑定的 BASIS 因而可能大于它实际据以判断的同类因果经验。

## 最小机制

不增加表、公共 RPC、调度 Job、分数阈值或新的 AI 分类调用。Core 在既有
`ConsolidateWindow` 内额外构造一份瞬时因果候选组：

1. 在冻结窗口中，从后向前选择最后一个恰有一份非 Agent Outcome 的完整 Episode，作为 anchor；
2. 用该 anchor 的 `Situation + AgentAct + Outcome` 纯文本查询同一 owner 的 MemoryIndex；
3. 仅接受关系库可重载、session 不同、具有完整 Situation／AgentAct、且恰有一份非 Agent
   Outcome 的 Episode；按 Provider 语义顺序最多保留两份历史候选；
4. 参考 Worker 只把 `anchor + 历史候选` 送入 focused 文本模型。模型仍负责判断它们是否真的
   表达同一条件性倾向；任一冲突即 `NO_CHANGE`；
5. Worker 不让模型生成 ref，而由程序把候选组中每一对 Episode／Outcome 全量绑定为 BASIS；
6. Core 提交前再次要求 `NEW_DISPOSITION TEXT` 的 BASIS 与该候选组精确相等。

MemoryIndex 的顺序只是瞬时候选顺序，不成为权威分数。“同类”在 V1 中指语义近邻候选经 focused
模型共同模式校验后成立，不伪造一个可跨 Provider 比较的 85% 阈值。

## 内部合同

`WindowEvidence.EvidenceEpisode` 增加只在内部 Worker RPC 使用的 `formation_role`：

- `anchor`：当前窗口最后一个完整因果对；
- `candidate`：索引返回且由关系库补齐 Outcome 的历史候选；
- 空值：不参与 focused Disposition 形成。

它不进入模型文本；模型看到的仍是无 ID 的
`USER_CONDITION / AGENT_RESPONSE / USER_OUTCOME`。Outcome 继续通过既有 `episode_ref` 配对，
因此无需第二份来源结构。

当因果查询返回至少一个 Episode ref 后，Core 才把当前完整因果对标成 anchor。此时统一 lane 的
outcome-backed `NEW_DISPOSITION TEXT` 被机械移除：候选随后全部校验失败时，该次形成 no-op，
而不是退回混杂窗口。Provider 未配置、失败或完全没有 Episode ref 时不建立 formation role，原
canonical lane 保持可用。原有直接 ADAPT、Recollection 和既有 Seed feedback 路径保持不变。

## ResultSpec

- feature: Disposition 因果候选组
- user value: 长历史中的无关 Episode 不再污染 Seed 形成，跨窗口 Outcome 可以真正参与闭环
- data class: runtime inference snapshot；关系库 Episode／Outcome 仍是唯一真源
- objects touched: Episode、Outcome、Disposition formation evidence
- write path: 仍为一次 `ConsolidateWindow`；Core 校验后自动提交
- source evidence: exact-owner Episode 与其关系库配对的 non-Agent Outcome
- tables / migration: 无
- public API: 无；内部 inference proto 增加一个 Episode 标记字段
- LLM boundary: Python；只输出一行正文或 `NO_CHANGE`
- rollback: 删除内部标记与候选构造，恢复原 material 读取规则；无需清理数据

## 验收

- 混合窗口只把 anchor 和按语义顺序选中的完整历史因果对送入 focused 模型；
- 没有 Outcome、Agent 自评 Outcome、跨 owner、同 session、缺失 Episode 的候选均被跳过；
- 候选的历史 Outcome 从当前关系库重载，并参与 snapshot hash / stale check；
- 模型输入没有 refs，最终 BASIS 恰好覆盖模型看到的每一对 Episode／Outcome，不能少也不能多；
- PostgreSQL 与 MySQL 行为一致；现有 ADAPT、Recollection、feedback 和无索引降级不回退；
- 原冻结 exact 与 generalization 数据、题目、门槛和模型配置不变，使用全新存储与输出目录复跑。
