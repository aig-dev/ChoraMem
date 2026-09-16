# 生产 Worker 因果配对形成设计

日期：2026-09-15
状态：用户已通过持续 Goal 批准

## 目标

把已经通过开发门的 outcome-backed Disposition 因果配对形成从 `eval/**` 移入参考
Worker，同时保留一个 Core `ConsolidateWindow` Job、一个内部 RPC 和一份原子 tagged-text
结果。新 Seed 形成不能吞掉同一窗口中的 Recollection、既有 Seed 反馈或直接 ADAPT。

## 最小机制

不增加表、公共协议、内部 RPC、scheduler Job 或人工节点。Core 继续发送已有
`window_text + WindowEvidence + allowed refs`。参考 Worker 在同一 RPC 内执行三个语义 lane：

1. 统一 tagged-text lane：处理既有 Recollection、ADAPT、Seed 反馈及其他合法变化；
2. 因果配对 lane：只在 Core 已开放 `ELIGIBLE_NEW_DISPOSITION`，且 current/related
   Episode 全部具有不同 session、实际 AgentAct 和配对非 Agent Outcome 时，判断一个新的
   `NEW_DISPOSITION TEXT`；
3. 现有 Recollection-only 后备 lane：统一结果没有处理 Recollection 时继续执行。

这些是同一无状态 Worker 请求内的文本加工，不是三套持久 Pipeline。调用可以顺序执行；
“并行存在”指语义职责互不覆盖，不增加运行时调度状态。

## 来源与输出边界

- Episode、actor、session、Outcome 配对与允许的 Basis 来自 typed `WindowEvidence`；不从用户
  文本猜 ID。
- `window_text` 只读取 Core 自己渲染的形成资格、APPLICATION、当前 Constitution 与已有
  Disposition 正文。模型看不到任何 ref。
- 因果配对模型只看 `CONSTITUTION / EXISTING_DISPOSITIONS / EXPERIENCES`，每份
  EXPERIENCE 为 `USER_CONDITION / AGENT_RESPONSE / USER_OUTCOME`。
- 模型只能返回一行倾向或 `NO_CHANGE`。Worker 绑定 `TARGET / APPLICATION / BASIS`，Core
  仍验证语法、scope、版本、因果资格和原子提交。
- 若窗口同时提供 direct ADAPT，新形成 lane 不接管 `NEW_DISPOSITION`；直接权威优先。
- 因果配对 lane 接管一个窗口的 outcome-backed `NEW_DISPOSITION TEXT` 后，统一模型产生的
  同类块必须机械移除，避免重复 Target；其他块保持不变。

## 失败语义

- 缺少 typed evidence、完整配对、不同 session 或权限：不启用该 lane，保留原统一路径。
- focused 输入超出 Worker 预算：不接管形成，保留原统一路径。
- focused 模型返回截断、空白、多行、协议标签或超长正文：RPC 失败并沿现有 Job retry，不能
  冒充 `NO_CHANGE`。
- focused 返回 `NO_CHANGE`：该窗口不接受统一模型对同一 outcome-backed evidence 的宽泛
  NEW_DISPOSITION 推断，但其他 Memory 变化仍可提交。

## 验收

- 同一请求可以同时返回一个合法新 Disposition 与一个或多个新 Recollection。
- existing Recollection 修订、direct ADAPT 和 Seed feedback 行为不回退。
- 正配对形成；缺失、负面、冲突配对不形成；已有同义 Seed 返回 no-op。
- 模型输入不含 Episode、Outcome、Seed 或 Constitution ref。
- MySQL/PostgreSQL 与协议均不需要变化；Core 最终 parser/transaction 继续是唯一权威。
