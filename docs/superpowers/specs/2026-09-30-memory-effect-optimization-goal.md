# ChoraMem 记忆效果优化 Goal

日期：2026-09-30

状态：执行中。用户已认可五个方向，并授权落档为 goal 后进行优化修复。

## 目标与完成边界

让有来源的经历在适当情境下改善未来理解与回应，并能在新证据出现后及时修正。分别验证具体经历的可访问性、Recollection 的理解与更新、Disposition 的条件迁移；不以“写出了记忆”代替行为效果。

本 goal 分成五个可独立验收的方向。每个方向按“复现具体失败 → 最小修复 → 工程回归 → 行为诊断”推进。已正确工作的机制只补必要的覆盖证据，不为完成列表制造新模块。效果不明确时保留结论，不把工程通过写成产品优势。

完成条件：五个方向的具体缺陷修复并通过对应回归；真实接线通过生命周期 smoke；未见轨迹的行为诊断具有完整 manifest、可辨识的 Oracle 对照和诚实的效果结论。若 Oracle 本身不能拉开基线，行为验证仍未完成，不以小样本多数票关闭 goal。

## 最小因果模型与权威

```text
有身份和来源的经历 → Episode
  → 可修订的 Recollection
  → 跨独立经历支持的条件性 Disposition
  → 有界 Episode 引用证据
按当前 Situation 选择 → Harness 实际注入并记录准确 Delivery
  → 实际 AgentAct → 真实外部反馈 → 理解修订／倾向修订／局部抑制
```

- `ChoraMem` 是 Core 源码与 Git 的唯一权威；Chorai 只消费公开协议／SDK。
- Core 拥有长期因果账、写入资格、版本和提交；Harness 拥有 prompt、模型、行动与最终表达。
- Episode 保存发生过什么；Recollection 保存可修订理解；Disposition 保存可能改变未来理解、关注与回应的潜在倾向。
- Constitution 是外生、只读、来源绑定的角色快照，不能作为 learned Target 或 Basis。
- 相邻回复提供记录关联，不自动证明回应效果或统计因果性；身份可信也不保证内容正确。
- `REENACT` 表示实际再现，不表示用户认可；沉默、礼貌回应和 Agent 重复输出不是独立成功证据。

## 五个优化方向

### G1 情境召回与局部抑制

**已复现失败：**语义索引命中可保留字面支持得分为 0 的 Seed；无关抑制记录的字面得分也为 0，`support <= inhibition` 把 `0 <= 0` 判为抑制。临时 Go overlay 探针得到同一候选 `selected: 1 → 0`。

**最小变化：**抑制证据先满足当前情境的相关性门槛，再参与竞争。保持同情境的等强或更强抑制有效；不把索引名次改写成永久强度。

**工程验收：**

- 语义改写／跨语言命中不会被零相关抑制屏蔽。
- 低于相关性门槛的反例不干扰；达到门槛且支持不胜出的反例仍抑制。
- 当前情境支持继续有效，其他情境的例外不全局删除 Seed。
- 空／坏 Basis、owner、预算与确定性排序原有约束不变。

**入口：**`internal/core/selection/selection.go`、`ranker.go` 及其测试。

### G2 Episode 证据与 Constitution 真实接线

**已确认缺口：**Core public protocol 已有 `episode_evidence_max_bytes`、`MemoryContext.episode_evidence` 和来源 Constitution；Chorai 的中立端口、outbox、adapter、assembly 尚未完整承载。

**最小变化：**沿现有中立端口映射公开字段；把同一角色快照绑定 Situation intake 与 Select；历史 Episode 在显式有界预算内进入 sealed RunContext，实际渲染的 refs 才进入 Delivery。

