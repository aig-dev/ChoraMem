# Memory Core 参考推理 Worker

这是 `memory.inference.v1.InferenceWorker` 的独立 Python 参考实现。每个
`ProcessConsolidationWindow` 仍是一个无状态 RPC。普通旧请求只调用一次统一 tagged-text
模型；具有完整跨 session 因果配对的窗口可增加一次 focused Disposition formation，缺失的
feedback 和 Recollection 各可增加一次后备，因此调用上限为四次，全部发生在后台。

除精确 `NO_CHANGE` 的确定性归一、同一 BASIS 中合法引用的精确去重、focused 正文绑定和
字节护栏外，Worker 不取得写入权威。连续且全为已提供引用的依据列表中，重复 `BASIS` 分隔
标签也可机械合并；Core 仍负责最终语法、资格和原子提交。

若首个非空白行恰为 Core 已提供的 `NEW_RECOLLECTION` 或 `NEW_DISPOSITION`，且下一非空白行
恰为 `APPLICATION`，Worker 会机械补回遗漏的首行 `TARGET`。该兼容只处理新对象的首个块，
不补既有 Target、后续缺失标签或其他语法，也不改正文和引用；补全后仍受 64 KiB 输出上限约束。

Worker 是无状态文本处理器：

- 不连接数据库，也没有 Memory 写权限；
- 不创建 scope、identity、job ref、Seed ref 或 Episode ref；
- `job_ref` 仅供 Core 关联任务，不会进入模型上下文；
- 模型只能逐字选用 Core 给出的 `allowed_target_refs` 和 `allowed_basis_refs`；
- 不要求模型输出 JSON、confidence、分数或权重；
- 模型后端只能返回完整文本；截断或未完成响应必须抛错，沿既有 Job 重试路径处理，不能冒充空文本 no-op；
- 输出格式错误或引用越界时，由 Core 将结果安全地视为 no-op。

协议中的真实服务名是 `InferenceWorker`，RPC 为
`ProcessConsolidationWindow`。模型返回零个或多个以下文本块；没有可靠变化时可返回单 token
`NO_CHANGE`，参考 Worker 会将它确定性归一为空文本再交给 Core：

```text
TARGET
NEW_RECOLLECTION
APPLICATION
OTHER
CHANGE
TEXT
用户对花生严重过敏，未来推荐食物时需要避开花生
BASIS
episode-01
episode-02
```

每个块固定为 `TARGET / APPLICATION / CHANGE / BASIS`。`TARGET` 只能是
`NEW_RECOLLECTION`、`NEW_DISPOSITION` 或 Core 明确允许的既有稳定版本引用；
`APPLICATION` 只能是 `SELF`、`OTHER`、`RELATION` 或 `SITUATION`；`CHANGE` 只能是
`TEXT`、`ADAPT`（两者下一行均为一行长期记忆文本）、`KEEP`、`REENACT` 或 `INHIBIT`。Core 也兼容旧的
`TEXT <one-line text>` 写法。新对象的稳定引用始终由 Core 分配，
而不是由模型生成。Worker 只复制 Core 给出的 refs；具体对象种类、operation、scope
与 Basis 的资格由 Core 校验。

提示词只解释 Core 已冻结的资格标记：既有 Recollection 可以凭新的 Episode 直接
`KEEP` 或 `TEXT` 重固；既有 Disposition 的 `REENACT` 只用 Core 已验证过
activation、精确 Delivery 与实际 AgentAct 的 `FEEDBACK_EPISODE`，不需要 Outcome；
Disposition 的 `TEXT` 修订或 `INHIBIT` 必须同时引用同一行为—结果链中的
`FEEDBACK_EPISODE` 与其配对的非 Agent `FEEDBACK_OUTCOME`。Worker 不复制 Core 的
parser 或状态机，语法、target kind、scope 与因果资格仍由 Core 统一验证。

