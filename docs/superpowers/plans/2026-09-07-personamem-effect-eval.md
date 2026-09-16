# PersonaMem-v2 Effect Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 OmniMemEval 可比较横评与六组效果诊断，并用完整漏斗选出唯一最大瓶颈。

**Architecture:** 公平横评通过上游 `add/search` Adapter，只使用公开 Core RPC；六组诊断保留在本仓库 eval 包，通过独立 gold 对齐和 MemoryIndex 只读查询测量上界。两条路径不修改 Core 权威状态机，也不共享会泄漏标签的运行入口。

**Tech Stack:** Python 3.11+、gRPC/Protobuf、现有 Memory Core SDK、OmniMemEval PersonaMem-v2 pipeline、pytest。

**Spec:** `docs/superpowers/specs/2026-09-07-personamem-effect-eval-design.md`

## Global Constraints

- 不修改 Chorai 主线、Memory Core 公共协议、数据库 schema 或 Seed 资格。
- Oracle 标签只在诊断进程读取，不能进入 Core、Worker、索引或普通回答路径。
- 横评严格沿用固定 OmniMemEval 的数据、prompt、答案模型与计分；不同 harness 成绩不得混排。
- 先测后改；完整 5,000 题和 validation 真实模型运行缺少凭证时保持目标未完成。

### Task 1: OmniMemEval 薄 Adapter

**Files:** `eval/omnimemeval/` 下 Adapter、安装器、环境模板、测试与中文说明；`eval/python/pyproject.toml`、`scripts/verify-eval.sh`。

**Interfaces:** `MemoryCoreClient.add(messages, user_id, **kwargs)` 与 `search(query, user_id, top_k)`；安装器只在已验证的上游结构中注册 `memory_core` 与 generic text search。

- [x] RED：在临时最小 Omni checkout 中证明未注册 Adapter、assistant-first／未知角色、跨批 Episode 丢失、query 被错误绑定或 gold 泄漏时测试失败。
- [x] GREEN：实现同步 gRPC 映射、跨 `add()` 对话缓冲、稳定 scope/idempotency、无绑定 query、纯文本 Select；安装器幂等且遇未知上游结构 fail closed。
- [x] VERIFY：运行 Adapter 单测，并对固定 OmniMemEval 源码执行安装及 ingestion/search smoke；记录上游 commit。

### Task 2: 六组 Context 与漏斗

**Files:** `eval/python/src/memory_core_eval/personamem_effect.py`、`personamem_diagnostics.py`、对应测试及必要的 MemoryIndex eval bindings。

**Interfaces:** `EffectMode` 六个固定值；`build_effect_context(...)` 返回正文、refs 与阶段证据；`summarize_funnel(rows)` 只从逐题记录计算。

- [x] RED：六组互斥、Oracle 不被普通 loader 暴露、精确/未匹配/歧义对齐、Top-40 顺序、Core selected/delivered 区别与漏斗条件指标均在旧代码失败。
- [x] GREEN：实现最小纯函数和只读 provider port；复用生产 renderer，不复制 Core 排序或 token 算法。
- [x] VERIFY：运行定向 pytest；对既有 100 题产物只读回算应复现已知 `70 -> 38 -> 34` 漏斗。

### Task 3: 可恢复 Runner 与真实实验

**Files:** `eval/python/src/memory_core_eval/personamem_effect_runner.py`、runner tests、`Makefile`、`eval/personamem.zh-CN.md`、新的报告产物。

**Interfaces:** `prepare/run/summarize`；manifest 冻结数据、六组、模型、prompt、Core/Worker/index revisions；逐题原子 checkpoint，测试阶段不得改变 learned memory。

- [x] RED：缺组、重复题、变更 manifest、测试写回、缺 funnel 字段、Oracle 进入非 Oracle Context 时拒绝汇总。
- [x] GREEN：实现可恢复 validation 六组运行与分 persona 聚类汇总；相同模型请求可复用结果，不能把随机波动算成机制差异。
- [x] VERIFY（无 key）：全套 eval gate、固定 Omni checkout、完整数据准备与 23,143 Episode ingestion 结构 smoke 通过。
- [ ] VERIFY（原始全量门槛）：完整成功的 5,000 题 Omni 横评和 2,061 题 validation 六组尚未满足；按下述用户最新口径结束补跑，不继续等待九题。
- [x] VERIFY（已接受范围）：Omni 完成 5,000 次尝试，4,999 成功、1 provider failure；validation 固定使用完整六组的 2,052 题，另披露精确九题免跑。
- [x] DECIDE：按 spec 的冻结规则选择 consolidation 为唯一优先修复阶段；它是候选中的最大显著点估计，不声称统计上唯一大于 retrieval。见 2026-09-11 基线报告。

## 2026-09-11 续执行口径

