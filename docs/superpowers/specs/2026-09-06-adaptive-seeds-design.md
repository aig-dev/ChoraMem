# 从经验形成适应性 Seed

## 用户确认的目标

保留一次后台 `ConsolidateWindow`，不新增模块、分类 Job 或人工决定。
Seed 是基于设定与共同经历，影响以后如何理解、关注、回应的潜在生成倾向，
不是 Agent 过去回复模式的复制。

1. 明确长期要求，或多源经验，可以形成尚未被 Agent 实施的新倾向。
2. 当前经历、相关旧经历、已有记忆和有效人格基准共同参与后台处理。
3. 用户直接修改相处要求不依赖 Delivery/Outcome；声称某次行为的效果仍须完整因果链。
4. Agent 重复输出不是用户认可。模型不生成 confidence、strength 或权重。

## 最小工程决策

### 初生与纠正

- 沿用 `TARGET / APPLICATION / CHANGE / BASIS`。`TEXT` 形成新的推断倾向须有至少两份
  来源不重叠的完整 Episode、非 Agent 情境依据，并包含当前窗口经历；不要求相似 AgentAct。
- 增加带单行正文的 `ADAPT` 操作，仅适用于 `NEW_DISPOSITION` 或已有 Disposition。
  它表示当前用户明确的长期交互要求或修正，并且只引用一份含用户 Situation 的当前完整 Episode。
  多份 Episode 才能成立的是归纳，只能在重复经验资格完整时走 `TEXT`，不能借 `ADAPT` 合并。
  Worker 判断文本是否真的是本人提出的长期要求；Core 只依据可信 actor、owner、当前证据、
  target/version、操作与来源资格校验，不用关键词猜用户意图。
- ADAPT 已有 Seed 只在正文语义改变时追加新版本；相同正文为 no-op，不能绕过行为归因充当强化。它不创建 Activation、
  Delivery、Outcome，也不记录“行为成功”。必须复用旧 Seed，不通过 NEW 绕过纠正。
- REENACT 仍须 exact Delivery + actual AgentAct，只记录又一次现行，不记录用户赞同。
  效果驱动 TEXT/INHIBIT 保留同链、非 Agent Outcome 要求。
- 静默窗口允许单个完整 Episode 执行，以免首次明确要求永久等不到巩固。
  未绑定的 SourceEvent（含 eval 问题）不进入学习，Episode 仍由 Situation + AgentAct 物化。

### 只读人格基准

- 公共 `SourceEvent` 增加可选 `Constitution`，与该来源一起保存不可变的外生版本和正文快照。
  `ReportOutcomeRequest` 的扁平来源入口也携带同一可选快照，以支持同一条用户消息既是上一轮
  Outcome、又是下一轮 Situation；不同到达顺序不能制造来源冲突。仍沿用现有四个公共 RPC；
  人格规范的权威及修改权仍归 Harness。
- Python、TypeScript 的 turn/Adapter 在记录 Situation 时传入与 Select 相同的基准；
  Go/Python/TypeScript 公共协议和生成物同步。没有基准的既有调用保持合法。
- 在当前窗口中使用最后首次录入的 Situation 的基准；该来源未提供时明确为未知，
  不从相关旧 Episode 偷用过时基准。基准变化和清空通过后续 Situation 快照表达。
  顺序由数据库在首次接纳来源时赋予，重试、后补关系及迟到反馈不推进；它是 Core 录入顺序，
  不宣称历史事件发生时间，也不作为模型输入。不能用窗口数组顺序代替，因为迟到反馈可把旧
  Episode 重新排到后面。旧来源迁移后仍为未知基准，不补造历史设定。
  旧经历可以保留其原始基准身份供解释，但不能覆盖当前基准。
- 基准正文进入同一个 Worker 的只读、非证据区；不能作为 Target 或 Basis，不能由 Worker 修改。
  快照参与来源幂等与巩固请求指纹，重试不偷换人格；两个数据库仅通过新增 migration 演进。
- 新字段为空时保留既有 intake 身份/幂等语义；外生版本相同而文本冲突不得静默覆盖已有来源。
  Observe 与 Outcome 两个 intake 路径都必须冻结完整快照，并拒绝冲突重放。

## 验收

- 单个明确要求可初生，并在后续相关情境被选择；随后一次用户直接修正可更新同一个 Seed，
  不伪造 Delivery/Outcome。新版本可影响未来 Select。
- 两份不同 Agent 回复的经历可以支持新的适应倾向；仅 Agent 自说自话不能获得形成/ADAPT 资格。
- 纯事实仍可形成 Recollection；同一意义不强制双写；旧记忆重复、引用中的伪要求、跨 owner、
  旧证据单独写入、陈旧并发、幂等重试受现有与新增测试约束。
- 真实行为反馈路径无回退：没有完整链不能 TEXT/INHIBIT 已有 Seed。
- 有基准/无基准/基准变化/未知基准/恶意基准文本不能改 Target 等情形有端到端证据。
- 真实 MySQL8、PostgreSQL、SDK/Harness 与 Worker 验证；真实模型受控语义用例和有界真实历史
  诊断（有凭证时）分开报告，不把受控模型或开发集成绩冒充人格成长效果。
- 中文理念、架构、协议、Worker、SDK 与 eval 文档同步，完整独立发布门通过。
- 仅独立 memory-core 仓库，不合并、不推送、不改 Chorai 主线或产品数据库。
