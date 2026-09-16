# Seed-essential Delayed v1：Outcome 形成路径复验

日期：2026-09-15

## 结论

本次结果**没有验证 Learned Memory Core 相对 Recollection RAG 的稳定优势**。

让非 Agent Outcome 成为新 Disposition 的可见、可引用形成依据，修复了一个真实的因果链缺口；
但在同一 8 例 v1 上，formed 从旧运行的 2/8 变为 1/8，Learned Seed 相对 RAG 仍只有 1 个
clear win，未达到预先冻结的 6–0 dominance 门。因此“Outcome 不可见是主要形成瓶颈”这个假设
没有得到支持。

当前证据更具体地指向 **Worker 的形成判定不稳定且任务耦合过重**：最终窗口已经包含完整的
三组 `Episode + non-Agent Outcome`，Core 也能验证、提交、选择和渲染合法结果，但同一模型
经常返回 `NO_CHANGE`。这首先是工程实现瓶颈，不足以直接否证 Disposition 架构；然而在
Learned 路径稳定胜过 RAG 之前，也不能宣称该架构具有实际优势。

## 受控条件

与 2026-09-14 基线保持不变：

- 数据、8 个实例和实例顺序；
- `MiniMax-M2.5@api.minimaxi.com-2026-09-08`；
- temperature 0、Answer/Judge 各 1,024 tokens、Worker 8,192 tokens；
- 每臂 2,048-token Memory 预算；
- Chroma 1.5.5 + `all-MiniLM-L6-v2@913d7300` exact cosine；
- quiet period 5 秒、投影轮询 100 ms；
- 四臂 Harness、Answer prompt、Judge prompt、双向判分与 6–0 dominance 门。

数据 SHA-256：
`c9938e05431347d5595f694b229be2104838d2e475e3965b79a1876ec92a12b8`。

本次 manifest SHA-256：
`0ffad1eca02c1776d3ed9eec059a4065a8f5be6d0c6328d56b2c87d5850a8893`。

自动对比确认：除预期改变的 Core／Worker 代码快照、revision 标记，以及为支持 Outcome ref
alpha-renaming 而变化的 eval Worker adapter 文件外，所有上述受控字段逐项相同。

运行使用新建空 MySQL 8 database 与独立空 Chroma；最终运行结束后数据库容器已删除，冻结产物
保留在 `.cache/seed-essential-delayed-v1-outcome-formation/`。

## 结果对比

| 指标 | 旧基线 | Outcome 形成路径 |
|---|---:|---:|
| formed / selected / rendered | 2 / 2 / 2 | 1 / 1 / 1 |
| Learned vs RAG | 1 胜 / 0 负 / 6 平 / 1 不一致 | 1 胜 / 0 负 / 7 平 / 0 不一致 |
| Oracle vs RAG | 8 / 0 / 0 / 0 | 6 / 0 / 0 / 2 |
| Oracle vs Anti | 8 / 0 / 0 / 0 | 8 / 0 / 0 / 0 |

本次唯一形成的是 `one_commitment_when_avoiding` 的一个 owner：

> When the user describes preparing materials instead of sending them, the agent responds with a specific time-bound action step that overrides the preparation urge.

它引用了三组 Episode 与配对 Outcome，通过 Core 校验后被选择并渲染。Learned 回答在双向 Judge
中均胜过 RAG，说明该样本的完整 `formed -> selected -> rendered -> behavior_changed ->
score_improved` 链成立。不过回答只建议先打开消息、甚至可以暂不发送，没有完全实现 Seed 所写
的“time-bound action”，所以生成文本精度和下游遵循仍可能是形成之后的第二瓶颈。

## 问题定位

### 1. Outcome 数据通路已经工作

- 24 个 primary Worker 窗口中，首轮 8 个窗口没有历史 Outcome，第二轮 8 个各有 2 条，第三轮
  8 个各有 3 条；这与逐 session 巩固和 overlap 设计一致。
- 每条可见 Outcome 都携带 `OUTCOME_EPISODE`，且只允许非 Agent actor。
- 唯一形成结果同时引用三份 Episode 和三份 Outcome，证明不是只改了 prompt 而没有进入提交路径。
- 24 个 primary 加 24 个 Recollection fallback 全部完成，正式运行无 Worker 错误。

因此本轮瓶颈不是“Outcome 仍未进入窗口”，也不是 Core 拒绝所有 Outcome Basis。

### 2. 选择、渲染和检索不是首要瓶颈

唯一形成的 Seed 以 1/1 的比例被选择并渲染。未形成的 7 例没有 Seed 可供检索，不能归因于
embedding threshold、VDB、token 裁剪或 Memory Assembly。

