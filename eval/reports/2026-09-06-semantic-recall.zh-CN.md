# 语义召回隔离实验：证据更容易找回，但尚未改变实际投递

本轮结论：修复后的后台在新的 84 个窗口中未发生模型截断或规范化后语法／提供引用拒绝；真实 Chroma/MiniLM 检索提高了相关 Episode 的召回，但现有 Core 的 100 个回答上下文完全没变，答题仍没有收益。不是“embedding 无效”，也不是“接入向量库就已提升个性化”。

> 2026-09-08 解释修正：本轮沿用的三组请求共同包含官方 system 完整用户画像，因此这里只能定位索引到投递之间的机制断点；其中的 `none` 不是真正的自然长对话无记忆基线。新的六组诊断已排除该生成侧画像。

公开数据：[配置与数据指纹](2026-09-06-semantic-recall.manifest.json)、[完整指标](2026-09-06-semantic-recall.summary.json)、[逐题分数](2026-09-06-semantic-recall.scores.csv)。上一步的修改与重试验证见[后台可靠性报告](2026-09-06-worker-reliability.zh-CN.md)。

## 只改变索引，不改变学习状态

- 固定代码 `0829e68f36c97f50a1f97dc7c5315c43d6f604e5`。Go Core、SQL 权威层、选择政策未改变；仅使用已有可替换 MemoryIndex contract 接入实验 provider。
- 从 PersonaMem-v2 32k 排除旧 pilot 的全部 20 位用户，再按 seed `20260906` 固定选择 20 位新用户、每人 5 题。100 题在运行前写入 manifest，不按成绩取舍。
- 回放 2,155 个历史 Episode，按既有 28 回合批次等待巩固。训练不使用索引；20 位用户全部训练完后冻结，再让两组读取同一数据库状态。
- 两组共有 155 个 active Recollection、157 个历史版本，0 个 Disposition。所有用户的 active Recollection 都只有 1–22 个，低于现有每 owner／kind 的 canonical 候选上限 40。
- 回答模型固定 `deepseek-v4-flash`，temperature=0，thinking disabled，输出上限 1,024 tokens，附加记忆上限 2,048 `cl100k_base` tokens；各组共享官方给定背景、题目和选项。
- 除 `none` 外，对比 `no_index` 与 `semantic_index`。运行前预先约定：完整模型请求逐字相同时复用同一个回答，避免把同 prompt 的模型波动误算为索引效果。300 条组别结果实际调用 198 次，复用 102 次；其中 2 题 Core 上下文为空，三组请求相同。
- QA 只提交未绑定 Episode 的 Situation 与 exact Delivery，不回写答案、Episode 或 Outcome。每次选择使用独立 attempt ID，避免恢复运行时误把 Context 缓存当成实时延迟。
- QA 前后及逐题检查记忆版本、Basis 和 Job 快照不变。测试前索引 outbox 为 0 pending，2,310 个 canonical 投影的文本哈希与真实远端 membership 全部核对通过。

## 真实向量检索配置

MySQL 8.0.46 作为权威数据库；本地 Chroma 1.5.5 为可重建投影，ONNX Runtime CPU 执行 `all-MiniLM-L6-v2`，384 维，cosine 检索。模型归档 SHA-256 为 `913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3`。

长文按真实 tokenizer 的 220 wordpieces 分块、重叠 32，避免默认截断只索引 Episode 开头。按最小分块距离聚合到唯一文档，tenant／agent／relationship 精确过滤。距离只作本地诊断，不由语言模型输出，也不是概率或权威记忆字段。

这不是 Chroma 官方 Memory Core Adapter，也不是生产性能认证。它是通过现有 gRPC MemoryIndex 协议运行的实验 provider；其 3 项测试实际执行 embedding、gRPC 与临时 Chroma，覆盖语义改写召回、跨 owner 隔离、长文尾部、去重及替换／删除分块。

## 最终回答没有改善

| 组别 | 正确 / 100 | 无效答案格式 | 非空长期上下文 | 平均记忆 tokens |
|---|---:|---:|---:|---:|
| 无长期记忆 | 34 | 1 | 0 | 0 |
| Core，无索引 | 31 | 0 | 98 | 136.73 |
| Core，真实语义索引 | 31 | 0 | 98 | 136.73 |

100/100 的两组 Core 上下文逐字相同；索引组与无索引组回答一致来自预先声明的同请求复用，并非两次独立模型采样的巧合。

Core 相对无记忆的差值为 −3 个百分点，按用户聚类的配对 bootstrap 95% 区间约为 [−10, +5] 个百分点。样本不足以宣布稳定退步，更不能宣布领先。索引开关的样本差值为 0，其 [0, 0] 区间只是相同输入／复用回答的必然结果，不代表我们精确测定了所有语义系统的零效应。

本机 Observe→Select→格式化／Delivery 的 p50/p95 耗时，无索引为 21.2/34.0 ms，有索引为 83.8/107.1 ms；不包含回答模型，也不是单独 Select RPC 的延迟。离线检索诊断在 QA 完成后运行，没有并发干扰这些查询时间。

不能把本次 31% 与旧 pilot 34% 直接比较来判断提示词修复的效果：两次是不同用户与题目。这里的有效对照是同一批 100 题内的各组。

## 证据召回确实提高

另外固定一个离线诊断：只在评分时读取官方 `related_conversation_snippet`，用仅归一化空白的、连续消息精确匹配映射到实际 Episode ID。89 题可以唯一对齐，11 题未匹配，单独报告并排除在这项诊断分母之外；未用模糊匹配或模型猜来源。100 题最终 QA 分母不受影响。