**执行前已定位的约束：**同一条真实用户消息可能先成为上一轮 Outcome，再成为下一轮 Situation。Core 按物理 `source_ref` 冻结 Constitution（包括未知）；只在 Situation 中补取当前角色会与已记录的 Outcome 冲突。异步重试也不能重新取当前值覆盖原快照。后续实现须先确定现有 durable intake 如何复用该快照，保持相同 source_ref；不能通过给不同用途制造假来源、放宽 Core 不可变校验来消除冲突。EpisodeEvidence 的公开映射可作为独立子步骤推进。

**2026-09-30 实施决策：**[Chorai 接线计划](../../../../Chorai_Go/docs/superpowers/plans/2026-09-30-memory-effect-integration.md)复用已有 unbound `ObserveSourceEvent`：在现有 outbox 增加 `SOURCE` 命令冻结真实来源，后续用途复用其快照。`SOURCE` 不形成 Episode、Seed 激活或反馈资格。为跨 run 的来源查找与旧 UNKNOWN 命令兼容，在现有 outbox 增加 `source_ref` 列与 tenant/source 索引，通过显式 runtime reconciliation 回填；不新增表。该 schema 变化防止按用途重取快照造成来源冲突，并避免每次来源入账扫描该 owner 的所有 JSON 命令。生产 Episode evidence 预算为 8192 UTF-8 bytes，保持协议最大 16384，完整证据不截断。

**工程验收：**

- 默认预算明确，有界、完整、不截断；非法预算／空引用／重复引用／跨类别重复被拒绝。
- 只看到文本的模型输入不泄露 Core refs；seal 能检测 evidence 文本或 refs 被替换。
- Episode 证据单独非空时也能记录准确 Delivery，且不授予 Seed 激活或反馈资格。
- 当前 Situation 的同一 Constitution 快照在异步 outbox 重试、角色更新和 replay 后保持一致；未知仍为未知。
- Chat 与 Proactive 都通过生产接线测试，不只验证 SDK demo。

**入口：**Chorai `internal/agentos/memory`、`internal/memorycore`、`internal/agentos/assembly`、chat/proactive assembly 与 composition root；Core 公共协议作为依据。

### G3 真实反馈归因与纠错

**风险：**Chorai 当前把紧接 Agent 回复的用户消息作为前一轮 Outcome。文本可能是换话题、事实确认、礼貌回应或真实回应效果，不能把结构链完整当成正收益。

**最小变化：**保留原始反馈与可信身份，由现有后台语义处理区分效果、事实确认和无法判断；明确反馈资格与反馈含义的不同。优先修复现有 `INHIBIT / TEXT / ADAPT` 的到达与处理遗漏。

**工程验收：**

- 换话题、事实确认、沉默／缺失反馈不形成“该回应有效”的新 Seed。
- 本人明确长期纠正只修订 exact existing Target；不能绕过跨 session 形成规则初生。
- 同链负反馈可触发局部抑制或修订，其他情境中有效的倾向仍可用。
- 迟到反馈只在可信显式关联存在时进入原链；无法确认的关联不由模型猜测。
- 不增加追问每轮反馈的产品流程，不推断自评 Outcome。

**入口：**Chorai `chat_agent_reply.go` 与 lifecycle outbox；Core feedback qualification、Worker feedback lane 及现有 causal-loop eval。

### G4 Disposition 条件与候选组织

**当前基线：**完整因果对＋Situation 双视图秩融合；exact owner 回填；当前 anchor＋至多两份跨 session 历史因果对；模型可见材料与提交 Basis 精确一致。2026-09-16 小样本形成门已通过，不把此前已修复问题重复登记为当前缺陷。

**最小变化：**在未见条件改写与跨话题轨迹上诊断候选 recall／precision；只有定位到候选损失才调整有界候选组织。相关反例参与判断，不能只挑成功经历证明倾向。

**工程验收：**

- 相同条件、不同任务的经历可进入同一候选组；同话题、不同条件的经历不能混成一个普遍倾向。
- 同条件失败或冲突不能被绕过；不同条件的例外不制造全局否定。
- 多视图失败、非法 refs、跨 owner／同 session／缺失或多 Outcome 保持原有回退与拒绝语义。
- Basis 仍精确等于模型实际所见的合格组；不把任意 active Seed 算成目标形成。

