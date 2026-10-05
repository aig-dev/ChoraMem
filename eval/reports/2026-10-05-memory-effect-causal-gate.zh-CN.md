# 五方向记忆效果优化：共同因果验证

日期：2026-10-05。状态：工程修复已验证，完整公开行为诊断已完成多轮；formation 处理能力诊断已保留完整与部分失败；最终整组审查待执行。

目标是使有来源的经历改善未来理解和回应，并在新证据出现后修正。保存文本、形成 Seed、模型再现旧回应和用户获益是不同结果；本报告不把其中一项代替另一项。实现保持 Episode、Recollection、Disposition、外生 Constitution 和现有因果账，没有增加 confidence、强度、治理状态或新的 Memory 类型。

## 五方向的工程变化与验证

|方向|实际变化|对应证据与限制|
|---|---|---|
|G1 情境召回与局部抑制|只有当前情境相关且达到门槛的 inhibition 才参与竞争，避免零相关 `0 <= 0` 错误屏蔽语义召回。|选择回归和独立审查已通过；共同 f8 迟到负反馈后实际 Select 排除原 Seed。不能据此推断总体召回优势。|
|G2 Episode/Constitution 接线|Chorai 映射有界完整 EpisodeEvidence；真实来源由现有 outbox SOURCE 冻结；实际渲染 refs 决定 Delivery；旧请求用只读 ReplayMemoryContext 恢复历史快照。|Chat/Proactive、source 复用/重试、legacy UNKNOWN、完整协议/SDK、迁移与运行中 health/endpoint 验证已保留。部署须 Core 在先、单个升级 writer；不支持混合新旧 writer。|
|G3 反馈归因与纠错|区分 REENACT 与效果；exact existing ADAPT 保留并可在原有恢复调用内补遗漏；新准确关联的 Outcome 可重新打开已消费的 Delivery 对；同一 USER 纠正来源不能在重叠窗口再次授权同 Seed。|两套 Store 的迟到结果/消费排除/不同 Session 新来源/兄弟 Target 合同通过；f1–f9 与真实纠正链已运行。外部评估记录仅证明合成回应条款，不证明真实用户执行收益。|
|G4 条件和候选组织|保留有界因果对＋Situation 双视图和精确模型所见 Basis；只修实际缺 opcode 主输出拖垮有效形成结果的问题；完整启动清单防止少报调用。|原六例54模型调用/20RPC、独立拥挤负例11调用/4RPC均完整；不扩大 K，不以成功筛选历史。有限案例不能估计自然分布候选精度。|
|G5 Worker 合同与覆盖|移除永远不能提交的新 ADAPT 教授路径，保留 existing ADAPT；有效主结果成为暂态 coverage，独立事实仍经过有界 fallback；保留报告归属/遗忘约束/当前 USER 条件；排除重复正文与完整字面分句；固定安全诊断。|真实 g1–g8 输出、合法 inline 形式、Target 局部恢复、格式/资格/预算/无变化诊断及生产 wheel/Go→Python 验证已运行。语义改写去重和模型是否采纳正确记忆仍须分别观察。|

## 最小因果链与认知科学边界

```text
Source 身份与角色快照 → 完整 Episode
  → 可修订理解 / 跨独立经历的条件倾向
  → 当前 Situation 下选择与有界渲染
  → 准确 Delivery → 实际 AgentAct → 外部 Result
  → exact Target 修订 / 情境抑制
```

经历和跨经历概括并存，对应互补学习系统提出的稳定性与快速学习权衡；保留来源、相似经历的区分和可修订版本，避免把反复回放同一来源当作独立经验。新反馈使旧理解可更新，访问失败也不能直接等同记忆不存在。这些是工程推导，依据见[goal 的认知科学说明](../../docs/superpowers/specs/2026-09-30-memory-effect-optimization-goal.md)。本实现不声称复制脑区、神经强度、睡眠周期或生物遗忘。

## 冻结条件和实验边界

既有完整共同实验使用 `deepseek-v4-pro`、既有 DeepSeek endpoint；远端 server build/权重版本不可取得。默认配置 temperature0、thinking disabled、retries0、每次 completion attempts1；provider timeout35s、Worker RPC120s。新 formation 诊断的显式处理模式差异另列，不追溯修改旧 manifest。Worker 单次最多4次模型调用；在线 Select 零生成调用。

Episode 完整证据8192 UTF-8 bytes；最终记忆768 cl100k tokens，foundation≤544、tendency≤192、join32。base/learned/Oracle 复制同一 foundation，倾向不得挤走基础证据；过大的完整条目可以被省略，实际 selected/rendered refs 分别记录。答案1024、judge512、Worker8192 tokens。四臂同一物理 Situation 和 Session、不同 run，先冻结 Select 和全部实际回答再反馈；不能被当作四次独立成功经历。Oracle/anti 为 eval-only，没有虚构 canonical ref 或反馈授权。

所有 canonical 变更经公开 SDK；SQL 只读诊断。隔离数据库由任务建立/迁移/删除，模型、索引、Core、RPC 的 starts/completed/failed、请求正文、返回值、来源哈希和快照保留。原工作区未改动，分支未 push/merge。

## 保留的共同原始实验

原数据 SHA256 `5ce8f4bda92fb5ff87c120c1e9953e44594636c4dad201a7a543ec17ff444d5b`：8行为轨迹、4负对照、9反馈场景、8来源语义场景，共29例。

|运行|实际完成|结果|
|---|---|---|
|31fce16 / run-cee6fe1b5838，答案512|r1–r3完成；192 starts、190 completed，r4 length 和停机取消各1|不完整，保留失败和 manifest，不能与1024结果合并。|
|2297a2a / run-033047b04266，答案1024|29/29；749模型调用、172Worker RPC，全部完成，零失败/重试|g6第三方报告、g8遗忘约束、f8迟到反馈遗漏；r1/r7/r8旧 Seed 再形成。|
|815a20f / run-d7ec2fae7f45，同一1024条件|29/29；736模型调用、173Worker RPC，全部完成；181来源/数据/二进制哈希一致|原17项语义检查全部满足；新发现 g3重复分句、r6重复纠正授权、r7丢失适用条件，单独登记，不能被17/17遮蔽。|