用户明确同意最后 9 题不再补跑。validation 后续分析使用已完成六组的 2,052 题；
原始 2,061 题 manifest、逐题结果和严格全量校验均保持不变。公开报告同时披露
覆盖率与精确排除列表，不将缺题当作成功或错误答案。Omni 保留 4,999 条有效回复
和 1 条失败记录，不声称 5,000 条全部成功。

### Task 4: 固定已接受样本并发布可复核基线

**Files:** 新增一个离线汇总脚本、该脚本的定向测试、中文结果报告；不改原 runner。

- [x] 校验原 manifest、逐题身份、六组完整性和精确缺题集合，调用现有汇总函数输出派生报告；不调用模型或修改数据库。
- [x] 报告 2,052 / 2,061 覆盖率、六组准确率、配对区间、漏斗和规则选定的优先瓶颈；原始缺题验证仍应拒绝全量完成声明。
- [x] 记录复现命令与输入文件摘要，验证额外缺题、错 owner、重复题或缺组不能悄悄混入统计。

### Task 5: 只修复最大的已测瓶颈

- [x] 沿已冻结窗口、Worker 原始输入输出、落库与投递定位可复现故障：首个新对象块遗漏 TARGET；同时确认 NO_CHANGE 过多，语义根因仍须真实模型实验。
- [x] 先建立可复现失败，再做一项局部修正；只补满足精确条件的初始 TARGET，不改 Chorai、公共协议、schema、Seed 写入资格或在线无生成式 AI 原则。
- [x] 离线原始输出重放与回归测试验证：3,436 attempts 中 60 份额外通过语法及提供引用检查；原有 3,211 份合格/no-op 输出不变。最终 eval + Worker 为 257 passed / 13 skipped，独立审查通过。
- [ ] 真实模型隔离 A/B 与端到端答题复测：检验过多 NO_CHANGE 导致的背景信息损失，不改变原始基线或同时调检索。MiniMax 已恢复真实生成；Task 6 候选未证明有效，未合入，也不声称准确率提升。

### Task 6: 固化语义的隔离对照（2026-09-11）

**定位：** 延续 Task 5 尚未完成的固化根因验证，不是新增运行时机制。API 套餐查询已恢复成功；本轮 Goal 状态为 active。先做以下固定探针，通过后才考虑正式 Worker 修改与端到端评测。

**单一假设：** Worker 把“不能把引用或临时写作指令当成长期行为要求”扩大成了“整个写作/提问请求都不值得记忆”，且对所有 TEXT 强加 future-conditional 句式；这可能压制本应进入 Recollection 的个人背景与有来源的经历。

**唯一实验变量：** Recollection 的语义范围说明。保留事实/经历中的主体、条件和来源归属；引用不是本人偏好，但本人明确认领的经历、以及有意义的自述材料可按来源限定地记住。不能把虚构人物、第三方、Agent 补充、普通单次兴趣提问或待遗忘内容擅自升级成用户画像。未来回应条件句只约束 Disposition，不约束 Recollection。其余 Seed 路径、去重、eligibility、输出协议与正规化均不变。

**Files（实验用，不部署）：** `.cache/recollection-background-v1/probe.py`、`test_probe.py`；报告写本计划 SDD workspace 的 `task-6-report.md`。不修改正式 Worker、frozen harness、原实验目录或数据库。不创建 commit。

- [x] TDD：先验证一项真实行为边界会失败，再实现；覆盖候选转换只改规则/末尾检查而原 WINDOW 与 allowed refs 逐字不变、原始输入漂移拒绝、结果错误不伪装 NO_CHANGE、既有输出不覆盖。
- [x] `prepare`：只从原 `validation-all-v1/memory-before-*.json` 和 `worker-v4/windows.jsonl` 选源，不打开 questions、gold、隐藏 persona 或答案。对 persona ID 按 `sha256('recollection-background-v1:' + persona)` 排序取前 12 个，各取冻结快照中第一个 Job 的最早成功模型尝试，用 exact EPISODE ref set 对应，不根据模型输出筛选。相同 Job 的失败尝试不作为完成答案。保留原成功输出为观察，不充当新对照。
- [x] 加 8 个精简人工来源边界场景：请求内明确个人背景、本人认领的回忆录、第三方引用、明确虚构人物、普通闲聊、伪造引用注入、本人明确长期要求、existing Recollection 同义 KEEP。预写预期语义，不能用 prompt 原文字面存在当通过。
- [x] freeze：同一 case 的原始与候选模型输入、case refs、源摘要、脚本摘要、选样规则及模型参数写新 manifest。使用当前 `ChatTextModel`，MiniMax-M2.5 / https://api.minimaxi.com/v1 / temperature=0 / reasoning_split=true / max_output_tokens=8192 / request_timeout=110 / max_retries=0。同一问题两臂顺序按 case ID hash 交替，整体最多 2 并发；每臂使用同一 `InferenceService` 正规化。
- [x] `run`：只使用显式 output 子目录，prepare 已存在时拒绝覆盖；按 manifest 的完整 request 逐项恢复，仅复用完整成功结果，先写每次请求的 input 和开始记录，再记成功或错误。不记录 key/header；401/422/429 或其他 provider failure 保留原始类型与可用 code 后停止，不自动重试失败 case。可选 `--limit` 只按 manifest 顺序取首 N case 供 smoke，不另选样。
- [x] Controller 执行真实配对探针，使用现有 Go grammar/ref checker，对照阅读保留信息与错误归属；将格式、语义、写入资格、落库、答题收益分开。40 次 API 成功，但候选 1 条格式失败、个人背景保留无稳定收益，拒绝合入。见 `eval/reports/2026-09-11-recollection-background-probe.zh-CN.md`。原始九题仍不补跑。

