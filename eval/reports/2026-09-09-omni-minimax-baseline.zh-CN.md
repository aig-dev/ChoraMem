# OmniMemEval / MiniMax 基线：4,999 条可评分回复，1 条 API 拦截

记录时间：2026-09-09 22:46，Asia/Shanghai。

## 当前结果与边界

固定的 5,000 题均已尝试，检索成功 5,000 题；回答成功 4,999 题，另有 1 题被供应商输出内容审查拦截。上游原生计分为 **1,942 / 4,999 = 38.8478%**。这是成功请求子集的分数，不是“5,000 条全部成功”的成绩。

已向用户提出完整分母的处理口径：保留全部 5,000 题，将拦截题单列为未答/错误，同时保留上游原生结果。确认前不把该口径作为已冻结的完整分数发布，也不删除拦截题或伪造成功记录。

本结果还不能证明 Memory Core 相比无记忆或其他框架有优势。独立的 validation 六组诊断仍在运行；完整漏斗、配对区间和唯一最大瓶颈尚未得出。`num_runs=1`，上游输出的标准差 0 不是置信区间。

## 固定实验条件

- OmniMemEval commit：`0b1ea8d28aa2d3e03ac4a6aee17b3006a131da7d`。
- 回答模型：`MiniMax-M2.5`，接口 `https://api.minimaxi.com/v1`；沿用上游 prompt、原选项和计分函数，temperature 0。
- 非 streaming，1 run，4 个回答并发；上游 `TOPK=20`，Core Episode Evidence 预算 16,384 字节。
- 不跳过搜索失败、不跳过回答失败、不允许缺失数据；未改问题、prompt 或模型来规避内容拦截。
- 实验版本：`memory-core-minimax-minilm-v1`；实际产物目录为 `.cache/OmniMemEval/results/pmv2/memory_core-memory-core-minimax-minilm-v1`。
- Core/Worker 源码基线：`0855b244458cfe0685f54602030201411635d55c`；Worker 使用此前冻结的 MiniMax reasoning-split 运行适配。索引为固定 MiniLM / Chroma 配置，具体恢复记录见同目录的基线续跑报告。

## 原生分类结果

| 类别 | 可评分题数 | 准确率 |
|---|---:|---:|
| therapy_background | 627 | 46.89% |
| neutral_preferences | 858 | 36.95% |
| ask_to_forget | 1,048 | 45.23% |
| anti_stereotypical_pref | 855 | 33.80% |
| stereotypical_pref | 532 | 43.05% |
| health_and_medical_conditions | 568 | 32.39% |
| sensitive_info | 511 | 30.33% |

内容拦截题属于 `stereotypical_pref`，topic 为 Activism。这些分类差异不是因果诊断，不能据此提前选择某项 Core 改动。

## 请求失败与重试完整性

首轮结束时为 4,971 条成功记录、29 条失败，失败 ID 与全部缺题逐一相等：28 条 `429 / 2056 或 2062`，1 条 `422 / output new_sensitive (1027)`。供应商将 1027 列为输出内容问题。[官方错误码](https://platform.minimax.io/docs/api-reference/errorcode)

一次限流专用补跑只调用这 28 题，全部成功；调用的是固定上游 `process_qa`，仍为同一模型、prompt 和 4 并发。没有调用内容拦截题，也没有重测已有错答或处理后空字段。该补跑器仅是本地运行辅助文件，6 项无 API 回归测试与实际数据预检通过，不改变 Core、Worker 或上游源码。

补跑后重新核对全部 4,999 份实际 `model_input`，均逐字匹配固定上游 prompt、检索正文与选项，未使用预生成答案。原 4,971 条记录的规范化 SHA-256 仍为 `0c460dfa9b80c1d06bef0f064ef27543928005f8a3c1b5967622efa0cf6ab15d`；原始内容拦截错误记录逐字段相同。

目前 13 条持久化 `answer` 字段为空，其中上游判错 12 条、判对 1 条。上游可从原始回复回退提取选项，所以这个数量不能等同于原始空输出或全部无效答案；完整 API 回复未落盘，不能事后统一改判。

## 用量与时延

两轮原始用量分别保存在 `answer-passes/`：

- `f014548b25f2d9f8ade3469ae60822ffade4ff7ac322505d0791b70c4bc6438e.json`：4,971 次成功请求。
- `859c52adfcf46728d3fd9a5a370420cbe33a6886ae27c1a151c2de95ef64a33d.json`：28 次成功请求。

`token_usage_answer.json` 仍是最后一轮原始统计；`token_usage_answer_total.json` 是带来源清单的合并值。已验证上游报告读取到的是合计 4,999 次调用，而不是最后 28 次。

| API 返回的回答用量 | Token |
|---|---:|
| 输入 | 14,026,166 |
| 输出 | 3,239,536 |
| 合计 | 17,265,702 |

这些数字不包含未返回 usage 的失败请求，也不代表完整账单或记忆构建成本。Worker 和索引的其他成本不能从这份回答统计推断。

8 条原生请求耗时超过 10 分钟，其中 4 条包含配额暂停，4 条与本机合盖休眠窗口重合。全部保留；本次墙钟时延不能直接用于框架或模型速度排名。

## 可复核产物

产物目录保留 `memory_core_pm_search_results.json`、`memory_core_pm_responses.json`、`memory_core_pm_response_status.json`、`memory_core_pm_grades.json`、`memory_core_pm_results.xlsx` 和 `exp_report.md`。上游原生报告明确显示 4,999 条可评分记录、1 条失败、0 条跳过；未对外发送通知。

完整 5,000 分母口径、validation 六组全量结果、漏斗和最大瓶颈的单项修复仍未完成。本报告不替代最终效果结论。
