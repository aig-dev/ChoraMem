# ChoraMem 公开冻结与 Chorai 主线接入设计

日期：2026-09-16
状态：已批准，待实施计划

## 目标

1. 将当前独立 Memory Core 的完整实现冻结为公开仓库
   `aig-dev/ChoraMem`，默认分支为 `main`，首个版本为
   `v0.1.0-alpha.1`。
2. 将当前 `codex/adaptive-seeds` 上的已验证实现完整合入独立仓库的
   `main`，不丢失尚未提交的新文件或评测证据。
3. 将 Chorai 主仓库继续保持在 `main`，改为引用公开身份
   `github.com/aig-dev/ChoraMem`，并让本地 workspace 指向同级
   `../ChoraMem`。
4. 使用现有 MiniMax 配置完成一次真实 Chorai 聊天验证，证明仓库更名与
   module 迁移没有破坏聊天和 Memory Core 接入。

## 当前边界

- `/Users/luffylu/Documents/Chorai/memory-core` 是独立 Git 仓库，只是位于
  `Chorai` 父目录下；它不是 `Chorai_Go` 内嵌目录。
- `/Users/luffylu/Documents/Chorai/Chorai_Go` 已在 `main`，并通过
  `go.work` 的同级 checkout 与 `github.com/chorai/memory-core` module 使用
  独立 Core。
- 公开后，`/Users/luffylu/Documents/Chorai/ChoraMem` 和
  `aig-dev/ChoraMem` 成为唯一活动真源；旧 `memory-core` checkout 只保留为
  可恢复的本地发布前历史，不再被 Chorai workspace 引用。
- 不把 Chorai 产品代码、数据库、环境文件、用户数据或模型凭证复制进
  ChoraMem。

## 发布方式

采用干净公开快照，而不是公开现有本地 Git 历史：

1. 在旧独立仓库中完成 README、module identity 与发布文档修改，提交当前
   全部项目文件，并将功能分支快进合入本地 `main`。
2. 从该 `main` 导出不含 `.git`、`.cache`、`.env*`、模型输出、数据库和
   下载数据的干净树到同级 `ChoraMem`。
3. 在 `ChoraMem` 初始化新的 Git 历史，以一个经过验证的根提交作为公开
   `main`。
4. 创建公开 `aig-dev/ChoraMem`，推送 `main`，打不可变 annotated tag
   `v0.1.0-alpha.1`，并创建明确标记为 pre-release 的 GitHub Release。

这样既保留本地开发历史用于追溯，又不会把旧命名、探索分支或潜在历史敏感
内容发布到新的开源仓库。

## 项目身份

- 品牌名：`ChoraMem`
- GitHub：`github.com/aig-dev/ChoraMem`
- Go module：`github.com/aig-dev/ChoraMem`
- Python 包、TypeScript 包和公开协议中的现有 `memory_core`／
  `@chorai/memory-core` 技术标识保持不变，避免无必要的协议与生态破坏。
- `memoryd`、Proto package、RPC service 和数据库 schema 名称保持不变；
  本次是仓库与 Go import identity 迁移，不是运行时协议迁移。

## README 结构

README 使用中文作为主语言，并在开头提供简短英文摘要。正文按以下顺序组织：

1. ChoraMem 是什么，以及它解决的不是普通聊天记录检索问题；
2. 项目状态：Research Alpha；
3. 有限主张与明确非主张；
4. 唯识启发的工程对应：种子、现行、熏习、连续性与我执边界；
5. `SourceEvent -> Episode -> Recollection/Disposition -> Select -> Delivery
   -> AgentAct -> Outcome -> 下一次巩固` 的因果闭环；
6. Recollection、Disposition、Soul／Constitution、Harness 的权责边界；
7. Core、Worker、SDK、关系库与 MemoryIndex 的模块架构；
8. PostgreSQL／MySQL 8、Chroma／Milvus／pgvector／Qdrant 等可替换边界；
9. Docker 快速启动与四方法公共协议；
10. Go、Python、TypeScript SDK 和 Harness Adapter；
11. 当前评测证据与限制；
12. 成熟度、待证伪假设与路线图；
13. 开发验证与 Apache-2.0 许可证。

README 不声称实现完整八识，不声称已优于 Mem0 等框架，也不把小样本
ANCHOR 结果写成人格陪伴优势。它会明确区分：

- 已实现的工程机制；
- 已通过的确定性／集成测试；
- 有限实验中的初步信号；
- 尚未得到外部同协议验证的研究假设。

## Chorai 接入迁移

Chorai 主仓库只做公开身份所需的机械迁移：

- Go imports 与 `chorai-go/go.mod` 从
  `github.com/chorai/memory-core` 改为 `github.com/aig-dev/ChoraMem`；
- `go.work` 使用同级 `../ChoraMem`；
- 部署脚本、规则和索引文档中的 canonical repository 文本同步更新；
- 不复制 Core 状态机，不改变 Chorai 的业务编排、MemoryContext 装配或数据库
  所有权。

Chorai 当前 `main` 上已有独立 Core 接入。这里不重新实现 Memory Core，只验证
更名后的 SDK／协议依赖仍然成立。

## 验证与发布门

### ChoraMem

1. 对即将提交和即将发布的文件树执行凭证与大文件检查；
2. `make release-gate` 全部通过；
3. 从 GitHub 新地址重新 clone；
4. 在 clone 中执行 `make verify-standalone`，证明不依赖父目录或旧 checkout；
5. 检查 default branch、公开可见性、tag 与 release 都指向同一 commit。

### Chorai

1. `go work sync` 后确认没有旧 module identity；
2. 运行受影响 Go package tests 和现有 Memory Core integration tests；
3. 启动新的同级 ChoraMem、Chorai Go backend 与 Python Agent；
4. 使用现有私有环境文件中的 MiniMax 配置，不复制或输出凭证；
5. `GET /health` 返回 200；
6. 通过真实聊天入口完成一个隔离 conversation，获得非空最终回答；
7. 日志与持久状态证明聊天没有因 Memory Core Observe／Select／Delivery 链路失败；
8. 只有上述验证通过后，提交并推送 Chorai `main`。

## 安全约束

- `.env`、`env/`、`.cache/`、数据库文件、模型缓存、上游 benchmark 下载内容和
  原始用户数据不得进入公开仓库。
- 扫描输出不得打印任何凭证正文；发现疑似凭证时只报告文件和类别。
- 不删除旧 checkout，不重写 Chorai 已发布历史，不强推远端。
- 若 GitHub 会话未认证，代码与本地提交继续完成；创建远端时暂停等待一次登录，
  不用其他凭证绕过。

## 完成标准

- `https://github.com/aig-dev/ChoraMem` 为公开仓库；
- 默认分支 `main` 与本地验证 commit 一致；
- `v0.1.0-alpha.1` pre-release 指向该 commit；
- README 如实表达设计、机制、证据和限制；
- ChoraMem release gate 与独立 clone gate 通过；
- Chorai `main` 使用新 module identity，并已推送；
- 使用 MiniMax 的真实聊天返回成功，且 Memory Core 接入没有致命错误。
