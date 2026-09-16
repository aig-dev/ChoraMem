# PersonaMem-v2：跨窗口个性化评测

目标是检验历史对话形成的记忆能否改善隐含偏好回答，不把个性化选择题成绩解释为 Agent 人格成长或完整反馈学习。

## 先判断哪里没有产生效果

旧四组只能回答“当前整条链路有没有净增益”，不能判断无增益来自哪里。新增 validation 六组使用同一题、同一选项、同一回答模型，逐层替换长期上下文：

| 模式 | 要回答的问题 |
|---|---|
| `none` | 没有历史或完整用户画像时，模型本身能答多少？ |
| `full_history` | 完整长上下文是否真的包含可利用信号？ |
| `oracle_episode` | 精确给出官方相关原文时，回答路径能否利用记忆？ |
| `semantic_top40` | 同一 MemoryIndex 的前 40 个 Episode 丢掉了多少 Oracle 效果？ |
| `current_core` | Core 的选择与预算装配又丢掉多少？ |
| `learned_core` | 只留 Recollection/Disposition 后，后台固化保留了多少？ |

同时记录相关原文 `source_available -> indexed -> selected -> delivered -> answer_correct`。其中主漏斗只跟踪真实 Episode；Basis 关联另行统计，不能把“引用过来源”冒充为压缩文本仍保留答案细节。最终只按最大的实测差距选择一个下一步瓶颈，不在结果前先改 rerank、固化规则或回答 prompt。

总体准确率和配对差值按题计算，95% 区间按 persona 整簇重采样。只有区间下界大于 0 的正差距才会触发 Core 优化；若只是点估计较大但区间仍跨 0，报告没有测得稳定 Core 瓶颈，不拿噪声指导改造。

这套诊断使用 2,061 题 validation，不与 OmniMemEval 的 5,000 题公开横评分数混排。完整 history、Oracle 和 semantic top-40 不施加 Core 的在线预算；`current_core` 与 `learned_core` 共用一次 Select 和同一 2,048-token 总预算。Oracle 文本只进入隔离组，不进入 Core、Worker、索引或其他回答组。

六组均排除历史开头的官方 system `expanded_persona`。它是数据生成用的完整用户画像，不是用户声明的配置，也不是 Agent 的 Soul；固定数据中 2,061/2,061 行的 `expanded_persona` 都逐字存在于该 system 文本。若把它放进 `none`，控制组就已经拿到本应由长期交互学习的用户信息。`full_history` 因而只渲染真实观察到的 user/assistant 对话。

六个回答组按 question identity 的 SHA-256 确定性交错调用，避免长时间运行中的模型服务漂移固定偏向某一组；逐题产物仍按固定模式顺序保存。相同完整模型请求使用内容寻址缓存，因此没有 Episode evidence 时，`current_core` 与 `learned_core` 不会因重复采样产生伪差异。

首轮真实结果见 [2026-09-05 实测报告](reports/2026-09-05-personamem-v2.zh-CN.md)：100 题中完整 Core 34%、给定完整用户画像但无长期记忆 39%，尚未观察到稳定增量。由于四组共同看到了官方完整画像，这不是新的六组诊断中的真正 `none`，不能用来判断自然长对话记忆是否有效。报告同时公开成本、失败窗口与逐题成绩，不把分数提高作为硬编码验收条件。

后续见[后台输出可靠性修复](reports/2026-09-06-worker-reliability.zh-CN.md)与[真实语义召回隔离实验](reports/2026-09-06-semantic-recall.zh-CN.md)：新一组 100 题中，语义检索改善了来源 Episode 的离线召回，但现有 Core 的投递上下文未变，开关索引均为 31%。不同用户批次的分数不能直接作为提示词修复的因果对照。

Recollection 形成与 Select 修复后的冻结 A／未调参 B 配对结果见
[2026-09-12 learned_core 效果报告](reports/2026-09-12-learned-core-recollection-effect.zh-CN.md)。
该实验只支持“在固定 MiniMax-M2.5 Harness 下，压缩后的 learned memory 对两个小型
persona 隔离样本产生可重复正向收益”；它不是 PersonaMem 全量成绩，也不证明完整
人格演化或跨模型普适性。