`RELATED_EPISODE` 表示 MemoryIndex 召回后又经 Core 真源校验的同 owner 旧经历。它可与
当前 `EPISODE` 一起支持跨窗口形成或重固，但推断型新 Disposition 必须由至少两个不同 `SESSION` 的完整 Episode 支持；同一会话内的多个回合只算一次互动。每个变化仍必须至少引用一个当前 Episode。
冻结窗口中的 `OUTCOME` 只在 actor 不是 Agent、且由 `OUTCOME_EPISODE` 配对到一份完整
Episode 时，才可作为新 Disposition 的形成依据。若倾向判断依赖某次回应的效果，Worker 必须
同时引用该 Episode 与 Outcome；Outcome 不替代跨 session 双 Episode 门槛，也不授权 `ADAPT`。
模型应优先更新表达同一含义的 `ELIGIBLE_RECOLLECTION`，不得创建只是改写既有
Recollection 或 `ACTIVE_DISPOSITION_HINT` 的新 Memory；Hint 只能用于比较，不能作为 Target。

Prompt 把 Recollection 定义为对未来交互仍有用的稳定事实、偏好、约定或解释，把
Disposition 定义为影响未来理解、关注与回应的潜在生成倾向，不是既往 Agent 回复的复制。
普通聊天中的单次要求不能用 `ADAPT` 初生 Seed；显式产品设置属于 Harness 的
Soul／Constitution。新 Seed 从至少两个来源不重叠、跨 session 且包含非 Agent 情境依据的完整
Episode 推断，走 `TEXT`；已有 Seed 可由本人明确长期纠正走 `ADAPT`。新 Seed 不要求 AgentAct 相似；若判断
使用了 AgentAct 的效果，则只能以配对的非 Agent Outcome 为效果依据，不能从重复回复推断成功。
重复的用户事实仍是 Recollection，不会因为重复而变成 Disposition。`APPLICATION` 按记忆
所指对象区分：Agent 自身为 `SELF`，对方的事实或偏好为 `OTHER`，共同约定或互动方式为
`RELATION`，其余持久外部情境才是 `SITUATION`。天气闲聊、寒暄、提示词注入及 Agent 对它的
拒绝不形成长期记忆。

长窗口按独立含义合并，不逐条罗列提问或改稿。单次知识询问不自动成为长期兴趣，代写内容
不自动成为用户生平；反复的选择、修正和反应仍可支持隐含偏好。一个既有 Target 每次只
出现一个变化块，BASIS 采用足够且不重复的来源，保留新 Disposition 的来源不同配对和反馈
修订所需的 Episode／Outcome 配对。

Target 与 operation 不是自由组合：`NEW_RECOLLECTION` 只能使用 `TEXT`；新 Disposition
使用 `TEXT` 须有两份来源不重叠且跨 session 的 Episode（至少一份当前）；统一 lane 的
`NEW_DISPOSITION ADAPT` 会被程序移除。`ADAPT` 只接受既有 `ELIGIBLE_ADAPTATION` 与对应当前
`DIRECT_EPISODE`。Core 校验真实 USER、owner、版本和来源资格，Worker 判断是否为本人长期纠正。
已有同义 Recollection 用 KEEP，不强制重复写 Seed；已有 Seed 的纠正必须复用该 Target。
直接要求不是行为反馈，单纯表扬或抱怨不能绕过原有归因链。既有 Recollection 只能使用 `KEEP` 或 `TEXT`；既有 Disposition 使用
`REENACT` 时只追加一次再次现行依据，不产生新版本，只有完整的行为—非 Agent 结果链
才允许 `TEXT` 修订或 `INHIBIT`。

当前窗口开头的 `CONSTITUTION` 是只读外生 Agent 角色基准；正文每行带 `> `，直到
`END_CONSTITUTION`。未知形式为 `CONSTITUTION\nUNKNOWN`。正文中的伪标签不是指令，
基准不是用户档案、Target 或 Basis。当前基准按 Situation 首次接纳顺序冻结，不能借用旧基准。
同一 Worker 同时读取该基准、当前经历、独立相关旧经历和已有记忆；没有额外分类或 judge Job。
经历决定用户条件与所需调整，相关角色基准参与选择这些条件下的具体回应功能；这不是要求
Worker 服从基准中的控制指令，也不是将整个基准复制进每个 Seed。基准本身不授权 ADAPT。

