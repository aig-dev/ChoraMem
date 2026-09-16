# Seed-essential Delayed v1：因果配对形成实验

日期：2026-09-15

## 结论

本轮首次在冻结的 8 例 Seed-essential Delayed v1 上达到预设门槛：

- `formed / selected / rendered = 8 / 8 / 8`；
- Learned Seed 相对 Recollection RAG 为 `6 胜 / 0 负 / 1 平 / 1 不一致`；
- 自动判定为 `preliminary_learned_advantage`。

主要改变量不是数据库、VDB、检索阈值或新的评分数值，而是后台 AI Job 的输入边界：Core 先把
每次经验确定性组装为 `用户情境 -> Agent 反应 -> 非 Agent Outcome`，再让模型从这些完整因果
配对中归纳一条 Disposition。模型只返回一行倾向或 `NO_CHANGE`；`TARGET`、`APPLICATION`、
`BASIS` 仍由程序补回。

因此，当前证据支持：此前 Learned Core 没有增益的首要原因是统一 Worker 的职责耦合和证据
组织方式，而不是 Disposition 因果架构本身没有行为容量。但这仍是 8 个合成实例上的初步开发
证据，不是对真实长期陪伴或跨模型泛化的证明。

## 实验链

### 1. 原统一 Worker 与简单专注 Worker

冻结上一轮 8 个最终窗口，每个窗口都有三组 Episode 与配对 Outcome。使用同一
`MiniMax-M2.7`、temperature 0、无缓存，每臂重复三次：

| Worker | 形成 | NO_CHANGE | 格式错误 | 三次形成决定稳定的案例 |
|---|---:|---:|---:|---:|
| 原统一 Worker | 13 / 24 | 11 / 24 | 0 | 2 / 8 |
| 简单专注 Worker | 24 / 24 | 0 / 24 | 0 | 8 / 8 |

简单专注 Worker 的 24 条文本都保持了对应的四类条件—反应关系，但逐字文本均有正常改写，
所以不应使用 exact-string 一致性评价语义稳定性。

产物：`.cache/disposition-formation-ablation-m27-v1/`。

### 2. 负对照揭示简单专注 Worker 会过度形成

对简单专注 Worker 构造四类负例，每类重复三次：

| 负例 | 错误形成 |
|---|---:|
| 三种无关模式混合 | 3 / 3 |
| 缺少 Outcome | 2 / 3 |
| Outcome 明确表示无效 | 0 / 3 |
| 两条一致、一条冲突 | 3 / 3 |

总计 `8 / 12` 假阳性。由此否定了“只缩短 prompt 就足够”的方案。模型会把异质经验宽泛概括
成一句听起来合理的话；这会制造人格趋同，而不是真实熏习。

产物：`.cache/disposition-formation-negative-controls-m27-v1/`。

### 3. 因果配对的单 Job

将证据改为逐经验配对，并明确任一缺失、负面或冲突配对都返回 `NO_CHANGE`。四类正例与四类
负例各重复三次：

- 正确：`24 / 24`；
- 假阳性：`0`；
- 假阴性：`0`；
- 格式错误：`0`。

这里没有增加第二个 verifier、confidence、权重、表或人工审批节点。真正有效的变化是让软件
先表达已有的因果关系，模型只负责语义归纳。

产物：`.cache/disposition-formation-paired-gate-m27-v1/`。

### 4. 既有 Seed 去重

第一次端到端试跑发现：第二个 Episode 后形成 Seed，第三个 Episode 后又创建了同义 Seed。
原因是最初的专注 Job 看不到既有 Disposition 文本。该运行立即中止并保留为失败证据：
`.cache/seed-essential-delayed-v1-paired-formation/`。

随后在同一个 Job 中加入不带 ref 的既有 Disposition 文本。用实际第二、第三窗口及冻结的
`MiniMax-M2.5` 各重复三次：

- 没有既有 Seed：`3 / 3` 形成；
- 已有同义 Seed：`3 / 3` 返回 `NO_CHANGE`。

