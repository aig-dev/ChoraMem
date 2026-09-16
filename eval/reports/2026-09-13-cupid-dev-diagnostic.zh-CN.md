# CUPID dev Seed 因果漏斗诊断

日期：2026-09-13

## 结论

首轮 dev 目标已达到：Memory Core 在完整的真实服务链上两次走通
`formed -> selected -> rendered -> score changed`。本轮定位并修复的首个通用断点是：
多份 Episode 归纳曾能借 `ADAPT` 伪装成一份直接授权，导致无关倾向被拼入同一个 Seed。

修复后，dev 中形成的两个 Seed 都只有一个语义干净的 v1，未再出现复合 v2。
Seed 相对 Recollection-only 的均值差仍为 `+0.13`，但 95% persona-cluster
bootstrap 区间为 `[0.0, 0.4]`。因此本轮证明的是因果链存在并可测，**不是**稳定正收益。

H1/H2 没有被运行或人工查看；当前只冻结正式评测入口，不作正式效果结论。

## 范围与隔离

- 数据：仅 CUPID `dev`，5 个完整 persona、15 个 instance。
- 三组：`none`、`recollection_only`、`seed_enabled`；共享同一次学习和同一次 Select。
- 环境：fresh MySQL database、fresh Chroma collection、真实 Core、真实 Worker、真实 Answer/Judge。
- 正式 holdout：未发现任何 CUPID formal/H1/H2 结果目录。
- v14 因 MemoryIndex 尚未就绪即开始写入而作废；v15 因合法 Judge 文本无法解析而中止。两者只作为故障证据，不进入结果。

## 修复前的通用断点

v13 的 dev 结果已经出现一条完整漏斗，但数据库审计发现同一 Seed 从干净的 v1 被改成了复合 v2：

- v1：技术报告采用表格、分节或要点等结构化形式；2 份形成依据。
- v2：在 v1 后又拼入“使用本地案例”和“警报模板加入本地地标”；7 份依据。

这些调整来自不同的当前任务。它们需要多份 Episode 才能归纳，却通过 `ADAPT` 获得了
直接修订权限。根因不是向量阈值，也不是模型分数，而是 Core 的写入资格把“直接授权”
和“跨经验推断”混在了一条路径中。

## 最小修复

冻结的规则是：

> `ADAPT` 只能引用一份当前、真实 USER 的完整 Episode；需要多份 Episode 才能成立的变化是归纳，只能在重复经验资格完整时走 `TEXT`。

该规则同时落在 MySQL 与 PostgreSQL 的三处权威边界：新 Seed 形成、现有 Seed 变更校验、
版本提交防线。Worker prompt 只负责表达同一语义；即使模型输出越权，Core 也会整批 no-op。
没有加入 confidence、weight、人工审批、关键词判断或新的运行时概念。

测试先在真实 MySQL contract 上观察到 RED：两份当前 Episode 成功产生了 `NEW_DISPOSITION ADAPT`；
加入单一直接依据门禁后，同一测试转 GREEN。随后 PostgreSQL 与 MySQL 的完整 storage contract
均通过。

另修复了一处 Harness 故障：官方 Judge 会返回 `**1**: Unacceptable - ...`。解析器现在允许
分数与说明之间没有空格，但仍要求整段尾部严格匹配单个 1-10 分数；它没有放宽评分含义或失败策略。

v16 完成后，独立 `summarize` 又暴露了一个只影响复核的 bug：它忽略 manifest 中冻结的
dev `persona_limit`，错误地按全量 dev 校验。新增回归测试后，汇总器现在从 manifest 重放该限制；
当前代码对 v16 重新汇总得到完全相同的结果和 manifest SHA-256。该修复不进入学习、Select、
Answer 或 Judge 路径，因此没有重跑付费模型。v16 的运行 manifest 保留当时的 CLI hash，正式运行
将记录下方新的冻结 CLI hash。

## v16 完整 dev 结果