原判别门：Oracle 对 base 和 anti 都须位置一致赢≥6/8、输0/8，ties/inconsistent 不算 win。2297a2a 的 base 比较0赢/0输/8不一致，anti1赢/1输/6不一致；815a20f 的 base0赢/1输/7不一致，anti1赢/1输/1平/5不一致。两次门均失败。富 foundation 已能再现部分行为，旧单 token judge 不返回平局且高度位置不一致，不能把 learned/base 差异解释成 Core 架构瓶颈。数据、门槛、分母、原始评分均不回写。

## 已观察的语义与实际回应

g1–g5/g7 的事实、修订、日期、撤回与去重在原链逐项核验；修复后 g6 同时保留 USER 居住地和“小林报告的居住地”，g8 保存未来不再使用门禁码的约束且不重复值。约束不是物理抹除，也不是未来遵从保证。

f1–f4 的话题切换、事实确认、礼貌和无 Outcome 不被当作效果；已有实际行为可 REENACT。f5 保存总体目标条件下“一步”的更准确含义；f6 保留明确请求全景时的例外；f7 exact existing Target 接受单一当前 USER 长期要求，未造新 ADAPT。f8 后来到达的非 Agent Outcome 精确进入原 Delivery/Episode inhibition Basis，后续 Select 排除 Seed；该次回答仍给出旧式一步建议，属于还存在的行为结果。f9 缺失/错链不取得反馈资格。

815a20f 纠正后实际三次同条件回应：r1/r3/r4/r5/r8为3/3，r2为0/3，r6为2/3，r7为1/3。r2、r6 正确 Seed 已选择并渲染仍出现旧建议/解释顺序，不能归因到索引滞后；r7还存在条件文本丢失。r3异条件回答编造未提供的原型细节，r8异条件只追问未完成扩展任务，均保留为答案质量限制。每例确切 Target/版本/Basis、首次正确回合与 elapsed milliseconds 绑定完整 raw record hash；不把“已经入库”写成“已改变行为”。

## 当前补充修复和独立诊断

e771c6e 的局部修复：字面完整分句 coverage；ADAPT 保留未被 USER 修改的 only/when/unless 适用条件；按该 Seed 全版本 revision Basis 的 owner＋Session＋Source 身份消费直接纠正授权，保留新精确 Outcome 和只读 active catalogue。混合已消费/新 USER Source 的 Episode 被保守排除，须独立当前 Episode 或准确新 Outcome；模型释义仍需真实输出。

声明的 r6/r7/g3 新子集 `affected-subset-a26318c90055` 完成3/3、126模型调用/25RPC，零失败，182来源/数据/二进制哈希一致。r6仅原 Seed@1/@2，没有重复授权@3；r7@2保留 only-sharing 条件，明确小册子任务实际给出步骤；g3最终无第二条重复地点记忆。三项具体修复在该链通过。同条件实际回应 r6为0/3，r7保守记0/3，均正确渲染@2；g3初始更新未保存菜园，下一当前查询后才补足，不能声称立即完整抽取。该子集不计算完整8例效果门，不与原29例合并。

独立 `memory-effect-diagnostic-v1` 数据 SHA256 `e2fdfc74b386bdcd3ef2e9c1c5e952d4dd318fb3884e5361bc719e04e4a5a00e`：8新未见条件＋4负对照＋8 known-answer calibration pairs。正例保留3跨任务成功经历与3较近、同话题但条件已解决的经历，K/角色/回答 prompt/模型/预算均不变。评分检查每个真实回应的意义、第一实质介入、是否等用户再建议；要求两位置逐条 flags 完全互换，不一致不多数投票。16校准记录全部准确后才运行容量轨迹；旧 judge 同时测作诊断，不决定接受。新 Oracle 仍须两比较各≥6/8赢、0输。这个独立判别只可能完成共同诊断要求，不能使原失败门变为通过，也不估计自然收益。

校准/评分的两次实际格式失败均保留：`diagnostic-3d80b923d6f5` 32/32模型调用、0Core操作/0RPC，严格 plain-JSON仅接受12/16，其余四项返回完整代码块，块内向量与冻结答案相符；原正式判失败，不回写。窄 envelope RED/GREEN后，`diagnostic-c135e3245bd7` 的新校准16/16、legacy11/16，但首例判断返回 `JSON` 标签＋代码块，实验中止；74模型 starts/73completed/1停机取消、9RPC starts/8completed/1停机取消，全部原始四臂/anti已保存但无完整案例/效果门。两次182来源/数据/二进制哈希一致，数据库和进程清理完成。

