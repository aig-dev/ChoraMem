# 固化职责拆分实验：草稿有内容，最终更新仍全部弃置

**结论：此两阶段方案未通过预写门槛，不接入 Core。** 三个完整窗口的 staged 最终输出全部为 `NO_CHANGE`，没有形成目标背景 Recollection。剩余十七个案例停止，不做本候选的端到端复测，不改 Chorai 或生产 Worker。

## 只改变什么

比较同一个最终更新处理器的两种输入：

- control：Task 8 的固定 tagged-text 合同、完整原始窗口、空笔记占位。
- staged：先用 Task 9 的固定纯文本合同整理笔记，再把新鲜笔记逐行引用，连同完整原始窗口交给同一个最终处理器。

笔记明确不是来源、现有记忆或写入权限，两臂使用相同说明。模型、原始来源与 allowed refs 不变；无问题、答案或隐藏 persona 注入。staged 只把第二步结果交给现有 Worker 正规化，草稿不落库。

这验证的是一个特定的内部职责拆分，不代表所有多阶段固化方案。拟议边界仍是一份 Worker 结果和一次 Core 提交；本次实际运行仅是隔离探针，没有提交数据库。

## 实际结果

| 完整窗口 | 预指定应保留背景 | control 最终输出 | staged 最终输出 |
|---|---|---|---|
| 929 | 外交工作、育儿及工作家庭平衡困难 | NO_CHANGE | NO_CHANGE |
| 374 | 产品发布、青年群体志愿服务及精力不足 | NO_CHANGE | NO_CHANGE |
| 742 | 退休、膝痛／行走疲劳及自我感担忧 | 非协议正文，格式失败 | NO_CHANGE |

预写门槛要求至少 2/3 目标背景成为合法 `OTHER` Recollection，并且最终结果没有实质来源／撤回错误。本次目标背景保留为 **0/3**，因此停止。

第一步确实输出了一些相关背景，例如外交工作和孩子、产品发布和志愿活动、退休后的膝痛。这些只是临时草稿，并非已保存或可投递的记忆。草稿也仍有来源／撤回问题，不能直接绕过最终路径写库。最终全部为空，不能解释成“第二步成功纠正了草稿”；它同时丢掉了有用内容。

## 验证与成本

- 本地行为测试：`8 passed`。独立实现审查通过。
- 真实请求：9 次，全部 API 成功且 `finish_reason=stop`；其中 3 次草稿、6 次最终处理，形成 6 条最终 arm 结果。原始生产请求仅供重建验证，没有额外执行。
- 独立出站核对：三份草稿逐字进入对应第二步的引用区，两个阶段均保留完整原始来源，6 份最终结果与实际 provider 输出对应。
- 实际 Go parser／offered-ref 检查：5/6 通过；这五份均为空更新。742 control 报 `invalid change block markers at line 1`，不做正则截取或伪造成功。`check` 返回 1 是这条真实格式失败，不是网络失败。
- 总用量：116,273 输入 + 11,283 输出 = **127,556 tokens**。其中三次草稿调用单独为 **40,461 tokens**；这不是两臂总成本差，因为最终输出长度和输入内容也不同。

模型为 `MiniMax-M2.5`，`https://api.minimaxi.com/v1`，temperature=0、reasoning_split=true、max_output_tokens=8192、单次 timeout=110s、max_retries=0。最多两个案例并行，案例内按冻结顺序执行。未改模型参数以重试坏结果，未运行免跑的九道 validation 题。

## 复核入口

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=sdk/python/src:eval/python/src:worker/python/src \
python3 -m pytest -q -p no:cacheprovider .cache/recollection-two-pass-v1/test_two_pass.py
```

真实运行目录为 `.cache/recollection-two-pass-v1/run-20260911`。只读检查已有 `provider_calls.jsonl`、`results.jsonl`、`grammar.jsonl` 和 `comparison.json`；不要重跑或扩展已停止的案例。`results.input` 在 staged 中是第一步复合入口，不冒充最终模型输入；每一次实际发送内容以 `provider_calls.jsonl` 为准。

| 产物 | SHA-256 |
|---|---|
| `manifest.json` | `14d927e2fd756d144acb6a09377d529bf2d6bc6db190352a03c5a913a1a6a3d5` |
| `results.jsonl` | `8b57ad298fe4934f65ad9c2b883a3cd7c50882627a707e99181022b62f2c92cd` |
| `provider_calls.jsonl` | `835d7ed7684fae65f9ac94249c91af042795517fa933e16842b03e842fbdb16c` |
| `usage.jsonl` | `df9494e5a6df7054889424a858f8999f895243a7081ade184df01f9779af3200` |

这轮没有得到答题收益，也不能从三个诊断窗口推出普遍根因。证据支持的下一步是审视固化流程如何表达内容保留与撤回、如何把整理结果变成可用记忆，不是继续给同一最终裁决合同追加说明。最大瓶颈修复与隔离 Core 端到端改善仍未完成。
