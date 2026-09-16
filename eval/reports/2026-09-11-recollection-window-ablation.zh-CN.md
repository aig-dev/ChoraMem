# 窗口消融：单片段也未恢复可用背景记忆

本实验不支持直接缩小生产巩固窗口。三个已经核对的真实个人背景片段，在原 28-Episode 窗口和单 Episode 条件下，都未形成格式合格、保留目标背景的 Recollection。

## 设计

固定诊断锚点为 persona 929 的外交工作与育儿压力、374 的工作与志愿服务背景、742 的退休与行动能力变化。它们来自上一实验实际已见源文本，不是随机 holdout，不读取答题标签。

每个锚点各运行 full/focal 两条件，每条件两个新试次，共 12 次调用。所有调用使用未修改的原始 Worker 提示词与 MiniMax-M2.5 参数。full 原文和 refs 逐字不变；focal 保留原 CONSTITUTION、完整锚点 Episode 与一致的新目标资格/ref 集。两个重复试次有独立请求身份，不能被缓存成功记录合并。

同一对条件最多两路并发，固定 hash 决定请求启动顺序，不代表完成顺序。复用调用器的源码摘要已冻结；这个运行边界另见实验目录的 `execution-notes.md`。

## 实际结果

| 条件 | API 成功 | NO_CHANGE | 合法非空输出 | 非法输出 | 合法目标背景 Recollection |
|---|---:|---:|---:|---:|---:|
| full | 6/6 | 4 | 2 | 0 | 0 |
| focal | 6/6 | 5 | 0 | 1 | 0 |

full 的两个合法输出仍是“尊重遗忘请求”的 Disposition，没有保留预先指定的背景。focal 的非法输出（742 第二试次）在说明文字里识别了行动能力变化，末尾也尝试写 Recollection，但混入协议外文字且缺少起始标记，真实 Go parser 拒绝。它不是 NO_CHANGE，也不能据此声称完全没有理解源内容；本次不通过截取尾部来制造成功结果。

12 次调用的 finish_reason 均为 stop。累计输入 99,498 tokens、输出 10,186 tokens，总计 109,684 tokens。

## 能说明什么

- 在这三个已知漏记案例上，缩短输入本身没有恢复可用背景记忆；不能仅把失败归因于长窗口。
- 仍应调查同一 Worker 内 Recollection 与 Disposition 的语义门槛是否混用，以及普通背景保留任务是否被行为规则判断挤掉。当前证据尚未证明哪个具体提示词改法有效。
- focal 同时减少无关内容、可比较经历和后续撤回信息；这是诊断消融，不是可以直接部署的单 Episode 学习或遗忘策略。
- 未改 prompt、检索、Seed 规则、数据库或 Chorai；未验证落库、投递或答题收益。此前九题免跑不变。

产物：`.cache/recollection-window-ablation-v1/run-20260911/` 中的 `manifest.json`、`results.jsonl`、`usage.jsonl`、`grammar.jsonl`、`review.md`。准备器独立审查通过；与复用调用器一起验证为 15 tests passed。
