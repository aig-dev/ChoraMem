# PersonaMem-v2 validation 因果诊断预检

结论：六组诊断的数据输入已经可全量复现，但这一步只证明“实验可运行”，不证明 Memory Core 有效果。预检同时证实，旧四组的 `none` 共享了完整用户画像，不能作为自然长对话的无记忆基线；validation 也不能把所有题都解释成长程记忆召回题。

## 冻结输入

- PersonaMem-v2 revision：`0622e56d1cc6f1bc990a5100a6ec4022a60e66a6`
- `val.csv`：17,474,290 bytes；SHA-256 `a47a7dd3879de5e282c6d15266437ed44cf69c0c93c79634645dbd73d655b29a`
- validation：2,061 题、735 个 persona、735 份 32k 历史
- selection manifest SHA-256：`0a305fd26cc564cb3ab9fed1f4987aeb49f20359f37f160d3829c1464919a97e`
- 官方说明将 validation 描述为 2,061 题的独立 split；本地固定文件的逐行检查是本报告数字的直接来源。

## Source availability

| 状态 | 题数 | 解释 |
|---|---:|---|
| 唯一映射到历史 Episode | 1,876 | 可进入长期记忆因果诊断 |
| 历史中不存在 | 185 | 保留总体分数，但不归罪为 Core 漏召回 |

185 条不可用来源中，184 条是 `sensitive_info`，其相关片段不在所链接的 32k 历史且距离字段为 0；另 1 条 `ask_to_forget` 的相关 user 位于未回复历史尾部，按协议不能虚构 assistant 后形成 Episode。

对齐不使用 embedding 或编辑距离。它只接受 user 原文被 Situation 精确包含、多个片段按历史顺序得到唯一 Episode 解；assistant 原文只作佐证，因为固定数据中确有 user 原文保留但 assistant 回复被重建替换的情况。

## 数据异常与确定性处理

- 2,061/2,061 行的 `expanded_persona` 都逐字存在于历史开头的 system 文本。六组全部排除该生成侧完整画像；它不是 Agent Soul，也不是用户声明的设置。旧四组报告保留原样，但其 `none=39%` 只代表“画像已知、无额外长期记忆”。
- persona 166 有一个空 user message：忽略无内容消息，保留所有非空原文。
- persona 731 有一条 `user + assistant=null`：整对丢弃，避免把该 user 错配到下一条 assistant。
- 固定 benchmark 文件实际是 200 个 persona、共 5,000 题，每人 6–42 题，而不是固定每人 25 题。完整横评必须跑所有行。
- 官方 OmniMemEval 文档称 validation 与 benchmark 无 persona 重叠；固定文件检查发现 persona `78` 同时出现（validation 2 题，benchmark 6 题，且链接同一历史）。因此报告不把 validation 称作“persona 完全未见”，但本诊断也不会用 benchmark 答案训练 validation。

## 验证

- 全量 735 份历史均通过 pinned upstream 指纹与结构解析。
- OmniMemEval 固定 revision `0b1ea8d28aa2d3e03ac4a6aee17b3006a131da7d` 的完整 benchmark 已准备：5,000 题、200 个 persona、200 份 32k 历史，引用缺失数为 0；CSV SHA-256 为 `95f2a8a324aab7baf2af937feae12731369e2abf7cad5ab3e170594cb25a3e52`。
- 按 Omni 的真实 20-message 切批重放全部历史，Adapter 得到 23,143 个完整 Episode，与独立历史解析逐一相等；9 个未回复 user 尾部没有被伪造成 Episode。
- 完整 benchmark 的只读 gold 对齐得到 4,480 条唯一 Episode 映射、519 条未匹配和 1 条歧义；519 条未匹配中 511 条属于刻意缺席的 `sensitive_info`，只有 3 条 gold 落在未回复 user 尾部。尾部限制不足以解释 Core 与无记忆接近。
- `make verify-eval`：Python 165 tests passed；TypeScript typecheck、3 tests 与 build passed；Python compileall 与 `git diff --check` 通过。
- 六组真实回答尚未运行；当前进程没有模型、Core、Worker、MemoryIndex 或评测数据库凭证/服务。没有真实答案产物前，不选择 rerank、固化、遗忘或 prompt 作为下一步优化。