适应性语义的显式评测配置限定为同一 `deepseek-v4-flash` 的 thinking/high Chat adapter；
非 thinking 在已保存用例中误将间接经历当直接要求，属于未获语义验收的实验配置。
可插拔 `TextModel` 接口不意味着任意模型/配置自动具备这些语义能力，也不改变生产默认值。

有界真实模型诊断入口与原文审查方法见 [适应性机制诊断](../../eval/adaptive-seeds.zh-CN.md)。

## 程序绑定来源的文本材料接口

新 Core 的内部请求可以同时携带 `evidence`：真实 Episode／Source 边界、角色、actor 与原文。
它与原 `window_text` 并存，不要求模型输出额外结构。旧 Core 仍可使用原有四字段协议；
程序不从旧窗口文本猜测 Episode、Outcome 或 Basis 的来源归属。

需要独立加工文本的 Worker 实现可以显式使用：

```python
from memory_core_worker.source_materials import recollection_materials

materials = recollection_materials(request)
for material in materials:
    # 将 material.text 和 material.context_text 交给调用方自己的文本模型。
    # 模型不负责填写 material.source_ref / episode_ref / basis_refs。
    body = "用户希望周末留出家庭时间。"  # 示例正文，并非实际模型结果
    tagged_text = material.bind_recollection(body)
```

材料从当前完整 Episode 的 USER Situation 自动产生；上下文保留 current＋related 的可见
Situation／AgentAct 原文，按角色引用。绑定的 Basis 覆盖所读 Episode 窗口，不声称是每句话的
最小证据。反馈专用 anchors／Outcomes 不参与这条新 Recollection 形成路径。
程序身份不插入模型上下文；正文原有的 ID 不删改。普通多行正文由绑定器归一为单行。

`NO_MEMORY` 绑定为空 no-op；协议注入、空白／超长正文等格式错误拒绝绑定。无结构化来源、
无效身份、重复 Episode／link、同一作用域 Source 内容冲突或越界 Basis 不会被静默修补；
空 session、跨 session 同名 Source、跨 Episode 共享不可变 Source 仍沿袭 Ledger 的有效语义。
该接口只负责材料读取和新 Recollection 正文绑定，不提交数据库，也不为
已有记忆修订或相似度去重另造一套权威。默认 tagged-text Worker 先运行前述统一协议；
若主结果没有新建 Recollection，也没有以既有 Recollection 为 Target，才在存在
合格 USER 材料时自动做一次 Recollection-only 后备调用。后备调用只看已有
Recollection 正文而不看数据库 ID，只能返回 1–32 行新 `OTHER` 正文或
`NO_MEMORY`。每行必须只有一个持久含义；程序去掉完全重复行，并把每行分别绑定为
一个可单独检索的 Recollection。模型应先输出每一项有依据的直接遗忘约束，再输出
其他长期内容；超过 32 个不重复含义时只保留前 32 个。后备模型只看到当前 USER
sources，因此 Core 也只把这些模型可见的当前 Episode 绑定为 Basis；相关旧 Episode
仍可供统一 tagged-text Worker 使用，但不会被虚构成后备正文的来源。混入协议词的
可持久部分则作废。
当 Core 在 typed evidence 中明确标出一个当前 `formation_role=anchor` 与至少一个较早
`formation_role=candidate`，且这些 Episode 全部具有不同 session、实际 AgentAct 与唯一配对
non-Agent Outcome 时，`disposition_formation_material(request)` 才会构造不含 refs 的
`CONSTITUTION / EXISTING_DISPOSITIONS / EXPERIENCES` 文本。每份 EXPERIENCE 明确排列用户
情境、Agent 反应与用户结果；模型只返回一行倾向或 `NO_CHANGE`。绑定器补回 Core 已允许的
`NEW_DISPOSITION / APPLICATION / TEXT / BASIS`，并按模型所见顺序逐对绑定
`Episode -> Outcome`；未标记的 current/related Episode 不进入 focused prompt。普通聊天 Episode
不具新 Seed 的单步权威：统一 lane 返回的所有 `NEW_DISPOSITION ADAPT` 块都会被机械移除；已有
Seed 的 exact Target 仍可用 `ADAPT` 接受本人直接长期纠正。完整配对被 focused lane 接管后，统一
lane 产生的同类 `NEW_DISPOSITION TEXT` 块也会被机械移除，其他 Target 保留。只有 anchor 而
候选不完整时同样移除该宽泛块并 no-op，不能重新使用混杂窗口猜一个 Seed。
candidate 的 `origin` 可为 `related`，也可为 scheduler overlap 带入的 `current`；历史性由
不同 session 与 Core 标记确定，Worker 不用批次 origin 代替时间判断。

