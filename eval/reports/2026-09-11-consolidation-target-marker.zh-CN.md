# 固化输出首标记兼容：局部修复与验证

六组基线见 [已接受样本效果报告](2026-09-11-personamem-effect-baseline.zh-CN.md)。
按冻结决策规则，本轮优先处理 consolidation；这里只修复其中一个已复现的文本协议故障，
不声称整个固化瓶颈已经解决。

## 改了什么

部分模型结果从 `NEW_RECOLLECTION` 或 `NEW_DISPOSITION` 开始，漏掉前一行
`TARGET`。即使后续内容完整，原有 Core parser 也会拒绝整批结果。

参考 Worker 现在只为**已提供的新对象 Target**补回这个首标记，且下一有效行必须是
`APPLICATION`。它不修复 existing Target、后续缺失 marker、推理过程或未知引用，
不改正文、Application、operation、Basis 或任何 ID。Core 的语法、因果资格、原子提交
与输出字节上限不变；没有新增 AI Job、模型调用、协议字段或数据库表。

## 离线确定性重放

使用原先保存的真实 Worker 输出，经参考 Worker 和实际 Go parser 重放；
没有向模型重新提问，也没有把新结果写入原评测数据库。

| 输出检查 | 修复前 | 修复后 |
|---|---:|---:|
| 成功模型输出尝试 | 3,436 | 3,436 |
| 无变化输出 | 2,825 | 2,825 |
| 语法不通过 | 216 | 153 |
| 语法通过但引用越界 | 9 | 12 |
| 语法与提供引用检查均通过的非空输出 | 386 | 446 |

这些是 attempt 数，覆盖 3,405 个不同 Job；包含重试，不等同于落库数量。
103 份输出仅增加了开头的 `TARGET` 行，其中额外 60 份通过语法和引用检查。
另外 3 份原先被语法错误遮住的越界引用被继续拒绝，并没有被放行。
原有 3,211 份可解析或 no-op 结果逐字保持不变。

证据产物：

- `.cache/personamem-consolidation-diagnosis/core-grammar-checks.json`
- `.cache/personamem-consolidation-diagnosis/after-leading-target-v1/core-grammar-checks.json`
- `.cache/personamem-consolidation-diagnosis/after-leading-target-v1/comparison.json`

## 最终代码验证

首标记比较使用与 Go `strings.TrimSpace` 一致的显式空白字符集合，避免 Python 默认
`strip()` 多识别 U+001C–U+001F。VT、FF 与 Unicode 空白的正例，以及额外控制字符的
反例均有回归测试。独立复审通过。

最终实现再次读取全部 3,436 份原始模型输出，构造出的模型输入与冻结输入逐字一致，
最终 Worker 输出与上述修复后重放结果逐字一致，因此表内计数适用于最终代码。
复核命令不会调用模型、写数据库或覆盖重放产物：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=worker/python/src \
  python3 .cache/personamem-consolidation-diagnosis/verify_final_worker.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=sdk/python/src:eval/python/src:worker/python/src \
  python3 -m pytest eval/python/tests worker/python/tests -q -p no:cacheprovider
go test -count=1 ./internal/core/... ./internal/inference/grpcworker
git diff --check
```

结果：重放全数一致；Python **257 passed / 13 skipped**；相关 Go 测试通过；diff 检查通过。
这不是依赖真实模型的集成测试或发布 gate 全量通过声明。

## 尚未解决

主要语义损失仍在：大量窗口返回 `NO_CHANGE`；只靠补标记不会自动得到高质量长期记忆。
已保存的 735 份历史快照总共只有 95 个 Recollection version、191 个 Seed version；
1,506 / 2,052 个 learned-only Context 为空，其最终模型输出与 none 组完全一致。
其余 546 题中，none 正确 160 题，learned-only 正确 154 题。

这说明下一项真实实验应检验：是否在不混淆本人陈述、代写材料与第三方引用的前提下，
更好保留交互中的可复用背景。不能为了分数把 Agent 自述或引用内容直接当成用户偏好。

完成上述离线修复时，连接 `api.minimaxi.com` 连续出现 DNS 解析超时，未进入鉴权。
同日后续连接已恢复，40 次真实语义 A/B 调用完成，但候选没有稳定改善背景保留，
因此未合入；见 [背景保留探针](2026-09-11-recollection-background-probe.zh-CN.md)。
端到端答题复测仍未完成。本报告不声称准确率提升，不改变原始基线成绩。
新 Worker 代码也未部署到原评测服务或 Chorai。
