# Memory Core Engineering Guide

## 项目边界

- 本仓库是协议优先、可独立部署的 Memory Service，不依赖 Chorai 内部包或数据库模型。
- Core 只拥有长期记忆的因果账、写入资格、状态迁移与 `MemoryContext` 输出。
- Harness 拥有 prompt、模型、规划、工具、行动、安全与最终表达；Adapter 必须保持薄。
- 项目只声称是唯识启发的工程模型，不声称实现完整八识、意识、灵魂、业或修行论。

## 设计原则

- 从最小因果闭环出发：记忆被选择，影响行为，结果再改变未来选择。
- 只有在改变存储归属、写入资格、权威或状态迁移时，才增加运行时概念、表或协议字段。
- 只有针对一个明确失败模式才增加复杂度；解释性区别留在文档中。
- AI 只做后台文本加工。默认输出浅层 tagged text；不让模型生成 ID、confidence、strength、权重或长理由。
- Core 在模型外决定 scope、目标资格、幂等、版本、并发与事务提交；普通流程没有人工审批节点。
- 在线 `Select` 不调用生成式 AI；索引与 embedding 是可替换、可重建的选择投影，不是权威事实。
- Seed 是未来理解、关注与回应的潜在倾向，不是 Agent 回复模式复制。一次本人长期要求可 ADAPT；间接推断须两份来源不重叠的经历。Core 校验 actor、owner、版本与资格，仅同一 Worker 判断长期要求语义；Agent 重复输出和沉默不算认可。
- Constitution 是来源绑定、只读的外生 Agent 角色快照，不是用户隐藏档案、Target 或 Basis。AI 仍只输出 `TARGET / APPLICATION / CHANGE / BASIS`，没有分类 Job。

## 代码边界

- `api/`：稳定公共协议与内部 Worker 协议。
- `internal/core/`：纯因果规则与选择规则。
- `internal/storage/`：冻结 `CoreStore`；`postgres/` 与 `mysql/` 是等价持久权威 Adapter，同一进程只选择一个。
- `internal/scheduler/`：后台巩固 Job 的 lease 与 retry。
- `worker/python/`：无数据库权限的参考文本 Worker。
- `sdk/`：协议薄封装与 Harness Adapter，不复制 Core 状态机。

## 变更与验证

- 先读 `docs/philosophy.zh-CN.md`、`docs/architecture.zh-CN.md` 和相关代码测试。
- 修改协议、迁移或状态机时，必须同步更新文档和生成代码；数据库变化只通过 migration。
- 优先做小而可逆的修改，并运行最小相关测试；发布前运行 `make release-gate`。
- 不得为了方便接入而把 Chorai 的运行时对象、内部包或业务语义复制进本仓库。