### Task 7: 不改提示词的窗口消融诊断

**定位：** Task 6 已否定其候选的可靠收益；其源核对仍显示未撤回的明确个人背景进入了 Worker，却未留下。只判断这一遗漏是否随同一源片段移出 28-Episode 窗口而消失。不改生产窗口策略，不把诊断样本当独立效果验证。

**Files:** 新建 `.cache/recollection-window-ablation-v1/prepare.py` 与 `test_prepare.py`，仅用于准备 manifest；报告写本计划 SDD workspace 的 `task-7-report.md`。不改 Task 6 脚本、生产文件或旧产物。无 commit。

**Interfaces:** 只读 `.cache/recollection-background-v1/run-20260911/manifest.json` 中 original requests 与来源元数据；复用其父目录 `probe.py` 的 `MODEL`、`parse_original_request`、`_canonical_hash`、`_write_new_json` 和已审查的 `run/check` CLI。不要再写网络客户端、重试机制、格式修复或语义判分器。现有 runner 对每个 case 只要 `arm_order=["original"]`、`requests.original` 就可调用未改的 Worker。

**三个固定诊断锚点（不是随机 holdout）：**

| persona / user SOURCE 后缀 | 审查前固定的应保留内容 | 本窗口相关撤回边界 |
|---|---|---|
| 929 / `:929:history:9:1` | 用户从事外交工作，有孩子，临时工作安排挤压家庭时间 | `history:12` 只撤回 daily planner；不能据此丢掉工作与育儿背景 |
| 374 / `:374:history:17:1` | 用户在产品发布工作之外还为本地 LGBTQ+ 青年群体做志愿服务，多重承诺让其精力不足 | `history:20` 撤回 workshop 学习偏好；不把参加工作坊的偏好作为应记内容 |
| 742 / `:742:history:24:1` | 用户已退休，膝盖疼和耐力下降影响步行，担心行动能力变化影响自我感 | `history:27` 撤回欧洲酒庄品酒偏好；不能记品酒偏好，不从本锚点单独断言具体职级 |

- [ ] TDD：用真实 CURRENT Worker 输入构建小 fixture，先看到焦点裁剪或原文保留测试的行为 RED，再实现。测试至少覆盖：full 逐字不变；focal 的完整 Episode（含 AGENT_ACT、SITUATION、ACTOR、SOURCE）逐字不变；缺失或重复 anchor 拒绝；允许的 refs 与 focal 内容一致且不残留其他 DIRECT_EPISODE；已有输出目录拒绝且不被改动；准备后的所有请求均由未改的 `build_model_input` 重建并具有正确 hash；两个重复试次的 hash 不被误合并。
- [ ] 每个锚点准备 full 与 focal 两种上下文。full 为原始 28-Episode WINDOW/refs 逐字复制；focal 只保留原 CONSTITUTION、该完整 Episode 和原 offered new-target sections，只过滤其他 DIRECT_EPISODE，allowed_basis_refs 仅含该 Episode。allowed_target_refs 保持原集合。若发现既有 memory/related/feedback 等不满足这种简单裁剪假设的段落则拒绝，不尝试泛化解析。当前三个窗口只含新目标资格。
- [ ] 全部请求使用现有原始 Worker 提示词，禁止调用 `build_candidate_input`。每种条件预先固定两个新试次，总计 12 次调用；request 中加入不发送给模型的 `experiment` 与 `replicate` 元数据，以防成功复用逻辑把两个有意重复的试次合并。`MODEL` 不改；case 使用单一 original arm，另设 condition 与 pair metadata，避免将 focal 称为新提示词候选。
- [ ] 新 manifest 记录精确 source manifest SHA、准备脚本 SHA、复用脚本 SHA、锚点 SOURCE 和 Episode refs、语义期待、窗口差异、条件、重复试次、完整原始请求及 hash。按固定 `sha256('window-ablation-v1:' + persona + ':' + replicate)` 决定每对先 full 还是 focal；整体按 persona 表顺序、replicate 顺序。`prepare.py --output NEWDIR` 遇已有目录拒绝。
- [ ] 实现者只运行定向离线测试，不调用模型、不写数据库。Controller 审查后使用已有 `probe.py run --output NEWDIR` 与 `check --output NEWDIR`，读真实源和输出判定锚点内容是否保留，并区分格式、语义、写入资格与效果。