两种排序使用同一 owner、同一 canonical Episode 投影原文。字符比较器直接调用生产 Go `selection.DefaultRanker` 的 bigram multiset Dice；语义比较器调用真实远端索引并保留 Episode 的顺序。Recall@k 指每题所标注 Episode 集合被前 k 个 Episode 覆盖的比例，再对 89 题取平均。

| 离线证据指标，n=89 | 字符排序 | Chroma/MiniLM |
|---|---:|---:|
| Recall@5 | 6.7% | 33.1% |
| Recall@10 | 12.9% | 43.3% |
| Recall@20 | 19.1% | 57.7% |
| Recall@40 | 31.3% | 74.5% |
| 前 10 个 Episode 至少命中一个标注 Episode | 22.5% | 46.1% |

这是来源证据诊断，不是官方 PersonaMem-v2 答题分数，也不是 Mem0 对照。诊断会搜索完整 owner 文档后筛选 Episode 排序；生产查询是 mixed-kind top 40，二者不混算。

另从 QA 的真实索引日志读取 mixed-kind top 40：89 题中 70 题至少检索到一个标注 Episode。全体 89 题中，39 题存在标注 Episode→active Recollection 的 Basis 边，37 题的最终投递含有这样的 Recollection。只看实际检索命中的 70 题，其中 30 题存在该边、29 题投递了该关联，另外 40 题没有该边。

这些边只证明显式来源关联，不证明压缩文本包含了正确细节，也不把没有边解释为绝对没有相关语义。

## 断点在哪里

1. **语义索引仅扩展候选，不决定最终排序。** `SelectMemory` 合并 canonical 与 indexed candidates 后，仍调用字符 ranker。索引距离与名次不会进入最终排序。本批全部 Recollection 已在 canonical 候选内，因此索引无法增加可选记忆。
2. **检索到 Episode，不等于原文会进入回答上下文。** 当前索引的 Episode 命中只沿 Basis 找 Recollection／Disposition；没有关联的原文不会直接投递。40 个“检索命中但无 active Recollection 关联”的问题因此无法靠重新排列现有 Recollection 补足这条证据路径。

代码锚点：`internal/storage/mysql/memory_context.go` 的 `SelectMemory`、`loadIndexedSelectionCandidates`，以及 `internal/core/selection/selection.go` 的 `bestSupportPath` 与排序。

下一轮更值得验证的是：在相同 token 预算下，如何让已检索到的相关 Episode 原文成为可投递证据，而不是要求所有有用信息必须先压缩成 Recollection。本轮没有修改这项输出约定，也没有重做选择器或加入在线生成式 AI；这些应成为单独的下一项改动与消融实验。

## 后台与成本

84 次后台调用均 `finish_reason=stop`，84 个 Job 均 attempts=1。最终生产 Worker 规范化后，再经真实 Go parser／提供引用检查：83 个非空窗口、1 个主动 NO_CHANGE、278 个合法变化块、0 个语法或提供引用拒绝。

278 个变化包含 155 个新 Recollection、106 个 KEEP、17 个既有 Recollection 的 TEXT。其中 15 个 TEXT 与原版本完全相同，按既有幂等逻辑不创建版本；2 个实际修订。因此最终有 157 个版本、155 个 active Recollection，而不是把 278 个变化误报成 278 条新记忆。没有输出 NEW_DISPOSITION；本次不评估人格倾向形成、修订或反馈闭环。

| 成本类别 | 真实模型调用 | 输入 tokens | 输出 tokens |
|---|---:|---:|---:|
| 后台巩固 | 84 | 1,225,751 | 57,771 |
| 回答 | 198 | 278,053 | 34,732 |

token 用量来自实际服务响应，不估算成货币价格；本地 embedding 不计入语言模型 tokens。

准备阶段遇到 Hugging Face 429，顺序下载及有限退避后完成。首次并发 intake 遇到 MySQL `ABORTED` 后停止；随后以并发 1、相同请求幂等键和有限重试恢复，没有删除已写数据或改 Core。QA 没有记录请求错误；无效答案格式照官方规则计错。

## 产物与验证边界

- 原始输入、回答、冻结快照、模型用量、索引日志与诊断脚本在本地 `.cache/semantic-recall/`，上游对话文本不随仓库分发。
- 公开 manifest 固定数据／模型指纹与实验脚本哈希；summary 包含测试前投影证明及原始产物哈希；CSV 不含对话、偏好文本或回答全文。此实验尚未封装成正式的一键开源 runner，不能把这些本地研究脚本宣称为已发布 Adapter。
- 4 个来源对齐合成单测和 3 个真实 gRPC／VDB 单测通过；独立只读审查未发现 Critical／Important。后台规范化复核不额外调用模型。
- 完成前重新运行 `make verify-worker verify-eval` 与 `go test ./...` 均通过：41 个 Worker 测试、83 个 Python eval 测试、3 个 TypeScript 测试及 Go-to-Python smoke；11 个 live 测试按 gate 默认跳过。另只读复算 300 条官方成绩、100 对上下文、公开 CSV／哈希，并核对 20 位用户冻结状态、200 个 Context 和 196 个非空 Delivery。
- 这里只评估单个模型、单个 reference harness、32k 历史、20 位新用户。没有运行 Mem0、完整 PersonaMem-v2、Chorai 人格陪伴或 Seed 的反馈闭环，不作相应性能声称。

本轮完成的是“后台可靠性修复后，隔离验证真实语义召回，并确定为什么没有形成回答收益”，不是“记忆系统已经优化到高分”。
