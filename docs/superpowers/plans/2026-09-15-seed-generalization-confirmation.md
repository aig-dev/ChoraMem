# Seed Generalization Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用生产 Worker 独立复验冻结 v1，并在未见模式、长历史、负对照上得到可证伪结论。

**Architecture:** 原 v1 原样复跑；新 holdout 复用 delayed 四臂 Effect/Runner，仅新增严格数据 loader、负例状态检查和聚合结论，避免复制 Harness。

**Tech Stack:** Python 3.11、pytest、Memory Core gRPC、MySQL 8、Chroma、MiniMax Chat API。

**Spec:** `docs/superpowers/specs/2026-09-15-seed-generalization-confirmation-design.md`

## Global Constraints

- 数据和终局门在首次外部模型调用前冻结；之后不得按结果改题仍称同一版本。
- 不调 VDB 参数；不复用旧 Worker、Answer 或 Judge 输出。
- 凭据不进入 manifest、JSONL、报告或命令输出。
- 不提交、不清理当前脏工作区；行为代码严格 RED → GREEN。

---

### Task 1: 冻结自然化 holdout 数据合同

**Files:**
- Create: `eval/seed-generalization-v1.json`
- Create: `eval/python/src/memory_core_eval/seed_generalization_data.py`
- Create: `eval/python/tests/test_seed_generalization_data.py`

**Interfaces:**
- Produces: `GeneralizationCase(expected_formation, history_sessions, probe, labels)`
- Produces: `load_seed_generalization(path) -> GeneralizationDataset`

- [x] 写失败测试：严格 4 正/4 负、每例 8 个不同 session、正例恰有 3 个 signal 和 5 个 distractor、回答输入不含 label/oracle/anti。
- [x] 运行 loader 测试，确认因模块缺失而 RED。
- [x] 写入冻结 JSON 与最小 loader；重跑确认 GREEN 并记录文件 SHA-256。

### Task 2: 复用四臂并汇总负对照

**Files:**
- Create: `eval/python/src/memory_core_eval/seed_generalization_runner.py`
- Create: `eval/python/tests/test_seed_generalization_runner.py`

**Interfaces:**
- Consumes: `prepare_delayed_instance`, `generate_delayed_answers`, `judge_delayed_answers`。
- Produces: `run_generalization_case(...)` 与 `summarize_generalization(...)`。

- [x] 写失败测试：正例生成四臂，负例只读取最终 active disposition；任何缺例均为 harness failure。
- [x] 写失败测试：手工记录分别触发 pass、positive failure、negative false-positive 和 Recollection coexistence failure。
- [x] 运行确认 RED；写最小 adapter/summary；重跑确认 GREEN。

### Task 3: 可恢复 CLI 与验证入口

**Files:**
- Create: `eval/python/src/memory_core_eval/seed_generalization_cli.py`
- Create: `eval/python/tests/test_seed_generalization_cli.py`
- Modify: `eval/python/pyproject.toml`
- Modify: `scripts/verify-eval.sh`
- Modify: `eval/README.zh-CN.md`

- [x] 写失败测试：manifest 冻结 dataset SHA、模型 revision、预算和 Core/Worker revision；resume 拒绝漂移；secret 不序列化。
- [x] 实现 `prepare / run / summarize`，复用现有 model cache 但本实验启动时必须为空。
- [x] 运行新增测试与完整 eval suite。

### Task 4: 两次 live 运行与客观报告

**Files:**
- Create: `eval/reports/2026-09-15-seed-production-confirmation.zh-CN.md`

- [x] 用生产 Worker 在新空存储完整复跑原 v1，离线重算原冻结 summary。
- [x] 再用另一组新空存储运行 holdout，保存 4×4 answers、32 个双向 Judge、4 个负例状态和 Recollection/Disposition 漏斗。
- [x] 运行 `make verify-worker`、完整 eval tests、`make build`、`git diff --check`。
- [x] 报告通过与失败项、调用完整性、数据 SHA 和结论限制；不调整失败题或门槛。
