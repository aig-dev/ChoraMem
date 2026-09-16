# Recollection 背景保留探针：不合入候选

本次候选没有证明能改善真实窗口中的个人背景保留，暂不修改正式 Worker 提示词，也不启动该候选的全量答题评测。此前九道未完成题仍按用户要求免跑。

## 实验边界

从冻结快照按 persona ID 的固定 hash 顺序取 12 个 persona，各取第一个 Job 的最早成功原始请求；不根据 NO_CHANGE、问题或标准答案选样。另加 8 个提前写明预期的来源边界场景。原始与候选各调用 20 次，WINDOW、refs、模型参数及正规化相同，只改 Recollection 语义说明的五处文本。旧输出不充当新对照。

MiniMax-M2.5，temperature=0，reasoning_split=true，max_output_tokens=8192，零自动重试，最多两个场景并发。40 次 API 调用均成功，finish_reason 均为 stop；这不代表输出均符合协议。

12 个真实原始输入各含 28 个 Episode，均提供 NEW_RECOLLECTION；输入为 47,968–56,755 bytes，低于 Worker 的 256 KiB 跳过阈值。源文本实际进入了模型，本次漏记不能解释成上游未送达或该字节上限触发。

## 结果

| 指标 | 原始 | 候选 |
|---|---:|---:|
| 真实窗口 NO_CHANGE | 11/12 | 7/12 |
| 真实窗口 Recollection 块 | 1 | 1 |
| 真实窗口 Disposition 块 | 0 | 8 |
| 真实窗口格式失败 | 0 | 1 |
| 全部场景 grammar/ref 合格 | 20/20 | 19/20 |
| 人工场景结构符合预期 | 5/8 | 5/8 |

两份真实 Recollection 都在概括“尊重用户遗忘请求”，不是本次想保留的个人背景。候选额外的八个 Disposition 块也都是遗忘请求或其泛化。候选在 persona 0 输出了协议外分析，Go parser 拒绝；不把这条当 NO_CHANGE，也不自动截取尾部作为成功。

人工语义核对：

- 四个排除场景（第三方引用、明确虚构、普通天气提问、引用中的伪造指令）两臂均未建立用户记忆。
- 本人明确长期要求两臂均正确输出 ADAPT。
- 本人回忆录、已有同义 Recollection 的 KEEP，两臂都漏掉。
- 童年背景：原始漏掉；候选保留了背景，但 APPLICATION 错选 SITUATION 而非 OTHER，且正文未保留要求的自述归属表达。因此只能算部分保留，不能算完整通过。

独立语义复审得到相同结论：真实候选的九个合法块全部使用显式遗忘 Episode 作为来源；三个 Recollection 正向人工场景为 0/3 完整通过，其中一个部分保留。协议与应用层归属错误分开记录，不能把 grammar/ref 合格当成语义正确。

结构检查不替代语义审查；本次没有验证数据库写入资格、落库或答题收益。样本量小，不能推算全量收益。更多块、较少 NO_CHANGE 本身都不是改进证据。

## 可复核产物

实验目录：`.cache/recollection-background-v1/run-20260911/`。其中 `manifest.json` 冻结输入、源摘要、请求摘要、顺序及参数；`attempts.jsonl`、`results.jsonl`、`usage.jsonl` 保存本次实际调用；`grammar.jsonl`、`comparison.json`、`review.md` 区分协议检查和待审语义。候选脚本与 8 项测试位于其父目录，独立实现审查通过。

累计输入 366,760 tokens，输出 28,155 tokens，总计 394,915 tokens；不换算未经确认的套餐金额。

下一步仍只调查 consolidation。固定样本中，未被撤回的直接个人背景也可能与多个遗忘请求一起被丢弃；应先用相同提示词做源文本的受控诊断，区分窗口内信息竞争与规则语义问题，不继续叠加提示词补丁，不同时改变检索或 Seed。
