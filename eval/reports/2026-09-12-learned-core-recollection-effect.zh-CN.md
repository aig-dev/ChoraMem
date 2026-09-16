# learned_core：Recollection 形成与检索效果

日期：2026-09-12

## 结论

本轮达成了一个有限但可复现的结果：在相同回答模型、Harness、2,048-token Core
预算下，`learned_core` 在两个按 persona 隔离的 PersonaMem-v2 validation 样本上都
高于 `none`：

| 样本 | questions / personas | none | learned_core | 配对差值 |
|---|---:|---:|---:|---:|
| A（开发冻结样本） | 35 / 12 | 14 / 35（40.0%） | 18 / 35（51.4%） | +4 题，+11.4pp |
| B（代码冻结后首次运行） | 32 / 12 | 9 / 32（28.1%） | 15 / 32（46.9%） | +6 题，+18.8pp |
| A + B | 67 / 24 | 23 / 67（34.3%） | 33 / 67（49.3%） | +10 题，+14.9pp |

按 persona 整簇、seed=0、2,000 次 bootstrap，`learned_core - none` 的 95% 区间为：

- A：`[-2.6pp, +30.8pp]`；
- B：`[+3.3pp, +32.4pp]`；
- A+B：`[+4.5pp, +25.5pp]`。

A 的小样本区间单独仍跨 0；未参与调参的 B 和合并样本为正。本报告因此只声明该固定
设置下的可重复正向收益，不声明 PersonaMem 全量或跨模型领先。

## 冻结条件

两组都使用：

- PersonaMem-v2 validation / 32k histories；persona 作为不可拆分抽样单元；
- 回答与 Worker：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`；
- MemoryIndex：Chroma 1.5.5、`all-MiniLM-L6-v2@913d7300`、cosine、chunk 220 / overlap 32；
- `memory_tokens=2048`、`episode_evidence_max_bytes=16384`、`batch_turns=28`；
- 六组逐题共享选项与回答协议，顺序按 question identity 确定性交错；
- 用户隔离 salt：A=`learned-core-heldout-a-20260912`，B=`learned-core-heldout-b-20260912`；
- B 使用全新 MySQL schema、全新 Worker 日志和冻结后的 Core binary；运行前未查看 B
  的题目、对话或答案。

原始产物：

- A 形成对照：`.cache/learned-core-eval-v4/heldout-a-v6-formation-complete/`；
- A 选择隔离重放：`.cache/learned-core-eval-v4/heldout-a-v7-selection-replay-v5/`；
- B 端到端验证：`.cache/learned-core-eval-v4/heldout-b-v1-final/`；
- B Worker：`.cache/learned-core-eval-v4/heldout-b-v1-final-worker/`。

## 两个根因与最小修复

### 1. 形成结果被静默整批丢弃

旧的纯文本后备最多保留 6 行，并把模型未看到的 related Episodes 也绑定到每条正文。
在 28 个对话 Episode 的窗口中，模型常返回超过 6 个独立长期含义；宽 Basis 又会把
序列化结果推过 64 KiB，导致整个后备结果成为 no-op。

修复后：

- 后备允许最多 32 个独立纯文本 Recollection；
- 明确要求先输出每个有依据的直接遗忘约束；
- Basis 只绑定该后备模型实际看到的当前 USER Episodes；
- AI 仍不输出 ID、confidence、weight 或 JSON 关系图，Core 继续拥有身份与提交权。

B 的 56 个窗口最终形成 334 个 active Recollection；54 次后备调用中有 30 次输出超过
旧的 6 行上限，最长 19 行。334 条 active 正文没有完全重复组。B 没有形成
Disposition，说明本轮增益主要来自 Recollection，而不是 Seed。

### 2. Core 丢掉了 VDB 的语义顺序

Storage 已拿到 MemoryIndex 的 `querySourceRef / rank / laneOrder`，但 rehydrate 和
canonical union 后丢失这些瞬时关联；`Select` 又用 character-bigram Dice 重排英文
长句。常见字母组合会让无关学术、服装或健康记忆高于真正主题。

修复后：

- 直接命中的 Recollection 保留 Provider 的 query／owner lane 顺序；
- exact-owner、active version、live Basis 校验仍由关系库完成；
- direct index association 只影响本次选择，不持久化 score，也不授予写入资格；
- Episode Basis 扩展出的兄弟 Recollection 仍须匹配自己的正文，不能因共享宽窗口一起
  现行；
- 无直接命中时，Latin 文本按 topic-term overlap 保底，中文等无词边界文本继续使用
  character bigram；
- 在线 `Select` 没有新增生成式 AI 调用。

A 的同一批已形成记忆在只清空 Context／Delivery 后重放，`learned_core` 从 15/35
升至 18/35，平均上下文从 181.2 降至 52.7 tokens；这是检索改动的隔离对照。

## 完整 Core 检查

| 样本 | semantic_top40 | current_core | learned_core | mean tokens（current / learned） |
|---|---:|---:|---:|---:|
| A | 19 / 35 | 19 / 35 | 18 / 35 | 1806.2 / 52.7 |
| B | 17 / 32 | 11 / 32 | 15 / 32 | 1737.7 / 67.1 |

`current_core` 对 `none` 的点估计在 A/B 均为正（+14.3pp / +6.3pp），但区间均跨 0，
不能声明稳定提升。更重要的是，B 中 `learned_core - current_core = +12.5pp`，95% 区间
为 `[+2.6pp, +29.2pp]`。也就是说，本次 learned memory 本身有效，但在同一 2,048-token
预算中再加入较长 Episode evidence 会稀释它。

因此默认 `episode_evidence_max_bytes=0` 的产品边界是合理的；原文证据应是 Harness
显式启用的诊断／引用能力，而不是 learned memory 的默认组成。下一轮若继续优化，应先
改进 Episode evidence 的精排与预算竞争，而不是增加更多记忆类型。

## 限制

- 总计只有 24 个 persona、67 题；不是公开 leaderboard 或全量 validation。
- 只验证一个回答模型和一个本地 embedding Provider。
- PersonaMem 同时测长程取回和对代写／翻译材料的 persona 归因；Core 没有为追分放宽
  source ownership，因此不能把剩余 Oracle 差距全解释为检索失败。
- B 的 `none/full_history/oracle_episode/semantic_top40` 分别出现 1/2/2/1 个无效回答；
  `current_core` 与 `learned_core` 均为 0。无效回答按错误计入。
- 本轮没有在 B 触发 Disposition 形成，也没有证明长期 Seed 重演、抑制或人格演化效果。
