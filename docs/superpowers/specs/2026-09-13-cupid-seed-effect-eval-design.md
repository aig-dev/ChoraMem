# CUPID Seed 增量效应评测设计

状态：预注册冻结。`eval/cupid-split-v1.json` 在查看任何数据语义前写入；Hugging Face
搜索页意外展示过的 `148+real_estate_developer` 已永久放入 dev。此后只允许 dev
参与实现与调参，H1/H2 各正式运行一次。

## 目标

PERMA 已证明当前 Seed 没有增量收益，其正式 H1/H2 不再复用。本评测改用 CUPID
检验一个更直接的问题：从多次带用户反馈的互动中形成的条件性 `Disposition`，能否在
新境遇中让同一回答模型比 `Recollection-only` 更贴近用户的情境化偏好。

它不改变 Core 定义，也不替代现有四类 companion lifecycle 因果门。

## 冻结数据

- 数据：`kixlab/CUPID@f6e5fdae9b31f2b400d6ceb281a6a6760cc00309`
- 文件：`test.parquet`，12,805,250 bytes，SHA-256
  `6d68af09f7fbe52df0a3bd621604104696d0f481f166be5c06dd65bbb089aaae`
- 官方 evaluator：`kixlab/CUPID@a8560cab293ae98be4fe260689d58bddf96b51ef`
- 756 个实例、252 个 persona；每人各有 consistent / contrastive / changing 一题。
- 按 persona 以冻结 salt 排序：dev/H1/H2 各 84 persona、252 题，集合摘要和完整算法见
  `eval/cupid-split-v1.json`。

H1/H2 的 `current_request`、偏好、checklist 和历史对话在正式运行前都不得人工查看。

## 数据边界

每一行是独立的 memory owner，三类实例不能互相学习；统计时仍以 `persona_id` 为不可拆分
cluster。owner ref 只使用 `persona_id + instance_type` 的 SHA-256，不把类型文字暴露给
Worker。

进入 Memory Core/Worker 的历史材料只有：

```text
prior_interactions[*].dialogue[*].role + content
```

以下字段不能进入 Memory Core、Worker 或回答模型：

```text
instance_type
current_context_factor
current_contextual_preference
current_checklist
prior_interactions[*].context_factor
prior_interactions[*].contextual_preference
```

`current_request` 只用于当前 Select 的 Situation 和三组回答。隐藏 preference/checklist
只在三组回答全部冻结后交给 scorer；`instance_type` 只用于汇总分层。

## 历史回放

八段 prior interaction 按原顺序分别成为八个 session/consolidation window。对话按真实角色
映射：

```text
USER request -> Situation
ASSISTANT response -> AgentAct
下一条 USER feedback -> 上一 Episode 的 Outcome，同时是下一 Episode 的 Situation
最后一条 USER feedback（若存在） -> 最后一 Episode 的 Outcome
session 停在 ASSISTANT -> 不伪造 Outcome
```

同一 USER SourceEvent 可以用不同幂等命令绑定两个关系，正文和来源身份保持不变。历史
Assistant 没有经过本次 Core Select，因此不伪造 Delivery；它可支持新 Seed 形成，但不能
冒充已有 Seed 的 REENACT/REVISE/INHIBIT 资格。每个 session 完成后等待后台巩固归零。

当前请求只创建未绑定的 Situation SourceEvent并执行一次 Select；评测回答不写回 Delivery、
AgentAct 或 Outcome，避免后续模式和题目被评测本身污染。

## 三组回答

三组共享一份学习状态和同一次 Select，只做确定性渲染过滤：

| 模式 | 上下文 |
|---|---|
| `none` | 空 |
| `recollection_only` | Recollection |
| `seed_enabled` | Recollection + Disposition |

冻结条件：

- Answer 与 Worker：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`
- Judge：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`，temperature 0
- Harness：`cupid-seed-generation-v1`
- learned memory：2,048 `cl100k_base` tokens；Episode evidence 关闭
- 回答和 Judge 请求均按完整输入做内容寻址缓存；三组顺序按 instance hash 交错
- Answer 只看到当前请求和可选 `LONG_TERM_MEMORY`，不得看到完整历史或隐藏标签
- Judge 使用 pinned 官方 response-judger rubric，只看到请求、回答、隐藏 preference 和
  checklist；不得看到模式名、memory、refs 或历史
- Judge 必须输出可解析的 1–10 分；任一回答、Judge、截断或传输失败都使正式 summary 拒绝，
  不静默记成零分

因为本 Harness 用 MemoryContext 代替官方 full-history prompt，且 Judge 不采用官方默认
GPT-4o，本结果只用于三组配对因果比较，不宣称可与 CUPID 公榜分数直接横比。

## 指标与通过门

每题得到三组 1–10 分。主要效应是 `seed_enabled - recollection_only`；按 persona 整簇、
seed=0、2,000 次 paired bootstrap。

必须同时满足：

1. H1 平均差值大于 0。
2. H2 平均差值大于 0。
3. H1+H2 的 persona-cluster 95% 区间下界大于 0。
4. H1/H2 都实际形成、选择并渲染过 Disposition，且所有三组与 Judge 无错误。
5. `formed -> selected -> rendered -> score changed` 漏斗、refs、token 数和请求 hash 可复算。
6. 现有四类 companion lifecycle 全部通过，在线 Select 不调用生成式 AI。
7. 若 Core/Worker/Select 有改动，PersonaMem `learned_core` 冻结 A 不低于 `18/35`、B 不低于
   `15/32`。

若正式门失败，该次结论就是失败；不得根据 H1/H2 修改 prompt、Core、选择规则、Judge 或
预算后重跑并继续称其为 holdout。

## 允许修改的唯一入口

正式运行前只在 dev 根据因果漏斗修复一般性失败。当前优先检验的根因是：Worker 把重复的
对话形状当成用户希望的未来响应倾向。修复必须是可脱离 CUPID 文本成立的语义规则，并以
人工通用 fixture 先 RED 后 GREEN；不得按 persona、职业、instance type、关键词或答案写特例。