Episode evidence 的同一冻结样本配对实验见[2026-09-06 Episode 原文证据报告](reports/2026-09-06-episode-evidence.zh-CN.md)。该报告负责记录实际投递覆盖、成绩、错误、延迟和模型服务用量；机制完成不预设成绩必须提高。

## 2026-09-13 阶段冻结

[50-persona 确认性复验](reports/2026-09-13-personamem-learned-core-confirmatory.zh-CN.md)
中，`learned_core - none` 为 +2.56pp，persona-cluster bootstrap 95% CI 为
`[-5.03pp, +10.60pp]`，没有复现稳定正收益。

自该报告起，PersonaMem 不再用于提示词、抽取、ranker、阈值或 Seed 机制调参，只作为
冻结版本的回归检查。下一阶段验收转向陪伴关系轨迹 eval；这里的选择题成绩不再承担
“长期人格或关系演化有效”的代理指标。

## 固定的比较

旧四组使用相同回答模型、官方题目、选择题选项、完整初始用户画像和输出预算。它们只测“画像已知时的增量”，保留用于历史复核，不再承担自然长对话记忆的效果判定。默认选择 20 个用户、每人 5 题，共 100 题；按固定 seed 的哈希顺序抽样，不按答题结果选择样本。完整文本测试集为 200 个用户、5,000 题。

| 模式 | 额外长期上下文 |
|---|---|
| `none` | 无；仍能看到官方历史自带的初始用户画像 |
| `episode_rag` | 现有 Reference Harness 的字符 trigram top-8，保留完整 Episode，限制在 2,048 tokens 内 |
| `recollection_only` | 严格只注入 Core 选择的 Recollection |
| `full_core` | Core 选择的 Recollection + Disposition；显式启用时再含 Episode evidence |

这是既有 RAG 基线，不是强 embedding RAG，也不是 Mem0 对照。`episode_evidence_max_bytes` 默认 0，因而默认四模式语义不变；公开 opt-in 需要真实 `MemoryIndex` 才可能返回证据。结果必须记录实际索引配置，不能推广成其他配置或框架的成绩。

官方 benchmark 是 200 个 persona、总计 5,000 题，但每人实际为 6–42 题，并非固定 25 题。下方旧 runner 的 `personas/per-persona` 参数只适合固定抽样；完整公开横评应使用 OmniMemEval Adapter，不能用 `--per-persona 25` 冒充全量。

## 数据与评分边界

- 官方数据：`bowen-upenn/PersonaMem-v2`，revision `0622e56d1cc6f1bc990a5100a6ec4022a60e66a6`。
- 官方评分代码：`bowen-upenn/PersonaMem-v2`，commit `dd52429f83ced4394be46c3849186a423942b2a5`。
- CSV 与评分源文件校验固定 SHA-256；所选历史先与固定 revision 的独立 Git blob 指纹核对（LFS 文件使用内容 SHA-256），再记录本地 SHA-256。已有缓存也必须通过上游指纹和已冻结 manifest 检查；准备阶段因此需要访问上游元数据。
- 旧四组会共享历史开头的官方 system 用户画像；六组诊断和 OmniMemEval Adapter 均排除它。CSV 中的 `preference`、`related_conversation_snippet` 等标签只进入隔离诊断或评分，不进入 Core、Worker、索引或普通回答组。
- 仅使用三段校验后的官方方法：选项生成、答案解析、正确性判断；不运行上游构造函数、训练代码或数据生成代码。不重新实现一个更宽松的评分器。
- 上游通过 Python `hash()` 决定选项排列；入口固定 `PYTHONHASHSEED=0`，逐题保存选项映射。正确答案只用于构造匿名选项与评分，不作为正确性提示传给回答模型，更不进入 Core。
- 沿用上游 query 的个性化提醒及 MCQ 指令，但把完整历史替换为各模式的受限上下文。因此这是官方文本 MCQ 的 memory-system 适配，不是对官方 full-context baseline 的原样复现。