| arm | mean score |
|---|---:|
| none | 4.1333 |
| recollection_only | 5.2667 |
| seed_enabled | 5.4000 |

- `seed_enabled - recollection_only = +0.1333`
- 95% paired persona-cluster bootstrap：`[0.0, 0.4]`
- changing：`+0.2`
- consistent：`+0.2`
- contrastive：`0.0`
- 漏斗：`formed 2 -> selected 2 -> rendered 2 -> score_changed 2`
- frozen summary manifest SHA-256：`ecd5746007bb06697395969c8a18a30916b71d3fa6f30db1221e2f9dd376f3e5`

两个实际形成的 Seed 为：

1. `When the user requests a scientific or technical report, the Agent structures the output with tables, sections, and bullet points to align with scientific conventions.`
2. `When the user requests a technical analysis or scientific report, the Agent structures the output using tables, sections, and bullet points to align with scientific conventions and enhance clarity.`

每条都由两个不同 session 中、各自完整支持同一调整的 Episode 形成；两条都保持 v1。
后续 Worker 曾对它们输出不具资格的 `KEEP` 或多依据 `TEXT`，Core 均未写入新版本，说明模型建议没有越过数据库权威边界。

## 四生命周期门禁

独立 synthetic preflight 使用同一套冻结服务，结果为 `passed: true`：

| case | 结果 |
|---|---|
| no_delivery | Seed 被形成、选择、渲染，但未产生 reenact、revision 或 inhibition |
| positive_complete | 完整 Delivery + AgentAct + 正向 Outcome 记录 reenact |
| no_outcome | 实际重演可记录 reenact，但没有 Outcome 时不能 revision 或 inhibit |
| negative_complete | 完整负向结果链产生 revision/inhibition，后续不再选择原 Seed |

这验证了“记忆被选择 -> 影响行为 -> 行为产生结果 -> 结果改变未来选择”的最小闭环，
同时证明缺少因果资格时不会语义修订。

## 冻结版本

- Core binary：`239b8eb4eab9455492924165f4708a224028e47ee46f5efedfb20df9dc2d44cf`
- Worker service：`583a60f1b0a99951d211b95f3432ec699b833df0a5e28ce9b59517abb23b1318`
- CUPID CLI：`c8490cffa192e356b454e8fb7c6350a847c7d97aec90dc321d31b1805f5d801c`
- CUPID loader：`43b61ce7dcd9149dccc0be3d85fbe09015f602f674cd8f7713b9308b3f09c9b1`
- CUPID effect/Judge：`19487b5df473ad1ebdfb977c27c432979ec97b6681f25103b5d5416da49c02d1`
- CUPID runner：`a0a7e1d519f273ac881e59009082ff87e5a1992b370fbf0be913310bd9486fdd`
- lifecycle：`467a1a4e4241b7872815d1b310616f815313f1147cf785b559bd5e483e89634b`
- MemoryIndex：Chroma 1.5.5，固定 MiniLM archive 与 index source revision，详见 v16 manifest。

## 冻结验证

- CUPID 单元测试：36 passed。
- `make verify-worker`：109 passed、11 skipped；协议漂移、wheel 与 Go-to-Python smoke 通过。
- `make verify-eval`：265 passed、2 skipped；TypeScript 3 passed，typecheck/build 通过。
- `go test -count=1 ./...`：全部 Go packages 通过。
- `scripts/test-mysql.sh`：MySQL storage 与 gRPC transport 通过。
- `scripts/test-postgres.sh`：PostgreSQL storage 与 gRPC transport 通过。
- live lifecycle：四种 case 全部通过。

## 决策

Core、Worker、Harness、Judge 已具备一次性 H1/H2 正式运行的前置条件。下一步如果启动正式评测，
必须直接使用上述冻结版本；不能再查看 holdout 后调参，也不能把 dev 的 `+0.13` 写成产品优势。
正式问题仍只有一个：在未见 persona 上，Seed 是否相对 Recollection-only 产生稳定的增量效果。