**解释规则：** focal 若也漏记，不能把故障归咎于长窗口；若 focal 保留而 full 丢失，支持窗口内信息选择/竞争问题，但本消融同时减少无关内容与可比较经历，不能进一步断言纯 token 长度、遗忘指令或 Disposition 竞争谁是唯一原因。没有答题分数，也不能据此直接部署单 Episode 巩固。原始九题不补跑。

**完成记录：** 实现、15 项离线测试、独立实现审查、12 次实际调用及 Go 检查已完成。full 为 4 NO_CHANGE + 2 遗忘 Disposition；focal 为 5 NO_CHANGE + 1 含部分背景识别的非法输出。没有合法目标背景 Recollection，不部署缩窗方案。见 `eval/reports/2026-09-11-recollection-window-ablation.zh-CN.md`。

### Task 8: 单 Worker 的记忆内容／行为学习门槛分离实验

**单一假设：** 现有任务表述主要要求判断长期行为方式，将“有意义的背景”误当成“不足以学习行为”的请求而拒绝。Task 6 的局部自述条款补丁和 Task 7 的缩窗都没有恢复可用背景。用一份统一、明确区分两类问题的合同替换原说明，仍为一个调用、同一个输出协议；不同时改模型、窗口、索引、Core eligibility 或 Seed 定义。

**Files:** 新建 `.cache/recollection-contract-v1/prepare.py`、`test_prepare.py`。精确候选文字在本计划 SDD workspace 的 `task-8-contract.md` 两个 code fences：RULES 与 FINAL_CHECK；该文档是当前固定规格，实现者不能改写候选内容。报告写 `task-8-report.md`。不改生产或旧脚本，不调用模型或数据库，不创建 commit。

**Interfaces:** 复用 `.cache/recollection-background-v1/probe.py` 的 MODEL、`parse_original_request`、`frozen_request_payload`、`_canonical_hash`、`_write_new_json` 及 run/check。每个 case 两臂 original/candidate，共用原始 window_text、allowed_target_refs、allowed_basis_refs；original 必须由当前未改 `build_model_input` 重建，candidate 只替换 instruction prefix 和最后 FINAL_CHECK，不改 ALLOWED_TARGETS_BEGIN 至 WINDOW_END 的任一字节。参照 Task 6 的两臂 manifest 合同，不复制网络或正规化代码。

**固定样本与顺序：**

1. Task 7 manifest 内三个 persona 的 focal / replicate=1 原始请求，顺序 929、374、742；只用请求和来源元数据，不读取其输出。
2. Task 6 manifest 内八个人工场景，保持 manifest 原顺序和人工预期。
3. `worker/python/tests/adaptive_cases.py` 的全部 11 个既有机制 fixture，保持 CASES 顺序，复制实际 window/targets/basis 与 rubric/expected 元数据，后者不能发送给模型。不另造 actor 缺失的旧 fixture。
4. Task 6 manifest 内十二个 frozen 原始请求，保持原顺序。

合计 34 cases / 68 新调用，全部 MiniMax-M2.5 参数不变。先由 Controller 只运行 `--limit 3` 的六次已知漏记对照；若候选不能在至少两个片段形成格式合格、正确 OTHER 归属且保留预先指定背景的 Recollection，停止本候选，不运行剩余案例。不根据回答标签改规则。若通过，运行全部冻结案例；复用已完成的六次请求，不重复付费。

**后续门槛：** 完成样本后，只有背景保留出现实质改善且来源/Seed 边界未出现新实质回退，才值得做隔离 Core 端到端试验；不以块数判优，不要求或宣称所有模型输出零失败。此处不授权部署。

- [ ] TDD：先验证候选 payload 保留的真实行为 RED，再实现。测试全文 payload/refs 不变、两个 code fences 精确取值且重复/缺失拒绝、原始请求漂移拒绝、source case 缺失/重复拒绝、34 case 顺序和完整 metadata、rubric/expected 不进入模型输入、hash 正确、已有目录拒绝且不被改动。
- [ ] 候选输入组装为候选 RULES + 原始 ALLOWED_TARGETS_BEGIN 至 WINDOW_END payload + 候选 FINAL_CHECK；不做正文编辑、regex 修复或角色猜测。使用 `frozen_request_payload` 验证全字节一致。仅为 manifest 添加 experiment 元数据进入 request hash，不发给模型。每个 case 的原／候选顺序由固定 `sha256('recollection-contract-v1:' + case_id)` 首字节奇偶决定，最多同时两个 case，各 case 两臂顺序调用，沿用现有 runner。
- [ ] `prepare.py --output NEWDIR` 写排他新目录；manifest 记录两个源 manifest、adaptive fixture、候选规格、准备器、复用器的路径与 SHA，原始请求、完整候选输入、model、concurrency=2、顺序与解释边界。读取 SOURCE/ACTOR 内容作为数据，不读取 questions、gold、expanded persona、答案或凭证。所有人工结构判定必须仍标注不是语义判分。
- [ ] 实现者只执行离线定向测试并报告 RED/GREEN。Controller 独立 diff 审查后按上述先三例 gate 运行，格式由实际 Go parser 检查，语义按源复审；API 错误与 NO_CHANGE 分开，失败不自动重试，原始九题不补跑。