PersonaMem-v2 明确把 `personal_email`、`creative_writing`、`translation` 等场景正文中
“隐含提到”的内容作为 persona preference。框架得分因而同时衡量两件事：长程取回，以及
是否愿意把代写、润色或翻译材料解释成用户本人属性。Memory Core 保留更严格的来源归属：
这类内容可以成为“用户曾围绕该内容交流”的可修订 Recollection，但不能仅因出现在引文中
就升级为用户事实或长期偏好。最终报告必须把这个策略差异写明，不能为追分取消来源边界，
也不能反过来把全部差距都归咎于 benchmark。

## 读写流程

```text
同一用户的有序历史
  → 连续同角色消息合并，保留原文
  → 每 28 个完整对话 Episode 为一次输入批次
  → 等待该 owner 的真实 consolidation jobs 完成
  → 后续批次可利用前批次形成的记忆与 Core 的窗口重叠
  → 冻结学习状态，回答该用户的测试问题
```

数据库的 job 上限仍是 32，默认 overlap 为 4；加速评测只把静默期设为 1 秒。窗口不按测试问题或标准答案切分。同一 persona 的历史只形成一套记忆，两个 Core 模式读取同一学习状态。默认预算 0 时，`recollection_only` 仅比 `full_core` 少 Disposition；启用 Episode evidence 时，严格的 `recollection_only` 同时移除 Disposition 与原文证据，因此两者差异不能只归因于 Disposition。

未收到助手回复的历史尾消息保留为不绑定 Episode 的 SourceEvent；不捏造历史回复。这部分可以被原文 RAG 使用，但不会单独触发 Core 巩固，结果会记录此限制。

测试问题也只写入不绑定 Episode 的 SourceEvent，用于公共 `SelectMemory`。实际注入的非空完整条目记录 exact Delivery。测试答案不写回 Episode，不报告虚构 Outcome，不把后一题的答案用于前一题。每个 persona 答题前后对比全部记忆版本、Basis 和 consolidation jobs；任何变化都会使运行失败。

启用证据时，只有 `full_core` 的 Select 携带非零字节预算；`recollection_only` 始终发送 0。Core 返回的冻结 Context 还要经过生产 Python SDK renderer 的统一 2,048-token 总预算，预算覆盖标题、标签、Recollection、Disposition 与引用证据的完整文本；Delivery 只记录真正装入的 refs。`cl100k_base` 是本实验的固定计数口径，任意历史原文（包括形似特殊 token 的字符串）都按普通文本编码，不宣称等于回答服务的计费 tokenizer。

首题前原子保存 `memory-before-{persona}.json`。中断恢复始终与这份原始快照比较，即使所有答题行已经落盘也不能跳过；有答题行却没有原始快照时拒绝恢复。最终汇总必须覆盖 manifest 中的全部题目，并有每个用户通过冻结检查的完成证明，不能把部分样本当作完整结果。

## 运行

从独立 Memory Core 仓库根目录安装：

```bash
python3 -m venv .venv
.venv/bin/pip install ./sdk/python ./worker/python './eval/python[personamem]'
export PATH="$PWD/.venv/bin:$PATH"
make eval-personamem PERSONAMEM_ARGS=prepare
```

六组诊断的数据准备命令：

```bash
make eval-personamem-effect PERSONAMEM_ARGS=prepare
```

可用 `--question-limit 1` 做接线 smoke；这会写进 manifest，不能当作完整 validation。
要做小规模但无用户泄漏的效果评测，使用 `--persona-limit N --persona-sample-salt <frozen-salt>`：
runner 只根据 salt 和 persona ID 的稳定 hash 选择完整用户组，不读题目、标签或
对话内容，并将 persona 列表与 salt 冻结到 manifest。两个限制参数互斥；
`--question-limit` 仍只适合接线，不是正式子集抽样。不设限制时覆盖全部 2,061 题。

正式运行默认最多并行处理 4 个彼此隔离的 persona，可用 `--persona-workers` 调整；该值进入 manifest，只影响耗时，不改变每题的六组 Context 或确定性组内顺序。MySQL 并发事务若返回明确的 gRPC `ABORTED`，runner 会用原请求和原幂等键短退避重试，默认总计 5 次，次数由 `--rpc-attempts` 冻结。其他错误不重试。若回答模型会输出较长推理，应显式提高 `--output-tokens` 并使用新 run/output；截断回答必须视为运行失败，不能当作错误答案吞掉。

