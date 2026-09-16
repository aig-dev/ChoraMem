# ANCHOR companion-disposition v1

这是一套 Memory Core 自定义的因果陪伴行为诊断，不是 AnchorBench 官方分数。

它复用 `seed-generalization-v1` 的四个正例形成历史与四个负对照，只以 overlay 改变正例的
answer-time 当前请求、Oracle／Anti Disposition 和目标行为。形成历史保持逐字相同，因此不会用
改题掩盖 Seed precision／recall 问题。

## 四臂

| arm | 上下文 |
|---|---|
| `rag` | Core 选择并渲染的 Recollection |
| `learned_seed` | 相同 Recollection + Core 选择的 learned Disposition |
| `oracle_seed` | 相同 Recollection + 冻结 Oracle Disposition |
| `anti_seed` | 相同 Recollection + 冻结 Anti Disposition |

每个 pair 由盲 Judge 正反顺序各评一次。只有 Oracle 在同一 checkpoint 同时、双向一致地优于
Anti 和 RAG，该 checkpoint 才有资格判断 learned Seed。任一题未通过，不输出架构失败结论。

```bash
python -m memory_core_eval.seed_generalization_cli prepare \
  --data eval/anchor-companion-v1.json

python -m memory_core_eval.seed_generalization_cli smoke \
  --data eval/anchor-companion-v1.json \
  --output .cache/anchor-companion-smoke
```

真实运行沿用 `seed-generalization` 所需的隔离 MySQL、MemoryIndex、Core、Worker 与固定模型环境，
并在 `run`／`summarize` 中都传入同一个 `--data eval/anchor-companion-v1.json`。

2026-09-16 首轮实测见
[实测报告](reports/2026-09-16-anchor-companion-v1.zh-CN.md)：形成、选择、渲染均为 4 / 4，
负对照 0 / 4；3 / 4 checkpoint 可操纵，learned 在这三题为 3 / 3，最终严格结论仍是
`evaluation_ceiling`，不作架构失败判决。