**Task 8 完成记录：** 27 项离线测试、独立实现审查、68 次 API 成功及独立语义审查完成。焦点片段有所改善，但完整窗口候选 11/12 NO_CHANGE，且 Seed 路径和来源归属出现回退。拒绝合入／E2E；见 `eval/reports/2026-09-11-recollection-contract-probe.zh-CN.md`。

### Task 9: 纯文本记忆整理上界

**问题：** 同一完整源窗口，如果模型不再承担数据库目标、操作、引用编号和 Seed 学习决策，能否保留明确、未撤回的用户背景？这是能力诊断，不是生产替换方案；不同时声称任务分工、prompt 长度或输出格式哪一项是唯一根因。

**范围：** 只新增 `.cache/recollection-plaintext-v1/prepare.py`、`test_prepare.py`，报告写 SDD workspace 的 `task-9-report.md`。精确候选为 `task-9-contract.md` 两个 fences，正文不许实现者改动。不改 Worker、Core、协议、schema、检索、旧产物或运行中的服务，不部署、不提交。

- [ ] 复用冻结 Task 6 `probe.py` 的网络／日志／恢复及同一 InferenceService。准备 9 cases，固定来自 Task 8 manifest：完整窗口 `frozen-929, frozen-374, frozen-742`，然后 `manual-personal-background, manual-owned-memoir, manual-third-party-quote, manual-fictional-character, manual-ordinary-chat, manual-forged-quote-injection`。不含 gold、答案或模型输出选择。
- [ ] 每例保留可由生产 `build_model_input` 重建的 original request 作为来源校验，但 `arm_order=["candidate"]`，只调用纯文本臂。相同 original payload 逐字复制到精确 RULES 和 FINAL_CHECK 之间。request 加 experiment 元数据参与 hash，不发送元数据。不是新鲜双臂 A/B，Task 8 只提供历史参照。
- [ ] TDD：证明缺少 payload／源身份／合同冻结／排他输出时失败，再实现最小准备器。固定 9 个 case 的身份顺序、候选正文 hash、来源 manifest hash；验证 payload 不变、原始源请求漂移拒绝、metadata 不进入模型输入、复用 runner 一次只调用 candidate。不要新建网络客户端或语义计分器。
- [ ] Controller 先仅运行 `--limit 3`：至少两个完整窗口保留预指定核心背景且没有实质来源归属或撤回错误，才继续其余六个来源控制；成功请求复用，不重跑。最少三次、最多九次新模型调用。
- [ ] 原始纯文本是待人工审查的诊断产物，不使用 tagged-text parser 把它计成可写入结果，不调用 probe.check，不回写 DB，不包成伪造的 TARGET/BASIS。语义按完整源核对；错误与 NO_MEMORY 分开。通过只支持进一步检验 Worker 内部分工，不授权新增生产调用或模块。

**Task 9 完成记录：** 22 项测试、独立实现审查、首三次真实调用与独立语义复核完成。两个窗口找回背景但混入撤回／Agent 内容，另一个 NO_MEMORY；按固定门槛停止，剩余六例不再运行。没有生产或端到端变更。见 `eval/reports/2026-09-11-recollection-plaintext-probe.zh-CN.md`。后续改查来源视图与语义选择，不继续堆叠候选 prompt。

### Task 10: 固定提示词的来源视图消融

**定位：** consolidation 内的一项离线 Spike。只改变模型看到的源文本，不增加生产调用或模块。比较同一完整窗口与删除 Agent 原文的用户视图；用户所有正文（包括撤回、引用、伪造标签）逐字保留。不声称缩短输入与角色隔离的作用已被分别识别。

**Files:** 仅新建 `.cache/recollection-source-view-v1/prepare.py`、`test_prepare.py`；实现报告写本计划 SDD workspace 的 `task-10-report.md`。复用冻结 `probe.py`，不另写网络、重试、输出修复或语义计分器。实现者不调用模型、不读凭证或 DB、不创建 commit，不改生产、Chorai 或旧产物。