如需代理，使用进程级 `HTTPS_PROXY`，同时令 `NO_PROXY=localhost,127.0.0.1`。数据保存在 `.cache/PersonaMem-v2`，不提交原始数据和上游代码。

启动独立评测数据库，不连接产品数据库：

```bash
docker run -d --name memory-core-personamem-eval \
  -p 127.0.0.1:33316:3306 \
  -e MYSQL_ROOT_PASSWORD=memory-core-eval-only \
  -e MYSQL_DATABASE=memory_core_personamem mysql:8.0.46
```

设置自己的模型凭证与 endpoint。以下使用 Chat Completions；参考 Worker 的规则与 tagged-text 输出保持不变。DeepSeek-v4 的适配显式关闭 thinking，两侧 temperature 均为 0；其他模型需确认自己的 endpoint 支持相同参数。

MiniMax 的 OpenAI-compatible 接口默认把 `<think>` 一并放进 `content`，不符合 Worker 的纯 tagged-text 合同。运行 MiniMax Worker 时使用 `--reasoning-split`，让 provider 把 reasoning 放到独立字段；不要在 Core parser 中猜测或裁剪模型思考文本。

```bash
export OPENAI_API_KEY='your-key'
export OPENAI_BASE_URL='https://api.deepseek.com'
export MEMORY_EVAL_MODEL='deepseek-v4-flash'
export MEMORY_WORKER_OPENAI_MODEL='deepseek-v4-flash'
memory-core-eval-personamem-worker --listen 127.0.0.1:18082 \
  --output .cache/personamem-pilot/worker
```

MiniMax 示例在同一命令末尾增加：

```bash
memory-core-eval-personamem-worker --listen 127.0.0.1:18082 \
  --output .cache/personamem-pilot/worker \
  --reasoning-split
```

另开终端启动可复现的评测索引。它是 Chroma 1.5.5 + 本地
`all-MiniLM-L6-v2` 的可丢弃投影；首次运行会下载并校验固定 SHA-256 的
模型归档。Episode 投影中的 Core-owned 标识会在嵌入前替换为固定占位符；
分块固定为 220 tokens、重叠 32，检索会读取 owner 内全部已存向量并计算
精确 cosine 距离，不使用近似 HNSW 查询。这些参数不是 Core 权威状态或
公共协议字段。

```bash
python -m pip install './eval/python[local-index]'
export MEMORY_EVAL_INDEX_TOKEN='eval-index-token'
memory-core-eval-local-index \
  --listen 127.0.0.1:18083 \
  --database .cache/personamem-index/chroma \
  --log .cache/personamem-index/calls.jsonl
```

另开终端运行 `memoryd`（先等待 MySQL 就绪）：

```bash
make build
MEMORYD_DATABASE_DRIVER=mysql \
MEMORYD_DATABASE_URL='root:memory-core-eval-only@tcp(127.0.0.1:33316)/memory_core_personamem?parseTime=true&charset=utf8mb4' \
MEMORYD_HTTP_ADDR=127.0.0.1:18080 \
MEMORYD_GRPC_ADDR=127.0.0.1:18081 \
MEMORYD_INFERENCE_GRPC_ADDR=127.0.0.1:18082 \
MEMORYD_MEMORY_INDEX_GRPC_ADDR=127.0.0.1:18083 \
MEMORYD_MEMORY_INDEX_RPC_TOKEN=eval-index-token \
MEMORYD_AUTH_MODE=trusted_loopback \
MEMORYD_CONSOLIDATION_QUIET_PERIOD=1s \
./bin/memoryd
```

`trusted_loopback` 仅用于这个明确绑定本机的评测实例；部署环境使用正常认证。就绪检查为 `GET /health/ready`。

在拥有模型环境变量的终端运行评测：

