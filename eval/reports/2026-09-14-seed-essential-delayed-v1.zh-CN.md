# Seed-essential Delayed Eval v1 真实实验报告

日期：2026-09-14

## 唯一结论

`learning_or_selection_bottleneck: formed`

当前结果不支持“Learned Seed 已经优于 Recollection RAG”，但也不支持“Seed 架构没有额外
价值”。Oracle Seed 在 8/8 实例中稳定胜过 RAG，并在 8/8 实例中稳定胜过 Anti Seed；说明
独立 Disposition 通道进入相同 Harness 后，确实能改变并改善延迟人格化行为。实际 Learned Seed
只在 2/8 实例形成；形成后两例都被选择并渲染。因此第一个、也是当前唯一明确的瓶颈是 Seed
初生，而不是检索、渲染或注入。

更具体地说，当前实现虽存储了每次非 Agent Outcome，却没有把它作为新 Seed 形成窗口的可见
证据。Worker 在其余条件相同的情况下只能看到重复 Situation/AgentAct，同时又被正确要求不得
仅因 Agent 回复重复就臆测用户认可。这构成形成路径的循环缺口：新 Seed 没有历史 Delivery，
Outcome 因而不具备现有 Seed 的反馈资格；没有 Outcome，Worker 又缺少“这类回应对该用户确实
有效”的因果证据。

这是一个核心工程机制缺口，但尚不是高层架构被否证。修复不需要新增记忆类型、数值权重或
人工审批；下一步应只让与完整 Episode 直接关联的非 Agent Outcome 参与 Seed 初生资格与来源，
保持现有 ADAPT 和已存在 Seed 的反馈规则不变，然后在新空库上原样重跑本 v1。

## 冻结条件

- 合成开发集：4 个模式、每种 2 个延迟探针，共 8 个隔离 relationship owner；
- 每例先回放 3 个独立 session，每个 session 都包含 Situation、AgentAct、非 Agent Outcome，
  并在下一 session 前等待后台巩固；
- 测试请求不复述目标行为；Episode evidence 为 0；探针只写 unbound Situation 并 Select；
- 四臂：`rag / learned_seed / oracle_seed / anti_seed`，都从同一 Select 派生；
- 四臂共同使用 2,048-token memory cap、同一 Persona、请求、MiniMax-M2.5、temperature 0 和
  1,024-token 回答预算；
- 四个答案全部完成后才加载 scorer target；每个比较交换 A/B 位置各判一次；
- Worker、Answer、Judge：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`；
- MySQL 8 空库 `memory_core_seed_delayed_v1`，独立 Chroma `all-MiniLM-L6-v2` exact-cosine
  投影，quiet period 5 秒；
- 运行前数据库为空，运行中未修改 `internal / worker / api / gen`。

数据 SHA-256：
`c9938e05431347d5595f694b229be2104838d2e475e3965b79a1876ec92a12b8`。

运行 manifest SHA-256：
`875f774311c20b617b215628df779d99fdb9ad6fc0e71153c1df4059c5699025`。

## 四臂结果

预先冻结的 dominance 门为：8 例中至少 6 个 clear win，且对手 0 个 clear win。

| 比较 | 左臂胜 | 右臂胜 | 平局 | 位置不一致 |
|---|---:|---:|---:|---:|
| Oracle Seed vs RAG | 8 | 0 | 0 | 0 |
| Learned Seed vs RAG | 1 | 0 | 6 | 1 |
| Oracle Seed vs Learned Seed | 7 | 0 | 0 | 1 |
| Oracle Seed vs Anti Seed | 8 | 0 | 0 | 0 |

Oracle 对 Anti 的操纵检查完整通过。Oracle 对 RAG 同样 8/8 clear win，排除了“当前任务本来
不需要 Seed”以及“Disposition 注入对回答模型完全无效”两种解释。

Learned 对 RAG 的 6 个平局都来自没有形成 Disposition 的实例，两臂答案逐字相同。剩余两例
都属于 `one_question_when_overloaded`：一例 Learned clear win；另一例一个位置判 Learned 胜、
另一个位置判平，按协议记为 inconsistent，不把顺序偏差算成收益。

## 因果漏斗

```text
formed 2/8 -> selected 2/8 -> rendered 2/8 -> learned clear win over RAG 1/8
```

形成后的选择率和渲染率都是 2/2。不存在“Seed 已形成但 VDB 没召回”或“已经选中却被 token
预算裁掉”的样本，所以当前不应优先调阈值、embedding 或 memory assembly。

唯一形成的倾向为：用户被多个竞争优先级压住时，Agent 通过询问后果最大、阻塞最多或最不
灵活的一项来帮助聚焦。它在两个独立 probe owner 中由相同的语义回放形成。相对 Oracle，
Learned 文本同时列了三个判据，导致一个回答扩展成多项问题；Oracle 明确要求“只问一个”，
所以即使形成成功，文本压缩仍可能是后续的第二瓶颈。但 2 例不足以把它提升为当前主结论。

## 形成失败的直接证据

- 数据库记录了 24 个 Episode 对应的 24 条非 Agent Outcome；所有 Outcome 都明确评价当次
  AgentAct 的实际效果。
- 24 个 consolidation job 全部完成；不存在服务、索引、超时或格式失败。
- 两个 probe 共享模式但 relationship 隔离。语义状态缓存只复用完全相同的学习内容，因此
  产生 12 个唯一 primary formation 决策：11 个 `NO_CHANGE`，1 个 `NEW_DISPOSITION`；该
  唯一决策在第二个隔离 owner 中语义回放，得到最终 2/8 formed。
- 实际 primary Worker WINDOW 包含每轮 Situation 和 AgentAct，但不包含对应 Outcome 文本。
- MySQL 与 PostgreSQL 的 window builder 都只从已有 feedback target 的 Delivery/Outcome
  交集中构造 `eligibleOutcomes`。新 Seed 尚无 target，也不可能预先有 Delivery，所以这些
  Outcome 虽已入库，却不会进入 Seed 初生窗口。
- Worker prompt 同时规定“Agent 回复和声称用户认可不构成用户认可”以及“不得仅因回复重复
  就复制偶然行为”。在 Outcome 不可见时，对另外三个模式返回 `NO_CHANGE` 是保守但合理的
  行为，不应简单归因于模型能力差。

## 完整性与限制

- 冻结结果包含 8 个实例、32 个回答和 64 个位置互换 Judge 记录；无缺失或非法 token。
- Answer/Judge 有 72 次真实 provider 调用，其余由完整 prompt 的内容寻址缓存复用；Worker
  有 24 次真实 provider 调用、24 次语义状态回放。所有真实调用均 `finish_reason=stop`。
- 平均 memory token 为：RAG 82.25、Learned 92.75、Oracle 118.75、Anti 116.25；四臂均未
  超过共同的 2,048-token cap。预算不是本轮瓶颈。
- 这是 8 例合成开发实验，同一模型同时承担生成和盲评，不构成统计证明或公开 benchmark
  成绩。它可以做机制定位，不能证明真实用户长期陪伴效果。
- Oracle 结果只证明“理想的独立 Disposition 在当前 Harness 中有增量潜力”；实际开源框架
  的优势必须在修复形成路径后，由 Learned Seed 在未修改的冻结数据上稳定胜过 RAG 才成立。
