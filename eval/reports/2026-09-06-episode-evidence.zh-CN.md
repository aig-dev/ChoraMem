# Episode 原文投递：路径成立，答题收益尚不稳定

本轮完成了一个具体机制：相关 Episode 不必先成为 Recollection，也能经公开 Core 协议、生产 SDK 的总预算装配和 exact Delivery，成为回答模型可见的历史原文。没有增加在线生成式 AI，也没有改变 Seed 的学习资格。

> 2026-09-08 解释修正：本轮沿用的旧四组请求共同包含官方 system 完整用户画像，因而只能验证 Episode evidence 的同请求增量，不能充当自然长对话的无记忆对照。新的六组诊断已排除该生成侧画像。

同一批开发集的正确率从 **31/100 到 35/100**；按用户聚类的配对 bootstrap 95% 区间为 **−5 至 +13 个百分点**。这支持继续验证这条证据路径，但不足以宣布稳定提升、领先 Mem0 或具有人格陪伴优势。

公开产物：[配置与指纹](2026-09-06-episode-evidence.manifest.json)、[完整指标](2026-09-06-episode-evidence.summary.json)、[逐题分数](2026-09-06-episode-evidence.scores.csv)。前一轮见[语义召回隔离实验](2026-09-06-semantic-recall.zh-CN.md)。

## 只改变原文是否投递

- 沿用前一轮已冻结的 [PersonaMem-v2](https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2) 32k：20 位用户、100 道题、2,155 个历史 Episode、84 个已完成 Job。该样本已用于机制诊断，是开发集，不是新的未见测试集。
- 关系库仍为专用 MySQL 8.0.46；语义索引仍为真实 Chroma / ONNX CPU `all-MiniLM-L6-v2`，分块与模型指纹沿用前一轮。运行前核对 2,310 个 canonical 文档的完整远端 membership 与文本哈希，outbox 为 0 pending。
- 两组使用相同的学习状态、Recollection／Disposition 选择规则、索引、官方给定背景、题目、选项和回答模型。只有 `episode_evidence_max_bytes` 不同：关闭为 0，开启为 16,384。
- 两组记忆文本上限均为 2,048 `cl100k_base` tokens，包括 SDK 标题、scope 标签、引用符号、分隔符和正文。这是沿用旧实验的比较单位，不是 DeepSeek 的计费 tokenizer；共同背景、问题与 MCQ 指令不计入这个附加记忆预算。
- 回答模型为 `deepseek-v4-flash`，temperature=0、thinking disabled、输出上限 1,024 tokens。按 question ref 的 SHA-256 交错两组顺序。
- 100 个关闭组的完整请求与旧对照逐字相同，按预先声明的规则复用旧回答；100 个开启组请求全部真实调用模型。该比较不是两组各自重新采样。
- 查询只写不绑定 Episode 的 SourceEvent；答案不写回、不产生测试 Episode 或 Outcome。逐题及结束时比较全部长期版本、Basis 和 Job，学习状态没有变化。没有运行后台 Worker。

实验所用 Core 与 SDK 生产提交为 `a8da4e239f69025fbe997df02e76675b316b3b16`，Core 哈希修复已包含在其中；manifest 另存实际服务二进制与相关生产源码指纹。随后公开 eval 入口的配置接线不改变这次已冻结的请求。

## 实际投递与答题

| 指标 | 原文关闭 | 原文开启 |
|---|---:|---:|
| 正确 / 100 | 31 | 35 |
| 官方解析无效的回答 | 0 | 6 |
| 非空记忆上下文 | 98 | 100 |
| 含实际投递 Episode 的题数 | 0 | 100 |
| 完整 Episode 投递条目总数 | 0 | 613 |
| 平均记忆 tokens | 136.73 | 1,870.01 |
| 最大记忆 tokens | 234 | 2,047 |
| 本机选择工作流 p50 / p95 | 67.2 / 102.8 ms | 92.2 / 108.2 ms |

100 对上下文全部发生变化；两组所选 Recollection 和 Disposition refs 完全相同。613 是按题累计的投递条目数，不是 613 个新 Episode，也不保证跨题唯一。

延迟测量包括 Observe、Select、SDK 装配、准备完整模型请求和 Delivery，不包括回答模型或额外 SQL 核验；这是本机实验工作流耗时，不是生产单 RPC 性能认证。

6 个无效回答直接生成普通对话建议，没有给出官方要求的选择题答案；它们均正常结束，并非输出截断。全部按官方规则计错，没有事后放宽解析、只重答失败题或挑选最优答案。增加原文后，回答格式遵循也是需要诊断的实际问题。

## 相关来源是否真的进入模型

沿用前一轮离线来源对齐：89 题可精确映射到标注 Episode，11 题不能对齐；这些标注只在答题完成后用于统计，不提供给 Core 或回答模型。最终 QA 分母仍为 100。