**固定输入：** Task 9 `run-20260911/manifest.json`，SHA `21176f37ea18f5b9e9bc5f10fe7c17f2f881681d16c2060c4e34c4e95afc82e0`，9 个 case 的身份与顺序不变。精确 RULES / FINAL_CHECK 仍为 `task-9-contract.md`，SHA `a8f1f110f9f91e48b2f814d874b02049dc411f2bc8b22d97cf2117ce98d1faa5`；复用器 SHA `f9c769f51d9e021f12645f9835eb9f55df810b0ef36df8bb2c3318e6768cf74e`。只读这些源和实现接口，不读 questions、gold、隐藏 persona、旧模型输出。

- [ ] TDD：先证明用户正文保留／Agent 删除这一真实行为未实现，再实现最小准备器。该视图只用于已哈希的九个已知 new-target-only 窗口，不是通用角色解析器。严格验证外层完整 Episode refs、固定 SESSION、确切 ACTOR/SOURCE 头与唯一边界，再删除从 AGENT_ACT 到真正 SITUATION 之前的字节。未知／重复／歧义 framing 拒绝，不从正文中的角色字样猜来源。保留 Constitution、Episode/Session、完整用户 SOURCE/正文、eligibility 和所有 allowed refs；不裁剪任何用户内容或撤回。输出前校验来源、合同、模型、原请求 hash 及生产输入重建。
- [ ] 准备 `requests.original`（仅校验，不运行）、`requests.full`（逐字沿用 Task 9 纯文本完整输入）、`requests.user`（相同合同，只有上述源视图变化）。所有 request 的 `window_text` 都保留完整 canonical 窗口供原 InferenceService 重建校验；实际发送的 `input` 是各自视图。manifest 明确记载两者区别、原始来源 hash、视图字节数／hash、保留用户 source 数和删除 Agent source 数。experiment/condition 参与 request hash 而不发送给模型；元数据白名单，不带入预期或评审文本。
- [ ] 每例两臂按 `sha256('recollection-source-view-v1:' + case_id)` 首字节奇偶交换 full/user 顺序，沿用最多两个 case 并发、case 内顺序执行。`prepare.py --output NEWDIR` 排他创建；冻结相同 MiniMax 参数和完整请求。测试验证纯文本输出不改写，runner 先前三例、恢复再全例时恰好 18 次 full/user 请求，没有 original 请求或重复成功调用；测试不产生真实网络请求。
- [ ] Controller 独立实现审查后只跑前三例／6 个新调用。用户视图至少保留 2/3 预指定核心背景，且三例都无实质来源／撤回错误，才继续同 manifest 剩余六个人工来源控制（总上限 18 新调用）；否则永久停止此实验。不是补跑 Task 9 未运行案例或原始九道题。完整源审查沿用 `task-9-source-review.md`；第三方内容只能按来源归属，不应成为本人事实。

**验收边界：** 这是新鲜配对的文本能力诊断，不调用 `probe.check` 或 Go tagged parser，不生成伪造 TARGET/BASIS，不回写 DB。通过也只允许在同一 consolidation 瓶颈内选择下一项工程实验，不证明 Seed 改善或答题收益。若结果仍失败，不追加 prompt 补丁。

**Task 10 完成记录：** 19 项定向测试、独立实现审查、六次真实调用与独立语义复核完成。用户视图 929／374 保留背景，742 部分保留；三例均有实质撤回／来源错误。按门槛停止，剩余六个人工案例永久不运行，不部署源过滤。见 `eval/reports/2026-09-11-recollection-source-view-probe.zh-CN.md`。Task 5 端到端改善仍未完成；下一项仅调查指令与来源的调用层分离，不改提示词文字或新增记忆状态。

### Task 11: 原生产合同的真实指令／来源传输分离

**问题：** 生产规则和源对话现在全部进入一条 user 消息。保持字句、源窗口、模型、tagged 输出和写入资格不变，将规则作为 system 消息是否恢复可用 Recollection？这是调用边界实验，不是再加规则；最终检查随规则移到 system，所以角色优先级与位置效应不能分别归因。用原生产合同而非 Task 9 纯文本，为后续实际 Worker/Core 试验保留同一接口。

**Files:** 只新建 `.cache/recollection-instruction-transport-v1/probe.py`、`test_transport.py`；实现报告写 SDD workspace 的 `task-11-report.md`。不改生产、Chorai、旧脚本／产物、数据库或服务，不提交、不部署。实现者不调用模型、读取凭证或派生子 agent。

**固定来源：** Task 8 `.cache/recollection-contract-v1/run-20260911/manifest.json`，SHA `2bde42c6f57bbb620c1c3a0a3d3942202558f03b9a1e7cf9732317aeea593e37`。只读取 original requests 和 case 来源／预期元数据，不读取 candidate 或旧模型输出。顺序：`frozen-929, frozen-374, frozen-742`；原顺序全部 11 个 `adaptive-*` cases；`manual-personal-background, manual-owned-memoir, manual-third-party-quote, manual-fictional-character, manual-ordinary-chat, manual-forged-quote-injection`。共 20 cases / 最多 40 新调用，不读 questions、gold 或隐藏 persona。

