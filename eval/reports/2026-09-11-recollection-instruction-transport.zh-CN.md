# Recollection 指令／来源传输分离实验

## 结论

**停止本候选，不合入 Worker。** 原方式与 system/user 分离方式各调用三次，六次均正常返回 `NO_CHANGE`；分离方式保留目标背景 **0/3**，未达到预先固定的至少 2/3 门槛。剩余十七个场景不再执行。

这证明本次调用边界确实改变，但没有恢复这三个长窗口中的 Recollection；不能由此声称 system 消息普遍无用，也没有新的答题成绩。Memory Core 的巩固瓶颈仍未解决。

## 唯一实验变化

完整使用 Task 8 保存的原生产 Worker 输入，不使用其候选合同或旧模型输出。每个场景两臂：

- original：整理规则、完整来源 payload、最终检查全部作为一条 user 消息。
- split：原规则与最终检查作为一条 system 消息，原来源 payload 作为一条 user 消息。

规则文字、Source 内容、canonical WINDOW、allowed targets/basis、模型参数、Worker 正规化和 Go parser 均未改变。最终检查同时移动了位置，因此角色优先级与位置效应不能分别归因。模型仍只输出浅层 tagged text；记录 messages 的 JSON 是实验日志，不是要求模型生成 JSON。

每例以固定 hash 决定两臂顺序；最多两个场景并发，场景内顺序执行。模型为 MiniMax-M2.5，temperature=0，reasoning_split=true，max_tokens=8192，timeout=110 秒，provider retries=0。日志中的 thinking_enabled=false 不是向 MiniMax 发送关闭推理的参数。

## 预写锚点与实际结果

| 完整窗口 | 未撤回、应保留的核心背景 | original | split |
|---|---|---|---|
| 929 | 外交工作、育儿、临时任务挤压家庭时间 | NO_CHANGE | NO_CHANGE |
| 374 | 产品发布工作、青年群体志愿服务、多重承诺造成疲惫 | NO_CHANGE | NO_CHANGE |
| 742 | 退休、膝痛及步行疲劳、行动变化带来的自我感担忧 | NO_CHANGE | NO_CHANGE |

六次 API 均 success / stop，六个请求 hash 唯一，没有运行 adaptive 或 manual 场景。原始输出均为字面 `NO_CHANGE`，现有 Worker 正确将它规范化为空 tagged text。Go grammar/ref 检查 6/6 通过，原因是合法 no-op，**不是形成了六份记忆**。

独立复核逐一确认 `transport.jsonl` 与冻结请求一致：original 只有 user；split 为 system 后接 user。没有内容可供审查，不能把“不输出错误事实”描述成来源或撤回处理正确。

本轮用量：82,836 prompt tokens + 4,521 completion tokens = **87,357 tokens**。单次耗时约 5.3–20.1 秒。没有截断、provider error 或自动重试。

## 实现验证与复核

仅新增 scratch 适配器及测试，复用既有 runner、ChatTextModel、日志恢复、正规化与 parser。17 项定向离线测试通过，覆盖实际 SDK 出站消息、原文不变、来源校验、普通恢复不重复、失败不重试，以及运行时拒绝调用顺序和复用脚本漂移。独立实现审查发现的运行前校验缺口已修复并通过复核；独立语义复核同意停止。

已有 runner 在 provider 接受请求后、terminal 记录写入前崩溃，会留下无法确定是否完成的调用。本次未发生；不得将普通恢复测试描述为网络层 exactly-once 保证，也不得自动重试这种歧义请求。

## 可复核产物

目录：`.cache/recollection-instruction-transport-v1/run-20260911/`。

| 产物 | SHA-256 |
|---|---|
| manifest.json | e9cdf4dd57ecf1e5bfd4d8f4bc1dd0e634520d728485790666e82b96c7756b84 |
| results.jsonl | d82320476d1e7b852043f07379a63cbda4511be7b8169e6ece34fd0df21cb57a |
| attempts.jsonl | 26c18b27887de01d82ee13153476584fadddd4ebeaf235c0f87f4088c3be0f22 |
| transport.jsonl | f2827c66803935be095ece66d9c24e4f56e7446196e04164df1de6282cb02e34 |
| usage.jsonl | bf53a7bb1125b40b394ee210c4fe54353e05e73a56fa394e5cc66bd22ea03bda |

运行进程 native session 50047 已 exit 0。原生产 Worker、基线 manifest、冻结 runner 均保持原 hash；没有数据库写入、部署、服务重启、Chorai 改动或新的端到端效果声明。原 validation 最后九题继续按用户要求免跑。

## 下一步边界

本实验不支持把“移动规则到 system”当作当前巩固缺失的修复。此前纯文本诊断能保留部分背景，却会恢复已撤回内容；当前生产合同又大量 no-op。接下来需要检验 Worker 内部内容整理与记忆归并／行为学习的职责分解，而非继续加规则、换消息位置或用原始 Episode 冒充 learned Recollection。

这只是下一项工程问题，尚未冻结新的生产架构。保持公开协议、Core 提交权威、Seed 因果资格和在线无生成式 AI 路径不变；若试验需要增加后台模型调用，应显式记录新增成本与来源／撤回效果，不能把调用增多本身当成改善。