**入口：**`internal/core/consolidation/memory_index.go`、两套 Store 的 formation candidates、`disposition_materials.py` 与未见轨迹 eval。

### G5 Worker 合同、覆盖率与失败可观测性

**已确认问题：**主 prompt 教授 `NEW_DISPOSITION ADAPT`，执行代码统一删除；主 pass 处理任意 Recollection 会关闭全窗口事实 fallback，可能遗漏其他独立事实。格式失败／预算失败与正常无变化缺少清晰诊断。

**最小变化：**对齐实际资格与 prompt；在现有单个 RPC 内覆盖尚未处理的独立事实，控制去重及调用上限；用现有 Job／Worker 日志区分失败原因，不新增权威状态。

**工程验收：**

- 不向模型提供永远不能提交的新 ADAPT 路径；existing ADAPT 正常保留。
- 一条 KEEP／REVISE 不吞掉同窗口其他独立事实；重复正文和已处理意义不重复写入。
- 时间、临时范围、撤回与归属在文本和既有版本中保持来源忠实。
- 正常 no-change、无合格材料、格式拒绝、预算上限、资格拒绝和 stale 可被诊断，日志不输出来源全文或凭据。
- 单个 consolidation RPC 保持无数据库权限、现有调用上限和冻结窗口提交资格。

**入口：**Worker `service.py`、`source_materials.py`，两套 Store 的 consolidation 及测试。

## 共同效果验证

使用同一未见关系轨迹与固定 Harness／模型／预算，比较：

1. 无长期记忆；
2. Episode＋Recollection；
3. 相同材料＋learned Disposition；
4. 相同材料＋Oracle Disposition。

沿实际链路分别记录材料存在、候选命中、选择、渲染、实际 Delivery、行为效果和纠正延迟。Oracle 必须先稳定拉开基础臂，才解释 learned 的失败为机制瓶颈。负对照、当前位置互换与模型调用完整性不可省略。PersonaMem 保持冻结回归，不再作为定向调参集。

## 认知科学依据与工程推导的边界