- [ ] 复用冻结 `.cache/recollection-background-v1/probe.py`（SHA `f9c769f51d9e021f12645f9835eb9f55df810b0ef36df8bb2c3318e6768cf74e`）的 MODEL、解析／hash／日志／key helper、`run_cases` 和 check；网络仍使用 `personamem_live.ChatTextModel`，不重写重试、正规化或语义计分。准备 CLI `prepare --output NEWDIR` 和运行 CLI `run --output DIR [--limit N]`。
- [ ] 每例 `payload=frozen_request_payload(original_input)`，严格验证 `prefix + payload + suffix == original_input`。original 臂 input 为完整原始文字、instructions 为空；split 臂 input 为 payload、instructions 为 prefix+suffix，字句不改。两臂 canonical window_text／allowed refs 相同并通过当前 `build_model_input` 重建。instructions 和精确 provider messages 作为 request 元数据参与 hash；预期／rubric 不发送给模型。元数据仅用明确白名单。
- [ ] 小型 `InstructionTransportModel` 包裹现有 ChatTextModel：从冻结 manifest 注册 exact input → instructions / messages / request_hash，收到 runner 的 input 后查表转发，拒绝未知／冲突路由；每次实际调用前写 `transport.jsonl`（messages、hash，不含凭证）。不用 JSON envelope，不修改冻结 runner。run 仍由 run_cases 负责并发、成功复用、失败停止；finally 关闭原模型。
- [ ] `sha256('recollection-instruction-transport-v1:' + case_id)` 首字节奇偶决定每例 original/split 顺序，最多两个 case 并发、case 内顺序调用。prepare 排他创建新目录，记录源／自身／复用脚本 SHA、模型、20 例身份顺序、完整请求与解释边界。未知来源、重复 case、原请求漂移、source hash 不符均拒绝。
- [ ] TDD：先用最小 pass-through scaffold 看到真实行为 RED（不是 import error）：经现有 ChatTextModel 和假的 SDK `chat.completions.create` 捕获的出站消息缺少 system。实现后验证确实是一条 system 规则＋一条 user 原 payload，original 仍只有完整 user；source 字节不进入 system，元数据不作为 prompt，使用／raw-output 日志与正规化保留。覆盖来源 hash／重建、排他目录、固定 20 例、注册路由拒绝、先 3 例再恢复总共恰好 40 次且无重复、既有 provider failure 不重试。只运行定向离线测试。
- [ ] Controller 独立实现审查后先 `--limit 3`，只跑六次新鲜配对调用。使用真实 Go parser／offered refs 检查和完整源审查（沿用 `task-9-source-review.md`）；split 至少 2/3 核心背景形成合法 OTHER Recollection，且三例没有实质来源／撤回错误，才继续剩余 17 例。否则停止此候选，不自动切换纯文本重跑。完整机制／来源控制没有实质回退才进入隔离 Core 答题复测；当前不授权部署，不声称格式通过就是效果提升。

**Task 11 完成记录：** 17 项定向测试、独立实现审查与修复复核、六次真实配对调用及独立传输／语义复核完成。两臂三个窗口全部 NO_CHANGE，split 背景保留 0/3；停止剩余十七例，不合入、不做 E2E。见 `eval/reports/2026-09-11-recollection-instruction-transport.zh-CN.md`。不把此零结果泛化为消息角色普遍无效；后续应检验 Worker 任务职责而非继续移动／追加提示词。

### Task 12: 同一 Worker 内的两阶段整理／落成实验

**假设与边界：** 将候选内容选择暂时外化为纯文本笔记，可能使最终更新阶段保留有用背景，同时对照完整来源淘汰候选错误。比较同一个最终处理器“无笔记”和“有新鲜笔记”两臂，不比较历史输出。仍是一个 Worker RPC、一个最终 tagged 结果、一个 Core 原子提交；额外成本是 staged 臂多一次后台模型调用。候选笔记不落库、不成为证据，不新增 Job、Proposal、schema、协议或 Seed 资格。这是隔离 Spike，未改变或部署生产架构。

**Files:** 只新建 `.cache/recollection-two-pass-v1/probe.py`、`test_two_pass.py`；实现报告写 SDD workspace 的 `task-12-report.md`。实现者不调用模型、不读凭证／DB、不派生 agent、不提交；不改旧产物、生产或 Chorai。沿用 Task 11 的固定 20 个 case 身份／顺序，但只读 Task 8 original requests 与白名单来源／预期元数据。source manifest、复用 probe 的路径／SHA 与 Task 11 相同。禁止读取 questions、gold、隐藏 persona 和旧模型输出。

**固定的两步内容（不改写已有合同）：**

