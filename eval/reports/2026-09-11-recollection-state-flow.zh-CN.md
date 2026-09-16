# 固化状态流诊断：主瓶颈仍是新记忆形成

**结论：Core 能接纳并选择合格的新 Recollection，但目前缺少已有 Recollection 的撤回操作。后者是真实能力缺口，却不足以解释本轮大量空更新。** 本次没有修改生产代码或调用模型，不声称固化效果已改善。

## 真实 MySQL 的五项特征测试

通过 Go overlay 复用现有 CoreStore contract factory，运行未修改的 MySQL Adapter、迁移、事务和 Select。Worker 是确定性文本实现；索引是受控 Provider，返回真实 Episode ref，正文仍由 Core 从关系库重建。因此这不是 embedding、Chroma 或真实模型效果测试。

| 场景 | 实际结果 |
|---|---|
| 同窗含独立背景和另一项遗忘请求 | Worker 给出有合法来源的外交工作／育儿背景后，Core 正常 FORM，并能 Select；遗忘请求不会自动拒绝全窗新内容 |
| 已有偏好由红茶改为绿茶 | `TEXT` 产生新版本，旧版本不再作为 active Recollection 返回；索引队列包含删旧／添新操作 |
| 用 `INHIBIT` 撤回已有 Recollection | 语法合法，但不满足该对象的操作资格；空 receipt，原版本仍可选择 |
| 用空 `TEXT` 撤回已有 Recollection | 语法不合法；空 receipt，原版本仍可选择 |
| 合法新背景与非法撤回放在同批 | 整批 no-op，合法新背景也丢弃；同 Job 重放复用空 receipt，不再调用 Worker；新 Job 仅提交相同合法块则成功 |

另一个重要边界：第二项完成语义修订后，启用历史 Episode Evidence 仍能返回旧红茶原文。**修订 learned view 不等于删除历史来源，也不保证历史证据不再出现。** 不能只增加一个 Recollection 状态就宣称实现了完整遗忘。

整批原子性、同 Job 冻结复用、Recollection 仅支持 `KEEP / TEXT` 都是现有架构明确约定，不把特征测试的通过包装为修复，也没有擅自改成逐块放行。

## 原始 Worker 记录的分布

只读复核 `.cache/personamem-effect-v2/worker-v4/windows.jsonl`，以原始输入含 `SOURCE personamem-effect-minilm-validation-all-v1:` 限定本轮 validation。6,829 行中有 6,731 条属于该范围：3,295 条没有 output，3,436 条有 output；其中 2,825 条为 `NO_CHANGE`，611 条为其他输出，与先前语法诊断计数一致。

以下两个标记只检查 `WINDOW_BEGIN` 和 `WINDOW_END` 之间的原始窗口，不把提示词内的术语算成已提供的目标。

| 窗口字面包含 `please forget` | 已提供 existing Recollection target | NO_CHANGE | 其他输出 |
|---|---|---:|---:|
| 是 | 是 | 81 | 27 |
| 是 | 否 | 2,734 | 579 |
| 否 | 否 | 10 | 5 |

**2,744 / 2,825 = 97.13% 的 NO_CHANGE 没有提供已有 Recollection 更新目标。** 因此，“补上已有对象撤回操作”不能直接解决绝大多数空更新所处的新形成路径；下一步仍应围绕内容形成和固化职责。

限制：这是 attempt 数，含重试，不是独立 Job 或 persona 数；“未提供已有目标”不等于证明数据库中完全没有该用户的记忆；`please forget` 是字面检索，不是语义或作者归属判断。无该字面的对照很少，不能由此断言遗忘请求造成了空更新，也不能认定每条 NO_CHANGE 都错误。

## 验证、隔离与清理

实际执行：

```bash
MEMORY_TEST_MYSQL_DATABASE_URL='root:state-probe-only@tcp(127.0.0.1:56885)/?parseTime=true&multiStatements=true&loc=UTC' \
go test -overlay=.cache/recollection-state-v1/overlay.json -count=1 -v \
  ./internal/storage/mysql -run '^TestRecollectionStateProbe$'
```

结果：五个子案例全部 PASS，包测试退出码 0，耗时 2.363 秒。上述口令只属于本次一次性合成测试容器，不是产品凭据；端口是当次动态分配结果，容器已清理，不能直接照抄命令连接已不存在的服务。

- 独立容器：`memory-core-state-probe-20260911`，`mysql:8.0.46`，无挂载；没有接触 baseline 或产品数据库。
- 五个 `memory_contract_*` namespace 均由 factory 清理；退出前 SQL 确认剩余数量为 0。
- 容器以 `--rm` 创建，随后 `docker stop` 完成且只读检查确认容器已不存在；移除的仅是本次合成测试数据。
- 原 Core／Worker／Index 进程仍在；没有重启或部署，也未运行端口冲突的全套 MySQL 脚本。
- 本次模型调用为 **0**；九道已免跑 validation 题未运行。

| 证据文件 | SHA-256 |
|---|---|
| `.cache/recollection-state-v1/probe.go` | `630e02ada955ee23cf25ee2a3aaeff4d654bd7dc4c3e767500bbb9bd690cc41d` |
| `.cache/recollection-state-v1/mysql_test.go` | `7c20f12af6a2366cba6cc3e1bcb817841ff9add1d4c12806b1aadecd953b8d90` |
| `.cache/recollection-state-v1/overlay.json` | `50b8bcc0a15211af8f3aa9d9645b8ac3d516fce21743676c6bb1df0c77690b85` |
| 原始 `worker-v4/windows.jsonl` | `8f07f9a264deb2811ac2f62285f11b52338715946be3579d8fd0d29d1dfeb28e` |

## 下一步边界

结合[两阶段实验](2026-09-11-recollection-two-pass.zh-CN.md)及之前已停止的候选，目前没有通过来源语义门槛、值得上线的固化修复。不能继续重复提示词、窗口或角色变体，也不能将 raw Episode 改名为 learned memory 来制造收益。

需要重新讨论现有“一个联合处理器同时整理内容并裁决全部更新”的职责边界。待讨论的方向是让 Recollection 有独立的文本加工路径，Core 继续拥有操作资格和状态提交；这不是把有错误的旧草稿直接落库，也不是再加一轮笔记后交回同一个联合裁决器。是否改变内部 Worker 职责、如何保留来源与跨种类去重、如何限制新增调用成本，尚未冻结或实现。

公共协议、Seed 因果资格、数据库状态机和 Chorai 主线保持原样。Goal 的基线与漏斗已有产物，但最大固化瓶颈修复及隔离端到端收益仍未完成。
