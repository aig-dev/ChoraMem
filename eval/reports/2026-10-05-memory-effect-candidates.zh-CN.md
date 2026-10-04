# G4 候选与真实提交诊断

2026-10-05，Core 工作分支 `codex/memory-effect-optimizations`。目标／计划分别见 [五方向 goal](../../docs/superpowers/specs/2026-09-30-memory-effect-optimization-goal.md) 与 [G4 plan](../../docs/superpowers/plans/2026-10-05-memory-effect-candidates.md)。

## 结果与边界

冻结的六例合成诊断完成：两例目标倾向形成，四例负例未形成 Disposition。两个正例的最后候选组均包含全部三份标注支持经历，所需四份历史支持经历都进入候选。目标正文与产生它的实际模型输出一致；版本的 Episode＋Outcome Basis 精确等于对应形成模型所见材料。c6 在第二份经历时已形成，因此提交 Basis 是两对；最后一组有三对，不能拿最后一组冒充该版本的形成来源。

未观察到候选损失，保留原来的双视图秩融合和 anchor＋至多两份历史经历。发现并修复主模型遗漏 `TEXT` 操作标签造成的提交损失。完整验证／独立审查结果在本报告末尾追加。

这是固定小样本的候选、语义与事务诊断，由执行者读取确切正文作显式语义评审。它不证明自然分布收益，也未包含共同四臂、实际 Delivery／AgentAct 或纠正延迟。该完整 goal 继续执行。

| 案例 | 冻结条件 | 最后组 | active Disposition | 精确目标 | 完成调用 |
|---|---|---|---:|---|---:|
| c1 | 相同选择困难跨任务，夹杂已决定后的信息请求及事实确认 | s1/s3/s5 | 1 | 形成；3 Episode＋3 Outcome | 14 |
| c2 | 同话题但已决定／求地址等不同条件 | s1/s2/s3 | 0 | 无 | 8 |
| c3 | 同条件先失败，再有两次成功 | s1/s3/s5 | 0 | 无；失败原文仍可见 | 8 |
| c4 | 同条件成功，具体回应策略互相冲突 | s1/s3/s5 | 0 | 无 | 8 |
| c5 | 只确认持久事实准确 | s1/s2/s3 | 0 | 无 | 8 |
| c6 | 同条件在课程／布局／志愿者安排中的改写 | s1/s3/s5 | 1 | 形成；2 Episode＋2 Outcome | 8 |

c1 实际正文：When the user is stuck circling between options that all feel equally important, the Agent asks which single tradeoff matters most while explicitly leaving the choice to the user.

c6 实际正文：When the user is stuck revisiting a consequential choice, the Agent reduces the options to a single tradeoff and asks which side matters more, leaving the decision to the user.

两行都保留“一项 tradeoff＋本人决定”的条件回应，未将“同话题”或“Agent 重复输出”当成成功。c3 的失败证据未按 Outcome 正负筛掉；c1 的不同条件例外没有全局否定。四份负例只是本 cohort 的零误形成，不是总体精度估计。

## 冻结与实际运行

数据在外部调用前提交 `e1b377f`：[`memory-effect-candidates-v1.json`](../cases/memory-effect-candidates-v1.json)，SHA256 `cde6bba9dc59b055a2b3c25860c81618683b2a91e0ab97750d63516f902d84b2`。两正／四负，期待与 rubric 只在 scorer 中，未传给检索或 Worker，也未包含在 replay input hash 中。PersonaMem 未改动。

调用模型是现有配置的 `deepseek-v4-pro`，temperature0、thinking disabled、8192 output tokens、35s 单次 provider timeout、provider retries0、completion attempts1。服务端构建号不可取得，manifest 明确记为配置别名，不声称可锁定远端权重版本。生产凭据第一次返回401；明确记录后改用本机已有 private DEEPSEEK_API_KEY，端点／模型不变，凭据未写入 artifacts。

