# Worker 来源边界：程序绑定，模型加工文本

用户已确认实施内部接口调整；本规格记录该确认，不另开一次设计审批。

## 目标与边界

让 Go 已知的来源结构无歧义地到达 Python，并提供不依赖固定 persona／来源位置的文本材料读取与正文绑定接口。解决原文含 `SITUATION / ACTOR / SOURCE` 时无法可靠反解 `window_text` 的问题。

不修改公开 API、数据库 schema、Core 写入资格、Seed 规则或 Chorai。不新增付费模型调用，不部署或重启已有评测服务，不改动原基线。默认 Worker 的模型输入和单次调用行为保持不变；本次不启用新的批量巩固策略，不声称改善答题准确率。

## 内部数据流

`CoreStore 快照 → WorkerRequest.Evidence → gRPC evidence → Python 材料读取／正文绑定`

- 保留原有四个请求字段与唯一 RPC；新增可选 `WindowEvidence evidence = 5`。未携带时表示旧请求，不通过解析旧文本补造来源结构。
- `WindowEvidence` 包含 ordered `episodes` 和有资格的 `outcomes`。
- 每个 `EvidenceEpisode` 携带 `episode_ref / session_ref / origin / sources`。`origin` 为 `current / related / revision_anchor`，明确区分新形成可用的当前／相关经历与仅限反馈解释的锚点。
- 沿袭 Ledger 的身份定义：`session_ref` 可以为空；Source 身份在当前 owner 下由 `session_ref + source_ref` 确定，role 属于 Episode link。同一不可变 Source 可以关联多个 Episode，不把合法共享来源或不同 session 下的同名 ref 误判为重复。
- 每个 `EvidenceSource` 携带 `source_ref / role / actor_kind / actor_ref / text`；role 仅为实际可见的 `situation / agent_act`。原文逐字保留，不解析其中的控制行。
- 每个 `EvidenceOutcome` 携带 `outcome_ref / episode_ref / actor_kind / actor_ref / text`；只复制当前旧路径已经判定有资格、实际向 Worker 展示的 Outcome。
- 当前经历、相关经历、实际展示的 revision anchors 按现有顺序传递。同一 Episode 重复出现时保留一次，优先 current、再 related、再 revision_anchor；不得带入 direct-only Seed 隐藏的 anchors、普通 outcome links 或不具资格的 Outcome。
- 结构化来源参与请求字节预算和重新读取快照时的 hash 检查；两种数据库实现一致。没有新持久状态。

## Python 使用方式

新增独立的 `source_materials.py`，提供：

```python
materials = recollection_materials(request)
material = materials[0]
# material.text 和 material.context_text 为纯文本；身份和 refs 留在对象字段。
tagged_text = material.bind_recollection("用户希望周末留出家庭时间。")
```

`recollection_materials` 自动遍历当前完整 Episode 中的 USER Situation，不读问题、答案或隐藏 persona，不使用临时别名。完整 Episode 必须具有 Situation 和实际 AgentAct；来自 related／revision_anchor 的材料不会被当成新增来源。上下文使用当前＋related 的完整可见 Situation／AgentAct 原文，逐行引用、明确角色；不把不可用于新 Recollection 的反馈锚点或 Outcome 混入该形成材料。其他 actor 的 Situation 保留为上下文，不自动作为用户事实焦点。

模型用的上下文标题只含经历类别与角色／actor kind，不插入 Episode、Source 或 actor 的数据库 ID；原文本来含有的 ID 仍原样保留。身份字段和 Basis 留给程序。

返回不可变的材料快照，包含真实 source/episode 身份及程序绑定的 current＋related Episode refs。这是所读窗口级出处，**不是逐句最小证据或语义正确性保证**。

绑定只支持本次明确的 `NEW_RECOLLECTION / OTHER / TEXT`，且必须获得该 Target 和所读 Episode refs 的许可。`NO_MEMORY` 返回 no-op；空值、混合 NO_MEMORY、模型生成的协议块、代码围栏、超长正文拒绝绑定。先检查原始正文中的协议控制行，再由程序将普通换行归一为空格，输出一行正文；遵守 Core 的 4 KiB TEXT／64 KiB 结果限制，不能由正文注入 Target／Basis。已有记忆修订、去重、Seed 以及新策略启用不属于这个辅助接口。

新材料读取器遇到缺失结构、无效身份／角色／origin、重复 Episode／link、同一作用域 Source 的内容或 actor 冲突、越界 Basis refs 时显式拒绝；旧默认 Worker 仍可接受原四字段请求。程序字段按既有 Ledger 身份约束读取，只有进入 tagged-text 的 Episode Basis 额外遵守其语法。结构化消息缺失不得被当作空白可信证据或从原文猜测。

## 验证

1. 两种数据库的真实请求构造函数：控制行正文不改变来源身份；current／related／anchor 和 Outcome 过滤一致；材料原文不变。
2. 相同旧窗口文本而来源划分不同，新 evidence 和请求 hash 必须不同。新增结构超过预算必须被计入。
3. Go → 真实本地 gRPC → Python 读取材料 → 程序绑定正文；返回值由实际 Go parser 解析。仅使用本地固定文本模型，不连接提供商。
4. Python 缺失／无效输入、不可变快照、来源绑定与格式边界；旧模型输入保持逐字一致。
5. 生成绑定无漂移、公开协议不变、相关 Go/Python 测试通过。另测发布包与现有无模型 Worker smoke。

完成的是来源传递与文本绑定基础，不是整个 Memory Core 效果目标。