`075fd64` 对新 clause grading 启用[供应商结构化 JSON 模式](https://api-docs.deepseek.com/guides/json_mode/)，其开关进入实际请求哈希、starts、provider usage 和 manifest。真实答案/Worker/旧单 token judge 使用既有 plain请求；判定 prompt、条款、标签、数据、门槛、模型和预算不变。结构化模式也可能空返回或含义错误，仍由新全16项校准、stop/schema/boolean和完整清单拒绝，不做重试或继续扩张包装 salvage。原部分实验不能合并到新完整 cohort。

## 完整短材料诊断及新冻结修复

`075fd64 / diagnostic-221a97525f9f` 已完成12/12例、509/509模型调用、92/92RPC，零失败/重试；182来源、数据、二进制哈希一致，任务数据库已删除且进程已停止。结构化校准16/16，legacy11/16；四项负对照均无Disposition。严格Oracle/base为5赢、0输、2平、1逐条位置不一致；Oracle/anti为8赢、0输。冻结门仍 **FAILED**。learned/base为3赢、0输、4平、1不一致，不能从该失败门推断Core架构瓶颈。相同learned回答在不同对手比较中可能得到不同条款flags，因此评分只报告协议下的配对结果，不写成绝对语义准确率。

12份exact body/Basis执行者语义检查10通过、2未通过。d4形成正文只要求回忆例外后制定安排，丢失具体提问和等USER补充信息的依赖；实际learned回答在问后立即给习惯方案。d6形成正文丢失“仅分享、不求建议”条件，扩大到所有同主题变化。它们是实际语义RED；`cbed77f`只补通用形成规则：保留已验证的意图限制、具体第一介入与信息依赖，没有新字段、权威或调用。声明的原d4/d6子集会单独验证实际新正文/Basis和回应，不用mock理想输出证明修复。

`cbed77f / formation-subset-c4bec79ced6e` 完成2/2、136/136模型调用、18/18RPC，校准16/16，零失败/重试，182来源/数据/二进制哈希一致且数据库/进程清理完成。d4和d6的必需形成语义仍未通过；两个实际prompt包含新增规则，排除部署遗漏。原第一次修复保留为失败。随后只澄清概括合同：当前USER意图是条件的一部分，需明确等待其答案再执行依赖动作；移除“第一步不足以表达回应”的歧义，并用未出现在评测中的出发时间/路线例子说明信息依赖。无数据/评分/阈值/权威/调用变化，工程回归后重新冻结这两例；不是重跑未改代码直到通过。

`a48086d / formation-subset-c0bbabda3bf2` 完成2/2、132/132模型调用、17/17RPC，校准16/16，零失败/重试；182来源/数据/二进制哈希一致且清理完成。d4仍省掉显式等待并提前给习惯方案。d6正文已从“提到变化”缩为“分享变化”，实际回应先中性事实、提供继续/安静选择并停止；但正文仍未明确只分享/未求建议，必需范围保守记未通过。保留这一失败，随后检验更具体的因果压缩假设：形成输入明确区分USER情境、先前AgentAct和后来non-Agent Outcome，不能把结果中出现的回答或后续规划并入先前回应。只用无关的项目汇报/出发时间中文例子澄清原意图和等待边界，没有额外证据/调用或新的资格。

`46d94db / formation-subset-93e1d8c766fc` 完成2/2、135/135模型调用、18/18RPC，校准16/16、零失败/重试且182来源/数据/二进制一致，清理完成。d6正文这次显式保留未请求建议，但加入失去感并省掉不代述情绪的边界；实际回应先解释情绪。d4仍省略等待，实际提前给习惯行动。两项完整目标未通过，不能把部分条件修复记为完整GREEN。随后只移动既有focused合同到引用证据之后，验证提示顺序/概括假设；内容、来源/Basis、调用、模型、预算、数据、评分和阈值均不变，工程回归后重新冻结，旧结果不改。

`4670f6b / formation-subset-c5f72f8e42ef` 完成2/2、122/122模型调用、18/18RPC、校准16/16；零失败/重试，182来源/数据/二进制哈希一致且清理完成。两项正例都没有形成Disposition，提示顺序实验造成回归；改动已撤回，不能用0候选隐藏正文失败。接下来的最小操作变化是在同一第二次调用中校对第一轮暂态句子。只展示精确同组/application的单个有界句子，无refs，绝不新增Basis/资格；有缺opcode的主结果仍被丢弃，只可作为不可信文本重新对照原证据。三种合法/缺opcode展示路径实际RED→GREEN、错误组/重复或多余依据/ADAPT/歧义/多行/4KiB/refs泄漏控制，以及可选句子不能挤走完整证据的预算检查，覆盖结构路由而非真实语义；原d4/d6实际模型验证仍必需。

`54232d7 / formation-subset-9582883a2d14` 完成2/2、140/140模型调用、19/19RPC，校准16/16、零失败/重试且182来源/数据/二进制一致，清理完成。两项完整目标仍未通过。实际最初d4主输出已写出等待用户回答，但Basis只列精确两份Episode；完整四ref暂态匹配要求阻止该句进入focused调用，focused重新概括时丢失等待。后续正确句子出现时已有弱Seed，不能用新Seed竞争替代。此为具体路由损失：可选文本匹配应要求精确完整Episode组，所列Outcome可为该组的子集；引用文本不成为效果证据，完整typed pairs仍全部展示/绑定。两个Episode-only/partial-Outcome路由RED→GREEN；外组、缺Episode、额外/重复refs、ADAPT等仍拒绝。真实语义修复继续独立原两例验证，旧140-call结果不回写。

d1/d8基础臂已从544-token内的两个完整短成功Episode再现目标行为；六份短历史不能保证容量差异。新的 `memory-effect-capacity-v2.json` 独立冻结8个新来源/问题轨迹和4负类型，复用diagnostic-v1协议、原校准/评分/门槛/角色/答案prompt/模型/K/预算。数据SHA256 `b4d4c9230a0da4980f0a09ed6fab82ad12191dddf401f6a8eae9aa06d173432f`；48份完整正例历史实测956–1003cl100k tokens、2479–2554UTF8bytes。固定具体现场材料带来完整条目预算压力，实际selected/rendered遗漏须逐项报告；这是受控合成容量诊断，不是自然分布或总体收益估计。外部调用前数据回归已通过，32校准调用先于任何入账，随后完整12例验证；旧509-call失败保持不变。

## 当前工程验证

e771c6e：full Core `go test ./...` 和 `go vet ./...` 通过；迁移后的 PostgreSQL Store22.517s/runtime RPC0.367s、MySQL Store46.049s/runtime RPC0.560s 通过。生产 Worker183通过/11可选跳过，codegen/wheel/license/Go→Python0.383s通过；完整 eval/Worker/SDK765通过/13可选跳过。没有 schema 变化。最终 reviewer/单轮修复后需要重跑的门尚未宣称通过。

075fd64 eval-only 结构化请求修复：2 adapter/capture RED→GREEN＋2 manifest/端到端清单 RED→GREEN；完整 Python776通过/13可选跳过。False默认请求哈希保持旧算法，原调用清单仍可验证。此时生产 Worker/Store代码自e771c6e未变。

当前 `8570b43` 工程门：完整Python793通过/13可选跳过2.19s；fresh生产Worker199通过/11可选跳过1.46s、codegen/clean wheel/license与Go→Python0.498s通过。最新未缓存full Core Go与vet通过；真实迁移后PostgreSQL20.613s Store＋0.360s RPC、MySQL44.943s Store＋0.532s RPC通过。Go/两Store/公开协议自e771c6e无新行为改动，后续只改Python形成加工；上述工程证据不替代尚在执行的语义和Oracle验收。

审查、最终验收、完整 Rulings/代价和 artifact 保存位置在共同[执行计划](../../docs/superpowers/plans/2026-10-05-memory-effect-causal-gate.md)及最终 evidence 文档中结算。goal 尚未完成。

第六次原始d4/d6声明回放 `formation-subset-f577703739e9`（8570b43）完成2/2，131模型调用、17Worker RPC，零失败/重试；新校准16/16、旧校准11/16，182源码/数据/二进制哈希吻合，隔离数据库已删除。d4正文明确等USER回答，实际learned只问自然发生的例外并停下，与Oracle平局、胜base。正文的`> `引号前缀保留为格式瑕疵。d6仍缺少两个Outcome共同赞赏的“不替用户解释感受”，初始primary就缺少，focused照抄；actual先情绪解释。learned/base虽赢，learned/Oracle两位置均失败，不能据此覆盖缺失边界。完整八案例Oracle门槛此子集未评估；两份哈希绑定的执行者语义检查与全部原始调用保留。

## 纠正延迟的完整分母

时间从明确USER长期纠正入账开始；正文提交延迟与实际首次遵从分开记。以下是原完整29例815a20f回放，不与后来子集合并。`未达到`表示冻结的三个后续回合内没有正确第一介入。

|案例|首窗口正确正文|提交秒|同条件逐回合|首次正确回应回合/秒|观察结束秒|
|---|---|---:|---|---|---:|
|r1|是|7.547|通过/通过/通过|1 / 14.465|29.674|
|r2|是|7.884|失败/失败/失败|未达到|36.130|
|r3|是|8.537|通过/通过/通过|1 / 19.860|46.441|
|r4|是|8.539|通过/通过/通过|1 / 18.406|40.370|
|r5|是|10.411|通过/通过/通过|1 / 20.583|41.606|
|r6|是|8.538|失败/通过/通过|2 / 26.332|36.536|
|r7|否|7.465|失败/失败/通过|3 / 34.688|34.688|
|r8|是|7.491|通过/通过/通过|1 / 17.427|37.625|

新增修复子集e771c6e的r6/r7：两项首窗口正文均正确，r6无重复授权@3，r7保留只分享条件且异条件小册子回答完成步骤；实际同条件三回合均保守失败。不能以正文提交替代行为遵从，也不能用旧回放的成功回合覆盖新子集失败。

|新子集案例|提交秒|同条件逐回合|首次正确回应|观察结束秒|
|---|---:|---|---|---:|
|r6|8.753|失败/失败/失败|未达到|36.132|
|r7|9.307|失败/失败/失败|未达到|36.473|

所有表项绑定原record SHA256、确切Target/version/Basis和实际模型输出；不同条件的完整回答亦保留。r3异条件编造细节、r8异条件只提问的质量缺口没有抹除。


第七次声明回放 `62ea50c / formation-subset-2d2f11a232fa` 完成132模型调用、17RPC、校准16/16，零失败/重试，182来源/数据/二进制吻合且已清理。d4等待正文及实际提问后停止通过；d6仍丢失共同Outcome赞赏的避免动作，实际先代述情绪，完整修复未通过。该规则实际存在于捕获请求中，不能用“提示已补齐”替代语义效果。停止继续堆叠提示，先完成独立冻结的12例长材料诊断以定位剩余瓶颈；只是执行顺序调整，d6缺口、原所有失败门和全goal完成条件均不豁免。

## Goal逐项验收映射（共同门仍进行中）

|要求|实际证据|边界或剩余项|
|---|---|---|
|G1：语义改写/跨语言不被零相关反例屏蔽|`selection_test.go:TestSelectIgnoresUnrelatedInhibitionForSemanticDisposition` 的zero/subminimum/zero-minimum/cross-language；原RED→GREEN和完整Core日志|未扩大语义权重或承诺总体召回率|
|G1：相关反例仍抑制，支持须严格胜出|同测试at-minimum对照、`TestSelectRequiresSupportToBeatThresholdAndInhibitionStrictly`；实际f8后续Select排除原Seed|f8实际模型仍可从基础材料/默认行为给旧建议|
|G1：局部例外不全局删除，资格/owner/预算/排序保持|完整selection/Core及两Store合同；f6异条件例外、exact Seed版本/状态记录|只证明已覆盖合同，不是所有自然情境|
|G2：默认有界完整证据、非法/重复refs拒绝|Core `episode_evidence_budget_skips_oversize_and_keeps_later_complete_entries`、exact-owner及snapshot合同；Chorai assembly malformed/byte-limit/blank-duplicate tests|整条过大可省略，实际省略必须报告|
|G2：模型不见refs，seal检测文本/ref替换|`TestBuildRunContextDeliversCompleteEpisodeEvidenceWithoutRefsInText`、`TestValidateRunContextSealRejectsSubstitutedDeliveredRef`及private seal tests|Harness拥有最终prompt，不能凭selected refs推断已投递|
|G2：Episode-only Delivery不授予Seed反馈资格|Core Evidence/Delivery合同、Chorai Chat/Proactive生产ModelRunner smoke、actual记录资格|来源可信与效果含义分开|
|G2：同物理来源/Constitution在角色变化、重试/replay/UNKNOWN后不变|SOURCE租约/并发/物理身份/UNKNOWN合同，legacy replay exact scope/run/source/budget；G2完整DB/SDK/迁移/health200/non404日志|Core先升级，单升级writer；不宣称混合版本兼容|
|G2：Chat和Proactive真实接线|`TestAgentChatSourceSnapshotAndEvidenceReachActualModelRequest`、`TestPersistedProactiveSourceSnapshotEvidenceAndRetry`、legacy/occurrence/replay tests；实际gRPC/outbox/model边界|受控生产接线测试；远端provider调用来自独立Core因果实验|
|G3：换话题/确认/礼貌/缺失不等于效果|完整29例f1–f4，原文/actual reenactment/无新独立成功Seed检查；Worker feedback资格测试|REENACT只记行为已再现|
|G3：本人长期纠正只改exact existing Target|f7及r1–r8的USER current Episode、origin/version/revision Basis；existing ADAPT恢复；new ADAPT禁止|正文正确不保证下一次回答遵从|
|G3：同链负反馈局部作用，异条件保留|f5/f6实际版本与局部抑制，Scope限定ADAPT；两Store exact因果对合同|模型对反馈含义仍会失误|
|G3：迟到只按可信显式关联，错/无关联不猜|f8实际old run/receipt/Situation/AgentAct Outcome链、两Store `late_outcome_after_reenactment_TEXT/INHIBIT`；f9负控|通用Core依赖caller提供可信完整身份，不能从文本辨认物理同源|
|G3：不引入每轮追问或自评Outcome|无新产品流程；真实模型答案先冻结再由外部评估记录Outcome，来源标为synthetic-external|合成外部条款评价不等于真实用户执行收益|
|G3：同一纠正不能再次授权同Seed|两Store `direct_authority_is_consumed_per_seed_*` 三个RED→GREEN，r6子集始终原@1/@2；新Outcome/不同Session/兄弟Target保持|历史Basis查询开销；混合已消费/新USER源Episode保守排除|
|G4：同条件跨任务成组、同话题异条件不混|c1/c6实际source/candidate/job/Body/Basis；c2负例；d1–d8六完整历史不筛成功，长材料12例已完成两轮，分别保留结果|保持原K，不把目标数量当行为收益|
|G4：失败/冲突不能绕开，异条件例外不全局否定|c3/c7实际失败原文进入组、0Seed；`formation_candidate_group_preserves_failure_and_filters_unqualified_pairs`|一例拥挤诊断不能覆盖所有候选饱和情况|
|G4：索引失败/坏refs/owner/session/Outcome回退拒绝|两Store共享formation候选合同四组；多视图失败保持原fallback|没有扩大K或引入confidence|
|G4：Basis精确模型所见，非任意active Seed|`formation_candidate_basis_is_exact_visible_group`、origin RPC哈希/完整typed pairs/committed formation Basis；optional primary不供依据|actual d6遗漏避免动作仍是未解决正文语义项|
|G5：不教不可提交的新ADAPT，existing保留|主prompt/Worker真实资格匹配，inline ADAPT/TEXT及target-local recovery RED→GREEN；f7/r1–r8实际existing修订|其他坏格式仍由Core整批拒绝|
|G5：KEEP/revision不吞独立事实且去重|完整g1–g3/g7实际来源/结果；coverage/residual/invalid-primary tests，完整字面分句三个RED→GREEN；g3子集final无重复地点|只做完整字面分句，语义改写可能重复；g3独立菜园事实到下一查询才补齐|
|G5：时间/临时/撤回/归属来源忠实|实际g4/g5/g6分别保留日期/撤回/USER与具名第三方报告；原17语义全审＋后来缺陷另列|报告不升级为verified truth；自然复杂引用仍未知|
|G5：无变化/材料/格式/预算/资格/stale可诊断且安全|safe diagnostic/overbudget/aggregate/retry tests与两Store运行日志；已有Job/日志类别无Source全文或凭据|语义错误可以shape-valid，日志分类不能证明意义正确|
|G5：单RPC无DB权、四次调用/窗口资格|完整生产wheel/Go-Python桥接、793 Python回归；actual每RPC调用上限/starts多重集/typed资格由Core复验|远端负载、并发、自然成本未测|
|共同：四臂完整链、同物理消息、固定角色/预算、全位置负控|两完整29例、完整短诊断12例、所有未完成运行单列；独立冻结长材料12例已完成两轮；actual hashes、Delivery、AgentAct、外部结果|原失败门保持FAILED；d72独立门通过，993最新门失败，不合并分母|
|共同：纠正正文与行为延迟分开|原8例全部三回合/异条件及两例修复子集表、raw record SHA绑定|r2/r6/r7仍有实际遵从缺口，不以提交成功遮蔽|

以上映射说明证据覆盖，不宣布全goal完成。最终只在独立Oracle可辨识、全部必需修复及最终整组审查闭环后更新状态。

长材料第一次 `d68434b / capacity-v2-d6ccc4b769f8` 已中止：7/12完整record、443/443模型调用、76/76RPC，新校准16/16/legacy11/16；第8例实际JSON模式评分返回裸数组及`responseb:`，严格解析正确拒绝。无provider-call失败，另有1次评分格式失败；d8四臂和anti实际原文已冻结，4项负控未执行，完整Oracle门不评估，182源码/数据/二进制吻合且清理完成。d3实际合格candidate/anchor和完整paired结果到达，focused仍NO_CHANGE；d6Oracle只复述事实而无选择题，却被某些评分误判全true并出现位置/对手漂移。这些缺口不因校准16/16被抹除。按[官方JSON Output指南](https://api-docs.deepseek.com/guides/json_mode/)补充缺失的格式示例，仅由checks数量生成、两位置相同、不含评分标签；不猜旧flags或改数据/门槛。新请求哈希及源码冻结，必须重过全部校准才开始新的完整12例。

格式示例版 `84e55f2 / capacity-v2-0c897fc9be62` 完成32/32模型请求、0案例/0Core操作/0RPC，校准15/16而正式停止，legacy11/16；182来源/数据/二进制吻合且清理完成。k8反位置把含禁止原值的回答判成没有原值，旧结果保留为FAILED。澄清判据与引用数据边界、每条检查所指的具体回答正文；全部合取条件都须由该回答满足，不能借当前用户请求补足缺失行为。固定条款/标签/数据/门槛/解析器/模型/预算不变，格式示例不改；真实错误为语义RED，新全校准和完整实验仍必需。

引用对象澄清版 `93cfad5 / capacity-v2-76f4658ead24` 仍为32请求、0案例/0Core/0RPC，校准15/16/legacy12/16，k8反位置再次错误；182来源/数据/二进制吻合且清理完成。下一最小变化是工程化字面检查：只有完整匹配`正文不能出现编号原值<literal>。`的判据由精确、大小写敏感的字符串缺席检查计算，其他语义条款仍由模型判断。数据/判据/期待向量/门槛不改；模型不再被问这项字面检查，因此没有事后覆盖其被问过的flags。实际减少后的请求、模型原文、完整组合向量、具体回答哈希和方法描述都保存；软件结果不能把模型的其他失败改成通过。新16项校准须称为模型＋软件组合判定校准，不能写成模型语义16/16准确。旧两次15/16均保留失败；不推论语义改写、视觉相似字符或自然收益。

`d72ac91` 组合评分修复：完整 eval/Worker/SDK 回归805通过、13可选跳过，1.94s；`git diff --check`通过。Worker/Go/协议/数据库实现没有改变，前述最新生产及两Store门仍对应当前实现。新运行 `capacity-v2-39533d393c62` 的16项软件＋模型组合校准全部匹配，legacy12/16，随后进入完整12例回放；校准通过不等于完整Oracle或形成语义通过。Manifest SHA256 `3aeda3e3da6438fdf30e3caf7fa30a6ce36882ab0c6dc607a098e299702eb1b0`，源commit `d72ac910d8844814d81bfb404501b6593bb0306c`。

完整 `d72ac91 / capacity-v2-39533d393c62` 已完成12/12、497/497模型、92/92RPC，无失败或重试。182来源/数据/二进制吻合，数据库已删除、运行时停止；12份记录哈希绑定的执行者语义检查、实际回答和全部评分请求验证通过。Oracle/base为6胜0负1平1位置不一致，Oracle/anti为6胜0负2不一致，冻结门通过。各自有效符号检验概率0.015625，6/8胜率Wilson95%区间[0.4093,0.9285]；二者复用Oracle回答，不能当作独立实验。learned/base为5胜1负2平，单侧符号概率0.109375，不能宣称统计稳定的总体提升。

语义检查8/12通过，负对照3/4通过。d1主处理已提出正确询问/等待，但focused返回NO_CHANGE、没有形成；d6遗漏共同结果赞赏的避免情绪代述，实际只说“你讲，我听着”；d7正确但抽象的正文已选择/渲染，实际仍先评估并给测试；dn3把纯数值准确确认误形成长期倾向。后者实际focused `model:3adb4a9240a4452f9f3b822d9f660c01`，规范Target `seed_fd3206211eb6f5cd178376363e409d6b@1`，primary本为NO_CHANGE。d6 Oracle自行编造木地板/窗边阳光细节且没有选择提问，却得到部分全true评分；d5/d8同一回答随对手发生flags变化。全部原始评分保留，不以人工检查覆盖模型结果；Oracle达门不等于评分绝对正确。

下一最小修复是收敛中文形成合同，去掉只针对人物事实的范围枚举，明确“内容正确”不能代替“该回应方式有效”；保留实际具体提问、等回答及被共同赞赏的避免动作。实际失败提供语义RED，引用/角色/Basis/调用边界回归只证明工程完整性；新的完整正负回放和生产门仍必需。对这份已检查数据的修复回放属于开发诊断，不再称作全新盲测或自然分布收益。

最新中文形成清单回放 `993290b / capacity-v2-767d49f62ae0` 完成12/12、516/516模型调用、94/94Worker RPC，零失败/重试/取消；组合校准16/16、legacy12/16。Manifest `eca7631e5c2a22250ec6f0c62e0a2785bd6834d53151d3c87ceaab9a9e2bd18c`，182源码/数据/二进制一致，隔离数据库及进程清理完成；全部实际请求、回答、评分和12份记录哈希绑定检查验证。工程回归805通过/13可选跳过2.04s；fresh生产Worker199通过/11可选跳过1.12s，wheel/codegen/license及Go→Python0.565s通过。Go/两Store/协议没有修改。

语义9/12，4/4负对照都没有形成Seed，修复了本轮dn3事实准确性误形成。d1正文形成且渲染正确，但实际仍先给排查步骤；d6遗漏共同赞赏的避免动作，实际只说“嗯，你讲，我听着。”；d7已渲染仍先指定验证测试。d2实际产生两个同义倾向，已有正文确实到达第二次focused；“有支持的条件正文”通过并不证明去重。learned/base为5胜1负1平1不一致。Oracle/base6胜0负1平1不一致，Oracle/anti5胜0负1平2不一致，后者单侧符号概率0.03125、Wilson95%区间[0.3057,0.8632]，所以本轮Oracle门失败。上一轮d72的通过仍独立有效，不能合并或代表当前门通过。

下一修复只改两个已有边界：有暂态草稿时将focused明确为依据全部经历校订、先检查已有覆盖的任务；共同Harness明确倾向对回应方法和信息依赖的作用，并禁止为准确协作角色虚构未提供的个人经历。所有答题臂及反控制使用相同合同，共同调用者/校验器同步；旧共享回答器与PersonaMem/延迟协议不变。失败原文提供语义RED，完整源码冻结后的正负实际回放才提供语义结果。该数据已经检查，结果属于开发诊断，不是新的盲测或自然收益。

最新 `861c3cd / capacity-v2-65cf5701dba4` 完成12/12、499/499模型调用、90/90RPC，零失败/重试/取消；组合校准16/16、legacy11/16。Manifest文件SHA256 `1b55490dfb2a4c4ffbb05e9bc5d6eec8ee21a895b6293d2c8b6c0d9f8ab9c584`；182源码/数据/二进制一致，隔离数据库与运行时清理完成；全部12份执行者检查和实际调用绑定验证通过。语义10/12、负对照4/4。d1本轮先问排除项并等待；d2本轮只形成一个倾向，不能据此保证一般同义去重。d6已补避免情绪分析/方案，但遗漏中性事实先行；实际问从哪讲而非继续/安静选择。d7仍先评价并给验证标准。

Oracle/base7胜0负1平，Oracle/anti7胜0负1不一致，冻结识别门通过；各自符号概率0.0078125、Wilson95%区间[0.5291,0.9776]，共享Oracle回答。learned/base按原模型flags为7胜0负1不一致，但执行者检查明确保留d7实际失败；d6 Oracle依然虚构曾提及的记忆且无指定选择。工程门806通过/13可选跳过，生产Worker199通过/11可选跳过与桥接已通过。效果检查不改写评分，不宣称自然收益或全goal完成。

下一步停止继续加形成规则，先复用现有provider思考模式，针对12份实际完整focused输入做单独的模型能力诊断，全部正负输入/源码/配置在调用前冻结。它不写Core，不能充作规范提交或行为通过；如果文字能力改善，仍须新的完整公开服务回放。8192总输出/35s超时及无重试保持，成本和截断风险如实记录。[官方思考模式说明](https://api-docs.deepseek.com/guides/thinking_mode/)支持已存在的开关与high参数；这是处理模式实验，不把模型推理内容写入Memory。

思考/high能力检查 `formation-thinking-2da92dd43058` 已失败：12份输入/187源码先冻结，首请求35s超时，1启动/0完成/1失败，剩11未尝试；零Core操作与提交，所有源码吻合。没有语义或负例结论，未返回请求的实际供应商token成本未知。下一声明实验只改为现有参考client的110s超时；同12输入/high/8192总输出/无重试。旧35s失败保留，不改变生产配置或直接算作效用证明。

Thinking/high capability formation-thinking-8a4e5b64a811 completes12/12 actual provider stops,0Core operations/commits,187sources match. d6 preserves fact-first/continue-or-quiet/wait/no-emotion-or-plan; d7 states the concrete observable-result question. However dn2 returns : NO_CHANGE。NO_CHANGE, and the actual binder emits NEW_DISPOSITION TEXT for that control-only garbage: overall FAILED, no negative precision pass. Latency6.65/37.07/64.18s min/median/max;27319prompt+30077completion tokens,29753reasoning. Existing README already distinguishes validated thinking/high adaptive semantics from unvalidated non-thinking; TextModel interchangeability never guaranteed equal ability. This processing-capability evidence is not canonical or behavior GREEN.

42. Reject a focused body made only of one or repeated NO_CHANGE control markers plus punctuation/whitespace; keep the exact valid NO_CHANGE no-op and meaningful sentences that mention the token unchanged — actual12-packet high-mode dn2 control-only garbage is currently wrapped as a new qualified tendency, so a real full-service replay could canonically store it — cost: a narrow deterministic transport guard may reject ornamental control variants, intentionally without salvage or guessed TEXT; it must not reject ordinary source-supported content that mentions the programming token. The actual raw output and binder preview are RED; exact binder/service-boundary tests must fail then pass with safe format_rejected/no appended Seed. No NLP classifier, new Memory/field/call/Basis/authority; prior high diagnostic remains FAILED and immutable, full actual positive/negative public replay still required.

## 当前格式修复与处理能力的隔离证据

`1f521ff` 严格拒绝仅由 `NO_CHANGE` 标记及标点／空白组成的畸形 formation 输出。准确的 `NO_CHANGE` 仍为空 no-op，有实际含义的 token 提及仍可绑定；拒绝不猜测或修复操作。真实 dn2 输出 `: NO_CHANGE。NO_CHANGE` 复现旧绑定器会生成伪倾向，四项 binder 加一项 RPC 边界 RED→GREEN。完整 Python812通过／13可选跳过，fresh生产Worker205通过／11可选跳过、发布包／生成漂移／Go→Python0.587s通过。格式错误仍沿既有 RPC 失败路径；原高模式诊断不会被此后代码改写为通过。

`formation-thinking-2da92dd43058`：一次冻结12份实际完整focused输入，thinking/high／8192输出／35s超时；首请求超时，1start、0completion、其余11未尝试，0Core／提交。保持失败和未知远端消耗。显式改为既有reference110s上限后，`formation-thinking-8a4e5b64a811` 12/12stop、0Core／提交、187源码一致。d6完整保留事实复述、继续／安静选择、等待及避免情绪代述／计划；d7写出具体可观察结果提问。但dn2返回上述畸形控制，整体 **FAILED**。27319prompt＋30077completion tokens，其中29753reasoning；时延min／median／max为6.65／37.07／64.18s。这里只保存最终正文和usage，不将推理过程写成Memory。

`formation-answer-8d41a6fa96fd` 是两个已知失败的非canonical隔离回答：复用上述完整d6/d7正文、861原foundation／当前输入／角色，以及未变的fast回答合同和1024输出。2/2stop，0Core／选择／Delivery／提交，187源码一致。d7具体问可观察结果何时改变判断并等待；d6仍只邀请继续讲述，省掉当前具体事实和继续／安静的选择，整体 **FAILED**。因此更完整的文本修复了该d7的输入损失，但不能保证d6的回答遵从。两个观察点不构成新的总体收益或公开链路GREEN，也不改任何评分标签／Oracle／门槛。

`7ebc781` 后声明的完整12例公开formation模式验证已按下节记录中止：仅程序生成的focused formation调用使用既有thinking/high TextModel，110sprovider／8192总输出；其余primary／fact／feedback／answer／judge仍使用fast35s及原预算。混合模式在manifest显式列出，每次实际SDK请求与model attempt独立绑定；没有生产默认值、Core协议／状态／资格或调用次数变化。120sRPC／原lease限制不扩大。该运行是发展中的能力诊断，必须完整保留所有尝试、自动Job重试和失败；不能用旧passed Oracle或两次非canonical回答替代它。

## 五方向验收的证据映射

以下指向已有实际回归和公开原文；“通过”限定于该合同／声明样本，未解决的回答行为另列。全部旧raw及运行日志保留在 `.cache/memory-effect-optimizations/` 下对应子计划／common-verification，最终保存清单将绑定每个文件hash。

|要求|工程／生命周期证据|真实链路与剩余限制|
|---|---|---|
|G1语义命中不被零分抑制；低于门槛不竞争|`selection_test.go: TestSelectIgnoresUnrelatedInhibitionForSemanticDisposition` 四项RED→GREEN，含跨语言、zero minimum；原tie／strong inhibition控制通过|共同f8准确负结果后的Select排除Seed；一般语义排序质量不能由此推断|
|G1有效当前支持、局部例外与原owner／Basis／budget约束|原selection阈值、独立预算／确定排序、两Store资格合同与完整Go gates|f6不同条件例外与真实Scope检查；实际响应可能不遵从所选文本|
|G2有界完整Evidence、无refs泄漏、seal拒绝文本／ref替换|Chorai RunContext完整Episode、byte limit、malformed evidence、delivered-ref seal测试；ModelRunner ordering|真实Chat／Proactive受控模型smoke；外部provider语义另经共同实验|
|G2Evidence-only Delivery不授予Seed资格|Core真实两StoreEpisode evidence与receipt合同、Chorai生产adapter／outbox链|公共Delivery与实际AgentAct顺序保留；不能把引用经历当Seed反馈资格|
|G2来源Constitution重试／角色改变／UNKNOWN／复用不变|SOURCE first-write竞争、Outcome→下一Situation、AgentAct继承、legacy UNKNOWN、physical-source substitution guard；SQLite＋实际MySQL／race|持久Chat／Proactive完整gRPC smoke，隔离profile／Make restart health200／non404；升级须Core先、单writer|
|G2旧零预算Select恢复原冻结Context|只读ReplayMemoryContext nil／known RED→GREEN，scope／run／ref顺序／原budget／source UNKNOWN／不创建Context／不调用index|旧run升级恢复不猜Current Constitution；多版本writer与降级不受支持|
|G3换话题／准确性／礼貌／无结果不冒充效果|反馈逐Target、完整receipt／run／Source qualification、无新ADAPT authority|f1–f4／f9实际17项语义组检查；REENACT只说明已现行|
|G3 exact existing lasting USER correction、不能新生／循环授权|ADAPT保留／同恢复lane补遗漏；双Store `direct_authority_is_consumed_per_seed` 跨全部版本、Session、兄弟Target、同Episode不同run|r6修复链不再@3复用同Source；所有8例3回合实际纠正延迟完整，部分正文正确而响应仍旧|
|G3同链负结果、条件例外、准确迟到关联|双Store `late_outcome_after_reenactment` TEXT／INHIBIT RED→GREEN；已消费REENACT不重复，新unused Outcome可进入精确原pair|f5／f6／f8真实meaning／Basis／Select；没有关联的迟到文字不能由模型补身份|
|G4跨任务同条件、同话题不同条件、冲突不可忽略|原完整因果对＋Situation双视图、exact visible group；两Store候选／错kind／owner／Session／missing-or-multiple Outcome合同|6例54model／20RPC、独立拥挤11／4、完整四项负对照；K未扩大，不能估计自然precision|
|G4故障回退、Basis等于模型所见、exact origin Target|原多视图失败回退；extra／missing／duplicate／anchor-only Basis拒绝；scorer完整starts／RPC／hash RED→GREEN|共同每个canonical origin Job／当前版本／selection／render／Delivery独立核验，未完成attempt保留|
|G5不用不可提交新ADAPT、保留existingADAPT|合法inline／separate-body ADAPT／TEXT、Target-local markers RED→GREEN；new ADAPT机械剥离|f7／r1–r8 public exact existing revisions；模型判断本人长期意图仍有能力依赖|
|G5 KEEP／revision不吞独立事实、去重覆盖准确|暂态primary coverage及revision替换、无效资格不授coverage；literal／complete-clause去重RED→GREEN|g1–g3／g6多事实、独立报告；g3初窗口漏菜园后续才补，语义改写去重不保证|
|G5时间／临时范围／撤回／归属忠实|来源validator／version／prompt合同保留，公开g4–g8 exact current／retired text与Basis|原17项满足；g6保存报告归属、g8不重复忘记值，约束不是物理删除|
|G5日志分类安全、DB无权、调用与窗口资格不变|fixed lane／status／count安全日志、raw64KiB budget先拒绝、aggregate output_budget、provider静态错误、stale两Store合同；production205与bridge通过|actual started/completed/failure inventory、最多4次/RPC、在线Select0生成；高模式耗时另报，不把实验当SLA|

共同效果另外保存完整四臂、4负对照、两位置所有pair、Oracle/anti与每例3回合纠正；冻结判别通过仅说明在该合成条件下Oracle具有可辨识性。模型grader flags与执行者完整原文检查分开，不用多数投票、改分母或改预期关闭失败。

## 公开思考模式的完整失败记录

`7ebc781 / capacity-thinking-5b46c1a7908b` 是 **FAILED_INCOMPLETE**：冻结12例，只完成d1，d2部分尝试，其余10未开始；全门不评估。新校准16/16、legacy11/16。Captured model97starts／95completed／2failure；实际provider97requests全部返回，其中95stop、2length；14RPC／12complete／2fail，两个失败属于同一d2Job的Core自动重试，provider与执行者手动重试均0。两次length将8192生成tokens全部用作reasoning，最终正文为空，分别耗时109.928s／103.521s，随后settle240s超时。186源码／数据／二进制hash一致，隔离数据库和进程清理完成；manifest文件SHA256 `3397c3161cf72adb5a35086c9dd66b213a77da90bbf15fab7c83c3ba64289ae9`。

d1的完整公开倾向与实际先问／等待成功；同一轨迹还形成来源支持的“已批准材料只执行”的不同条件倾向，并非同义重复。这个前缀不能替代12例或与旧run合并。静态12包能力结果没有覆盖此时的完整partial-window输入，高模式在现有预算下 **不获生产推荐**；不会为过门扩大token／RPC／lease上限或继续调参。当前default的完整861行为诊断和全部原始失败保持不变。

本轮优化完成的依据遵循最初goal：实际工程缺陷及对应回归、生产生命周期smoke、完整可辨识的行为诊断和诚实局限。正确记忆不必然被模型采用、某些正文仍可丢条件、语义改写去重不稳定，这些都在结论中保留；不把它们改成通过，也不将“模型永远正确”变成该goal的新承诺。严格控制格式修复将用全部90份861真实RPC捕获做当前服务回归，只证明已有观察结果未被这次guard改变，不增加实际模型／自然效果证据。最终独立审查将检验此完成解释与全部边界。
