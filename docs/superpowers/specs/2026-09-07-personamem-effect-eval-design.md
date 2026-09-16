# PersonaMem-v2 效果优先评测设计

目标：先证明长期记忆能稳定改变正确答案，再讨论成本或继续扩展 Seed。公开横评与机制诊断分开，二者共享官方 PersonaMem-v2 数据，但不混算成绩。

## 两条评测路径

### OmniMemEval 横评

提供一个可安装到固定上游版本的 `memory_core` Client Adapter，只把 OmniMemEval 的 `add(messages, user_id)` 和 `search(query, user_id, top_k)` 映射到公开 Memory Core RPC。答案 prompt、选项顺序、5,000 道 benchmark 题、回答模型和规则计分全部由 OmniMemEval 控制。

- 历史只按真实 user/assistant 对写成 Episode；system 文本不学习，不虚构缺失回复。Omni 会先按 20 条原始消息切批，Adapter 因此跨 `add()` 保留未闭合的 user 消息，并合并连续同角色文本；只有完整对才写入。
- query 只写成未绑定 SourceEvent，用于 Select，不形成 Episode，不进入后台巩固。
- Adapter 返回 Core 选择并确定性渲染的纯文本；Omni 的 `top_k` 不伪装成 Core 权重或置信度。
- 使用唯一 version/user scope 隔离运行。V1 没有删除长期 owner 的公共 RPC，因此只支持非 streaming 横评；清理依赖新 version，而不是直连数据库删数据。
- Core 明确返回 `ABORTED` 时，Adapter 以同一请求和幂等键做有界短退避重试；其他错误不吞掉。该次数属于运行配置并随实验冻结。
- 后台巩固通过 Omni 的显式 `--wait-after-ingest` 等待；结果必须记录 Core、Worker、索引、上游 commit 和所有模型配置。

### 六组效果诊断

在 validation split 上使用同一批问题、同一选项顺序、同一回答 prompt 和同一模型，逐题产生以下六个互斥 Context：

1. `none`：无历史上下文。
2. `full_history`：完整历史对话，是效果上界对照，不写入 Core。
3. `oracle_episode`：只注入官方 `related_conversation_snippet`，是证据可用性的上界，不是框架成绩。
4. `semantic_top40`：直接读取同一个 MemoryIndex 的前 40 个 Episode，使用 canonical Episode 文本，不施加 Core 8 条/16 KiB 或 SDK token 预算。
5. `current_core`：当前公开 Select，启用有界 Episode Evidence，并包含 learned memory。
6. `learned_core`：当前公开 Select，但关闭 Episode Evidence，只保留 Recollection + Disposition。

六组都排除历史开头的 system `expanded_persona`。它是数据生成时使用的完整用户画像，不是用户向 Agent 声明的设置，也不是 Agent 的 Soul；把它提供给 `none` 会让控制组直接看到本应由长期交互学习的信息。`full_history` 只包含真实观察到的 user/assistant 历史。若要评估 Soul 或显式用户设置，应另做独立实验，不能借用这份隐藏画像。

不同 persona 可并行处理，但并发数和 Core `ABORTED` 重试次数必须进入 manifest；同一 persona 的历史顺序与单题六组顺序不变。

官方 evidence label 仅由独立诊断加载器读取。对话生成过程中可能把连续同角色消息合并，或替换 assistant 回复；因此对齐以 user 原文在 Situation 中的精确包含关系为主，assistant 原文只作可选佐证。多个相关对话可以映射到非连续 Episode，但必须按历史顺序形成唯一解；零解或多解均报告，不做 embedding／编辑距离猜测。未回复的历史尾 user 不算可学习 Episode。

## 漏斗与判定

每题记录：`source_available -> indexed -> selected -> delivered -> answer_correct`。

- `source_available`：官方 snippet 能唯一映射到历史 Episode。
- `indexed`：至少一个 gold Episode 出现在实际 semantic Top-40。
- `selected`：至少一个 gold Episode 本身出现在 Core 返回的 Episode Evidence。
- `delivered`：至少一个已选择的 gold Episode 本身出现在最终模型输入中，且 ref 在 exact rendered refs 中。
- `answer_correct`：沿用官方 MCQ 规则。

另行报告 `learned_basis_selected / learned_basis_delivered`：gold Episode 是否通过 Basis 连接到所选／所投递的 Recollection 或 Disposition。Basis 只证明来源关系，不证明压缩文本仍保留了本题所需细节，因此不得把这条辅助轨道混入原文证据主漏斗。

汇总必须同时给出整体准确率、按 persona 聚类的配对区间、各 `pref_type`、无效答案和漏斗条件准确率。禁止用 benchmark 标签参与检索、巩固、rerank 或普通 Core 回答。

准确率与配对差值以题为权重；95% 区间对 persona 整簇重采样，避免把同一 persona 的多题当作独立样本。只有配对区间下界大于 0 的正差距才可触发 Core 优化；若 Oracle 未稳定优于 none，则归入回答路径，若 Oracle 有效但所有 Core 阶段差距均不稳定，则报告 `no_measured_core_bottleneck`，不得按最大的噪声点估计调参。

总体产品分数保留所有 validation 题；Core 因果判定只使用 `source_available=true` 的题。阶段差距也使用各自已经具备上游资格的子集：retrieval 在 source-available 题上比较，selection/assembly 在 indexed 题上比较，consolidation 在 direct gold Episode 已 delivered 的题上比较。来源不在历史中的题单独报告，不能算成 Core 漏召回。

## 最大瓶颈决策

- source-available 子集上 Oracle 接近 none：先修回答 prompt/模型利用，停止改检索。
- source-available 子集上 Oracle 显著高于 none、semantic Top-40 低：先修召回或 query-aware rerank。
- indexed 子集上 Semantic Top-40 高、current Core 低：先修 Select/装配/预算。
- direct gold Episode 已 delivered 的子集上 Raw Episode 高、learned Core 低：先修隐含偏好固化。
- `ask_to_forget` 独立失败：再引入 Recollection 撤回状态；没有实测失败前不提前扩展状态机。

完成条件不是“runner 能启动”，而是：Adapter 在固定 OmniMemEval 上游 smoke 通过；六组离线/服务边界测试通过；完整 5,000 题横评与 validation 六组都有可复核产物；根据漏斗只确定一个下一步最大瓶颈。
