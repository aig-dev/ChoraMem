# Recollection 逐源定向加工：执行链成立，语义门槛未通过

**结论：不合入，不扩大运行。** 用户批准后完成了三组配对、六次真实调用。将目标、操作、来源绑定移到程序，并把 AI 的加工职责缩小到一段用户经历，仍未稳定形成合格记忆。最大固化瓶颈和端到端收益尚未解决。

## 本次改变的职责

- 程序指定 `MATERIAL_TO_REMEMBER`，AI 只返回记忆正文或 `NO_MEMORY`，不生成 ID、操作、引用或数值。
- AI 同时看到完整的 `CONVERSATION_CONTEXT`，包含按对话顺序排列的全部 28 对 USER／ASSISTANT 原文，用来处理归属、更正和遗忘。没有截掉后续撤回，也没有再把正文交回联合裁决器。
- 程序将正文绑定为 `NEW_RECOLLECTION / OTHER / TEXT`，使用当前窗口全部 Episode refs。这里明确是**粗粒度窗口来源**，不是逐事实最小依据，更不是每条来源都语义支持正文的证明。
- `NO_MEMORY` 不形成候选；格式异常不自动修复成正文。Seed、公共协议、Core 资格校验和事务、Chorai 均未改动。

新鲜对照为：control 的待加工材料是整窗 USER 内容，focused 是预先指定的一段 USER 内容；两侧使用相同指令和相同完整对话背景。模型实际输入不含程序提供的 Episode ref、allowed-target/basis 或资格清单；实际发送文本保存在每条结果的 `request.instructions` 与 `request.input`。

选择的是先前来源诊断已经发现遗漏的三段经历，而不是根据本次结果挑选，也没有向模型提供问题、答案或隐藏 persona。这是定向诊断，不能当作代表性 benchmark 成绩。

## 实际结果

| 完整窗口／指定来源 | control | focused |
|---|---|---|
| 929／history:9 | `NO_MEMORY` | 抄回 `MATERIAL_TO_REMEMBER` 标题、第一人称原文和问题，还保留了 history:12 已撤回的 planner 内容；不是合格记忆正文 |
| 374／history:17 | 有内容，但混入用户背景归属不成立的 Vancouver 等信息 | `NO_MEMORY`，遗漏产品发布、青年志愿服务及精力不足背景 |
| 742／history:24 | 生成较宽背景摘要，但把请求为他人翻译为 Hindi 扩展成了用户理解 Hindi 的能力 | 保留退休、膝痛、耐力和旅行困难；未明确保留自我感丧失的担忧 |

742 focused 的“船长”和“数十年海上经历”在完整窗口中确有用户本人认领的材料支持：history:5 为自己的回忆录、history:14 为自己的个人笔记、history:22 为自己的文章。它们属于上下文补充，不能误报为仅来自 Agent 的幻觉。

预写门槛要求至少 2/3 focused 结果保留指定背景，并且没有实质来源／撤回错误。**即使宽松地把 742 算作完全通过，也只有 1/3；929 另有明确撤回错误，因此门槛失败。** 独立复核同意停止。

结果中的 `status=success` 只表示模型正常返回、绑定函数完成，不表示语义合格。929 的输入回显仍穿过了浅层绑定检查；本次没有在看见结果后删掉标题、清理内容或重跑，再把它记为成功。

## 验证和成本

- 测试先行的隔离代码：最终 `18 passed`；独立代码审查及格式修复复审通过。
- 真实执行：6 条 result、6 条 usage、12 条 started／success 记录，6 个唯一请求；全部 `finish_reason=stop`。
- 独立检查确认：完整 28 对来源无遗漏，配对上下文逐字相同，实际出站输入、原始正文和程序绑定结果各自对应。
- 用量：**45,106 输入 + 4,242 输出 = 49,348 tokens**。control 共 26,406，focused 共 22,942；货币费用没有账单证据，不估算。
- 模型：`MiniMax-M2.5`，`https://api.minimaxi.com/v1`，temperature=0、reasoning_split=true、max_tokens=8192、timeout=110s、max_retries=0。记录中的 `thinking_enabled=false` 不等于关闭了 MiniMax 服务端思考；现有客户端只对 DeepSeek 发送 thinking 开关。

