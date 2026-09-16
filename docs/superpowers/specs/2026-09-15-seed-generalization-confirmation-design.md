# Seed 独立复验与自然化长对话泛化设计

日期：2026-09-15
状态：用户已通过持续 Goal 批准

## 要回答的问题

1. 上一轮 8 例优势能否在生产 Worker 路径、全新数据库和全新模型调用中独立复现；
2. paired formation 是否只记住了四种开发模式，还是能处理未见回应模式；
3. 在长历史、无关经历和 durable facts 共存时，Seed 能否形成而 Recollection 不被吞掉；
4. 冲突、失败或不具单一模式的长期窗口是否保持不形成。

## 两项冻结实验

### A. 原 v1 独立复验

不改 `eval/seed-essential-delayed-v1.json`、四臂、门槛、顺序或 Judge。使用新的空 MySQL、
新的 Chroma collection、新 evaluation ref、生产 Worker（无 eval flag）、不复用 Worker/Answer/Judge
缓存。仍以 `learned_seed` 对 RAG 至少 6 胜、RAG 0 胜为通过。

### B. 未见模式的自然化合成长对话 holdout

在任何真实调用前冻结 4 个未见正模式与 4 个负对照。每例 8 个历史 session，即 24 条
Situation/AgentAct/Outcome 消息等价；三段目标经历被五段无关但自然的事实、任务和情绪交流
隔开。正例同时包含可形成 Recollection 的 durable facts。

正例沿用 RAG / Learned / Oracle / Anti 四臂和双向 Judge；负例只检查学习状态，不生成答案。
数据仍称“自然化合成”，不冒充真人长对话 benchmark。

预注册通过门：

- 正例至少 `3/4 formed -> selected -> rendered`；
- Learned vs RAG 至少 3 个 clear win，RAG 为 0；Oracle 至少 3/4 支配 Anti；
- 四个负例均不得形成 Disposition；
- 每个正例至少形成一个 Recollection，证明两种 Memory 在同一后台链共存；
- 任何输入、输出、Judge 或状态缺失均为 harness failure，不从分母删除。

## 结论边界

- A、B 同时通过：只称“初步跨模式与长历史泛化证据”；
- A 通过、B 不通过：开发模式过拟合或长历史证据组织失败；
- A 不通过：优先判为形成/回答/Judge 方差，不能声称稳定优势；
- 负例形成：persona drift 风险，不能进入默认生产配置。

无论结果如何，不据此声称真实长期陪伴、统计显著、跨 provider 稳定或优于 Mem0。
