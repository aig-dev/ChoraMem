# Memory Core Eval V0

四模式 Eval V0 评测的对象始终是：

```text
Memory Core + 固定 Reference Harness + 固定文本模型
```

它不把 Harness 或模型效果误写成 Core 的单独分数，也不新增 Memory RPC、表或学习规则。场景和结果使用 JSONL；模型只接收、返回纯文本。MemoryAgentBench 是独立的检索/Recollection 测试路径，不混入四模式成绩。

公开个性化数据集入口见 [PersonaMem-v2 评测说明](./personamem.zh-CN.md)。它沿用官方文本选择题评分，同时单独实现 persona 级历史回放、确定性等待与只读测试阶段，不使用下面 V0 的固定 5 秒等待。

Disposition 的专项证据见 [PERMA Seed 因果效应评测](./perma-seed.zh-CN.md)。它把长期陪伴
效应与 `Delivery -> AgentAct -> Outcome` 因果资格分成两个门，不把普通检索分数当成 Seed
闭环成立。

CUPID 的冻结三组长期陪伴评测见 [CUPID Seed 增量效应评测](./cupid-seed.zh-CN.md)。它在
persona 隔离的 dev/H1/H2 上比较无记忆、Recollection-only 与 Seed-enabled。

[ANCHOR v0 因果评测](./anchor-v0.zh-CN.md)进一步把公开长程 Trajectory 与 6 个 Memory Core
Behavior checkpoint 放进同一三臂 Harness，检查人格连续性、关系适应、关系修复及
`formed -> selected -> rendered -> behavior_changed -> score_improved` 漏斗。公开 dev 与
Behavior 扩展都不冒充官方隐藏集分数。

[ANCHOR companion-disposition v1](./anchor-companion-v1.zh-CN.md)把上述结论拆开：公开
Trajectory 只承担长期 Recollection／状态轨迹基础能力；自定义 companion checkpoint 才比较
learned Disposition 与相同 Recollection RAG。每题必须先通过 Oracle-vs-Anti 与
Oracle-vs-RAG 的双向操纵检验，未通过的题不能用于架构判决。

[Seed-essential Delayed Eval v1](./seed-essential-delayed.zh-CN.md)使用 8 个合成、隔离的延迟实例，
比较同一 Select 派生的 RAG、Learned Seed、Oracle Seed 和 Anti Seed。它专门区分“架构注入本身
没有额外价值”与“架构可行但 Seed 形成/选择尚未工作”，不把小样本开发门称为 benchmark 成绩。
Outcome 形成路径的受控复验与负结果见
[2026-09-15 实验报告](./reports/2026-09-15-seed-essential-delayed-v1-outcome-formation.zh-CN.md)。

`Seed Generalization v1` 是自然化合成的确认集，不是公开自然对话 benchmark。它在首次真实模型
调用前冻结 4 个未见正例与 4 个负对照；每例含 8 个交错 session（3 个信号、5 个干扰），并复用
Delayed Eval 的四臂回答与双向 Judge。确认门要求：正例至少 3/4 的 Learned Seed 明确胜过 RAG、
Oracle 操纵成立、4 个负例均不形成 Disposition，且正例中 Recollection 与 Disposition 能并行存在。

```bash
make eval-seed-generalization GENERALIZATION_ARGS=prepare
make eval-seed-generalization GENERALIZATION_ARGS='smoke --output .cache/seed-generalization-smoke'
# 启动一组全新 Core/Worker/Index 后：
make eval-seed-generalization GENERALIZATION_ARGS='run --output .cache/seed-generalization-live --run-ref seed-generalization-live-001'
```

真实确认运行必须使用生产 Worker 默认路径；旧的 `--paired-disposition-formation` 只保留为历史消融
入口，不得用于本确认集，否则会把同一形成机制包裹两次。

## 固定流程

Core 模式使用同一条公开生命周期：

```text
回放历史 SourceEvent
  -> 等待后台巩固
  -> Observe 当前 Situation
  -> Select MemoryContext
  -> 注入纯文本并记录 exact Delivery
  -> 调用模型
  -> Observe 实际 AgentAct
  -> 仅在场景明确提供外部结果时 ReportOutcome
```

每个场景、模式使用隔离的 relationship scope，避免评测组之间互相熏习。四组共用同一模型、instructions、场景与输出预算，只改变长期上下文：

| 模式 | 注入内容 | 是否依赖 Core |
|---|---|---|
| `none` | 无长期记忆 | 否 |
| `episode_rag` | 历史 Episode 的确定性字符 3-gram top-k | 否 |
| `recollection_only` | Constitution + Recollection | 是 |
| `full_core` | 完整 `MemoryContext` | 是 |

Episode RAG 不读取 Recollection 或 Disposition。Core 不可用时，两个 Core 模式会输出错误结果，不会伪装成 RAG。空 Context 不写 Delivery；空模型输出不写 AgentAct。主要指标是场景 `expected_output` 与模型输出去除首尾空白后的 exact match；未命中是有效成绩，运行错误才令命令非零退出。

## 离线发布门

```bash
make verify-eval
```

该命令不需要模型 key，会在临时 Python 环境里执行四模式、生命周期顺序、RAG 隔离、结果格式、失败语义和 MemoryAgentBench contract test，并构建 Eval wheel。它已包含在 `make release-gate` 中。

## 真实模型评测

先安装本地 SDK 与 Eval：

```bash
python3 -m venv .venv
.venv/bin/pip install -e ./sdk/python -e './eval/python[live]'
```