- 具体经历与跨经历结构学习的权衡支持“保留 Episode，同时形成可修订概括”，不规定软件必须等待特定时长：[McClelland et al., 1995](https://pubmed.ncbi.nlm.nih.gov/7624455/)。
- 对相似经历进行区分支持 owner／情境隔离与避免话题混组，不规定新增脑区同名模块：[Bakker et al., 2008](https://pubmed.ncbi.nlm.nih.gov/18356518/)。
- 新信息与再巩固的实验联系启发更新诊断；恐惧记忆实验结果不是所有 AI 记忆的统一学习定律：[Sevenster et al., 2013](https://pubmed.ncbi.nlm.nih.gov/23413355/)。
- 记忆痕迹与可访问性可分离启发拆分存储、召回、利用的诊断；不以时间流逝自动删除所有长期材料：[Ryan et al., 2015](https://pubmed.ncbi.nlm.nih.gov/26023136/)。

## ResultSpec 与工程约束

- **用户价值：**相关经历可用、回应倾向可迁移、新证据能纠正旧理解。
- **归属：**Core 长期账本／资格；Chorai Domain lifecycle outbox；可重建 MemoryIndex projection；sealed RunContext 和诊断日志为 runtime。
- **Ontology：**只消费既有 Actor／关系／消息／run 的可信引用，不新增 Kernel ObjectType、RelationType 或 ActionType。
- **写路径：**不新增 Kernel 或产品身份命令；现有来源绑定的 outbox operational evidence 与 Core 因果写路径保持不变，不制造重复 Activity。
- **权限：**exact owner 校验及 permission-filter-first 不变；不由文本推断身份或权限。
- **协议／数据：**接线已有公开字段；G2 在原 outbox 增加来源索引并显式回填，用于物理来源快照的不变性和重放。无新 learned-memory 表。G2 独立审查复现旧零预算 Select 在升级后的冻结请求冲突；追加只读 `ReplayMemoryContext`，验证原完整 scope/run、来源顺序和预算后恢复历史 Constitution，不创建 Context、不刷新 UNKNOWN。此为防止已选择但尚未输出的旧 run 永久受限的必要公开契约变化；Core 先升级。后续其他 schema／公开契约变化仍须先记录具体资格或状态变化。
- **冻结：**在线 Select 无生成式 AI；LLM 执行留在 Python；Go 负责业务编排。模型不生成 ID、confidence、权重或长理由；普通 AI 结果不成为 durable Proposal。
- **回滚：**各子任务独立提交，按提交撤回；不清理既有用户数据。索引仍可由规范真源重建。

## 基线证据

- Core 基线：`82a657c`；Chorai 基线：`90025dac`。
- [PersonaMem 确认复验](../../../eval/reports/2026-09-13-personamem-learned-core-confirmatory.zh-CN.md)：156 题 learned 相对 none 为 +2.56pp，95% CI 跨零，未确认稳定正收益。
- [ANCHOR companion](../../../eval/reports/2026-09-16-anchor-companion-v1.zh-CN.md)：形成／选择／渲染 4/4；三个可操纵 checkpoint learned 胜基础 RAG，第四题仍有 Oracle 天花板。属于小样本自定义受控评测，不宣称自然长期陪伴优势。

## 执行记录

| 方向 | 状态 | 交付与验证 |
|---|---|---|
| G1 | 工程修复与独立审查通过；共同效果待验证 | [选择器计划与证据](../plans/2026-09-30-memory-effect-selection.md)；修复 `1d6bbfc`；RED→GREEN 覆盖零分、低于门槛、零 minimum 与跨语言语义命中；选择器与完整 Go suite PASS；独立审查无阻断项 |
| G2 | 工程与独立审查完成，行为验证待完成 | [接线计划](../../../../Chorai_Go/docs/superpowers/plans/2026-09-30-memory-effect-integration.md) Task 1 已完成，Chorai `86868f77`：完整有界 Episode evidence 的中立映射／渲染／准确 refs，Source／Outcome Constitution 公共映射，拒绝文本／refs 替换的 private memory seal；changed packages 与恢复本地 MySQL 后的完整 Go suite PASS。Task 2 已完成，Chorai `64e9172a`：精确 tenant/source 索引、typed projection 校验、旧 outbox 显式补列／索引和只回填投影；SQLite／真实 MySQL、changed packages 与完整 Go suite PASS；隔离后端实际迁移及重启 health 200（无真实 provider 调用）。Task 3 已完成，Chorai `da3aab8a`：内部 SOURCE 冻结精确物理来源和 Constitution（含 UNKNOWN），用途／跨 run 复用；新 AgentAct 继承 causal Situation；unbound Observe 只有 source receipt，用途 lease 等待其确认。非法 UTF-8 在 JSON 入账前拒绝，SOURCE 投影恢复不改证据；private SOURCE 不绕过有效用途 intake。memorycore／repository／database、真实 MySQL／SQLite 并发首写与 UNKNOWN 竞争、targeted race PASS。Task 4 已完成，Chorai `9111dabe`：生产 Outbox 捕获 governed Soul，assembly 按准确 Situation 来源读取持久快照并启用 8192-byte evidence；真实 adapter／outbox／repository／公开 gRPC／ModelRunner 的受控 Chat／Proactive smoke 验证完整引用、refs 隐藏、模型前 durable Delivery 与 remote Delivery 在 AgentAct 前确认。Snapshot 变化／UNKNOWN、上一 Outcome→下一 Situation、周期 occurrence／旧 run 来源重试通过。affected packages／targeted race／完整 Go suite PASS；真实 MySQL 的 Core Episode 合同确认引用不授予 Disposition 反馈资格；隔离后端实际重启 health200。无外部 inference provider 或自然分布 uplift 结论。Task 5 完成：Chorai `b783b1f8` 与 Core `f4df58c` 修复独立审查的旧零预算 frozen Select 升级冲突；原历史 Constitution／Context 只读恢复，UNKNOWN 来源不变。Core／Chorai uncached full Go suites、targeted race、两数据库完整 Store 与运行中 memoryd replay、Go SDK、Python46 tests、TypeScript40 tests／build／package／生成漂移通过；isolated profile/Make 后端 health200。Critical0／Important1 已一轮 RED→GREEN 修复／Minor0；全部 Ruling 和边界见 [审查记录](../../../../Chorai_Go/docs/evidence/2026-10-04-memory-effect-integration-review.md)。共同 held-out 行为效果仍待验证 |
| G3 | 工程与逐项公开语义已实测；纠正后的模型遵从限制保留 | f1–f9：话题／事实／礼貌／无Outcome不证明效果；准确迟到结果可进入原Delivery并抑制；同一USER来源不重复授权同Seed。两Store回归与实际3回合纠正链均有完整分母；若正文正确而回答仍旧，不宣称立即改变行为。详见共同报告 |
| G4 | 候选工程、真实模型／提交诊断与独立审查完成；共同效果待验收 | [候选诊断计划](../plans/2026-10-05-memory-effect-candidates.md)；冻结六例2/2目标形成、0/4负例Seed，精确origin Job／可见Basis／提交一致；54次完成调用，无候选损失，保留现有K；共同效果仍待验收 |
| G5 | 工程与公开来源语义已实测；模型加工／遵从仍有限 | 原17项实际检查满足后另修g3重复分句、r7条件丢失、r6重复纠正；g6保留归属报告、g8不重复原值。当前完整Python812／production Worker205通过，可选13／11跳过明确列出。新增畸形控制拒绝、完整形成模式与回答侧能力分开诊断，最终整组审查待完成 |

每项完成后追加实际命令、结果、提交及未解决限制；只有验收得到证据才更新状态。

G3／G5 的具体遗漏与验收分解见 [Worker 优化计划](../plans/2026-10-04-memory-effect-worker.md)：反馈逐目标覆盖、existing ADAPT 保留、剩余事实覆盖和安全诊断，Task1–4工程与审查已完成，真实语义尚待实测。G4 候选与共同效果评测保持原目标。

G3／G5 Worker 进展：Task1 `dc9d48e` 已修复逐目标反馈遗漏和 existing ADAPT 被清除，主 prompt 不再提供不可提交的新 ADAPT；Task2 已通过 new／KEEP／revision 的独立事实覆盖、准确重复过滤、无效 Target/Application/Basis 不授予 coverage 和完整四次调用上限。Worker147 tests通过，11 optional live tests跳过。日志分类、两数据库提交链与未见模型行为仍待完成，不能将受控文本回归解释为自然分布 uplift。

G3／G5 Task3：安全日志已区分正常无变化、无合格材料、格式／资格拒绝、输入／输出预算、Worker 失败与 stale。超长 feedback 在归一化前拒绝；异常消息不携带 provider／来源正文，失败仍可重试。Worker155 tests通过，11 optional live跳过；完整 Core Go 及 PostgreSQL／MySQL 迁移后完整 Store、运行中 memoryd RPC测试通过。Task4 整组审查与共同 held-out 尚未完成。

G3／G5 Task3提交 `26f03f8`。Task4的完整 Core／vet／Worker 发布包与跨进程 smoke、两数据库最新 memoryd runtime 均已通过；架构与 Worker 文档已同步，整组独立审查正在进行。共同效果 gate 仍保持待验证，不将11个跳过的主 prompt 语义用例计作实测通过。

G3／G5整组独立审查已闭环：行内合法 ADAPT／TEXT、临时覆盖与 aggregate预算误报经6项RED→GREEN修复；cross-target恢复错误经4项RED→GREEN修复，只使用对应Target已有markers，不猜关联。完整Worker165通过／11 optional live跳过；最终 clean wheel／生成漂移／生产 Go→Python smoke通过。全部审查决定与风险见 [持久审查记录](../../evidence/2026-10-05-memory-effect-worker-review.md)。G4、真实模型语义与共同四臂行为验收仍保持执行中。

G4 Task1：共享真实数据库合同首次执行即通过，当前双视图／资格机制保持不变。负Outcome原文进入模型材料；cross-owner／same-session／无、多、Agent Outcome与错kind不成为候选；缺少、多余、重复、仅anchor的Basis不提交，精确可见组可提交；因果视图失败、情境视图失败及全候选过滤的原回退／no-op保持。完整Go／vet、PostgreSQL25.185s Store＋0.331s RPC、MySQL41.709s Store＋0.470s RPC通过。这是已给定候选顺序的资格证据，尚不能证明语义召回、条件精度或行为收益。

G4 Task2完成：冻结合成六例数据 `e1b377f`；真实Chroma／配置DeepSeek／生产reference Worker／Core公开入账与规范提交验证2/2目标、0/4误形成，54/54 stop调用及20/20 RPC完成；c6早期两对Basis准确关联，未拿最后三对窗口替代来源。此前完整候选已到达且focused正文正确，却被主pass缺TEXT操作标签拖成整批no-op，`bee69d3`窄检查丢弃该无效主结果；2项RED→GREEN／Worker167通过／11 optional跳过，未扩大K或改变Core原子提交。最新完整Core／vet、eval424／2 optional跳过、clean wheel／生产bridge、PostgreSQL21.461s＋0.398s和MySQL44.260s＋0.449s均通过；整组独立审查与共同四臂仍待完成。详细模型别名、认证失败／不完整尝试、manifest、哈希与有限样本边界见 [G4实测报告](../../../eval/reports/2026-10-05-memory-effect-candidates.zh-CN.md)。

G4 Task3闭环：一次独立审查核对原始54调用／20RPC、origin Job与exact Basis，126项独立测试通过；scorer完整性finding由Minor升级Important，5项RED→GREEN修复。另冻结拥挤负例11调用／4RPC，相关失败未被两槽挤掉，无Seed；Core canonical同源复用保留Session、ChoraiSOURCE拒绝物理消息session替换。最新全Python596通过／13可选跳过、完整Core／vet、PostgreSQL25.470s＋0.336s与MySQL43.684s＋0.449s通过；没有候选或身份协议变化。所有12项Ruling／代价见 [G4审查记录](../../evidence/2026-10-05-memory-effect-candidates-review.md)，该局部计划已完成；完整goal仍继续共同四臂／Oracle、实际Delivery／AgentAct、G3真实反馈及G5来源语义验收。

2026-10-05共同进展：完整29例原始因果回放、全部G3／G5语义检查及修复子集保存，三轮完整长材料12例不合并；最新fast形成／common policy回放499模型调用／90RPC，全部完成，Oracle两比较均7/8位置一致赢、0输，通过冻结判别。执行者逐项检查10/12，d6正文信息和d7回答序仍有损失，完整结果和模型flags分别保留。当前formation thinking/high的12包及两个非canonical回答揭示文本加工能力与回答遵从的区别，公开mixed-profile回放在d2两次reasoning耗尽8192token后中止，完整失败被保留且不获生产推荐；现有strict guard对90份真实RPC捕获回归保持字节一致。目标的完成依据仍是具体缺陷回归、真实接线smoke、完整可辨识诊断和诚实局限；模型个别回应不遵从不能被改评分掩盖，也不能推导新增Core状态／置信度。详见 [共同因果报告](../../../eval/reports/2026-10-05-memory-effect-causal-gate.zh-CN.md)及 [共同计划](../plans/2026-10-05-memory-effect-causal-gate.md)。
