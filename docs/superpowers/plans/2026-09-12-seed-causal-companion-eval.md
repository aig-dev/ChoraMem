# Seed 因果闭环与长期陪伴评测实施计划

> 本计划只改独立 `memory-core`。先完成可复算评测，再依据漏斗证据决定是否修改 Core。

**目标：** 在冻结的 PERMA H1/H2 holdout 上比较 `none`、`recollection_only`、`seed_enabled`，并用真实生命周期场景验证 Seed 只有在 Delivery、AgentAct、Outcome 完整时才修订或抑制。A 已用于开发诊断，不进入正式结论。

**边界：** 在线 Select 无模型；Worker 只处理纯文本；不增加 owner decision、权重或 benchmark 特例；不改 Chorai。

---

### Task 1：固定 PERMA 数据入口

**文件：**
- 新增 `eval/python/src/memory_core_eval/perma_seed_data.py`
- 新增 `eval/python/tests/test_perma_seed_data.py`

**TDD：**
1. 先写 synthetic tree/raw-dialogue/timeline/meta fixtures，覆盖 pinned revision、H1/H2、10-task hash 选择、只接收成对 Type 2/3、日期排序、隐藏字段不进入公开对象、manifest resume 漂移失败。
2. 运行 `pytest tests/test_perma_seed_data.py`，确认因模块/API 缺失而失败。
3. 实现最小 loader/downloader；正式文件下载后记录 SHA-256，不导入上游代码。
4. 重跑该测试至通过。

### Task 2：固定三模式与配对统计

**文件：**
- 新增 `eval/python/src/memory_core_eval/perma_seed_effect.py`
- 新增 `eval/python/tests/test_perma_seed_effect.py`

**TDD：**
1. 先测试同一个 `MemoryContext` 被确定性过滤为三组；两组 Core 使用同一 Select，2,048-token 总预算下只记录真正渲染 refs。
2. 测试 option token 精确评分、question-hash 组顺序、按 persona 整簇 bootstrap、H1/H2 独立胜出和最终 gate。
3. 确认 RED，再实现最小纯函数并确认 GREEN。

### Task 3：实现只读 PERMA Reference Harness

**文件：**
- 新增 `eval/python/src/memory_core_eval/perma_seed_runner.py`
- 新增 `eval/python/tests/test_perma_seed_runner.py`

**TDD：**
1. 用 fake Memory API/模型测试：时间线只增量录入一次；session 对应 window；问题只在冻结日期后执行；每题只 Select 一次；三模式无 Delivery/AgentAct/Outcome 写回；调用顺序确定且可 resume。
2. 确认 RED 后实现 ingestion、settle、Select、渲染、模型调用、逐题冻结和状态快照。
3. 增加 formed/selected/rendered/flip 漏斗字段，所有 refs 和 token 数可追溯。

### Task 4：实现数据 preflight 与 CLI

**文件：**
- 新增 `eval/python/src/memory_core_eval/perma_seed_cli.py`
- 新增 `eval/python/tests/test_perma_seed_cli.py`
- 修改 `eval/python/pyproject.toml`
- 修改 `Makefile`

**TDD：**
1. 测试 `prepare` 只下载冻结 persona 的必要文件并冻结 manifest；运行拒绝参数、模型、revision 或文件 hash 漂移；`summarize` 只接受完整三模式结果。
2. 确认 RED，随后接入现有 OpenAI-compatible text model、Memory Client 和 disposable DB snapshot helper。
3. 新增 `memory-core-eval-perma-seed` 与 `make eval-perma-seed`。

### Task 5：实现真实 companion lifecycle gate

**文件：**
- 新增 `eval/python/src/memory_core_eval/seed_lifecycle.py`
- 新增 `eval/python/tests/test_seed_lifecycle.py`
- 视需要新增 `eval/cases/seed-lifecycle-v1.jsonl`

**TDD：**
1. 先写四种场景：正链、无投递、无 Outcome、完整负反馈链。
2. 测试精确记录渲染 refs、真实 AgentAct 与非 Agent Outcome；只有完整链可改变 Seed；延迟复测必须观察到改变。
3. 确认 RED 后实现最小 runner。场景只含文本与固定选项，不含模型 confidence 或 judge。

### Task 6：加入离线发布门和中文说明

**文件：**
- 修改 `scripts/verify-eval.sh`
- 新增 `eval/perma-seed.zh-CN.md`
- 修改 `eval/README.zh-CN.md`

**步骤：**
1. 将所有 synthetic contract tests 纳入 `make verify-eval`。
2. 文档记录冻结参数、prepare/run/resume/summarize 命令、报告字段和不能声称的结论。
3. 运行目标测试及 `make verify-eval`。

### Task 7：先跑冻结评测，再决定 Core 改动

**产物：**
- `eval/reports/2026-09-12-perma-seed-effect.zh-CN.md`
- 外部数据、逐题结果、模型 cache 与 Worker 原始结果只放 `.cache/` 或被忽略的运行目录

**步骤：**
1. 下载并校验 pinned PERMA 必要文件；只做 schema preflight，不人工查看语义内容。
2. 用固定服务和参数依次运行 H1、H2；传输恢复只复用相同内容寻址请求。
3. 汇总 `formed -> selected -> rendered -> correct flip` 和 H1/H2/pooled paired effect。
4. H1/H2 是最终 holdout；看到结果后不得再修改并重跑同一 cohort。失败则本轮结论不通过。

### Task 8：回归与完成审计

**步骤：**
1. 若 Core/Worker/Select 有改动，按原参数重跑 PersonaMem A/B；否则校验相同 revision 的既有 frozen artifacts 与回归测试。
2. 运行相关 Go/Python tests、`make verify-eval`、`make release-gate`。
3. 对设计文档七条通过门逐项绑定文件、测试、运行结果和报告证据；缺一项不标记完成。