为避免为 Eval 增加管理 RPC，V0 固定等待后台巩固 5 秒。运行 Eval 的 Core 应设置 `MEMORYD_CONSOLIDATION_QUIET_PERIOD=1s`，并保持其余 Core 配置一致。

```bash
export MEMORY_CORE_ENDPOINT='http://127.0.0.1:8081'
export MEMORY_CORE_TOKEN='tenant-scoped-jwt'
export MEMORY_EVAL_MODEL='your-openai-compatible-model'
export MEMORY_EVAL_SCENARIOS="$PWD/eval/cases/v0.jsonl"
export OPENAI_API_KEY='...'

make eval-live | tee eval-results.jsonl
```

可选 `OPENAI_BASE_URL`。`MEMORY_EVAL_REF` 可固定一次评测批次；省略时生成新值，以免重复运行复用已冻结的 run。结果逐行记录 `evaluation_ref`、Harness/version、model、prompt version、mode、scenario、output、exact match、注入字符数、耗时与错误。

## 同条件双 Harness Matrix

OpenAI Agents 与 Vercel AI 只承担一次纯文本模型调用；四模式调度、Memory Core 生命周期和结果格式仍由同一个 `ReferenceHarness` 实现。一次命令只加载一份 [`profiles/v0.json`](./profiles/v0.json)，因此两边固定使用同一 model、scenario set、instructions、constitution、输出预算与有序四模式。

```bash
python3 -m venv .venv
.venv/bin/pip install -e ./sdk/python -e './eval/python[openai-agents]'

export MEMORY_CORE_ENDPOINT='http://127.0.0.1:8081'
export MEMORY_CORE_TOKEN='tenant-scoped-jwt'
export MEMORY_EVAL_MODEL='gpt-4.1-mini'
export OPENAI_API_KEY='...'
export AI_GATEWAY_API_KEY='...'

make eval-matrix | tee eval-matrix-results.jsonl
```

`make eval-matrix` 会构建固定版本的 Vercel AI Runner，并显式把 profile 路径和 Runner 路径交给调度器。任一 Runner 缺失、返回空文本或执行失败时，该次运行失败，不降级到另一个模型接口。

## MemoryAgentBench Adapter

Adapter 对齐 [MemoryAgentBench commit `fe1735de8cf8b9908e1e3d3b5612afc815698062`](https://github.com/HUST-AI-HYZ/MemoryAgentBench/tree/fe1735de8cf8b9908e1e3d3b5612afc815698062) 的入口：

```python
send_message(message, memorizing=False, query_id=None, context_id=None)
```

`memorizing=True` 时，每个 chunk 写成一个完整 Episode：chunk 是 Situation，Adapter 实际返回的固定文本 `"Memorized"` 是 AgentAct。该 AgentAct 不调用模型，也不是伪造 Outcome；它让后台 consolidation 能合法形成 Recollection。query 时执行 Select、纯文本注入、exact Delivery、模型调用与实际 AgentAct，返回上游要求的 `output`、`input_len`、`output_len`、`memory_construction_time`、`query_time_len`。

将它作为上游 `AgentWrapper` 的一个 delegate 使用，并为每个 benchmark context 传入唯一 `benchmark_context_ref`。由于上游 memorizing 调用不携带 `context_id`，持久 scope 不能等到 query 时再决定。`memory_factory` 和 `model_factory` 会在 Adapter 自有的同一 event loop 中运行，避免 `grpc.aio` channel 跨 loop；使用完应调用 `close()` 或使用 context manager。

MemoryAgentBench 没有提供可信外部 Outcome，所以 Adapter 永远不调用 `ReportOutcome`。因此它适合评估 Recollection/检索，不足以证明 Disposition 的形成、重演、修订与抑制；后者由本目录的 causal-loop 场景承担。每次运行的 metadata 会固定记录 `capability_scope=retrieval_recollection` 与 `disposition_causal_loop_evaluated=false`。

独立运行官方小样本：

```bash
python3 -m venv .venv
.venv/bin/pip install -e ./sdk/python -e './eval/python[memoryagentbench]'

export MEMORY_CORE_ENDPOINT='http://127.0.0.1:8081'
export MEMORY_CORE_TOKEN='tenant-scoped-jwt'
export MEMORY_EVAL_REF='mab-eventqa-001'
export OPENAI_API_KEY='...'

make eval-memoryagentbench
```

若运行环境无法连接 Hugging Face，可显式提供官方数据文件：

```bash
export MEMORY_AGENT_BENCH_DATA_FILE='/path/to/Accurate_Retrieval-00000-of-00001.parquet'
```

Runner 只接受已登记文件名且 SHA-256 与官方 LFS 对象一致的文件；它仅替换下载层，上游的数据筛选、切块、模板和指标实现保持不变。

对于默认会消耗输出预算进行推理的兼容模型，可显式设置
`MEMORY_AGENT_BENCH_REASONING_EFFORT=none`。该值只作用于回答问题的模型调用；后台
consolidation Worker 应通过自己的 `MEMORY_WORKER_REASONING_EFFORT` 独立配置。

启动器会把官方仓库缓存到 `.cache/MemoryAgentBench`，强制检出固定 commit，并默认运行 `Eventqa_64k.yaml` 的一个测试 query。运行前应启动 Memory Core、consolidation Worker 与向量索引；同一份 benchmark 状态必须持续使用同一个 `MEMORY_EVAL_REF`。

一次确实形成并投递 Recollection 的官方 LongMemEval smoke 结果及其失败解释，见
[2026-09-05 MemoryAgentBench 基线](./reports/2026-09-05-memoryagentbench-smoke.zh-CN.md)。
