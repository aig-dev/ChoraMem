# PERMA Seed 因果效应评测

这套评测只回答一个问题：在相同模型、Harness、检索结果和总记忆预算下，加入
`Disposition` 是否比仅使用 `Recollection` 更能延续同一用户的偏好；同时，Seed 的修订或
抑制是否真的需要完整的 `Delivery -> AgentAct -> Outcome` 因果链。

它包含两个不可互相替代的门：

1. PERMA 外部效应门：100 道 Type 2/3 选择题，精确选项评分，不使用 LLM judge。
2. 固定 companion lifecycle：形成、现行、正向重演、无投递、无结果、负反馈修订或抑制。

完整冻结依据见
[`Seed 因果闭环与长期陪伴评测设计`](../docs/superpowers/specs/2026-09-12-seed-causal-companion-eval-design.md)。

## 固定输入与三组对照

- PERMA 代码：`MINE-USTC/PERMA@d678640987170e8cfbe9260b311e0493b9cd2c31`
- PERMA 数据：`ustclsc/PERMA@440e64e4fb8baec6f7ad10c1de135505f93e7cb1`
- A 已用于开发诊断，不进入正式结论
- H1 为 `419, 112`，H2 为 `507, 108, 334`；两组在 persona 层互斥且未参与调参
- 每人 10 个完整 task，每个 task 同时评 Type 2 和 Type 3；H1 40 题、H2 60 题，共 100 题
- `none`：不注入长期记忆
- `recollection_only`：只渲染同一次 Select 返回的 Recollection
- `seed_enabled`：渲染同一次 Select 返回的 Recollection 与 Disposition

三组只改变确定性的文本过滤，不各自学习。问题阶段只写一个未绑定 Episode 的 Situation，
不写 Delivery、AgentAct 或 Outcome，所以评测答案不会污染后续题目。`gold_label` 只由落盘后的
scorer 使用。

## 准备数据

```bash
make eval-perma-seed PERSONAMEM_ARGS='prepare --data .cache/PERMA-seed-v2'
```

命令只下载所需的 110 个官方文件，并在 `selection-H1.json`、`selection-H2.json` 中冻结每个
文件的 SHA-256。若 Hugging Face 主站不可达，可设置 `HF_ENDPOINT`；镜像只改变传输路径，
不能改变 pinned revision 或文件 hash。

## 正式运行

先启动独立、一次性的数据库、MemoryIndex、Core 和 Worker，再提供：

```bash
export MEMORY_EVAL_DATABASE_URL='mysql://.../isolated_eval_db'
export MEMORY_CORE_ENDPOINT='http://127.0.0.1:18081'
export MEMORY_CORE_TOKEN='...'
export MEMORY_EVAL_MODEL='MiniMax-M2.5'
export MEMORY_EVAL_MODEL_REVISION='api.minimaxi.com-2026-09-08'
export MEMORY_WORKER_OPENAI_MODEL='MiniMax-M2.5'
export OPENAI_BASE_URL='https://api.minimaxi.com/v1'
export OPENAI_API_KEY='...'
export MEMORY_EVAL_CORE_REVISION='...'
export MEMORY_EVAL_WORKER_REVISION='...'
export MEMORY_EVAL_INDEX_REVISION='...'

make eval-perma-seed PERSONAMEM_ARGS='run \
  --data .cache/PERMA-seed-v2 \
  --output .cache/perma-seed-effect-v2 \
  --run-ref perma-seed-effect-v2'
```

运行先执行四个 lifecycle 场景；未通过就不会开始昂贵的 PERMA 回放。通过后依次运行 H1、H2，
每个 persona 最多一个并发 worker 槽。相同输出目录只允许内容一致的恢复；改变参数或出现半截
lifecycle 时应使用新的 `run-ref` 和空输出目录。

只重算已冻结结果：

```bash
make eval-perma-seed PERSONAMEM_ARGS='summarize \
  --output .cache/perma-seed-effect-v2'
```

## 通过条件

- H1、H2 分别 `seed_enabled > recollection_only`
- H1+H2 的 persona-cluster paired bootstrap 95% 区间下界大于 0
- 每个 split 至少一个净正确 Seed flip，且两边都实际渲染过 Disposition
- 四类 lifecycle 因果门全部通过
- 同 revision 的 PersonaMem `learned_core` 不退化

H1/H2 一旦产生正式结果，就不得据此修改 prompt、Core、选择规则或参数后重跑并继续称为
holdout。失败是有效结论，不允许通过更换切分掩盖。

失败时先看 `formed -> selected -> rendered -> answer flip -> feedback update` 的第一个断点。
本评测不能证明佛学或神经科学理论正确，也不能把模型、Harness 与 Core 的联合效果宣称成
Core 的单独分数。