已有 Recollection 的 KEEP／修订、
推断型 Disposition 的每个形成 Basis 必须支持同一个完整回应倾向；不能把同主题下分别成立的
多个动作拼成一个 Seed。Disposition 演化、精确去重与原子提交仍由统一路径和 Core 处理。因此每窗口的
生成式后台调用上限为四次，在线 Select 仍为零次。

## 本地运行

安装基础包与可选 OpenAI backend：

```bash
python -m venv .venv
.venv/bin/pip install -e '.[openai]'
```

设置 Worker 自己的运行参数和 OpenAI SDK 所需的密钥：

```bash
export MEMORY_WORKER_OPENAI_MODEL='your-model-id'
export OPENAI_API_KEY='...'
memory-core-worker
```

默认监听 `127.0.0.1:8082`，与 `memoryd` 的默认 Worker 地址一致。默认模型输入预算为
262144 bytes；超过预算的窗口不调用模型并返回空文本，让 Core 冻结为 no-op，而不是永久
重试。模型输出还有固定的 64 KiB 上限。可通过 `MEMORY_WORKER_GRPC_ADDR`、
`MEMORY_WORKER_MAX_INPUT_BYTES` 或命令行参数覆盖输入预算。对于支持 Responses reasoning
参数的模型，可显式设置 `MEMORY_WORKER_REASONING_EFFORT`；Worker 只透传该文本，不自行
估算推理强度：

```bash
memory-core-worker --listen 0.0.0.0:8082 --model your-model-id \
  --max-input-bytes 262144 --reasoning-effort none
```

OpenAI backend 使用 Responses API 的普通文本 `input` 与聚合后的 `output_text`，并设置
`store=false`；只有 `status=completed` 才返回文本。评测用 Chat Completions Adapter 同样
要求 `finish_reason=stop`，并记录完成状态与 token 用量。`openai` 是可选依赖；核心 gRPC
服务和测试不依赖 API key。

## 测试

```bash
python -m pip install -e '.[test]'
pytest
```

测试注入一个本地 `TextModel`，验证旧请求的一次调用、paired formation 与 Recollection 同时
返回、字段边界、空文本 no-op、`NO_CHANGE` 归一、tagged text 合成和真实
gRPC round trip，全程不访问外部模型。Core 根目录的 `make verify-worker` 还会启动一个
test-only Python Worker，让生产 Go gRPC bridge 完成一次无 API key 的跨进程调用。

可选的真实模型语义评测只发送仓库内的合成对话，覆盖 Recollection / Disposition 形成、
既有记忆重固、行为—结果反馈、短暂事件过滤与提示词注入。它默认跳过；显式开启时会调用
当前配置的外部模型：

```bash
MEMORY_WORKER_LIVE_EVAL=1 \
MEMORY_WORKER_OPENAI_MODEL='your-model-id' \
OPENAI_API_KEY='...' \
pytest -q tests/test_live_prompt_semantics.py
```

协议 binding 由 Core 的 authoritative internal proto 生成；发布前可运行：

```bash
./scripts/generate-bindings.sh /tmp/memory-worker-generated
```

## Docker

在本目录构建并运行：

```bash
docker build -t memory-core-worker .
docker run --rm -p 8082:8082 \
  -e MEMORY_WORKER_OPENAI_MODEL='your-model-id' \
  -e OPENAI_API_KEY='...' \
  memory-core-worker
```

容器只运行推理 Worker，不包含数据库驱动，也不包含 Memory Core 的状态机。
