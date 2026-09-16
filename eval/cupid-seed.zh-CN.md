# CUPID Seed 增量效应评测

这项评测只回答一个问题：相同回答模型、相同 2,048-token 记忆预算下，加入
`Disposition` 是否比只有 `Recollection` 更贴近用户在具体境遇中的长期偏好。

它比较三组：

- `none`：无长期记忆；
- `recollection_only`：只渲染 Recollection；
- `seed_enabled`：渲染同一批 Recollection 与被选中的 Disposition。

三组共享一次历史学习和一次在线 `Select`。历史回放只向 Core/Worker 提交对话正文；偏好、
checklist 与 instance type 只进入全部回答生成完成后的 Judge。

## 冻结输入

- 数据：`kixlab/CUPID@f6e5fdae9b31f2b400d6ceb281a6a6760cc00309`
- 文件：`test.parquet`
- SHA-256：`6d68af09f7fbe52df0a3bd621604104696d0f481f166be5c06dd65bbb089aaae`
- persona split：[`cupid-split-v1.json`](./cupid-split-v1.json)
- Answer、Judge、Worker：`MiniMax-M2.5@api.minimaxi.com-2026-09-08`
- Harness：`cupid-seed-generation-v1`

将 pinned parquet 放在 `.cache/cupid-source/test.parquet` 后先做结构校验：

```bash
make eval-cupid-seed CUPID_ARGS="prepare"
```

该命令只输出每个 split 的实例数与 persona 数，不打印 scorer 标签或 holdout 语义内容。

## dev 诊断

dev 可以按完整 persona 做小样本诊断：

```bash
make eval-cupid-seed CUPID_ARGS="run \
  --cohort dev \
  --persona-limit 3 \
  --run-ref cupid-seed-dev-v1 \
  --output .cache/cupid-seed-dev-v1"
```

只允许用 dev 修复一般性的形成、选择或渲染问题。不能根据 H1/H2 的题目、标签或结果修改
Worker prompt、Core、选择规则、预算或 Judge。

## 正式 H1/H2

正式运行必须使用全新的隔离数据库、Core、Worker 与 Index 状态，并设置：

```text
MEMORY_EVAL_DATABASE_URL
MEMORY_EVAL_MODEL=MiniMax-M2.5
MEMORY_EVAL_MODEL_REVISION=MiniMax-M2.5@api.minimaxi.com-2026-09-08
MEMORY_WORKER_OPENAI_MODEL=MiniMax-M2.5
OPENAI_API_KEY
OPENAI_BASE_URL=https://api.minimaxi.com/v1
MEMORY_CORE_ENDPOINT
MEMORY_CORE_TOKEN
MEMORY_EVAL_CORE_REVISION
MEMORY_EVAL_WORKER_REVISION
MEMORY_EVAL_INDEX_REVISION
```

然后只运行一次：

```bash
make eval-cupid-seed CUPID_ARGS="run \
  --cohort formal \
  --run-ref cupid-seed-formal-v1 \
  --output .cache/cupid-seed-formal-v1"
```

运行器先执行四类 Seed lifecycle 门，再完整运行 H1，最后完整运行 H2。每个实例只在三组回答
和三次 Judge 都完成后原子落盘；中断恢复只复用逐字相同的 manifest 和内容寻址模型请求。

离线重新汇总不会调用模型：

```bash
make eval-cupid-seed CUPID_ARGS="summarize \
  --output .cache/cupid-seed-formal-v1"
```

正式通过要求 H1、H2 的 `seed_enabled - recollection_only` 均大于零，合并后的 persona-cluster
bootstrap 95% 区间下界也大于零，并且两个 holdout 都真实发生 Disposition 的形成、选择与渲染。
正式结果失败就是该冻结版本的失败，不得改参重跑后继续称为同一 holdout。
