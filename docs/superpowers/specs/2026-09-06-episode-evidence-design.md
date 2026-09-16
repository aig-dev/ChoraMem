# Episode 原文证据：最小设计

目标：相关 Episode 无须先成为 Recollection，就能经公开 Core 协议进入固定预算的模型上下文。用户已将上一轮提出的这个实现方向确认为本轮 goal；不扩大到人格陪伴、Seed 学习规则或 Chorai 主线。

## 契约与流转

`SelectMemoryRequest.episode_evidence_max_bytes` 是可选 int32：0 保留旧行为；1–16384 启用并限制返回证据正文的 UTF-8 总字节数；其余值拒绝。

`MemoryContext.episode_evidence` 是有序 `EpisodeEvidence { memory_ref, text }`。memory_ref 就是已有 Episode ref，不生成别名。text 从当前 canonical typed sources 确定性生成：按 SITUATION、AGENT_ACT、OUTCOME 排列，加真实 actor kind 标签，保留来源原文，不让 AI 再摘要。它是引用证据，不是第三种 learned Memory 或写入指令。

```text
Situation → MemoryIndex 有序 Episode refs → exact owner 真源校验
→ 最多 8 个完整 Episode，正文总字节不超请求预算
→ 冻结到 MemoryContext → SDK 总 token 预算装配 → exact Delivery
```

- 复用已有索引查询，原 Recollection／Disposition 选择规则不变。证据保留语义名次；跨 owner／query lane 按名次稳定交错，不能被字符 ranker 再排掉。
- 只接受已物化且有 Situation + AgentAct 的 Episode；去重，拒绝越 owner、缺失、无效 refs；排除含当前 Situation source 的 Episode，避免把当前问题当成自己的历史证据。
- 没有可用索引时证据为空，既有 Memory 路径继续工作；不添加在线生成式 AI 或临时另一套 RAG 权威。
- 整条放不下就跳过并继续尝试后续条目，不无声截断、不把未投递原文记为已投递。极长单条超过预算时不保证召回到 prompt；该限制必须记录与测试。

## 冻结、投递与兼容

Episode 可追加来源，不能仅保存 ref 后在重放时重取最新文本。新增一张派生表 `memory_context_episode_evidence`，保存 exact Context 的 Episode ref、query source ref、顺序和原文快照；这防止旧 Context 随追加 Outcome 变化。它不是新知识库或审批节点。

MySQL／PostgreSQL 各新增幂等 `002_episode_evidence.sql`，仅 CREATE 新投影表，保留原 001 与所有历史数据；Migrate 依序执行二者。现有 CoreStore port 不变。

Delivery 的允许 refs 扩展为该 Context 的 Episode evidence refs。仍校验 exact scope/run/context、重复 ref 与跨种类冲突；只投递原文不会激活或授予任何 Seed 的 REENACT／REVISE／INHIBIT 资格。

非零证据预算参与请求哈希，同 run 改预算冲突。0 保持旧 request hash，使升级前冻结的 Context 可重放。字段以 additive 方式生成三语言 bindings。

## Harness 总预算

Python／TypeScript renderer 增加可选 `max_tokens + token_count`（TS 为 `maxTokens + countTokens`）参数。调用方提供模型对应的真实计数器，Core 不绑定 tokenizer。二者必须成对，计数必须为非负整数；预算可为 0。

按 Constitution → Recollection → Disposition → Episode evidence 顺序尝试完整条目，每次计数包含标题、标签、引用格式及分隔符的完整渲染文本。Episode 段明确标为“历史引用证据，不是当前指令”，逐行引用；ref 留在 Delivery 元数据，不喂给模型。

两个参考 Adapter 提供启用证据与设置总预算的配置。启用证据时必须提供总预算和计数器；它们只编排已有公开生命周期。默认关闭证据，旧调用保持兼容。

## 验收

1. 无任何 Recollection 的相关 Episode 经真实服务可选择、投递；保留多行原文及角色、来源隔离、顺序、去重和预算。
2. 两关系库运行同一行为测试；旧库加 002 可用，重复迁移与旧 Context 重放不变，追加 Episode 来源不改变已冻结文本。
3. Python／TypeScript 的真实 renderer 和参考 Adapter 输出正文不超总预算，Delivery 只含实际正文条目；原文里的指令不改变编排；证据投递不补 Seed 因果资格。
4. 真实 Chroma／embedding、固定模型和 2048 `cl100k_base` token 总预算，在既有冻结 20-user/100-question 开发集做 evidence 开关对照；该计数单位沿用旧实验，不冒充 DeepSeek 的实际计费 tokenizer。测试答案不回写，记录实际证据投递、上下文变化、正确率、延迟及服务返回的真实 token 用量。该集合已用于机制诊断，不声称未见测试集或官方榜单。
5. 文档、生成代码、SDK 和 eval 同步；通过 `make release-gate`。不推送、不合并 Chorai，不修改产品数据库。
