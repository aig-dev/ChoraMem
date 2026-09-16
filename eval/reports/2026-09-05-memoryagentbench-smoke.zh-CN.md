# MemoryAgentBench 官方小样本基线（2026-09-05）

这份报告只记录一次可复核的本地 smoke run，不代表统计显著的 benchmark 成绩。

## 固定条件

- 上游：MemoryAgentBench commit `fe1735de8cf8b9908e1e3d3b5612afc815698062`
- 数据：官方 `Accurate_Retrieval-00000-of-00001.parquet`
- 数据 SHA-256：`56c3cd80fb6731a3e53cd1a6be3148f54df60ff2d290ee50e28f8acebf9655c1`
- 子集：`longmemeval_s*`
- 查询数：1
- 回答模型：`deepseek-v4-flash`
- 官方回答预算：50 tokens
- Core 存储：MySQL 8 Adapter
- 能力边界：`retrieval_recollection`

使用 LongMemEval 对话样本，是因为它与 Core 的个性化长期 Recollection 语义更接近。
此前用 EventQA 做过一次诊断：模型答对首题，但 Core 没有形成或投递任何 Recollection，
因此该 100% 不能作为 Memory Core 的检索证据。

## Core 侧证据

- 输入 88 个 Episode。
- 22 个 consolidation window 全部完成。
- 形成 8 个 Recollection、9 个 RecollectionVersion。
- 查询生成 1 个 MemoryContext，其中包含 8 个 item。
- Harness 记录 1 个真实 MemoryDeliveryReceipt。
- OutcomeEvent 为 0；Disposition 为 0。

因此这次运行确实覆盖了“形成 Recollection -> Select -> 注入 -> Delivery”的路径；它没有、
也不声称覆盖 Disposition 的形成、重演、修订或抑制闭环。

## 首题结果

- 问题：峰值 campaign 期间，用户通常每周工作多少小时？
- 标准答案：`50`
- 模型回答：`During peak campaign seasons, you work up to 60 hours a week.`
- Exact match / F1：`0 / 0`

失败发生在 Recollection 形成阶段：窗口中存在“峰值期可到 50 小时”的来源文本，但形成的
8 条 Recollection 没有保存它。这个结果可作为后续 extraction / consolidation prompt
优化的基线，不能被解释为检索成功率结论。
