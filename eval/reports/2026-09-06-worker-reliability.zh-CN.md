# 后台文本可靠性修复：先固定写入，再测试召回

本轮只修复后台文本输出可靠性，没有修改 Core 的状态机、来源资格、数据库结构或在线选择器。尚未重跑修复后的 PersonaMem-v2 答题分数，不能从本报告推导个性化准确率提升。

## 原因与修复

旧 pilot 的 93 个冻结窗口中，9 个输出未通过真实 Core 的语法或提供引用检查。进一步逐项对齐模型用量后发现，其中 8 个达到 8,192 completion tokens；另一个是重复 BASIS。旧报告提到的 4 个截断现象仅覆盖语法不完整这一子类，不是所有达到上限的失败输出。

修复保留一次窗口一次纯文本调用：

- Responses 只有 `status=completed`、Chat Completions 只有 `finish_reason=stop` 才返回文本。未完成响应抛错，沿既有 Job lease/retry 路径重试，不变成永久 no-op receipt。
- 提示词要求读完整窗口后按独立含义合并，不逐条罗列提问或代写内容；保留从反复选择、修正、反应中归纳隐含偏好的能力。
- 同一 BASIS 只去重精确匹配 Core 提供集合的引用。不补 ID、不移除未知引用、不合并重复 Target、不扩大写入资格。
- 去重遇到未知非空行就退出；只按 LF 分行，保留 CRLF、Unicode 文本及异常引用，防止规范化误删跨块来源。

## 真实模型重放

输入使用旧 pilot 的全部 93 个冻结窗口，包括原来的候选与来源集合；没有重新训练记忆来改变输入。模型保持 `deepseek-v4-flash`，temperature=0，thinking disabled，输出上限仍为 8,192 tokens。

| 首轮 93 个真实窗口 | 数量 |
|---|---:|
| 正常完成，真实 Core 语法与提供引用检查通过 | 91 |
| 其中非空变化 | 88 |
| 其中主动 NO_CHANGE | 3 |
| 明确截断，抛错等待重试 | 2 |
| 正常完成但语法／提供引用检查失败 | 0 |

非空输出合计 331 个变化块。首轮 93 次调用共 1,302,743 输入 tokens、74,500 输出 tokens，包含两次截断成本；旧 93 次调用为 1,288,235／108,486。这里不把较少输出 tokens 等同于更好的记忆质量。

随后重复原来失败的 9 个窗口：8 个正常完成，窗口 87 再次截断；窗口 87 第三次完成，输出 413 tokens。窗口 75 第二次完成。所有失败尝试均保留，未提高 token 上限、补引用或替换成 NO_CHANGE。因而是“已观察的 9 个旧失败窗口最终均有完整合法结果”，不是“模型永不截断”。

11 个现有语义分流用例随首轮及重复轮各运行一次，22/22 的 Target、Application、Operation、Basis 预期通过；这不替代对全部真实文本内容的人类语义判定。

三轮共 125 次真实调用：122 个完成输出、3 个明确截断。最后的 Unicode 去重修复后，离线用最终生产 Worker 再处理全部 122 个已完成原始输出，结果与保存输出逐字相同，且重新通过真实 Go parser 与提供引用检查。实际模型调用时的源码指纹和最终规范化源码指纹分别保留，不混称同一版本。

## 实际重试与数据库副作用

另用专用 MySQL 8、真实 memoryd 调度器、真实 gRPC Worker，控制模型后端依次返回“未完成”和“完整”的同一合法文本：

| 状态 | Job attempts | receipt | 新记忆版本 |
|---|---:|---:|---:|
| 首次未完成，lease 释放后 | 1 | 0 | 0 |
| 同一 Job 成功重试后 | 2 | 1 | 1 |

前后 JobRef、冻结 window hash、模型输入一致。此探针证明重试不会因未完成输出提前写 receipt；它使用受控模型响应，不冒充另一次真实模型质量实验。

## 验证与本地产物

- `make verify-worker verify-eval`：41 个 Worker 测试通过、11 个 live 测试默认跳过；真实 Go-to-Python smoke 通过；83 个 Python eval 测试、3 个 TypeScript 测试及类型检查／构建通过。上面的 22 个真实模型语义用例由独立重放运行，不来自默认跳过的 gate。
- `go test ./...` 通过；不声称这条命令运行了 PostgreSQL 真实部署实验。
- 独立审查复核后没有剩余 Critical／Important。

原始模型输入／输出及失败尝试保存在 `.cache/worker-reliability/{replay-v1,repeat-v1,window87-diagnostic}`；最终离线复核在 `final-recheck`，数据库重试证据在 `retry-probe.json`。上游对话文本不随仓库重新分发。

最终复核指纹：

| 文件／数据 | SHA-256 |
|---|---|
| Worker model.py | `bc3b05c04575dd558e187866b79cd859166c3e0cdbdecfa0b8162cb0f96b6499` |
| Worker service.py | `1b9e756d9633fd3beb39f31d7852f70b92aefe79bb0010500913a2c6c1f68d8a` |
| personamem_live.py | `1bc7b1de6b26bda1699098dc6d70e69e956030d059deab6879c2ca0fb48be922` |
| 首轮 windows.jsonl | `0653d0528f937eb418440ad9da98ad36b02e6f1b4661911de93391a86391f810` |
| 重复轮 windows.jsonl | `5fb8d06ec291085d3d5b1caca9cd59ca6874d4c66adb40b4cfe06ecb6354a2ee` |
| 窗口 87 第三次 windows.jsonl | `5e0b543f8d961c9ebd1aed8ae091a7c4c6dc0f385586608695000d32532616f4` |

下一步固定本修复，在未参与这些提示词诊断的 PersonaMem-v2 用户上，只比较语义索引开启／关闭。两组共享同一份冻结学习状态，分别检查候选、实际投递上下文和回答，不能只以索引可连通作为召回有效的证据。