索引使用现有 eval Chroma1.5.5／all-MiniLM-L6-v2，CPU、cosine、220/32 分块、exact scoped search，模型 archive SHA256 `913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3`。Core 用生产 memoryd／MySQL Store，由真实公开 ObserveSourceEvent／ReportOutcome 入账、独立 owner/session、实际 migration、scheduler、projector、Select。只为实验将 quiet period 设为2s、projector poll100ms；Worker RPC 仍是 stock120s。每份历史经历等待投影与巩固完成后再进入下一份。

Python Worker 是生产 reference InferenceService；eval wrapper 只记录 typed RPC 和 ContextVar 调用关联，无替代形成器、语义 replay、Oracle 注入、DB 写权限或候选修改。DB probe 仅 SELECT 规范版本、origin_job_ref、Episode／Outcome Basis。测试 DB 从空开始，结束后仅删除本次生成的 schema；既有容器／数据库不变。

完整运行 `run-91b0f97980f5`：20/20 started RPC完成、54/54 started模型调用 stop完成、失败0、重试0；6个RPC各2次，14个各3次，均小于现有四次上限。原始 request 的合格组通过现有 `disposition_formation_material` 构造输入，并与实际 formation call 的输入逐字比对；Target 的 origin Job、实际输出、bound tagged block、提交 Basis 逐个关联，未用任意 active Seed 或最后一次窗口替代它。

工作区本机证据保存在 `.cache/memory-effect-optimizations/g4-live/`；run.py 的调用前冻结来源／哈希见 runtime manifest；summarize.py 的调用后只读分析来源与输入／输出哈希见 analysis-manifest.json，全文只包含合成案例。此目录忽略 Git，需连同 artifact 目录保存以本地重放诊断；公开冻结数据与测试已提交。

| 证据 | SHA256 |
|---|---|
| manifest.json | 50ada03bf2ac69939ec98e05277a30b0b3de502f647253f66cff2fa827a66cac |
| core binary | 5f3cc10325a87a43c5cb98d68b85f60f120393a9f7667a1aabe05181aaa46923 |
| cases.jsonl | d1fb4dc8cc1b2c530b1cb9089d60be44284715e8d2b2ca2172e4831d7b6c1d23 |
| rpcs-started.jsonl | 9ef194589f74cf3f1be3e2c602c8d8493d1c22a52dcd70389f4f9e88c3301f0d |
| rpcs.jsonl | 555e966d4991bd18bdc2ef9f3f2117c6e685d9cd3d2640008780bbb37607c87a |
| calls-started.jsonl | f4312462ef023d074a65e0f54d884cf3dbee0c317ca78422156f44f04dbc1441 |
| calls.jsonl | b6165491690de1b6801e202084525bc3504d509719f1cabfe7f51bc4bee523e9 |
| usage.jsonl | c72dac04806255ede2a971e5fc03a4fd38ddffd064839501f2063ae83f8e9c2d |
| index.jsonl | a8312f855bdd4a98e36b587a3bed548fe3e0495c8710065b277fee91ac976112 |
| scored-records.json | cac4c4f12e748efb304cf91f4fda3a42f7659910596c04e25443a73ee9a0d5af |
| summary.json | 6a60adcc624c2b7ac1e465d439d549d6e9efc6db0c03c10f7978d1197b6d1f6c |

## 复现到的修复

首个认证成功的尝试 `run-c3991884c37b` 中，c1 最后形成组确实是 s5＋s1＋s3，focused 模型写出了上面的正确倾向；主 pass 的 Recollection／Disposition 却在 CHANGE 后直接写正文，缺少 TEXT。Core 的严格 parser 因此拒绝整批，未提交 Seed。该尝试后来因 eval JSON 序列化 TypeError中断；它是定位证据，不计入完整效果分母。最初返回401的 `run-8795f8eb328c` 同样只记录认证失败。

`bee69d3` 增加窄检查：识别 TARGET／APPLICATION／CHANGE 头后，操作必须是既有 TEXT／ADAPT／KEEP／REENACT／INHIBIT。缺失时丢弃整个主 pass 并记安全 rejected_format；不猜操作／正文，不提供 coverage，仍允许独立的形成／事实绑定。Core 最终整批 parser、精确资格、冻结快照与原子提交不变；其他格式错误仍可能使整批 no-op。

