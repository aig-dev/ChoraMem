# OmniMemEval：完整尝试分母的补充核算

这是对 2026-09-09 原生基线的只读补充，不是重跑、重判答案或替换上游成绩。原实验数据、失败记录、模型和原生报告均未改动。

| 口径 | 分子 / 分母 | 结果 |
|---|---:|---:|
| 上游原生准确率：成功回复 | 1,942 / 4,999 | 38.8478% |
| 补充准确率：全部尝试，未答计错 | 1,942 / 5,000 | 38.84% |
| 回复完成率 | 4,999 / 5,000 | 99.98% |
| 检索完成率 | 5,000 / 5,000 | 100% |

第二行是显式采用“失败计错”的派生统计，不冒充原生 5,000 条成功回复成绩。与其他框架比较时，必须使用相同数据、回答模型和失败计分口径。只有本框架的一个成绩，仍不能证明相对优势。

## 覆盖核对

固定上游 commit 为 `0b1ea8d28aa2d3e03ac4a6aee17b3006a131da7d`。原 CSV 有 5,000 行，SHA-256 为 `95f2a8a324aab7baf2af937feae12731369e2abf7cad5ab3e170594cb25a3e52`。

独立只读审查和 Controller 回算核对了题目／检索／回复／失败／评分的身份集合：

- 检索覆盖原始全部 5,000 个题目 ID。
- 回复与原生 `user_scores` 是同一组 4,999 个 ID，每题一次评分，正确数均为 1,942。
- 回复与失败集合不重叠，两者并集恰好覆盖全部题目，没有遗漏或额外题目。
- 唯一失败为 `pm_exper_user_1181_memory-core-minimax-minilm-v1`，供应商返回 `422 / output new_sensitive (1027)`。保持失败，不重试、不改写问题或规避拦截。

原生 scorer 只给成功回复计分，因此该失败不在 `user_scores` 中；不能只看这个文件就声称原始数据少一题。

## 只读复算

在 Memory Core 仓库根目录执行；不读取凭证或调用模型：

```python
import json
from pathlib import Path

root = Path(".cache/OmniMemEval/results/pmv2/memory_core-memory-core-minimax-minilm-v1")
def load(name):
    return json.loads((root / name).read_text())

search = load("memory_core_pm_search_results.json")
responses = load("memory_core_pm_responses.json")
grades = load("memory_core_pm_grades.json")["user_scores"]
failed = {item["user_id"] for item in load("memory_core_pm_response_status.json")["failed_users"]}
expected = {f"pm_exper_user_{i}_memory-core-minimax-minilm-v1" for i in range(5000)}
assert set(search) == expected
assert set(responses) == set(grades)
assert not set(responses) & failed
assert set(responses) | failed == expected
assert failed == {"pm_exper_user_1181_memory-core-minimax-minilm-v1"}
assert all(row["total_runs"] == 1 for row in grades.values())
correct = sum(row["correct_runs"] for row in grades.values())
assert correct == 1942
print({"native": correct / len(responses), "all_attempt": correct / len(expected),
       "completion": len(responses) / len(expected)})
```

输出为 `native=0.3884776955391078, all_attempt=0.3884, completion=0.9998`。

本次确认的原始文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `memory_core_pm_responses.json` | `81e6f66d3ba33a753ff60bb01479a1a8799816ab18748fab1979e9881fafd7fd` |
| `memory_core_pm_response_status.json` | `4a0c186a7c20582d207011c3f26f0edab0bdfc1b3ad82c664e5745cc103337bd` |
| `memory_core_pm_grades.json` | `18611033791cc4eae790139cd6c4d9ef1a34949fa3d4a8de6c6f64f9a4e3142f` |
| `exp_report.md` | `14a973ab92a72929297385b08cdbc79a21e9fc4f18366925e07feb3709875261` |

这项补充完成的是分母披露，不是效果优化。已接受的 validation 六组与漏斗见 `2026-09-11-personamem-effect-baseline.zh-CN.md`；最大已测瓶颈仍是固化，真实修复与端到端收益尚待验证。
