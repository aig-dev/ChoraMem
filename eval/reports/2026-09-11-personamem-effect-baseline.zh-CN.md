# PersonaMem-v2 已接受样本效果基线

记录日期：2026-09-11，Asia/Shanghai。

## 结论

本报告只统计冻结 manifest 中已经完整得到六个配对回答组的 **2,052 / 2,061** 题，覆盖率 **99.5633%**，涉及 732 个 persona。九道未完成题按本轮一次性、精确的免跑集合显式排除；因此这是“已接受样本基线”，不是原始全量完成声明。

在可进入因果判断的 1,867 题中，现有冻结决策规则按显著阶段差值的点估计大小选择 **consolidation**：`current_core - learned_core`（仅 delivered 条件）为 **+28.9873 个百分点**，95% CI **[+24.9057, +33.4981]**，n=790。这是该规则选出的最大点估计，不证明 consolidation 在统计上大于 retrieval；retrieval 的条件差值也为正，且两者区间重叠。该结果也不等于已经确定某个 prompt 修复方案。

## 六组准确率

| 回答组 | 正确 / n | 准确率 | 平均 memory tokens | 无效答案 |
|---|---:|---:|---:|---:|
| none | 578 / 2,052 | 28.1676% | 0.0000 | 3 |
| full_history | 879 / 2,052 | 42.8363% | 32,775.7919 | 11 |
| oracle_episode | 1,370 / 2,052 | 66.7641% | 442.3255 | 28 |
| semantic_top40 | 880 / 2,052 | 42.8850% | 12,253.8981 | 8 |
| current_core | 826 / 2,052 | 40.2534% | 1,761.4542 | 11 |
| learned_core | 572 / 2,052 | 27.8752% | 16.9951 | 5 |

## 全样本配对差值与区间

差值按题加权；95% 区间使用 persona 聚类配对 bootstrap，固定 seed=0、2,000 次采样。

| 比较 | 差值（百分点） | 95% CI（百分点） |
|---|---:|---:|
| oracle_episode − none | +38.5965 | [+35.8001, +41.2698] |
| full_history − none | +14.6686 | [+11.8845, +17.5158] |
| semantic_top40 − oracle_episode | −23.8791 | [−26.5509, −21.4752] |
| current_core − semantic_top40 | −2.6316 | [−5.1572, +0.0489] |
| learned_core − current_core | −12.3782 | [−15.1469, −9.7092] |

## 因果漏斗

| 阶段 | 题数 | 条件转化率 | current_core 条件准确率 |
|---|---:|---:|---:|
| 总题数 | 2,052 | — | 40.2534% |
| source_available | 1,867 | 90.9844%（占总题数） | 40.8141% |
| indexed | 1,510 | 80.8784%（给定 source） | 43.1788% |
| selected | 853 | 56.4901%（给定 indexed） | 51.9343% |
| delivered | 790 | 92.6143%（给定 selected） | 53.1646% |
| learned_basis_selected | 81 | — | 45.6790% |
| learned_basis_delivered | 81 | — | 45.6790% |

185 题因 `source_available=false` 排除出 Core 因果瓶颈决策；它们仍留在六组总体准确率中。条件阶段差值分别为：oracle_episode − none（source available）+42.6888pp，CI [+40.0000, +45.4887]；oracle_episode − semantic_top40（source available）+25.8168pp，CI [+23.2004, +28.3659]；semantic_top40 − current_core（indexed）+3.3775pp，CI [+0.5295, +6.3342]；current_core − learned_core（delivered）+28.9873pp，CI [+24.9057, +33.4981]。

## 输入完整性与边界

- 输入：`.cache/personamem-effect-minilm/validation-all-v1`，只读。
- manifest SHA-256：`8612287421f11e000e1220d5732c78c6c81eb713717b7150742615bf4fbefbd4`。
- 2,052 份结果文件的有序文件名与原始字节聚合 SHA-256：`617862ef0483aa7ddc35a03cb6404d1c77282b718e499c9b4893e6f6beaf2bc4`。
- 精确缺题：`row-00361`、`row-00362`、`row-00364`、`row-00519`、`row-00815`、`row-01007`、`row-01232`、`row-01756`、`row-01976`。
- 来源记忆覆盖 735 个 persona；原 completion proofs 为 731 份，persona 286 仅部分问题完成且有 before snapshot。此前只读核对的 735 份 before snapshot 一致，但本报告不伪造缺失 proof。
- 离线校验会拒绝 manifest 漂移、额外缺题、非 manifest 题、重复题、文件名/题目身份不一致、错 owner、缺失或重复回答组。若要求零缺题，原始输入仍以精确九题缺失而失败。

补充诊断仅用于解释 consolidation 观察，不改变统计：冻结的 735 份 snapshot 含 84,290 Episodes、3,405 jobs、95 个 Recollection versions、191 个 Seed versions；1,506 / 2,052 个 learned_core prompt 为空。对原始模型输出按未修改的 Worker normalization 与实际 Go parser 重放时，3,436 次成功输出尝试覆盖 3,405 jobs，其中 2,825 次 `NO_CHANGE`、216 次 grammar failure、9 次 reference failure、386 次同时通过 grammar 与 reference。这里是 attempt 数，不是 committed-window 数，不能据此声称具体修复已经成立。

## 与独立横评的关系

既有 `eval/reports/2026-09-09-omni-minimax-baseline.zh-CN.md` 报告的是独立 OmniMemEval 横评：**1,942 / 4,999 = 38.8478%**，另有 1 次 provider failure。其数据集、分母和判分链与本报告不同，数值不得混算，也不能据此单独证明 Memory Core 优于其他框架。

## 离线复现

```bash
PYTHONPATH=sdk/python/src:eval/python/src \
  eval/python/.venv/bin/python \
  eval/python/scripts/summarize_accepted_validation.py
```

派生 JSON 写入 `.cache/personamem-effect-minilm/accepted-validation-baseline-v1/summary.json`，不会覆写源目录。该入口不调用模型、不连接或修改数据库，也不改 frozen harness。