两项测试完整复现原形的 RED→GREEN。完整 Worker167 pass/11 optional primary-live skips。修复后完整运行里该 guard实际触发6次，两个正例准确提交；未扩大K或新增运行时概念／表／协议。此前 Worker 审查曾接受 malformed-primary 浪费独立调用的代价；本次真实损失提供了新增这项窄检查的具体依据。

## 资格回归

共享两数据库合同首次通过：完整同条件失败证据保留；cross-owner、同 session、非法／重复 ref、错 kind、无／多／Agent Outcome被过滤；Basis 少、多、重复、仅 anchor不提交，精确组可提交；双视图错误保持原有 fallback／anchor-only no-op。此处给定候选次序，不能替代上面的真实语义检索。

诊断器19 pass；完整 eval424 pass/2 skips是当前 codegen venv 未装 chromadb 的两项可选集成测试；本次真实运行则使用现成 ChoraMem/Chorai Python 环境中的 Chroma1.5.5。Worker的11 skips是既有可选 primary prompt 语义测试；本次 full service 调用仍不涵盖 G3 feedback资格或 G5所有时间／撤回／归属语义。

完整工程 gate：uncached Core Go与vet通过；eval424 pass/2 optional Chroma skips；clean Worker wheel／生成漂移／发布包167 pass/11 optional primary-live skips、生产Go→Python bridge0.331s通过；完整迁移后 PostgreSQL Store21.461s／运行中公开RPC0.398s，MySQL Store44.260s／公开RPC0.449s通过。独立整组审查随后进行。

## 独立审查闭环与补充边界

[完整审查Ruling与代价](../../docs/evidence/2026-10-05-memory-effect-candidates-review.md)。一次审查独立126测试与54调用／20RPC原始证据核对通过，Critical0／Important0／Minor1；scorer完整性Minor由执行者升级Important，一轮5项RED→GREEN修复，现须保存启动清单并以attempt_ref／RPC／input hash核对每条完成；截断实测记录到6调用现在拒绝。原始54调用与评分结论不变；原v1分析在analysis-v1/，最新scored-records/analysis-manifest为只读v2，调用前source逐个按manifest哈希保存到sources/，没有修改原模型输入／输出。

额外拥挤控制在调用前单独冻结 [boundary-v1](../cases/memory-effect-candidates-boundary-v1.json)，SHA256 `7d9e71fa0f3a20a591d2c90abd33d1079f47694019e5c1b26477d1b01d35a5f4`，原六例不变。s1同条件失败与s2/s3成功竞争两历史槽；当前s4时实际组为s4＋s1＋s2，失败原文保留，无Seed。完整运行run-efe8a5250a62，manifest `432aa216224179f4c9d8d4de81d1235199d5a82de24fe6a7f77cbe19e2744b0a`，11/11 stop调用／4/4RPC／0重试失败，每RPC2–3次；summary `127bd5f55450790d63dcf1fa96c69e3f2088310447568bbff350b734451da214`。此一例不保证所有拥挤历史的召回。

Source边界保持既有合同：Core的身份是full scope/ref；同一canonical SourceEvent重用于两个run时保留原Session，不能增加一个独立candidate。Chorai的真实物理消息由SOURCE的tenant/source冻结，已有测试确认session/scope/原文/actor/Constitution替换被拒绝，Outcome→下一Situation复用同一快照。初始错误期待Core对改变namespace的同字面ref报冲突，仅是测试假设错误，未算生产RED或改变Source身份合同；通用Core producer仍须提供可信来源身份。

最终一轮fix：Python596通过／13可选跳过，完整Core／vet；PostgreSQL25.470s Store＋RPC0.336s、MySQL43.684s Store＋RPC0.449s最新完整迁移/runtime通过；ChoraiSOURCE guard0.561s。没有第二review／模型重跑来替代证据；补充c7为执行者实测。共同四臂与G3/G5完整语义仍开放。