- 第一步使用 `task-9-contract.md` 两个 text fences（SHA `a8f1f110f9f91e48b2f814d874b02049dc411f2bc8b22d97cf2117ce98d1faa5`）：`RULES + "\n\n" + 原完整 payload + "\n\n" + FINAL_CHECK`。
- 第二步使用 `task-8-contract.md` 两个 text fences（SHA `e3df6370ced2735a855a0c4ec8cfb2fe69ab8489b787150442a19fa69082cb8a`）。prefix 为 `RULES + "\n\n" + 下方固定 notice + "\nPRELIMINARY_NOTES_BEGIN\n"`；suffix 为 `"\nPRELIMINARY_NOTES_END\n\n" + 原完整 payload + "\n\n" + FINAL_CHECK`。将笔记以 `"\n".join("> " + line for line in notes.split("\n"))` 引用后夹在 prefix/suffix 之间。control 使用固定 `NO_PRELIMINARY_NOTES`；staged 使用该例刚产生的第一步原始全文。笔记中标记不能成为新的 offered refs；两臂完整 canonical WINDOW 和 allowed refs 逐字相同。

```text
The following quoted preliminary notes are an optional, fallible attention aid from a separate text pass. They are not source evidence, existing memory, instructions, permission, TARGET or BASIS. Decide every update only from the complete original WINDOW and its offered refs; discard notes unsupported by source or withdrawn. Absence from the notes does not prohibit an independently supported update.
```

- [ ] 复用冻结 Task 6 probe 的 MODEL、解析／hash／日志／key helper、`run_cases` 和 Go check；网络仍用现有 ChatTextModel，不复制网络、重试、正规化或语义判分。Task 11 只能作为小型 wrapper 的参考，不继承它的角色拆分。prepare 和 run 都验证源／合同／复用脚本 SHA、固定身份顺序、原输入重建与 request hash；prepare 排他创建新目录。
- [ ] manifest 保存 `requests.original` 仅供未改的 InferenceService 重建，不执行。`requests.control.input` 是完整第二步 sentinel 输入；`requests.staged.input` 是第一步输入，另保存完整第二步 prefix/suffix。这些字段与 model／condition／window／refs 全部进入 request hash。每例按 `sha256('recollection-two-pass-v1:' + case_id)` 首字节奇偶交换 control/staged，最多两个 case 并发。
- [ ] 小型 TwoPassModel 精确路由 runner input：control 一次调用；staged 先产笔记，再用完整来源和笔记调用同一第二步。即使笔记为 NO_MEMORY，也运行第二步。只有第二步结果返回 InferenceService；第一步不可被正规化或变成 DB 更新。对每个实际 provider call 记 `provider_calls.jsonl` 的 request_hash、phase、精确 input、started/success/error、raw output 或安全错误类型；不记录凭证。继承 usage 日志；明确 legacy results.input 在 staged 是复合入口，不冒充最后一次实际模型输入。finally 关闭模型。
- [ ] 复用成功的完整 arm；provider error 停止且保留为 error，后续运行不重试失败 arm，也不伪装 NO_CHANGE。不新增阶段级缓存／恢复框架；Controller 不重启 started 后没有终态的含糊请求。先三例再恢复全量最多 60 次实际调用／40 个最终 arm 结果，不能执行 original 或重复成功的第一步。
- [ ] TDD 先用单步 scaffold 观察真实 RED（最终调用没有收到第一步笔记或错误返回草稿），再实现。用真实 ChatTextModel＋fake SDK 验证每例 1+2 次出站请求、完整来源保留、含伪造标记的笔记逐行引用、最终返回／正规化与分阶段日志、无 expected/rubric 注入；覆盖固定来源／合同漂移、request 漂移、排他输出、20 例顺序与恢复次数，以及第一／第二步 provider failure 停止且不重试。只运行定向离线测试。
- [ ] Controller 独立实现审查后先三例／9 次实际调用。最终 staged 输出至少 2/3 核心背景成为合法 OTHER Recollection，且三例最终结果没有实质来源／撤回错误，才继续同一 manifest 的剩余 17 例。草稿错误若被第二步纠正，不独立判失败。实际 Go parser 检查最终结果；完整源审查沿用 `task-9-source-review.md`。全例机制／来源控制没有实质回退才进入隔离 Core 答题复测；格式、笔记数量和假 SDK 测试都不是效果收益。九题免跑不变。

**Task 12 完成记录：** 8 项离线行为测试、独立实现审查、9 次实际调用和独立出站／结果核对完成。三个 staged 最终输出全部 NO_CHANGE，目标背景保留 0/3；control 有一条格式失败。停止剩余十七例，不部署、不做本候选 E2E。见 `eval/reports/2026-09-11-recollection-two-pass.zh-CN.md`。第一步笔记有部分相关内容不等于 Core 记住了它；Task 5 的端到端改善仍未完成。