产物：`.cache/disposition-formation-dedup-m25-v1/`。

## 端到端正式结果

正式运行继续使用冻结的：

- 8 个实例、4 种模式与原实例顺序；
- `MiniMax-M2.5`、`https://api.minimaxi.com/v1`、temperature 0；
- Answer/Judge 各 1,024 tokens，Worker 8,192 tokens；
- 每臂 2,048-token Memory 预算；
- MySQL 8 空数据库；
- Chroma 1.5.5 + 固定 `all-MiniLM-L6-v2` exact cosine；
- quiet period 5 秒与相同选择、渲染和双向 Judge 协议。

正式产物：`.cache/seed-essential-delayed-v1-paired-dedup/`。

Manifest SHA-256：
`0c91f8015b2e77fe8723946a2c82585cf44dd27dc91de989eac7ba9025e110d1`。

| 比较 | 第一方胜 | 第二方胜 | 平 | 不一致 |
|---|---:|---:|---:|---:|
| Learned Seed vs RAG | 6 | 0 | 1 | 1 |
| Oracle Seed vs RAG | 6 | 1 | 0 | 1 |
| Oracle Seed vs Anti | 7 | 0 | 0 | 1 |
| Oracle Seed vs Learned Seed | Oracle 1 | Learned 2 | 4 | 1 |

数据库只读核验：

- 8 个 owner 各恰好 1 个 Seed；
- 共 8 个 SeedVersion，无同义重复版本；
- 每个 SeedVersion 引用 2 个不同 Episode 与各自的 2 个非 Agent Outcome；
- 8 个 Seed 全部被选择并渲染；
- 32 个回答、64 个 Judge 记录完整；
- 96 次 Answer/Judge 与 31 次 Worker provider 调用全部 `finish_reason=stop`，无回答错误。

## 对架构的客观判断

本轮验证的是一个真实而有限的优势：与只提供事实/事件的 RAG 相比，Core 从跨会话成功经验中
形成条件性反应倾向，随后能在新情境中改变 Agent 行为。完整链路在 8 个实例上均发生，且 Learned
在冻结判分门下首次支配 RAG。

这也说明“Seed 是事实集合”并不准确。工程上真正产生增量的是：多个带结果的经验共同支持一个
可迁移的条件—反应倾向；Episode 和 Outcome 是它的因，Seed 是可在未来现行的压缩倾向。

但当前还不能声称：

- 已优于 Mem0 等通用 Memory 框架；
- 已证明真实长期陪伴效果；
- 已证明跨模型、自然聊天或大数据量下仍稳定；
- 唯识理论本身得到实证验证。

Oracle 对 RAG 仍出现 1 个反向 clear win，Learned 也有 1 个方向不一致，说明回答生成和同模型
Judge 仍有方差。下一步应复验这一结果并加入未见模式、自然长对话与负例，而不是继续调 VDB
阈值或扩展新的 Memory 类型。

## 最小工程机制

推荐冻结为：

```text
Core 确认跨会话结构资格
  -> Core 配对 Episode / AgentAct / non-Agent Outcome
  -> 后台模型读取因果配对 + 既有 Seed 文本
  -> 输出一行新倾向或 NO_CHANGE
  -> Core 确定性补回 TARGET / APPLICATION / BASIS 并提交
  -> 后续选择、渲染、行为和 Outcome 继续形成闭环
```

这一机制保持 AI 只做文本理解，不要求模型生成 ID、JSON、confidence 或数值权重。

## 安全事项

本轮定位配置时发现真实 Token Plan Key 存在于 Chorai 的已跟踪
`env/templates/dev/chorai_agent.env.example`，包含它的提交已经存在于 `origin/main`。仅在新提交中
替换文本不能使旧 Key 安全；应先在 MiniMax 控制台撤销/轮换，再把模板恢复为显式占位符，并
另行决定是否清理 Git 历史。
