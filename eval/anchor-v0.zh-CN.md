# ANCHOR v0 因果评测

ANCHOR v0 用一个固定 Reference Harness 比较 `none`、`recollection_only`、`seed_enabled` 三臂，回答两个问题：Memory Core 是否保留普通长程事实轨迹，以及 Seed 是否进一步改变长期人格连续性、关系适应和关系修复。

## 数据边界

- Trajectory 使用 AnchorBench commit `41bd0e20b9524ce484db301ac15dc14121bf06ad` 的 3 个公开样例 bank、15 道选择题，只安装 12 个 allowlist 文件，不下载 `user_profile.json`。
- 上游数据采用 `CC-BY-NC-4.0`。本仓库的代码仍采用 Apache-2.0；运行者必须分别遵守数据许可。
- 6 个 Behavior checkpoint、因果 evidence 与单分数 rubric 是 Memory Core 的扩展，不是 AnchorBench 官方题目。
- 上游没有可供本项目调用的官方 hidden evaluation service。因此这里的结果不能称为官方 ANCHOR public development-set score，也不能与隐藏集成绩混写。

## 最小工作流

```bash
make eval-anchor-v0 ANCHOR_ARGS="install-source --output .cache/anchor-source"
make eval-anchor-v0 ANCHOR_ARGS="prepare --source .cache/anchor-source"
make eval-anchor-v0 ANCHOR_ARGS="smoke --source .cache/anchor-source --output .cache/anchor-v0-smoke"
```

`prepare` 只验证 revision、逐文件 hash、15+6 数量和三个 Behavior 维度。`smoke` 不读取模型 key，使用固定 fake Memory/Answer/Judge 跑完 63 个回答和 18 次盲评，以验证 Harness、数据隔离、三臂和恢复契约；它没有模型效果含义。

真实 dev 必须使用独立空数据库，并固定 Worker、Answer、Judge 为 `MiniMax-M2.5@api.minimaxi.com-2026-09-08`。Trajectory Answer、Behavior Answer 与 Behavior Judge 三类评测调用的 provider 生成预算均为 1,024 tokens，后台 Worker 保持 8,192 tokens；该模型把内部推理计入 `max_tokens`，而 Trajectory/Judge 的可见正文仍严格只接受一个 token：

若 provider 以 `finish=length` 返回不完整 completion，Answer/Judge 会用完全相同的 prompt、模型与 1,024-token 预算最多尝试 5 次，并保留每次 usage。内容过滤、tool call、空正文或非法单 token 仍立即判为 Harness/Judge contract 失败，不会通过反复采样挑选答案。

```bash
make eval-anchor-v0 ANCHOR_ARGS="run \
  --source .cache/anchor-source \
  --output .cache/anchor-v0-dev \
  --run-ref anchor-v0-dev-20260914"

make eval-anchor-v0 ANCHOR_ARGS="summarize \
  --source .cache/anchor-source \
  --output .cache/anchor-v0-dev"
```

运行所需环境变量为：`MEMORY_EVAL_DATABASE_URL`、`MEMORY_CORE_ENDPOINT`、`MEMORY_CORE_TOKEN`、`MEMORY_EVAL_MODEL`、`MEMORY_EVAL_MODEL_REVISION`、`MEMORY_WORKER_OPENAI_MODEL`、`MEMORY_EVAL_WORKER_OUTPUT_TOKENS=8192`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`MEMORY_EVAL_CORE_REVISION`、`MEMORY_EVAL_WORKER_REVISION`、`MEMORY_EVAL_INDEX_REVISION`、`MEMORY_EVAL_CORE_TIMING_PROFILE=quiet5s-indexpoll100ms-preconsolidation-settle-timeout-v2`。API key 不进入 manifest、结果或报告。

## 判定

每个 probe 使用独立 relationship owner；历史按真实 session 截断，每个目标只执行一次 `Select`，三臂由同一个冻结 `MemoryContext` 派生，测试请求不写 Delivery 或 Outcome。相同 bank 前缀只在完整 evidence、正文和 memory state 等价时使用 eval-only Worker 内容寻址缓存；它仅对 Core 生成的 Episode、Session、Source、Actor 与 memory ref 做保持相等关系的 alpha-renaming，并规范化无语义的候选顺序，不合并数据库 owner，也不改变 Seed。正文、角色或 Session 相等关系不同都会强制 cache miss。Answer 生成完成后才读取正确选项或 Behavior rubric；Judge 看不到 arm、memory ref 或检索元数据。

eval-only MemoryIndex 会先把 Episode 投影里的 Core-owned Episode、Session、Source、Actor 标识替换成固定占位符，再进行分块和嵌入；之后读取 owner 内全部已存向量并计算精确 cosine 距离，不使用近似 HNSW 查询，在距离相同时以规范化正文 SHA-256 解并列。opaque ref 不进入向量语义，也不参与 Related Episode 集合的选择。该行为同时冻结在 Index revision 与 Harness source hash 中。

Core 的评测时序同样属于 Harness 契约：MemoryIndex projector 每 100ms 轮询，consolidation quiet period 为 5s；每个 session 在等待 consolidation 前，Harness 使用与完整 settle 相同的调用方 timeout 等待该 owner 的待投影操作归零。这样当前 Episode 必然先成为可检索证据；超时直接归为 `data_isolation_or_harness`，不以重试或近似结果掩盖调度竞态。

结果首先验证四类 Seed lifecycle，然后统计：

```text
formed -> selected -> rendered -> behavior_changed -> score_improved
```

最终只允许一个结论：`holdout_ready`，或一个按因果顺序定位的 `bottleneck`。公开 dev 只用于判断是否值得进行一次性 holdout，不用于反复调参；`internal/`、`worker/`、`api/`、`gen/` 的运行前快照会被冻结，结果恢复时重新校验。