### 3. Worker 的离散形成决定存在高方差

调试期间，一个被冻结、SHA-256 为
`efaef3c47caddfadb597db375a37ceb86d0c7bfb8049690611cb3e98448a9652` 的完全相同 Worker 输入，
在同一 `MiniMax-M2.5`、temperature 0 下真实调用三次，得到：

```text
NEW_DISPOSITION
NEW_DISPOSITION
NO_CHANGE
```

三次 provider `finish_reason` 都是 `stop`。前两次结果由于当时 eval adapter 尚不能 alpha-rename
Outcome ref 而在回放记录阶段报错，第三次则返回 `NO_CHANGE`；这个 adapter 缺口随后以失败测试
复现并修复。虽然这三次不计入正式成绩，它们构成了形成判定方差的直接证据。

正式运行结束后，又把 8 个最终 Worker 输入逐字、只读地重放一次，不写数据库：正式运行仅在
avoidance 的一个 owner 形成；隔离重放则改为在 overload 和 permission 各一个 owner 形成，
其余返回 `NO_CHANGE`。相同证据可以生成合理 Seed，但是否生成缺乏稳定性。

### 4. Recollection 生成会改变后续形成上下文

每个 session 的 primary pass 后还运行 fact-only Recollection fallback。相同历史的不同真实调用
会形成略有差异的 Recollection 集合；下一窗口又把这些文本作为去重候选交给 primary Worker。
例如同一 overload 第二窗口，一次运行保存“打开表格并冻结”与表格内容两条，另一次只保存表格
内容一条，随后形成决定也不同。

这说明当前统一 Worker 实际同时承担：

```text
事实／偏好去重
+ Recollection 演化
+ Disposition 归纳
+ 既有 Seed 反馈
+ 多种写入语法选择
```

Outcome 可见之后，Core 的因果资格已经足够清晰，但模型仍需在过多相邻语义规则之间做一次
离散裁决。当前失败更像这个后台 AI job 的职责耦合和判定稳定性问题，而不是关系库或 VDB 问题。

## 对架构的客观评价

可以确认的只有两件事：

1. 独立 Disposition 通道有**行为容量**。Oracle 对 RAG 为 6 个 clear win、0 个反向 clear win，
   达到冻结门；Oracle 对 Anti 为 8–0。一个合适的倾向通过同一 Harness 注入后确实能改变结果。
2. 当前 Learned Core 没有把这种容量稳定转化为学习收益。1–0–7 不能视为优势，Outcome 修复也
   没有提升 formed。

Oracle 结果不能证明唯识启发架构优于主流 Memory：向 prompt 注入一条人工写好的好指令，本来就
可能胜过事实 RAG。它只排除“Disposition 通道完全不起作用”，不能排除“更好的 Episode RAG、
摘要或普通用户偏好 prompt 也能达到同样效果”。

所以当前最严格的表述应是：

> 高层因果闭环仍然可行，但其独特优势尚未被验证；现有实现的主要可观测瓶颈是 Disposition
> 归纳 Worker，而不是选择层。若在聚焦的形成任务中仍不能获得稳定 Learned 增量，就应接受
> 该架构在当前模型与数据规模下没有可证明优势。

## 下一步实验

暂时不调检索阈值，也不进入更大的 benchmark。先做一个最小 ablation：

```text
同一批冻结 Episode + Outcome pairs
  -> 当前统一 Consolidation prompt
  vs
  -> 只负责 Disposition 归纳的短文本 prompt
```

两臂都使用相同模型、无 JSON、无 confidence、无新增表；Core 的资格门和提交规则不变。先比较
是否形成、文本是否覆盖完整响应倾向、以及同一输入重复调用的一致性，再把胜出的 Worker 方案
原样带回本 v1。这样可以直接检验“只是 prompt／job 拆分工程问题”还是“Seed 归纳本身没有稳定
可实现性”。

## 完整性与限制

- 正式结果包含 8 个实例、32 个回答和 64 个双向 Judge 记录；68 次 Answer/Judge provider 调用、
  28 次 Worker provider 调用，全部 `finish_reason=stop`。
- 第一次启动因错误等待不存在的 readiness 日志标记而在进入实验前终止；第二次运行发现 Outcome
  ref 语义回放缺口并中止。两次均使用独立空库、不计入成绩，失败日志被保留。
- 本评测只有 8 个合成实例，同一模型参与 Worker、回答和 Judge。它适合定位机制，不是统计证明，
  也不是公开 benchmark 成绩或真实长期陪伴效果。