```bash
export MEMORY_CORE_ENDPOINT='http://127.0.0.1:18081'
export MEMORY_CORE_TOKEN='eval-loopback'
export MEMORY_EVAL_DATABASE_URL='mysql://root:memory-core-eval-only@127.0.0.1:33316/memory_core_personamem'
export MEMORY_EVAL_CORE_REVISION="$(git rev-parse HEAD)"
export MEMORY_EVAL_WORKER_REVISION="$(git rev-parse HEAD)"
make eval-personamem
```

六组诊断还必须直连与 Core 相同的 MemoryIndex，并冻结 Provider 版本：

```bash
export MEMORY_EVAL_MEMORY_INDEX_ENDPOINT='http://127.0.0.1:18083'
export MEMORY_EVAL_MEMORY_INDEX_TOKEN='eval-index-token'
export MEMORY_EVAL_INDEX_REVISION='chroma-1.5.5:all-MiniLM-L6-v2@913d7300:cosine:chunk220-overlap32:episode-alpha-v1:exact-all-scoped-semantic-tiebreak-v1'
export MEMORY_EVAL_MODEL_REVISION="$MEMORY_EVAL_MODEL"
make eval-personamem-effect
```

Runner 在每批历史后同时等待 consolidation jobs 和 `memory_index_operations` 清空；写入时保存 Core 返回的真实 Episode refs。每题只产生未绑定 query SourceEvent 与两份 exact Delivery，不写答案或 Outcome。每个问题以独立 JSON 原子落盘；相同模型请求按完整 prompt 内容缓存，断点恢复不会把回答模型随机波动记成 memory 差异。任一 persona 的 learned versions、Basis 或投影待处理数在答题阶段变化都会使运行失败。

公开 Episode evidence opt-in 示例：

```bash
make eval-personamem PERSONAMEM_ARGS='run --episode-evidence-max-bytes 16384 --run-ref episode-evidence-01 --output .cache/personamem-episode-evidence'
```

该参数只接受 0–16384，并连同总 token 预算、索引描述和实现指纹写入冻结 `manifest.json`；同一输出目录改变预算会拒绝恢复。非零预算没有真实 MemoryIndex 时不会产生证据。

SQL probe 仅查询 owner 范围内的 jobs、Episode 数与记忆版本；不写数据库，也不新增 Core RPC。代码同时接受 PostgreSQL URL，运行报告应明确实际验证过的 Adapter。

完整 5,000 题使用 [`eval/omnimemeval/README.zh-CN.md`](omnimemeval/README.zh-CN.md) 的固定上游流程。使用不同配置时必须更换 `--run-ref` 和 `--output`。模型服务的同名模型可能更新；保存的答案与调用记录才是这次实验的证据。

## 产物与解释

旧四组 runner 的输出目录包含 `manifest.json`、`results.jsonl`、`summary.json`、回答调用 token 用量以及每个 persona 的记忆快照。六组诊断则保存 `questions/{question_ref}.json`、`answer-cache/`、`answer-usage.jsonl`、`memory-before-{persona}.json`、`memory-{persona}.json` 和最终 `summary.json`。manifest 同时冻结各超时、bootstrap 次数、回答组交错规则以及 Core／Worker／MemoryIndex revisions；改变任何一项都必须使用新输出目录。单独启动 Worker 时指定相同实验目录下的 `worker/`，可保存原始巩固窗口、文本结果与后台 token 用量。

只读复核已完成结果：`make eval-personamem PERSONAMEM_ARGS='summarize --output .cache/personamem-pilot'`。

评分报告包括总体和分类型准确率、无法解析的回答数、调用错误、非空记忆覆盖率，以及按用户聚类的配对 bootstrap 区间。API 错误不伪装成正常错误答案：有错误的模式不给有效 accuracy。无效答案格式则按官方规则计错。

默认零证据预算下，`full_core` 与 `recollection_only` 只有在实际形成并投递 Disposition 时才构成有意义的机制对照。启用证据后，`full_core` 还可能多出 Episode evidence，此时差异不能单独解释为 Disposition 或原文证据的贡献。即使 temperature=0，相同输入的独立模型调用也可能不同。本评测不包含行为后的外部反馈，不能证明 Seed 修订／抑制闭环。

测试：`make verify-eval`。评分提高是实验结果，不是发布门的硬编码通过条件。
