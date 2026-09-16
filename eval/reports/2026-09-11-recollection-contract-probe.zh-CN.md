# 单 Worker 内容／行为门槛分离：短片段改善，完整窗口未通过

## 结论

**不合入候选，不进入端到端复跑。** 候选恢复了短片段和部分人工背景记忆，但 12 个真实完整窗口中仍有 11 个返回 `NO_CHANGE`，且直接 Seed 初生／修订、跨窗归纳出现新回退。现有正式 Worker 不替换，原始基线、Chorai、数据库和公共协议均未改变。

这是一个整体任务合同的对照，不是某一句 prompt 的独立因果证明，也不是答题成绩。

## 固定方法

- 34 cases，原／候选各一次，共 68 次调用。顺序：3 个既有焦点片段、8 个人工来源场景、11 个已有 adaptive fixtures、12 个既有完整窗口。
- 原提示词由当前 `build_model_input` 构建；候选只替换规则与 FINAL_CHECK，`ALLOWED_TARGETS_BEGIN` 至 `WINDOW_END` 逐字相同。模型没有读取问题、gold、隐藏 persona 或答案。
- MiniMax-M2.5，`https://api.minimaxi.com/v1`，temperature 0，reasoning_split true，max_tokens 8192，timeout 110，客户端不重试。`thinking_enabled=false` 是 harness 记录；当前客户端仅对 DeepSeek 发送 thinking 开关，不能据此声称关闭了 MiniMax 的服务端思考。
- 最多两个 case 同时在途，每个 case 两臂顺序执行。首批 3 cases 满足至少 2 个目标背景保留的 gate 后才继续，六次成功请求被复用，没有重复调用。
- 原／候选使用同一 Worker 正规化、同一真实 Go grammar/offered-ref 检查。没有执行数据库资格检查或写入。

## 结果

| 观测 | 原版 | 候选 |
|---|---:|---:|
| API 成功且正常结束 | 34/34 | 34/34 |
| 实际 grammar/ref 通过 | 33/34 | 31/34 |
| 完整窗口 NO_CHANGE | 10/12 | 11/12 |
| 焦点片段中形成 OTHER Recollection | 0/3 | 3/3 |

候选三份焦点 Recollection 中：929、374 保留了预指定核心背景；742 保留膝痛、疲劳及自我感但漏掉退休。929 增加了源没有明确给出的“年幼”年龄限定。focal 不含后续撤回，不能用它的结果证明遗忘机制正确。

人工来源场景中，两臂都守住第三方引用、虚构人物、普通闲聊和伪造引用四个否定边界。候选新增保留童年经历、本人认领的回忆录，并正确对既有同义 Recollection 使用 KEEP；但把明确长期要求写成单 Episode 的 `NEW_DISPOSITION TEXT`，应为 `ADAPT`。这不是有效 Seed 初生。

已有机制样例还出现：

- `direct_birth`：原版正确 ADAPT，候选错误 TEXT，且扩展到源未说的“情绪低落”。
- `direct_correction`：原版修订既有目标，候选选择 NEW 并产生非法 block／引用。
- `cross_window_inference`：原版产生两段依据的 TEXT，候选输出 WINDOW_END 和解释，被 parser 拒绝。
- `role_companion`：候选正确使用双经历 TEXT 并体现倾听与情绪理解；原版误用 ADAPT。这是局部改善，不抵销其他路径的回退。
- `role_coach`：候选双写同义 Recollection 与 Disposition；不能将新增块数计作收益。
- `fact_only`：两臂都识别花生过敏，但两份结果都因格式失败而不可用。
- 用户中性问题、帖子引用、无行为链的赞美、角色正文注入在两臂中均未凭空形成 Seed；既有 Recollection 去重在候选中恢复 KEEP。

完整窗口只在候选 565 产生个人背景 Recollection。自由产品设计师、工作坊主持人、墨尔本工作地等有用户自述依据；18 岁迁居和部分身份叙述出现在 Agent 写的简介中，用户对润色版本的赞许不能直接当作明确的个人事实确认。该摘要混入这些内容，故不能将整份记忆视为来源合格。其新 Disposition 的两个 refs 中，只有一个明确表达归属感困扰，另一个是职业简介，不能仅凭“两条 ref”认定两次同类经验成立。

原版两个非空完整窗口输出均为泛化的遗忘回应规则，也不是目标个人背景的成功保留。

独立语义复核同意拒绝进入 E2E。12 个窗口的 NO_CHANGE 比例不是完整召回率：部分窗口确有应遵守的撤回；明确未撤回的 929／374／742 背景也消失，才是这里的关键反证。

## 工程判断

“多解释普通内容值得记住”能够改善部分短输入，但不足以修复完整固化路径；同一请求同时承担背景整理、去重、Seed 路径选择与精确协议输出的负荷仍值得诊断。现有结果不能区分其中哪一项是唯一原因，也不能据此部署拆任务、缩窗或替换模型。

下一步只做纯文本整理上界的小探针：同一完整源窗口，暂不让模型选择 memory target、操作或引用编号。它用于判断是否值得进一步简化 Worker 的任务分工，不计作可落库结果，不增加运行时模块。

## 复核与产物

实验准备器：19 项测试；连同冻结运行器为 27 项，独立实现审查通过。68 次调用消耗 405,936 input + 44,808 output = 450,744 tokens。两个 native sessions 12637、48278 均结束；最终 Go check 因四份格式失败返回 1，这是已记录的模型结果，不是实验未完成。

目录：`.cache/recollection-contract-v1/run-20260911/`。候选原文：`.superpowers/sdd/2026-09-07-personamem-effect-eval/task-8-contract.md`。复现检查会排他创建产物，既有目录不要重复执行 `probe.py check`；只读复核用：

```bash
go run ./eval/adaptivecheck .cache/recollection-contract-v1/run-20260911/results.jsonl
```

| 文件 | SHA-256 |
|---|---|
| manifest.json | `2bde42c6f57bbb620c1c3a0a3d3942202558f03b9a1e7cf9732317aeea593e37` |
| results.jsonl | `97394d3669586308c91f00a59d65cd33c16c73de369505ea5402d27e28314658` |
| grammar.jsonl | `17fc21d15e472bde5ba7631dfaaa41f6041bd051bdf8eb9f25417058527a893e` |
| usage.jsonl | `9993856b463e3f3b242c14dab5fd2eb0518c2d3a097639580d77e0c4e550e659` |

最后九道 validation 题仍按用户决定不补跑。Goal 尚未完成，没有新的端到端准确率提升证据。
