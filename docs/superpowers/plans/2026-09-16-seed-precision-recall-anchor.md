# Seed 精度、召回与 ANCHOR 复验计划

> 本计划在当前脏工作树直接执行，不创建提交，也不清理既有改动。

**目标：** 先通过冻结的 4 正 4 负形成门，再运行无天花板的 ANCHOR 人格增量诊断。

**约束：** 不增加生产 LLM 调用；不改公共协议或 schema；PostgreSQL 与 MySQL 行为一致。

**状态：** 2026-09-16 已完成。形成门为 `4 / 4`、事实型错误 `0`、负对照 `0 / 4`；
改良 ANCHOR 中 3 / 4 checkpoint 可操纵且 learned 为 3 / 3，剩余一题仍有 Oracle 天花板，
因此结论是评测瓶颈而非架构失败。

---

### Task 1：双视图秩融合

**文件：**

- 修改 `internal/core/consolidation/memory_index.go`
- 修改 `internal/core/consolidation/memory_index_test.go`
- 修改 `internal/storage/postgres/disposition_formation_candidates.go`
- 修改 `internal/storage/mysql/disposition_formation_candidates.go`
- 修改两套 formation integration tests

**TDD：**

1. 先写失败测试：完整视图把 distractor 排第二、情境视图把第二个目标排第二，融合后两个目标必须占前二。
2. 运行最小 Go 测试，确认 RED 来自缺少秩融合。
3. 实现共享的稳定 ordinal-rank fusion，并让两套 Store 发出完整视图与情境视图查询。
4. 测试查询失败回退、去重、非 Episode 过滤与 limit。
5. 运行 core 单测和两套数据库最小 integration tests。

### Task 2：纯事实拒写规则

**文件：**

- 修改 `worker/python/src/memory_core_worker/disposition_materials.py`
- 修改 `worker/python/tests/test_disposition_materials.py`

**TDD：**

1. 先写失败测试，要求 focused 输入明确区分 durable fact 与 response strategy。
2. 确认 RED 后，只修改现有 `_FORMATION_RULES`，不新增调用或分类 Job。
3. 运行 Worker 最小测试并验证 service 调用数不变。

### Task 3：同步语义文档

**文件：**

- 修改 `README.md`
- 修改 `docs/architecture.zh-CN.md`
- 修改 `docs/memory-index.zh-CN.md`
- 修改 Chorai 的 `docs/rules/agentos-core-contract.md`
- 修改 Chorai 的 `docs/index/tasks/memory-read-assembly-path.md`

记录双视图只改变候选顺序，Outcome 仍决定因果资格而不是相似身份。

### Task 4：冻结 4+4 live gate

1. 新建隔离数据库、索引和输出目录；使用同一冻结数据、模型与预算。
2. 先 smoke，再完整执行和 summarize。
3. 逐条读取 tendency 与 exact BASIS，不以“存在任意 Seed”代替语义核对。
4. 通过标准：目标 `4 / 4`、纯事实错误 `0`、负对照 `0 / 4`；否则停止 ANCHOR 并报告具体断点。

### Task 5：改良 ANCHOR Reference Harness

**文件：**

- 修改 `eval/python/src/memory_core_eval/anchor_effect.py`
- 修改 `eval/python/src/memory_core_eval/anchor_runner.py`
- 修改相应测试、eval 文档与 manifest

**TDD：**

1. 先写失败测试：事实型 checkpoint 不计入 Seed 增量；companion checkpoint 必须先通过 Oracle-vs-Anti 和 Oracle-vs-RAG。
2. 将绝对 0/1/2 增量结论替换为盲双向 pairwise；保留 trajectory/Recollection 基础结果。
3. Reference Harness 明确 Recollection/Disposition 的不同用途，但不把 tendency 改写成命令。
4. smoke 验证 arm 隔离、label 隔离、调用计数与恢复契约。

### Task 6：live ANCHOR 与客观结论

1. 在新隔离状态上用当前 Core 跑完整 ANCHOR。
2. 依次报告 formed、selected、rendered、Oracle 操纵检验、learned-vs-RAG。
3. 若 Oracle 检验失败，结论是评测或 Harness 尚不可辨识；若 Oracle 通过但 learned 无稳定增益，结论才是 Seed 文本形成/架构瓶颈。
4. 运行 `make verify`、Worker/SDK/eval 验证与 PostgreSQL/MySQL 官方脚本；记录所有失败与限制。
