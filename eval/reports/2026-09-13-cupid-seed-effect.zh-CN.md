# CUPID Seed 正式效应验证：冻结失败结论

日期：2026-09-13

## 结论

本次一次性正式验证的冻结结论是 **FAIL-CLOSED / 未证明稳定增量**。

正式 runner 在 H1 阶段因一份 Judge 返回无法通过冻结的 1–10 分解析契约而终止：

```text
ValueError: invalid CUPID judge score
```

终止时 H1 仅有 `17 / 252` 个实例原子完成，H2 尚未开始。完整的 H1、H2 均值、
pooled persona-cluster 95% 区间和七条通过门因此都不存在。不能据这 17 个先完成的实例
判断 Seed 的方向或大小，也不能把“评测失败”改写为“Seed 没有效果”。可以冻结的唯一
正式判断是：**Seed 相对 Recollection-only 的稳定增量没有被本次实验证明。**

按照预注册规则，本次错误不静默计零，不修改 Judge parser、Prompt、Core、选择规则、
预算或参数后重跑，也不把部分 H1 当作新 dev。CUPID 定向优化阶段到此结束。

## 一次性运行事实

- evaluation ref：`cupid-seed-formal-v1`
- protocol：`cupid-seed-generation-v1`
- 固定顺序：H1 全部完成后才可进入 H2
- 计划规模：H1 `252 instances / 84 personas`；H2 `252 instances / 84 personas`
- 实际完成：H1 `17 / 252`；H2 `0 / 252`
- lifecycle gate：`passed: true`
- Worker：333 个新窗口；未使用 replay
- 终止时缓存：37 份 Answer、32 份 Judge
- 正式 manifest SHA-256：
  `ab2f42c3595860e8262f158516841c178fdfb03b629b17afd7a0d23eb3c7105b`

17 份完整实例及其 memory、Answer/Judge cache 保留在
`.cache/cupid-seed-formal-v1/` 作为失败取证材料，但本报告没有读取其对话、回答、
评分或局部效应。H1/H2 的语义内容仍未被人工查看。

## Fail-closed 复核

正式进程退出后，对同一冻结目录运行离线 `summarize`，汇总器按设计拒绝部分 cohort：

```text
ValueError: CUPID summary requires the complete frozen cohort
```

这证明部分结果没有被误汇总成正式效应。七条门禁状态如下：

| 门禁 | 状态 | 依据 |
|---|---|---|
| H1 平均差值大于 0 | 未评估 | H1 不完整 |
| H2 平均差值大于 0 | 未评估 | H2 未启动 |
| pooled 95% CI 下界大于 0 | 未评估 | 完整双 holdout 不存在 |
| 两组形成、选择、渲染 Seed 且无 Answer/Judge 错误 | 失败 | Judge contract error；H2 未运行 |
| 完整漏斗、refs、tokens、request hashes 可复算 | 失败 | 仅有部分 H1 |
| 四类 lifecycle 通过且 Select 无生成式 AI | 通过 | lifecycle summary 通过；manifest 固定为 0 次 Select 生成调用 |
| PersonaMem A/B 回归门 | 未运行 | 正式结论已因前置硬门失败；旧 A/B 的 Core/Worker revision 不同，不冒充本次证据 |

七条门必须同时成立；第 4、5 条已确定失败，所以整体正式结论为失败。继续花费模型调用
补做不可能改变本次结论的后置门，不会使证据更诚实。

## 冻结边界

正式 manifest 固定了以下运行边界：

- 三组：`none / recollection_only / seed_enabled`
- MemoryContext 总预算：2,048 tokens
- instance workers：4
- Answer / Judge / Worker：
  `MiniMax-M2.5@api.minimaxi.com-2026-09-08`
- Answer / Judge：temperature 0，model retry 0
- Core binary：
  `239b8eb4eab9455492924165f4708a224028e47ee46f5efedfb20df9dc2d44cf`
- Worker service：
  `583a60f1b0a99951d211b95f3432ec699b833df0a5e28ce9b59517abb23b1318`
- CUPID CLI：
  `c8490cffa192e356b454e8fb7c6350a847c7d97aec90dc321d31b1805f5d801c`
- CUPID loader：
  `43b61ce7dcd9149dccc0be3d85fbe09015f602f674cd8f7713b9308b3f09c9b1`
- CUPID effect / Judge：
  `19487b5df473ad1ebdfb977c27c432979ec97b6681f25103b5d5416da49c02d1`
- CUPID runner：
  `a0a7e1d519f273ac881e59009082ff87e5a1992b370fbf0be913310bd9486fdd`
- lifecycle：
  `467a1a4e4241b7872815d1b310616f815313f1147cf785b559bd5e483e89634b`
- Answer prompt：
  `e82a19c05da5c9e1fd1e3f0d8e7e849e2c4aefb0a54a5d4176c28ec86f164d03`
- Judge prompt：
  `44e05df6549d73bbc429c7877b55f8d75a3cdd34a3e199c60ffa4ea0bccd3aa9`
- pinned dataset：
  `6d68af09f7fbe52df0a3bd621604104696d0f481f166be5c06dd65bbb089aaae`
- split manifest：
  `6c0672b252cce5652dc55e53c8450c4bd826ea1ad2ac239380ec4da79fdca5c2`
- MemoryIndex：Chroma 1.5.5、固定 MiniLM archive 与 index source revision

启动前的冻结验证全部通过：`make verify-worker` 为 109 passed / 11 skipped，
`make verify-eval` 为 265 passed / 2 skipped 且 TypeScript 3 passed，
`go test -count=1 ./...` 全部通过。正式运行期间没有修改代码、Prompt 或参数；失败后也没有
恢复或重跑。Core、Worker、Index 服务已经停止，正式数据库与取证目录保持原样。

## 阶段决策

CUPID 提供了有价值的工程结论：生命周期闭环和部分端到端实例能够运行，但冻结 Judge
契约没有支撑完整正式 cohort。它没有提供可发布的 Seed 效果结论。

因此不再针对 CUPID 修 parser、调 Seed、改检索或重复 holdout。下一阶段如继续评测，
应把 CUPID 仅作为已封存的失败实验，把研发入口转向独立预注册的长期陪伴／关系轨迹 eval；
新的 eval 必须使用新的未见数据与自己的运行契约，不能把本次 H1/H2 当作开发集。