每臂一轮文本调用；如果未来对一个窗口的每段经历都这样处理，后台调用数量会增加。本次只处理三个指定来源，不能据此宣称整窗吞吐或成本已经可接受。

来源语义门槛已失败，因此**没有继续 Go/Core、数据库写入或问答复测**，没有扩大到其他样本，没有重跑九道已免跑 validation 题。原 Core／Worker／Index 服务未替换。

## 复核入口

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=sdk/python/src:eval/python/src:worker/python/src \
python3 -m pytest -q -p no:cacheprovider .cache/recollection-focused-v1/test_probe.py
```

真实调用目录：`.cache/recollection-focused-v1/run-20260911`。该候选已停止，不重跑真实调用。正常退出的 native session 为 `48836`，退出码 0。

| 文件 | SHA-256 |
|---|---|
| `probe.py` | `233f9dbab7f383339a3c64fc20e76de69b6e2db2f00df7a309e5136bbd61bd76` |
| `test_probe.py` | `ad31d17d2f6b2a659bf3e86be68fc6a156ee7828306c8add808ea2a459128960` |
| `manifest.json` | `dab4637a560c90fba39853fcc6ff9c958dce0de3fd9ec4d9d2174523a71edc09` |
| `results.jsonl` | `4882f40e8fdc78939a0ac87da35a1ac062d58761ecfc225765848151ceb8203b` |
| `usage.jsonl` | `6f473683fc7604c6a71442c44bc9ebf303c027965e53c8adef376b1279491757` |
| `attempts.jsonl` | `72280f018e66b1d47b159000b7d33a0aadd1ffa5cd5ccb4ff70b6b1e3994e766` |

## 对架构决策的意义

本次说明：在该固定模型与这些长窗口上，减少数据库协议负担、缩小文本加工职责，仍不足以消除复制原文、遗漏背景和来源混入。不能据此认定所有职责拆分都无效，也没有证据支持把当前单 Worker 架构立即替换成更昂贵的逐源流程。

建议保留生产架构，不继续叠加同类提示词或 Core 模块。若继续定位，优先用另一固化模型做受控能力对照，回答模型、来源和评分保持不变；但这将是新的模型条件，必须单独报告，不能混成同模型横评的收益。本报告不预先授权或实施该下一实验。

### 后续只读核对：尚无调用参数修复依据

未重跑上述候选。新鲜离线验证 `test_personamem_live.py` 与 `test_probe.py` 共 **48 passed**；六条结果的 `raw_output` 与对应 usage 逐字一致，均正常结束，四份运行文件仍匹配上表摘要。源码直接取 `message.content`，没有发现这些正文被客户端丢弃的证据；这不等于语义正确，也不能证明其他模型一定更好。

官方旧兼容指南的搜索摘录把 temperature 范围写为 `(0, 1]`，但本次打开的[当前 Chat Completions 接口说明](https://platform.minimaxi.com/docs/api-reference/text-chat-openai)明确允许 `[0, 2]`，同时说明 M2.x 无法关闭 thinking、`reasoning_split` 仅改变输出格式。因此不能仅凭旧摘录，把已正常返回的 `temperature=0` 认定为故障，或据此再开启采样参数试验。

使用原凭证只读查询 `GET /v1/models` 返回 200，列表包含 `MiniMax-M2.7` 与 `MiniMax-M2.5`。这只证明模型列表可查询，不证明 M2.7 的实际生成权限、配额或效果。本轮新增生成调用为零，未读取其他服务凭证。

下一步建议是新的、待确认的模型条件：相同三份 focused 输入，M2.5 与同服务 M2.7 各一次新鲜调用，总上限六次；正文、来源、参数、绑定及预写语义门槛不变，失败不扩跑，不写 Core。它只回答固化输出是否对模型选择敏感，不形成新公开成绩；回答模型和原基线保持不变。此前逐源方案的停止决定不变，不能把本段理解为模型对照已获准、已执行或整个瓶颈已解决。