- 38/89 题的 Core 返回证据至少包含一个标注 Episode。
- 经过 SDK 总预算后，34/89 题仍实际投递了至少一个标注 Episode。
- 标注 Episode 集合的逐题平均覆盖率为 34.3%。这不是官方答题准确率。
- 其中 22 题的已投递标注 Episode，没有通往 active Recollection 的显式 Basis 边。这直接验证了原先缺失的路径：没有先压缩成 Recollection，也可以提供来源证据。

这也说明下一处限制：找到候选不等于全部能进入预算，而“每题有原文”不等于“每题有正确相关证据”。未对齐的 11 题不据此判为召回失败。

## 本次工程变化

1. **Core：** 新增可选 Episode evidence 输出；精确校验 owner、完整 Situation + AgentAct，并排除当前 Situation。复用已有语义查询顺序，跨 lane 稳定交错；最多 8 条、16 KiB，整条放不下就跳过，不截断来源。
2. **快照与兼容：** 两关系库各增加 002 快照表，冻结文本而不是重放时重取来源。零预算保留旧请求哈希，非零预算使用独立哈希域。真实旧库的 100 个历史 Context 已经通过新版服务重放，文本与 refs 不变。
3. **SDK／Harness：** Python 和 TypeScript 共用相同语义的完整条目预算规则；历史原文逐行引用，refs 只进元数据。OpenAI Agents 与 Vercel AI 薄 Adapter 可显式启用；默认关闭，已有调用兼容。引用格式不被宣称为安全沙箱。
4. **学习边界：** 原文 Delivery 不授予任何 Disposition 的重演、修订或抑制资格。本批长期记忆中仍是 155 个 active Recollection、0 个 Disposition，因此本实验不测人格倾向成长或反馈闭环。

## 用量、失败与复现边界

新增 100 次完成的模型调用，共消耗服务返回的 320,005 输入 tokens 和 21,975 输出 tokens；全部 `finish_reason=stop`，无上层模型调用错误。模型调用 p50 / p95 为 2,445.5 / 4,247.7 ms。旧控制组的 100 次回答复用不计入新增用量；本地 embedding 不计入语言模型 tokens。

选择阶段在保存第 71 对结果后，只读 SQL 探针遇到一次新连接握手中断。数据库未重启、未 OOM，最大使用连接数为 4，未达到上限 151；一次诊断重试也失败，随后连接与完整冻结检查恢复。未更改代码、数据库或服务配置，保留既有结果后继续完成。底层网络原因尚未确定，不把它说成已修复的 Core 缺陷。

离线核验重新执行官方选项生成、答案解析和判分，并核对 200 个 Context 快照、198 份非空 Delivery 的 refs、完整模型请求指纹、100 个旧对照请求，以及全部 owner 的冻结状态。

公开 CLI 已提供 `--episode-evidence-max-bytes 16384`，仍须配置真正的 MemoryIndex；默认 0。详见 [PersonaMem 运行说明](../personamem.zh-CN.md)。开启后 `full_core` 与 `recollection_only` 同时相差 Disposition 和原文，不能把差值归因于 Disposition 一项；本报告使用的是单独的原文开关配对实验。

原始请求、模型输出、查询／Delivery refs、快照、用量和配对研究脚本只保存在本地 `.cache/episode-evidence/`，不随仓库分发。公开 manifest 保留其哈希；本地冻结库配对脚本尚不是通用的一键开源 runner，也不是 Chroma 官方 Adapter。

## 发布验证

从 `e2f28776be613d667615dd5c57e8d45e5afa2c75` 的 Git 已提交文件导出，不携带本地缓存、数据集、索引或父工程依赖，再运行 `make release-gate`，完整通过：

- Go 全量测试、race、vet、协议生成漂移、独立性与构建；
- PostgreSQL、MySQL 的真实数据库与 live gRPC 测试；
- Go SDK；Python SDK 45 项、TypeScript SDK 39 项及发布包；
- Worker 41 项及 Go-to-Python smoke；11 项需模型环境的 live 测试按默认门跳过；
- 部署检查；Python eval 96 项、TypeScript eval 3 项与发布包。

TypeScript 生成检查仍有既存的 Node `--localstorage-file` 警告，命令退出成功；不把本次输出描述为完全无警告。

跨模块独立复核通过，没有 Critical／Important；复算了全部 206 个产物哈希、200 个完整请求、官方评分与当前冻结状态。一个非阻塞后续项是把本次已实测的“有历史 Context 的旧库升级重放”固化成可复用的自动化 fixture；当前两方言自动升级测试使用空 001。

实验服务已停止，专用数据库、索引和结果保留；只保留本地分支 `codex/episode-evidence`，没有推送或合并 Chorai。

## 判断

这次解决的是“语义召回的原文能否真正进入有界模型上下文”，不是证明 Memory Core 已经取得高分。下一步更值得在未见样本上检验相关证据选择与回答格式遵循，再谈更大规模的长程个性化评测。没有 Mem0 对照、完整官方榜单成绩或 Chorai 人格陪伴实测，不作相应优势声称。
