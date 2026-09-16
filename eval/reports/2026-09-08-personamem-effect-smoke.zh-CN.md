# PersonaMem-v2 六组效果诊断 smoke

结论：修正无记忆基线泄漏和 Worker 模型输出协议后，20 题真实模型 smoke 已证明 PersonaMem-v2 能测出长期记忆作用；当前 Memory Core 尚未兑现该作用。样本识别出的最大性能瓶颈是选择与装配，而不是继续扩展 Seed。

本报告是方向性诊断，不是完整 validation 或 OmniMemEval 公开成绩。

## 冻结运行

- 数据：PersonaMem-v2 validation 前 20 题，20 个 persona，32k 历史
- 数据 revision：`0622e56d1cc6f1bc990a5100a6ec4022a60e66a6`
- Core revision：`0855b244458cfe0685f54602030201411635d55c`
- 回答与后台巩固模型：`MiniMax-M2.5`
- profile policy：六组均排除生成数据时使用的隐藏 `expanded_persona`
- 运行产物：`.cache/personamem-effect-v2/smoke-20-v8/`

## 结果

| Context | 正确数 | 准确率 | 平均记忆 token |
|---|---:|---:|---:|
| none | 7/20 | 35% | 0 |
| full history | 10/20 | 50% | 32,466 |
| Oracle Episode | 15/20 | 75% | 412 |
| semantic Top-40 | 13/20 | 65% | 9,634 |
| current Core | 4/20 | 20% | 1,500 |
| learned Core | 5/20 | 25% | 30 |

关键配对差值：

- Oracle Episode − none：`+40%`，95% persona-cluster bootstrap CI `[+15%, +65%]`。
- current Core − semantic Top-40：`−45%`，95% CI `[−65%, −25%]`。
- learned Core − current Core：`+5%`，95% CI `[−20%, +30%]`，没有可靠提升。

Oracle 显著高于 none，说明问题不是“回答模型即使拿到正确记忆也不会用”，也不是 PersonaMem-v2 完全无法测量记忆。当前 Core 低于 none，说明架构价值尚未转化成这一任务上的产品效果。

## 因果漏斗

20 题中 18 题能把官方相关片段唯一映射到历史 Episode；另外 2 题不进入 Core 因果归因。

```text
source available  18
  -> indexed       9   (50%)
  -> selected      6   (67% of indexed)
  -> delivered     6   (100% of selected)
  -> answer correct 1  (17% of delivered)
```

在 gold 已被索引的 9 题上，semantic Top-40 比 current Core 高 `44.4%`，95% CI `[11.1%, 77.8%]`。按冻结判定规则，当前唯一最大性能瓶颈为 `selection_assembly`。

## 失败机制

这不是单纯的 Top-K 参数问题。向量检索只恢复语义相关性，没有恢复记忆的时间与变更关系。

一个 `ask_to_forget` 样本中，Core Top-8 送达了较早的“喜欢 diner 早餐、鸡蛋和煎饼”，却遗漏了同一主题后来明确的“请忘记这一偏好”；后者虽然存在于 semantic Top-40，却排在预算之外。Oracle 同时提供两段历史时回答正确，current Core 只提供旧状态时回答错误。

因此下一步应让 VDB 负责找到主题入口，而由 Core 恢复该主题的更新链并装配当前有效状态。单纯扩大 Top-K 可以暂时提高召回，却不能可靠处理更正、撤回和遗忘。

## 巩固与遗忘风险

后台 91 个巩固窗口最终只产生 6 个 Recollection 和 3 个 Disposition Seed；learned Core 没有可靠提升。更严重的是，部分巩固文本把“用户要求遗忘的事实清单”重新写成了新的 Recollection。它在语义上描述了遗忘请求，却在存储上保留了本应撤回的内容。

明确遗忘必须成为对已有记忆的撤回／失活状态变更，不能生成一条包含被遗忘内容的新记忆。这个问题在生产接入前属于安全门槛；它与当前分数最大的选择／装配瓶颈分别报告，不用小样本将二者混成一个调参结论。

## 下一步

先优化自然长对话的基础记忆闭环，不继续扩展人格概念：

1. 检索只定位相关主题；选择阶段沿同一主题补齐后续更正、否定和撤回。
2. 装配只暴露当前有效状态，并保留最少必要 Episode 证据。
3. 明确遗忘使旧状态失活并退出索引；禁止把被遗忘事实复制进新 Recollection。
4. 用同一六组重新跑 smoke，再跑完整 2,061 题 validation；最后才做人格陪伴与 Seed 因果闭环的独立评测。

`make verify-eval` 在本次模型适配修正后通过：Python 182 tests，TypeScript typecheck、3 tests 与 build 均通过。
